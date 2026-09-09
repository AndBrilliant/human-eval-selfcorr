#!/usr/bin/env python3
"""benchmark_v2 Stage 0: generate and score the immutable HumanEval baseline.

    HumanEval problem
          |
    one GPT-5.4 candidate (pinned snapshot, temperature 0)
          |
    deterministic extraction + hashing
          |
    fsync frozen candidate record  ->  data/frozen_candidates.jsonl
          |
    ONLY THEN: HumanEval reference tests in a fresh subprocess
          |
    scored record (derived from the frozen candidate)  ->  data/baseline.jsonl

On-disk transaction boundary: an API response is extracted, hashed, and
fsynced to frozen_candidates.jsonl IMMEDIATELY, before any test execution.
Once a task_id exists in frozen_candidates.jsonl, NO code path calls the
model for that task again — even if testing crashes, scoring is
interrupted, baseline.jsonl is absent, or the run is resumed later.
Testing consumes frozen_candidates.jsonl; it never generates candidates.

Sampling statement: each HumanEval task contributes exactly one
successfully returned and frozen candidate. Transient transport failures
may cause the identical logical request to be reissued; retries are
infrastructure-driven and never conditioned on candidate content.

This script contains NO evaluator-arm logic: no self-check, no debate, no
revision, no execution-feedback loop, no acceptance decisions, no stats.
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
from typing import Callable, Dict, List, NamedTuple, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
from common.dataset import load_tasks
from common.extraction import extract_candidate
from common.execution import (
    TestInfraError,
    run_tests_once,
    test_python_provenance,
)
from common.model_client import (
    FatalConfigError,
    InfrastructureExhausted,
    cost_usd,
    generate_one_candidate,
    make_client,
)


class Paths(NamedTuple):
    frozen: Path
    baseline: Path
    failures: Path
    log: Path


DEFAULT_PATHS = Paths(
    frozen=config.FROZEN_PATH,
    baseline=config.BASELINE_PATH,
    failures=config.FAILURES_PATH,
    log=config.RUN_LOG_PATH,
)


# ── Small utilities ───────────────────────────────────────────────────

def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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


# ── Ledger validation (startup, before anything expensive) ────────────

PROTOCOL_FIELDS = {
    "generator_model": config.GENERATOR_MODEL,
    "generator_temperature": config.GENERATOR_TEMPERATURE,
    "reasoning_effort": config.REASONING_EFFORT,
    "max_completion_tokens": config.MAX_COMPLETION_TOKENS,
    "schema_version": config.SCHEMA_VERSION,
    "experiment_version": config.EXPERIMENT_VERSION,
    "dataset_sha256": config.DATASET_SHA256,
}

FROZEN_REQUIRED_FIELDS = list(PROTOCOL_FIELDS.keys()) + [
    "task_id", "task_index", "prompt", "entry_point", "generation_prompt",
    "raw_model_response", "raw_response_sha256", "candidate_code",
    "candidate_sha256", "extraction_method", "response_id",
    "response_model", "finish_reason", "usage", "generation_duration_s",
    "transport_attempts", "estimated_cost_usd", "generated_at",
    # API/backend provenance (null when genuinely unavailable):
    "system_fingerprint", "response_created", "service_tier", "request_id",
]


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
                    f"CORRUPT LEDGER: malformed JSON in {path} line {lineno}: "
                    f"{exc}. Fix manually before resuming."
                )
    return records


def validate_frozen_ledger(path: Path) -> Dict[str, dict]:
    """Load and certify the frozen candidate ledger. Loud refusal on any
    integrity problem; never regenerates a frozen task."""
    frozen: Dict[str, dict] = {}
    for rec in load_jsonl(path):
        tid = rec.get("task_id")
        missing = [f for f in FROZEN_REQUIRED_FIELDS if f not in rec]
        if missing:
            raise SystemExit(
                f"CORRUPT FROZEN LEDGER: record for {tid} missing fields "
                f"{missing} in {path}. Fix manually before resuming."
            )
        if tid in frozen:
            raise SystemExit(
                f"CORRUPT FROZEN LEDGER: duplicate task_id {tid} in {path}. "
                f"Fix manually before resuming."
            )
        if sha256_text(rec["raw_model_response"]) != rec["raw_response_sha256"]:
            raise SystemExit(
                f"IMMUTABILITY VIOLATION: raw_response_sha256 does not match "
                f"raw_model_response for {tid} in {path}. Refusing to run."
            )
        if sha256_text(rec["candidate_code"]) != rec["candidate_sha256"]:
            raise SystemExit(
                f"IMMUTABILITY VIOLATION: candidate_sha256 does not match "
                f"candidate_code for {tid} in {path}. Refusing to run."
            )
        for field, expected in PROTOCOL_FIELDS.items():
            if rec[field] != expected:
                raise SystemExit(
                    f"PROTOCOL MISMATCH: {tid} has {field}={rec[field]!r}, "
                    f"configured experiment requires {expected!r}. A frozen "
                    f"candidate must never be silently regenerated under a "
                    f"new protocol. Refusing to run."
                )
        frozen[tid] = rec
    return frozen


def load_scored_ledger(path: Path) -> Dict[str, dict]:
    scored: Dict[str, dict] = {}
    for rec in load_jsonl(path):
        tid = rec.get("task_id")
        if tid in scored:
            raise SystemExit(
                f"CORRUPT BASELINE: duplicate task_id {tid} in {path}. "
                f"Fix manually before resuming."
            )
        if rec.get("generation_status") != "success":
            raise SystemExit(
                f"CORRUPT BASELINE: non-success record for {tid} in {path}. "
                f"Successful records only."
            )
        scored[tid] = rec
    return scored


# ── Phase A: generation + immediate freezing ──────────────────────────

def build_frozen_record(task: dict, gen, generation_prompt: str) -> dict:
    candidate_code, extraction_method = extract_candidate(gen.text)
    return {
        "schema_version": config.SCHEMA_VERSION,
        "experiment_version": config.EXPERIMENT_VERSION,
        "task_id": task["task_id"],
        "task_index": task["task_index"],
        "prompt": task["prompt"],
        "entry_point": task["entry_point"],
        "dataset_sha256": config.DATASET_SHA256,

        "generator_model": config.GENERATOR_MODEL,
        "generator_temperature": config.GENERATOR_TEMPERATURE,
        "reasoning_effort": config.REASONING_EFFORT,
        "max_completion_tokens": config.MAX_COMPLETION_TOKENS,

        "generation_prompt": generation_prompt,
        "raw_model_response": gen.text,
        "raw_response_sha256": sha256_text(gen.text),
        "candidate_code": candidate_code,
        "candidate_sha256": sha256_text(candidate_code),
        "extraction_method": extraction_method,

        "response_id": gen.response_id,
        "response_model": gen.response_model,
        "finish_reason": gen.finish_reason,
        "system_fingerprint": gen.system_fingerprint,
        "response_created": gen.response_created,
        "service_tier": gen.service_tier,
        "request_id": gen.request_id,
        "usage": {
            "prompt_tokens": gen.prompt_tokens,
            "completion_tokens": gen.completion_tokens,
        },
        "generation_duration_s": gen.duration_s,
        "transport_attempts": gen.transport_attempts,
        "estimated_cost_usd": round(
            cost_usd(gen.prompt_tokens, gen.completion_tokens), 6),
        "generated_at": utc_now(),
    }


def generation_phase(
    tasks: List[dict],
    paths: Paths,
    client,
    log: Callable,
    cap_usd: float,
    limit: Optional[int] = None,
) -> Dict[str, dict]:
    """Generate + freeze candidates for tasks not already frozen.

    The frozen record is appended and fsynced IMMEDIATELY after the API
    response — before any test execution. Returns the full frozen map
    (pre-existing + new). Cost accounting is cumulative: spend already
    stored in frozen records is carried forward across resume runs.
    """
    frozen = validate_frozen_ledger(paths.frozen)
    spent = sum(r.get("estimated_cost_usd", 0.0) for r in frozen.values())
    todo = [t for t in tasks if t["task_id"] not in frozen]
    if limit is not None:
        todo = todo[:limit]
    log("generation_phase_start", already_frozen=len(frozen), todo=len(todo),
        carried_over_spend_usd=round(spent, 4), cost_cap_usd=cap_usd)

    for task in todo:
        tid = task["task_id"]
        est = cost_usd(max(1, len(task["prompt"]) // 4), config.MAX_COMPLETION_TOKENS)
        if spent + est > cap_usd:
            log("cost_cap_abort", task_id=tid, spent_usd=round(spent, 4),
                projected_usd=round(est, 4), cap_usd=cap_usd)
            break
        generation_prompt = config.GENERATION_PROMPT_TEMPLATE.format(
            prompt=task["prompt"])
        log("gen_start", task_id=tid)
        try:
            gen = generate_one_candidate(client, generation_prompt, log, task_id=tid)
        except InfrastructureExhausted as exc:
            append_jsonl(paths.failures, {
                "schema_version": config.SCHEMA_VERSION,
                "experiment_version": config.EXPERIMENT_VERSION,
                "task_id": tid,
                "task_index": task["task_index"],
                "generation_status": "failed",
                "failure_stage": "generation",
                "error": str(exc)[:500],
                "failed_at": utc_now(),
            })
            log("task_failed", task_id=tid, stage="generation",
                error=str(exc)[:300])
            continue
        # FREEZE FIRST. Extraction + hash + fsync before any test runs.
        record = build_frozen_record(task, gen, generation_prompt)
        append_jsonl(paths.frozen, record)
        frozen[tid] = record
        spent += record["estimated_cost_usd"]
        log("candidate_frozen", task_id=tid,
            candidate_sha256=record["candidate_sha256"],
            extraction_method=record["extraction_method"],
            finish_reason=gen.finish_reason,
            transport_attempts=gen.transport_attempts,
            spent_usd=round(spent, 4))
    return frozen


# ── Phase B: scoring (consumes frozen candidates; never generates) ────

def run_tests_with_infra_retries(candidate_code, test_code, entry_point, task_id, log):
    """Retry ONLY on TestInfraError; the identical test is re-run. A
    candidate timeout is an incorrect solution, never retried."""
    for attempt in range(1, config.TEST_MAX_ATTEMPTS + 1):
        try:
            status, details, correct, dur = run_tests_once(
                candidate_code, test_code, entry_point, config.TEST_TIMEOUT_S
            )
            return status, details, correct, dur, attempt - 1
        except TestInfraError as exc:
            if attempt == config.TEST_MAX_ATTEMPTS:
                raise
            log("test_infra_retry", task_id=task_id, attempt=attempt,
                error=str(exc)[:300])
            time.sleep(config.TEST_BACKOFF_BASE_S * attempt)
    raise AssertionError("unreachable")


def build_scored_record(frozen_rec: dict, status: str, details: str,
                        correct: bool, test_dur: float, test_retries: int) -> dict:
    """Derive a scored baseline record EXCLUSIVELY from a frozen candidate."""
    rec = dict(frozen_rec)  # immutable generation fields carried verbatim
    rec.update({
        "baseline_correct": correct,
        "test_status": status,
        "test_details": details,
        "generation_status": "success",
        "attempt_count": 1,
        "test_duration_s": test_dur,
        "test_infra_retry_count": test_retries,
        "test_timeout_s": config.TEST_TIMEOUT_S,
        "scored_at": utc_now(),
    })
    rec.update(test_python_provenance())
    return rec


def scoring_phase(
    tasks: List[dict],
    paths: Paths,
    frozen: Dict[str, dict],
    log: Callable,
) -> Dict[str, dict]:
    """Score every frozen candidate that lacks a scored record. Test
    infrastructure failures leave the candidate frozen; resume retries
    TESTING ONLY."""
    task_by_id = {t["task_id"]: t for t in tasks}
    scored = load_scored_ledger(paths.baseline)
    todo = [tid for tid in (t["task_id"] for t in tasks)
            if tid in frozen and tid not in scored]
    log("scoring_phase_start", already_scored=len(scored), todo=len(todo))

    for tid in todo:
        frozen_rec = frozen[tid]
        task = task_by_id[tid]
        try:
            status, details, correct, dur, retries = run_tests_with_infra_retries(
                frozen_rec["candidate_code"], task["test"],
                task["entry_point"], tid, log,
            )
        except TestInfraError as exc:
            append_jsonl(paths.failures, {
                "schema_version": config.SCHEMA_VERSION,
                "experiment_version": config.EXPERIMENT_VERSION,
                "task_id": tid,
                "task_index": task["task_index"],
                "generation_status": "failed",
                "failure_stage": "testing",
                "error": str(exc)[:500],
                "candidate_sha256": frozen_rec["candidate_sha256"],
                "failed_at": utc_now(),
            })
            log("task_failed", task_id=tid, stage="testing",
                note="candidate remains frozen; resume retries testing only",
                error=str(exc)[:300])
            continue
        rec = build_scored_record(frozen_rec, status, details, correct, dur, retries)
        append_jsonl(paths.baseline, rec)
        scored[tid] = rec
        log("score_written", task_id=tid, test_status=status,
            baseline_correct=correct)
    return scored


# ── Driver ────────────────────────────────────────────────────────────

def run(paths: Paths, client, cap_usd: float, limit: Optional[int],
        log: Callable, tasks: Optional[List[dict]] = None) -> int:
    """Full Stage 0 pass: generation phase then scoring phase."""
    # Hard dataset integrity check BEFORE anything expensive (explicit
    # exceptions, not assert — active even under python -O).
    if tasks is None:
        tasks = load_tasks()
    log("dataset_ok", n_tasks=len(tasks), sha256=config.DATASET_SHA256,
        model=config.GENERATOR_MODEL, temperature=config.GENERATOR_TEMPERATURE,
        reasoning_effort=config.REASONING_EFFORT)

    frozen = generation_phase(tasks, paths, client, log, cap_usd, limit)
    scored = scoring_phase(tasks, paths, frozen, log)

    frozen_ids = set(frozen)
    scored_ids = set(scored)
    expected_ids = {t["task_id"] for t in tasks}
    ok = (frozen_ids == expected_ids and scored_ids == expected_ids)
    log("run_end", frozen=len(frozen), scored=len(scored),
        expected=config.EXPECTED_N_TASKS, complete=ok)
    if not ok:
        print(
            f"\nINCOMPLETE BASELINE: frozen={len(frozen)} scored={len(scored)} "
            f"expected={config.EXPECTED_N_TASKS}. Missing tasks are NOT "
            f"excluded from the experiment; re-run to retry infrastructure "
            f"failures. Validation will FAIL until both ledgers cover all "
            f"{config.EXPECTED_N_TASKS} task IDs.",
            file=sys.stderr,
        )
        return 1
    print(f"\nStage 0 complete: {len(frozen)} frozen, {len(scored)} scored. "
          f"Run validate_baseline.py to certify.", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--frozen", type=Path, default=config.FROZEN_PATH,
                    help="frozen candidate ledger (default: data/frozen_candidates.jsonl)")
    ap.add_argument("--out", type=Path, default=config.BASELINE_PATH,
                    help="scored baseline JSONL (default: data/baseline.jsonl)")
    ap.add_argument("--failures", type=Path, default=config.FAILURES_PATH)
    ap.add_argument("--log", type=Path, default=config.RUN_LOG_PATH)
    ap.add_argument("--cap", type=float, default=config.DEFAULT_COST_CAP_USD,
                    help="hard cost ceiling in USD, cumulative across resume "
                         "runs (default: %(default)s)")
    ap.add_argument("--limit", type=int, default=None,
                    help="generate at most this many new candidates this run")
    args = ap.parse_args()

    paths = Paths(frozen=args.frozen, baseline=args.out,
                  failures=args.failures, log=args.log)
    log = RunLogger(paths.log)
    client = make_client()  # verifies the API key exists, before any work
    try:
        return run(paths, client, args.cap, args.limit, log)
    except FatalConfigError as exc:
        log("fatal_config_error", error=str(exc)[:500])
        print(f"\nFATAL: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
