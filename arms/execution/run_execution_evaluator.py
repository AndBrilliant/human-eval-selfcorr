#!/usr/bin/env python3
"""Arm 3 PHASE B: execution-grounded evaluator (positive control).

PREREQUISITE: Phase A complete (164 frozen execution signals) AND
certified by validate_execution_signals.py. This module refuses to run if
the signal ledger does not cover every frozen task.

Per task, in a fresh API context (single user message), the evaluator
receives: the HumanEval problem, the exact frozen candidate, and ONLY the
binary PASS/FAIL treatment text — never test source, assertion text,
stack traces, failing inputs, exception details, or the T0 label.

    frozen execution signal (reused, never re-executed)
        ->
    instantiate evaluator prompt
        ->
    one evaluator API request
        ->
    raw response + provenance
        ->
    append + fsync arm3_execution_responses.jsonl     <-- FREEZE POINT
        ->
    ONLY THEN parse YES/NO
        ->
    append + fsync arm3_execution.jsonl

Once a task_id exists in arm3_execution_responses.jsonl, NO code path
queries the evaluator for that task again. A malformed verdict is frozen,
recorded parse_status="invalid_verdict", and the arm halts loudly — NO
RESAMPLING. Structural API malformation is fatal: no freeze, no second
model call.

BLINDNESS: this module NEVER reads baseline.jsonl, Arm 1 ledgers, or
Arm 2 ledgers. T0 lookup is never coupled into the evaluator request path.

NO CORRECTION/REVISION: there is no code path that asks for, accepts,
retests, or persists modified code. The frozen candidate is the object
evaluated, unchanged.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, NamedTuple, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import config as root_config
from arms.execution import config as acfg
from arms.execution.run_execution_signals import (
    RunLogger,
    append_jsonl,
    load_frozen_records,
    load_jsonl,
    sha256_text,
    utc_now,
    validate_signal_ledger,
    verify_source_ledger,
)
from common.model_client import (
    FatalConfigError,
    InfrastructureExhausted,
    StructuralResponseError,
    _is_retryable,
    make_client,
)


class Paths(NamedTuple):
    signals: Path
    responses: Path
    decisions: Path
    failures: Path
    log: Path
    certification: Path = acfg.CERTIFICATION_PATH


DEFAULT_PATHS = Paths(
    signals=acfg.SIGNALS_PATH,
    responses=acfg.RESPONSES_PATH,
    decisions=acfg.DECISIONS_PATH,
    failures=acfg.FAILURES_PATH,
    log=acfg.RUN_LOG_PATH,
    certification=acfg.CERTIFICATION_PATH,
)


# ── Mandatory Phase B preflight (executable integrity gate) ──────────

CERT_REQUIRED = {
    "schema_version": acfg.SCHEMA_VERSION,
    "experiment_version": acfg.EXPERIMENT_VERSION,
    "arm": acfg.ARM_NAME,
    "source_frozen_ledger_sha256": acfg.APPROVED_FROZEN_SHA256,
    "baseline_ledger_sha256": acfg.APPROVED_BASELINE_SHA256,
    "expected_tasks": root_config.EXPECTED_N_TASKS,
    "frozen_execution_signals": root_config.EXPECTED_N_TASKS,
    "execution_pass": 155,
    "execution_fail": 9,
    "execution_t0_mismatches": 0,
    "certification_status": "valid",
}


def verify_phase_b_preflight(paths: Paths) -> List[dict]:
    """Executable integrity gate. Raises SystemExit (ABORT) unless EVERY
    check passes; in particular, the current signal ledger must be
    byte-identical to the ledger the certification artifact certifies.

    This function reads ONLY the approved Stage 0 frozen ledger, the
    execution-signal ledger, and the certification artifact. It NEVER
    reads baseline.jsonl. There is no route around it: run_evaluator()
    invokes it before any model request, and main() invokes it before
    any client construction.
    """
    # 1. Stage 0 frozen ledger SHA is approved.
    verify_source_ledger()
    # 2. Exactly the expected 164 frozen tasks exist.
    frozen_records = load_frozen_records()
    if len(frozen_records) != root_config.EXPECTED_N_TASKS:
        raise SystemExit(
            f"ARM 3 PHASE B ABORT: expected {root_config.EXPECTED_N_TASKS} "
            f"frozen records, got {len(frozen_records)}.")
    frozen_by_id = {t["task_id"]: t for t in frozen_records}
    # 3-5. Signal ledger passes all protocol/hash consistency checks and
    # contains EXACTLY the 164 approved task IDs (no missing, no
    # unexpected — set equality, not length).
    signals = validate_signal_ledger(paths.signals, frozen_by_id)
    if set(signals) != set(frozen_by_id):
        missing = sorted(set(frozen_by_id) - set(signals))
        raise SystemExit(
            f"ARM 3 PHASE B ABORT: execution-signal ledger does not cover "
            f"exactly the approved 164 task IDs (missing "
            f"{len(missing)}: {missing[:5]}...). Complete Phase A first.")
    # 6. Certification artifact exists.
    cert_path = Path(paths.certification)
    if not cert_path.is_file():
        raise SystemExit(
            f"ARM 3 PHASE B ABORT: certification artifact missing: "
            f"{cert_path}. Run validate_execution_signals.py successfully "
            f"BEFORE any evaluator call. Zero API calls made.")
    try:
        cert = json.loads(cert_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"ARM 3 PHASE B ABORT: certification artifact is not valid "
            f"JSON: {exc}.")
    # 7-10. Exact required protocol metadata, valid status, approved
    # source/baseline hashes.
    for field, expected in CERT_REQUIRED.items():
        if cert.get(field) != expected:
            raise SystemExit(
                f"ARM 3 PHASE B ABORT: certification field {field}="
                f"{cert.get(field)!r}, required {expected!r}. "
                f"Re-run validate_execution_signals.py.")
    # 11. Certification matches the CURRENT signal ledger byte-for-byte.
    current_sha = hashlib.sha256(
        Path(paths.signals).read_bytes()).hexdigest()
    if cert.get("execution_signals_sha256") != current_sha:
        raise SystemExit(
            f"ARM 3 PHASE B ABORT: certification execution_signals_sha256 "
            f"{cert.get('execution_signals_sha256')} does not match the "
            f"CURRENT signal ledger {current_sha}. The certification is "
            f"stale or the ledger changed. Re-run "
            f"validate_execution_signals.py.")
    return frozen_records


# ── Evaluator request ─────────────────────────────────────────────────

def build_evaluator_prompt(task_prompt: str, candidate_code: str,
                           execution_treatment: str) -> str:
    return acfg.EVALUATOR_PROMPT_TEMPLATE.format(
        task_prompt=task_prompt, candidate_code=candidate_code,
        execution_treatment=execution_treatment)


@dataclass
class EvalResult:
    text: str
    response_id: Optional[str]
    response_model: Optional[str]
    finish_reason: Optional[str]
    prompt_tokens: int
    completion_tokens: int
    duration_s: float
    transport_attempts: int
    system_fingerprint: Optional[str] = None
    response_created: Optional[int] = None
    service_tier: Optional[str] = None
    request_id: Optional[str] = None


def evaluate_one(client, evaluator_prompt: str, log: Callable,
                 task_id: str = "") -> EvalResult:
    """One successfully returned evaluator response for one task. Same
    retry/structural discipline as Arms 1-2."""
    for attempt in range(1, root_config.GEN_MAX_ATTEMPTS + 1):
        try:
            t0 = time.time()
            resp = client.chat.completions.create(
                model=acfg.EVALUATOR_MODEL,
                messages=[{"role": "user", "content": evaluator_prompt}],
                temperature=acfg.EVALUATOR_TEMPERATURE,
                max_completion_tokens=acfg.EVALUATOR_MAX_COMPLETION_TOKENS,
                reasoning_effort=acfg.EVALUATOR_REASONING_EFFORT,
                timeout=root_config.API_TIMEOUT_S,
            )
            duration = time.time() - t0
            choices = getattr(resp, "choices", None)
            if not isinstance(choices, list) or len(choices) != 1:
                raise StructuralResponseError(
                    f"expected exactly 1 completion choice, got "
                    f"{None if choices is None else len(choices)}")
            response_model = getattr(resp, "model", None)
            if not response_model or not isinstance(response_model, str):
                raise StructuralResponseError(
                    f"empty or non-string response_model: {response_model!r}")
            content = getattr(choices[0].message, "content", None)
            if not isinstance(content, str):
                raise StructuralResponseError(
                    f"assistant content is not a Python str: "
                    f"{type(content).__name__}")
            usage = getattr(resp, "usage", None)
            return EvalResult(
                text=content,
                response_id=getattr(resp, "id", None),
                response_model=response_model,
                finish_reason=getattr(choices[0], "finish_reason", None),
                prompt_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
                completion_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
                duration_s=round(duration, 2),
                transport_attempts=attempt,
                system_fingerprint=getattr(resp, "system_fingerprint", None),
                response_created=getattr(resp, "created", None),
                service_tier=getattr(resp, "service_tier", None),
                request_id=getattr(resp, "_request_id", None),
            )
        except Exception as exc:
            if isinstance(exc, StructuralResponseError):
                raise  # fatal: no retry, no second model sample
            if not _is_retryable(exc):
                raise FatalConfigError(
                    f"Non-retryable API error on {task_id}: "
                    f"{type(exc).__name__}: {exc}") from exc
            if attempt == root_config.GEN_MAX_ATTEMPTS:
                raise InfrastructureExhausted(
                    f"{task_id}: transport failed after "
                    f"{root_config.GEN_MAX_ATTEMPTS} attempts; last error: "
                    f"{type(exc).__name__}: {exc}") from exc
            delay = min(
                root_config.GEN_BACKOFF_MAX_S,
                root_config.GEN_BACKOFF_BASE_S
                * root_config.GEN_BACKOFF_FACTOR ** (attempt - 1),
            )
            delay = random.uniform(0.0, delay)
            log("arm3_transport_retry", task_id=task_id, attempt=attempt,
                max_attempts=root_config.GEN_MAX_ATTEMPTS,
                error=f"{type(exc).__name__}: {str(exc)[:300]}",
                backoff_s=round(delay, 1))
            time.sleep(delay)
    raise AssertionError("unreachable")


# ── Verdict parser (identical semantics to Arm 1) ────────────────────

def parse_verdict(raw: str) -> Tuple[Optional[str], Optional[int], str]:
    """Strip surrounding whitespace, uppercase, accept exactly YES or NO.
    Anything else: parse_status="invalid_verdict", no binary assignment,
    never resampled."""
    token = (raw or "").strip().upper()
    if token == "YES":
        return "YES", 1, "valid"
    if token == "NO":
        return "NO", 0, "valid"
    return None, None, "invalid_verdict"


# ── Ledgers ───────────────────────────────────────────────────────────

RESPONSE_REQUIRED_FIELDS = [
    "schema_version", "experiment_version", "arm", "task_id", "task_index",
    "source_frozen_ledger_sha256", "candidate_sha256",
    "execution_status", "execution_treatment_text",
    "evaluator_model", "evaluator_temperature",
    "evaluator_reasoning_effort", "evaluator_max_completion_tokens",
    "evaluator_prompt", "raw_evaluator_response",
    "raw_evaluator_response_sha256", "response_id", "response_model",
    "system_fingerprint", "response_created", "service_tier", "request_id",
    "finish_reason", "usage", "evaluator_duration_s", "transport_attempts",
    "estimated_cost_usd", "evaluated_at",
]

RESPONSE_PROTOCOL_FIELDS = {
    "evaluator_model": acfg.EVALUATOR_MODEL,
    "evaluator_temperature": acfg.EVALUATOR_TEMPERATURE,
    "evaluator_reasoning_effort": acfg.EVALUATOR_REASONING_EFFORT,
    "evaluator_max_completion_tokens": acfg.EVALUATOR_MAX_COMPLETION_TOKENS,
    "schema_version": acfg.SCHEMA_VERSION,
    "experiment_version": acfg.EXPERIMENT_VERSION,
    "arm": acfg.ARM_NAME,
    "source_frozen_ledger_sha256": acfg.APPROVED_FROZEN_SHA256,
}


def cost_usd(prompt_tokens: int, completion_tokens: int) -> float:
    in_p, out_p = acfg.PRICE_PER_MTOK
    return (prompt_tokens * in_p + completion_tokens * out_p) / 1_000_000


def build_response_record(task: dict, signal: dict, res: EvalResult,
                          evaluator_prompt: str) -> dict:
    return {
        "schema_version": acfg.SCHEMA_VERSION,
        "experiment_version": acfg.EXPERIMENT_VERSION,
        "arm": acfg.ARM_NAME,
        "task_id": task["task_id"],
        "task_index": task["task_index"],
        "source_frozen_ledger_sha256": acfg.APPROVED_FROZEN_SHA256,
        "candidate_sha256": task["candidate_sha256"],
        "execution_status": signal["execution_status"],
        "execution_treatment_text": signal["execution_treatment_text"],
        "evaluator_model": acfg.EVALUATOR_MODEL,
        "evaluator_temperature": acfg.EVALUATOR_TEMPERATURE,
        "evaluator_reasoning_effort": acfg.EVALUATOR_REASONING_EFFORT,
        "evaluator_max_completion_tokens": acfg.EVALUATOR_MAX_COMPLETION_TOKENS,
        "evaluator_prompt": evaluator_prompt,
        "raw_evaluator_response": res.text,
        "raw_evaluator_response_sha256": sha256_text(res.text),
        "response_id": res.response_id,
        "response_model": res.response_model,
        "system_fingerprint": res.system_fingerprint,
        "response_created": res.response_created,
        "service_tier": res.service_tier,
        "request_id": res.request_id,
        "finish_reason": res.finish_reason,
        "usage": {
            "prompt_tokens": res.prompt_tokens,
            "completion_tokens": res.completion_tokens,
        },
        "evaluator_duration_s": res.duration_s,
        "transport_attempts": res.transport_attempts,
        "estimated_cost_usd": round(
            cost_usd(res.prompt_tokens, res.completion_tokens), 6),
        "evaluated_at": utc_now(),
    }


def build_decision_record(task: dict, response_rec: dict) -> dict:
    verdict, acceptance, parse_status = parse_verdict(
        response_rec["raw_evaluator_response"])
    return {
        "schema_version": acfg.SCHEMA_VERSION,
        "experiment_version": acfg.EXPERIMENT_VERSION,
        "arm": acfg.ARM_NAME,
        "task_id": task["task_id"],
        "task_index": task["task_index"],
        "candidate_sha256": response_rec["candidate_sha256"],
        "execution_status": response_rec["execution_status"],
        "raw_evaluator_response_sha256":
            response_rec["raw_evaluator_response_sha256"],
        "verdict": verdict,
        "acceptance": acceptance,
        "parse_status": parse_status,
        "parsed_at": utc_now(),
    }


def validate_response_ledger(path: Path) -> Dict[str, dict]:
    responses: Dict[str, dict] = {}
    for rec in load_jsonl(path):
        tid = rec.get("task_id")
        missing = [f for f in RESPONSE_REQUIRED_FIELDS if f not in rec]
        if missing:
            raise SystemExit(
                f"CORRUPT ARM 3 RESPONSE LEDGER: record for {tid} missing "
                f"fields {missing} in {path}.")
        if tid in responses:
            raise SystemExit(
                f"CORRUPT ARM 3 RESPONSE LEDGER: duplicate task_id {tid} in "
                f"{path}.")
        if sha256_text(rec["raw_evaluator_response"]) != \
                rec["raw_evaluator_response_sha256"]:
            raise SystemExit(
                f"IMMUTABILITY VIOLATION: raw_evaluator_response_sha256 does "
                f"not match raw_evaluator_response for {tid} in {path}.")
        for field, expected in RESPONSE_PROTOCOL_FIELDS.items():
            if rec[field] != expected:
                raise SystemExit(
                    f"PROTOCOL MISMATCH: {tid} has {field}={rec[field]!r}, "
                    f"Arm 3 protocol requires {expected!r}. Refusing to run.")
        responses[tid] = rec
    return responses


def load_decisions(path: Path) -> Dict[str, dict]:
    decisions: Dict[str, dict] = {}
    for rec in load_jsonl(path):
        tid = rec.get("task_id")
        if tid in decisions:
            raise SystemExit(
                f"CORRUPT ARM 3 DECISION LEDGER: duplicate task_id {tid} in "
                f"{path}.")
        decisions[tid] = rec
    return decisions


# ── Phase B driver ────────────────────────────────────────────────────

def run_evaluator(paths: Paths, client, frozen_records: List[dict],
                  cap_usd: float, log: Callable) -> int:
    """Full Arm 3 Phase B pass. Return codes:
        0 = arm complete (164 responses + 164 valid decisions)
        1 = incomplete (infrastructure; resume later)
        3 = invalid verdict (halted loudly, nothing resampled)

    The executable integrity gate (verify_phase_b_preflight) runs FIRST;
    there is no route to client.chat.completions.create without a valid
    certification for the CURRENT signal ledger.
    """
    # MANDATORY GATE — aborts unless the current 164-signal ledger is
    # covered by a valid matching certification artifact.
    full_frozen = verify_phase_b_preflight(paths)

    # Signal validation is always against the FULL approved corpus.
    frozen_by_id = {t["task_id"]: t for t in full_frozen}
    signals = validate_signal_ledger(paths.signals, frozen_by_id)
    # Every task submitted for evaluation must be covered by a signal.
    missing_signals = [t["task_id"] for t in frozen_records
                       if t["task_id"] not in signals]
    if missing_signals:
        raise SystemExit(
            f"ARM 3 PHASE B ABORT: no execution signal for "
            f"{missing_signals[:5]}. Complete Phase A first.")

    responses = validate_response_ledger(paths.responses)
    decisions = load_decisions(paths.decisions)
    spent = sum(r.get("estimated_cost_usd", 0.0) for r in responses.values())

    for tid, dec in decisions.items():
        if dec.get("parse_status") != "valid":
            log("arm3_halt_invalid_verdict", task_id=tid, resumed=True)
            return 3

    log("arm3_run_start", total=len(frozen_records),
        responses=len(responses), decisions=len(decisions),
        carried_over_spend_usd=round(spent, 4), cost_cap_usd=cap_usd)

    for task in frozen_records:
        tid = task["task_id"]
        if tid in decisions:
            continue
        if tid in responses:
            # Frozen response exists but no decision: parse it now.
            rec = build_decision_record(task, responses[tid])
            append_jsonl(paths.decisions, rec)
            decisions[tid] = rec
            log("decision_written", task_id=tid, verdict=rec["verdict"],
                parse_status=rec["parse_status"], resumed_response=True)
            if rec["parse_status"] != "valid":
                log("arm3_halt_invalid_verdict", task_id=tid)
                return 3
            continue

        signal = signals[tid]
        # The treatment shown to the evaluator comes ONLY from the frozen
        # execution signal — never from T0 or any other arm.
        evaluator_prompt = build_evaluator_prompt(
            task["prompt"], task["candidate_code"],
            signal["execution_treatment_text"])

        est = cost_usd(max(1, len(evaluator_prompt) // 4),
                       acfg.EVALUATOR_MAX_COMPLETION_TOKENS)
        if spent + est > cap_usd:
            log("cost_cap_abort", task_id=tid, spent_usd=round(spent, 4),
                projected_usd=round(est, 4), cap_usd=cap_usd)
            return 1

        log("eval_start", task_id=tid)
        try:
            res = evaluate_one(client, evaluator_prompt, log, task_id=tid)
        except InfrastructureExhausted as exc:
            append_jsonl(paths.failures, {
                "schema_version": acfg.SCHEMA_VERSION,
                "experiment_version": acfg.EXPERIMENT_VERSION,
                "arm": acfg.ARM_NAME,
                "task_id": tid,
                "task_index": task["task_index"],
                "failure_stage": "evaluation_transport",
                "error": str(exc)[:500],
                "failed_at": utc_now(),
            })
            log("task_failed", task_id=tid, stage="evaluation_transport",
                error=str(exc)[:300])
            continue

        # FREEZE FIRST: raw response + provenance, fsynced, BEFORE parsing.
        resp_rec = build_response_record(task, signal, res, evaluator_prompt)
        append_jsonl(paths.responses, resp_rec)
        responses[tid] = resp_rec
        spent += resp_rec["estimated_cost_usd"]
        log("response_frozen", task_id=tid,
            raw_evaluator_response_sha256=resp_rec["raw_evaluator_response_sha256"],
            transport_attempts=res.transport_attempts,
            spent_usd=round(spent, 4))

        dec_rec = build_decision_record(task, resp_rec)
        append_jsonl(paths.decisions, dec_rec)
        decisions[tid] = dec_rec
        log("decision_written", task_id=tid, verdict=dec_rec["verdict"],
            parse_status=dec_rec["parse_status"])
        if dec_rec["parse_status"] != "valid":
            log("arm3_halt_invalid_verdict", task_id=tid)
            return 3

    expected_ids = {t["task_id"] for t in frozen_records}
    complete = (set(responses) == expected_ids and set(decisions) == expected_ids
                and all(d.get("parse_status") == "valid"
                        for d in decisions.values()))
    log("arm3_run_end", responses=len(responses), decisions=len(decisions),
        expected=len(frozen_records), spent_usd=round(spent, 4),
        complete=complete)
    if not complete:
        print("\nINCOMPLETE ARM 3: re-run to resume. Frozen evaluator "
              "responses are never resampled.", file=sys.stderr)
        return 1
    print("\nArm 3 complete. Run validate_execution.py to certify.",
          flush=True)
    return 0


def main(argv=None, client_factory=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--signals", type=Path, default=acfg.SIGNALS_PATH)
    ap.add_argument("--responses", type=Path, default=acfg.RESPONSES_PATH)
    ap.add_argument("--decisions", type=Path, default=acfg.DECISIONS_PATH)
    ap.add_argument("--failures", type=Path, default=acfg.FAILURES_PATH)
    ap.add_argument("--log", type=Path, default=acfg.RUN_LOG_PATH)
    ap.add_argument("--certification", type=Path,
                    default=acfg.CERTIFICATION_PATH)
    ap.add_argument("--cap", type=float, default=acfg.ARM_COST_CAP_USD)
    args = ap.parse_args(argv)

    # ABORT BEFORE ANY API CALL if the Stage 0 corpus is not byte-exact
    # (also enforced inside verify_phase_b_preflight).
    verify_source_ledger()

    paths = Paths(signals=args.signals, responses=args.responses,
                  decisions=args.decisions, failures=args.failures,
                  log=args.log, certification=args.certification)
    # MANDATORY GATE: preflight BEFORE any client factory invocation.
    # If the gate fails, zero API calls are possible — no client is ever
    # constructed.
    verify_phase_b_preflight(paths)
    frozen_records = load_frozen_records()

    log = RunLogger(paths.log)
    factory = client_factory or make_client
    client = factory()
    try:
        return run_evaluator(paths, client, frozen_records, args.cap, log)
    except FatalConfigError as exc:
        log("fatal_protocol_error", error=str(exc)[:500])
        print(f"\nFATAL: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
