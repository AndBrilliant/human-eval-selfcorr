#!/usr/bin/env python3
"""Arm 3 PHASE A: execute the exact frozen Stage 0 candidates against the
HumanEval reference tests and freeze the binary execution signals.

    approved frozen Stage 0 candidate
        ->
    recompute candidate_sha256 (must match frozen ledger)
        ->
    HumanEval reference tests in a fresh subprocess (15 s timeout)
        ->
    reduce to execution_status pass/fail + exact treatment text
        ->
    append + fsync arm3_execution_signals.jsonl      <-- FREEZE POINT

This phase makes NO API calls and reads NO ground truth: baseline.jsonl is
never opened here. The candidate is NEVER modified — there is no
correction or revision code path in this module.

Signal semantics:
  - candidate timeout (15 s) is a genuine FAIL outcome, never retried;
  - assertion/other candidate-caused failure is FAIL, never retried;
  - local test-runner infrastructure failure (spawn/IO) retries the
    identical test per the established policy (max 3 attempts), and if
    exhausted leaves the task unsignaled for resume (never a silent drop).

Once a task_id exists in arm3_execution_signals.jsonl, it is never
re-executed — including on resume after later evaluator-phase failures.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import config as root_config
from arms.execution import config as acfg
from common.dataset import load_tasks
from common.execution import (
    TestInfraError,
    run_tests_once,
    test_python_provenance,
)


class Paths(NamedTuple):
    signals: Path
    failures: Path
    log: Path


DEFAULT_PATHS = Paths(
    signals=acfg.SIGNALS_PATH,
    failures=acfg.FAILURES_PATH,
    log=acfg.RUN_LOG_PATH,
)


# ── Utilities ─────────────────────────────────────────────────────────

def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def append_jsonl(path: Path, record: dict) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


class RunLogger:
    def __init__(self, path: Path, echo: bool = True):
        self.path = path
        self.echo = echo

    def __call__(self, event: str, **fields) -> None:
        rec = {"ts": utc_now(), "event": event, **fields}
        append_jsonl(self.path, rec)
        if self.echo:
            print(json.dumps(rec, ensure_ascii=False), flush=True)


def load_jsonl(path: Path) -> List[dict]:
    records = []
    if not Path(path).is_file():
        return records
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(
                    f"CORRUPT ARM 3 LEDGER: malformed JSON in {path} line "
                    f"{lineno}: {exc}. Fix manually before resuming."
                )
    return records


# ── Source corpus (Stage 0 frozen ledger; the ONLY candidate input) ───

def verify_source_ledger(path: Path = acfg.FROZEN_SOURCE_PATH) -> None:
    """Abort BEFORE any execution/API work if the Stage 0 frozen ledger
    is not byte-identical to the approved corpus."""
    digest = sha256_file(path)
    if digest != acfg.APPROVED_FROZEN_SHA256:
        raise SystemExit(
            f"ARM 3 ABORT: source frozen ledger {path} sha256 is {digest}, "
            f"approved corpus is {acfg.APPROVED_FROZEN_SHA256}. "
            f"Refusing to proceed."
        )


def load_frozen_records(path: Path = acfg.FROZEN_SOURCE_PATH) -> List[dict]:
    """Load frozen candidates in canonical order (ground-truth-free fields
    only). Verifies each candidate hash (abort on mismatch)."""
    records = load_jsonl(path)
    out = []
    for rec in records:
        for field in ("task_id", "task_index", "prompt", "entry_point",
                      "candidate_code", "candidate_sha256"):
            if field not in rec:
                raise SystemExit(
                    f"ARM 3 ABORT: frozen record {rec.get('task_id')} missing "
                    f"required field {field!r}."
                )
        if sha256_text(rec["candidate_code"]) != rec["candidate_sha256"]:
            raise SystemExit(
                f"ARM 3 ABORT: candidate_sha256 does not match candidate_code "
                f"for {rec['task_id']} in the frozen ledger. Refusing to "
                f"proceed."
            )
        out.append({k: rec[k] for k in
                    ("task_id", "task_index", "prompt", "entry_point",
                     "candidate_code", "candidate_sha256")})
    out.sort(key=lambda r: r["task_index"])
    return out


# ── Treatment mapping (exact; reveals only PASS vs FAIL) ─────────────

def treatment_for(test_status: str):
    """Map the raw runner outcome to (execution_status, treatment_text).

    The treatment NEVER includes test source, assertion text, stack
    traces, expected values, failing inputs, or exception details.
    """
    if test_status == "pass":
        return "pass", acfg.TREATMENT_PASS
    if test_status == "timeout":
        return "fail", acfg.TREATMENT_FAIL_TIMEOUT
    # fail_assertion / fail_error: candidate-caused failure
    return "fail", acfg.TREATMENT_FAIL


# ── Signal ledger ─────────────────────────────────────────────────────

SIGNAL_REQUIRED_FIELDS = [
    "schema_version", "experiment_version", "arm", "task_id", "task_index",
    "source_frozen_ledger_sha256", "candidate_sha256",
    "execution_status", "execution_treatment_text", "execution_test_status",
    "execution_duration_s", "execution_infra_retry_count",
    "test_python_executable", "test_python_version", "executed_at",
]

SIGNAL_PROTOCOL_FIELDS = {
    "schema_version": acfg.SCHEMA_VERSION,
    "experiment_version": acfg.EXPERIMENT_VERSION,
    "arm": acfg.ARM_NAME,
    "source_frozen_ledger_sha256": acfg.APPROVED_FROZEN_SHA256,
}


def validate_signal_ledger(path: Path,
                           frozen_by_id: Dict[str, dict]) -> Dict[str, dict]:
    """Startup validation of the frozen execution-signal ledger.

    Rejects: malformed records, missing fields, duplicate task IDs, ANY
    task_id not present in the approved frozen corpus, task_index
    disagreement, candidate-SHA disagreement, protocol-metadata
    disagreement, and any execution_test_status / execution_status /
    treatment combination outside the allowed mapping table. The ledger
    is self-validating; it is never trusted merely because the current
    writer produced it.
    """
    signals: Dict[str, dict] = {}
    for rec in load_jsonl(path):
        tid = rec.get("task_id")
        missing = [f for f in SIGNAL_REQUIRED_FIELDS if f not in rec]
        if missing:
            raise SystemExit(
                f"CORRUPT ARM 3 SIGNAL LEDGER: record for {tid} missing "
                f"fields {missing} in {path}.")
        if tid in signals:
            raise SystemExit(
                f"CORRUPT ARM 3 SIGNAL LEDGER: duplicate task_id {tid} in "
                f"{path}.")
        if tid not in frozen_by_id:
            raise SystemExit(
                f"CORRUPT ARM 3 SIGNAL LEDGER: unexpected task_id {tid} in "
                f"{path} (not present in the approved frozen corpus).")
        fr = frozen_by_id[tid]
        if rec["task_index"] != fr["task_index"]:
            raise SystemExit(
                f"CORRUPT ARM 3 SIGNAL LEDGER: task_index {rec['task_index']} "
                f"for {tid} disagrees with frozen record "
                f"{fr['task_index']}.")
        if rec["candidate_sha256"] != fr["candidate_sha256"]:
            raise SystemExit(
                f"IMMUTABILITY VIOLATION: signal candidate_sha256 for {tid} "
                f"does not match the frozen Stage 0 ledger.")
        for field, expected in SIGNAL_PROTOCOL_FIELDS.items():
            if rec[field] != expected:
                raise SystemExit(
                    f"PROTOCOL MISMATCH: signal {tid} has {field}="
                    f"{rec[field]!r}, Arm 3 protocol requires {expected!r}.")
        # Allowed status/treatment mappings ONLY (self-validating ledger):
        ts = rec["execution_test_status"]
        es = rec["execution_status"]
        tt = rec["execution_treatment_text"]
        allowed = {
            "pass": ("pass", acfg.TREATMENT_PASS),
            "timeout": ("fail", acfg.TREATMENT_FAIL_TIMEOUT),
            "fail_assertion": ("fail", acfg.TREATMENT_FAIL),
            "fail_error": ("fail", acfg.TREATMENT_FAIL),
        }
        if ts not in allowed:
            raise SystemExit(
                f"CORRUPT ARM 3 SIGNAL LEDGER: {tid} has unknown "
                f"execution_test_status {ts!r}.")
        exp_es, exp_tt = allowed[ts]
        if es != exp_es or tt != exp_tt:
            raise SystemExit(
                f"CORRUPT ARM 3 SIGNAL LEDGER: {tid} has inconsistent "
                f"execution_test_status={ts!r} with execution_status={es!r} "
                f"and treatment {tt!r} (expected {exp_es!r} / "
                f"{exp_tt!r}).")
        signals[tid] = rec
    return signals


def build_signal_record(task: dict, test_status: str, duration_s: float,
                        infra_retries: int) -> dict:
    execution_status, treatment = treatment_for(test_status)
    rec = {
        "schema_version": acfg.SCHEMA_VERSION,
        "experiment_version": acfg.EXPERIMENT_VERSION,
        "arm": acfg.ARM_NAME,
        "task_id": task["task_id"],
        "task_index": task["task_index"],
        "source_frozen_ledger_sha256": acfg.APPROVED_FROZEN_SHA256,
        "candidate_sha256": task["candidate_sha256"],
        "execution_status": execution_status,
        "execution_treatment_text": treatment,
        "execution_test_status": test_status,
        "execution_duration_s": duration_s,
        "execution_infra_retry_count": infra_retries,
        "executed_at": utc_now(),
    }
    rec.update(test_python_provenance())
    return rec


def execute_task(task: dict, test_code: str, log: Callable) -> dict:
    """Execute the EXACT frozen candidate against the reference tests and
    return a signal record. Retries ONLY local infrastructure failures."""
    # Hash discipline: execute exactly the ledger-attested candidate.
    if sha256_text(task["candidate_code"]) != task["candidate_sha256"]:
        raise SystemExit(
            f"ARM 3 ABORT: candidate hash mismatch for {task['task_id']} "
            f"immediately before execution.")
    for attempt in range(1, root_config.TEST_MAX_ATTEMPTS + 1):
        try:
            status, details, correct, dur = run_tests_once(
                task["candidate_code"], test_code, task["entry_point"],
                root_config.TEST_TIMEOUT_S)
            return build_signal_record(task, status, dur, attempt - 1)
        except TestInfraError as exc:
            if attempt == root_config.TEST_MAX_ATTEMPTS:
                raise
            log("exec_infra_retry", task_id=task["task_id"],
                attempt=attempt, error=str(exc)[:300])
            time.sleep(root_config.TEST_BACKOFF_BASE_S * attempt)
    raise AssertionError("unreachable")


# ── Phase A driver ────────────────────────────────────────────────────

def run_signals(paths: Paths, frozen_records: List[dict],
                tests_by_id: Dict[str, str], log: Callable) -> int:
    """Freeze execution signals for all tasks lacking one. Return codes:
        0 = all 164 signals frozen
        1 = incomplete (infrastructure; resume later)
    """
    frozen_by_id = {t["task_id"]: t for t in frozen_records}
    signals = validate_signal_ledger(paths.signals, frozen_by_id)
    todo = [t for t in frozen_records if t["task_id"] not in signals]
    log("arm3_signals_start", total=len(frozen_records),
        signals=len(signals), todo=len(todo))

    for task in todo:
        tid = task["task_id"]
        test_code = tests_by_id.get(tid)
        if test_code is None:
            raise SystemExit(
                f"ARM 3 ABORT: no reference test found in the canonical "
                f"dataset for {tid}.")
        try:
            rec = execute_task(task, test_code, log)
        except TestInfraError as exc:
            append_jsonl(paths.failures, {
                "schema_version": acfg.SCHEMA_VERSION,
                "experiment_version": acfg.EXPERIMENT_VERSION,
                "arm": acfg.ARM_NAME,
                "task_id": tid,
                "task_index": task["task_index"],
                "failure_stage": "execution_infra",
                "error": str(exc)[:500],
                "failed_at": utc_now(),
            })
            log("task_failed", task_id=tid, stage="execution_infra",
                error=str(exc)[:300])
            continue
        append_jsonl(paths.signals, rec)
        signals[tid] = rec
        log("signal_frozen", task_id=tid,
            execution_status=rec["execution_status"],
            execution_test_status=rec["execution_test_status"],
            infra_retries=rec["execution_infra_retry_count"])

    complete = set(signals) == set(frozen_by_id)
    log("arm3_signals_end", signals=len(signals),
        expected=len(frozen_records), complete=complete)
    if not complete:
        print("\nINCOMPLETE ARM 3 PHASE A: re-run to resume execution of "
              "the remaining tasks.", file=sys.stderr)
        return 1
    print(f"\nArm 3 Phase A complete: {len(signals)} execution signals "
          f"frozen. Run validate_execution_signals.py (integrity check vs "
          f"Stage 0 T0) BEFORE any evaluator call.", flush=True)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--signals", type=Path, default=acfg.SIGNALS_PATH)
    ap.add_argument("--failures", type=Path, default=acfg.FAILURES_PATH)
    ap.add_argument("--log", type=Path, default=acfg.RUN_LOG_PATH)
    args = ap.parse_args(argv)

    # ABORT BEFORE ANY EXECUTION if the Stage 0 corpus is not byte-exact.
    verify_source_ledger()
    frozen_records = load_frozen_records()
    if len(frozen_records) != root_config.EXPECTED_N_TASKS:
        raise SystemExit(
            f"ARM 3 ABORT: expected {root_config.EXPECTED_N_TASKS} frozen "
            f"records, got {len(frozen_records)}.")

    # Reference tests come from the hash-pinned canonical dataset (the
    # same source Stage 0 used). Neutral input, no ground-truth labels.
    dataset_tasks = load_tasks()
    tests_by_id = {t["task_id"]: t["test"] for t in dataset_tasks}
    # Sanity: frozen prompts/entry points must match the canonical dataset.
    for t in frozen_records:
        canon = next(d for d in dataset_tasks if d["task_id"] == t["task_id"])
        if canon["prompt"] != t["prompt"] or \
                canon["entry_point"] != t["entry_point"]:
            raise SystemExit(
                f"ARM 3 ABORT: frozen prompt/entry_point for {t['task_id']} "
                f"does not match the canonical dataset.")

    paths = Paths(signals=args.signals, failures=args.failures, log=args.log)
    log = RunLogger(paths.log)
    return run_signals(paths, frozen_records, tests_by_id, log)


if __name__ == "__main__":
    sys.exit(main())
