#!/usr/bin/env python3
"""Arm 1: PURE SELF-CHECK evaluator runner.

For each of the 164 immutable Stage 0 frozen candidates, in a completely
fresh API context, the evaluator (same model snapshot) receives only the
HumanEval problem and the exact frozen candidate code, and answers one
binary YES/NO judgment: is the candidate fully correct?

    frozen Stage 0 candidate
        ->
    one evaluator API request (fresh context, single user message)
        ->
    preserve raw evaluator response + provenance
        ->
    append + fsync arm1_selfcheck_responses.jsonl     <-- FREEZE POINT
        ->
    ONLY THEN parse YES/NO
        ->
    append + fsync arm1_selfcheck.jsonl

Once a task_id exists in arm1_selfcheck_responses.jsonl, NO code path
queries the evaluator for that task again — even if parsing crashes, the
machine crashes, the decisions ledger is missing, validation fails, or
the run is resumed. Resume parses the existing frozen response; it never
resamples.

GROUND-TRUTH BLINDNESS: this module NEVER reads data/baseline.jsonl.
It has no programmatic access to ground-truth labels, Stage 0 test
statuses, reference tests, or execution output. Ground truth is joined
only later, in a separate analysis stage.

NO content-conditioned resampling: a returned response is never re-queried
because of YES vs NO, malformed verdict, finish_reason, or any content.
A malformed verdict is frozen, recorded parse_status="invalid_verdict",
and the arm halts loudly. A structurally malformed API response is a
fatal protocol error (no freeze, no second model call) — the Stage 0 rule.
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
from arms.selfcheck import config as acfg
from common.model_client import (
    FatalConfigError,
    InfrastructureExhausted,
    StructuralResponseError,
    _is_retryable,
    make_client,
)


class Paths(NamedTuple):
    responses: Path
    decisions: Path
    failures: Path
    log: Path


DEFAULT_PATHS = Paths(
    responses=acfg.RESPONSES_PATH,
    decisions=acfg.DECISIONS_PATH,
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
                    f"CORRUPT ARM 1 LEDGER: malformed JSON in {path} line "
                    f"{lineno}: {exc}. Fix manually before resuming."
                )
    return records


# ── Source corpus (Stage 0 frozen ledger; the ONLY permitted input) ───

def verify_source_ledger(path: Path = acfg.FROZEN_SOURCE_PATH) -> None:
    """Abort BEFORE ANY API CALL if the Stage 0 frozen ledger is not
    byte-identical to the approved corpus."""
    digest = sha256_file(path)
    if digest != acfg.APPROVED_FROZEN_SHA256:
        raise SystemExit(
            f"ARM 1 ABORT: source frozen ledger {path} sha256 is {digest}, "
            f"approved corpus is {acfg.APPROVED_FROZEN_SHA256}. "
            f"Refusing to make any evaluator call."
        )


def load_frozen_records(path: Path = acfg.FROZEN_SOURCE_PATH) -> List[dict]:
    """Load frozen candidates in canonical order. Uses ONLY fields that
    carry no ground truth: task_id, task_index, prompt, candidate_code,
    candidate_sha256. Verifies each candidate hash (abort on mismatch)."""
    records = load_jsonl(path)
    out = []
    for rec in records:
        for field in ("task_id", "task_index", "prompt", "candidate_code",
                      "candidate_sha256"):
            if field not in rec:
                raise SystemExit(
                    f"ARM 1 ABORT: frozen record {rec.get('task_id')} missing "
                    f"required field {field!r}."
                )
        if sha256_text(rec["candidate_code"]) != rec["candidate_sha256"]:
            raise SystemExit(
                f"ARM 1 ABORT: candidate_sha256 does not match candidate_code "
                f"for {rec['task_id']} in the frozen ledger. Refusing to make "
                f"any evaluator call."
            )
        out.append({k: rec[k] for k in
                    ("task_id", "task_index", "prompt", "candidate_code",
                     "candidate_sha256")})
    out.sort(key=lambda r: r["task_index"])
    return out


# ── Evaluator request ─────────────────────────────────────────────────

def build_evaluator_prompt(task_prompt: str, candidate_code: str) -> str:
    return acfg.EVALUATOR_PROMPT_TEMPLATE.format(
        task_prompt=task_prompt, candidate_code=candidate_code)


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
    """One successfully returned evaluator response for one task.

    Transport failures BEFORE a usable response may reissue the identical
    logical request (logged, content-independent). Once the server has
    returned a response: NO retry of any kind. Structural malformation is
    a fatal protocol error: no freeze, no second model call.
    """
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
            log("arm1_transport_retry", task_id=task_id, attempt=attempt,
                max_attempts=root_config.GEN_MAX_ATTEMPTS,
                error=f"{type(exc).__name__}: {str(exc)[:300]}",
                backoff_s=round(delay, 1))
            time.sleep(delay)
    raise AssertionError("unreachable")


# ── Verdict parser (canonical, deterministic) ────────────────────────

def parse_verdict(raw: str) -> Tuple[Optional[str], Optional[int], str]:
    """Accept ONLY 'YES' or 'NO' after stripping surrounding whitespace
    and uppercasing. Anything else is a malformed evaluator output and
    must never be silently mapped to a binary decision.

    Returns (verdict, acceptance, parse_status).
    """
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


def build_response_record(task: dict, res: EvalResult,
                          evaluator_prompt: str) -> dict:
    return {
        "schema_version": acfg.SCHEMA_VERSION,
        "experiment_version": acfg.EXPERIMENT_VERSION,
        "arm": acfg.ARM_NAME,
        "task_id": task["task_id"],
        "task_index": task["task_index"],
        "source_frozen_ledger_sha256": acfg.APPROVED_FROZEN_SHA256,
        "candidate_sha256": task["candidate_sha256"],
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
        "raw_evaluator_response_sha256":
            response_rec["raw_evaluator_response_sha256"],
        "verdict": verdict,
        "acceptance": acceptance,
        "parse_status": parse_status,
        "parsed_at": utc_now(),
    }


def validate_response_ledger(path: Path) -> Dict[str, dict]:
    """Startup validation of the frozen evaluator-response ledger."""
    responses: Dict[str, dict] = {}
    for rec in load_jsonl(path):
        tid = rec.get("task_id")
        missing = [f for f in RESPONSE_REQUIRED_FIELDS if f not in rec]
        if missing:
            raise SystemExit(
                f"CORRUPT ARM 1 RESPONSE LEDGER: record for {tid} missing "
                f"fields {missing} in {path}.")
        if tid in responses:
            raise SystemExit(
                f"CORRUPT ARM 1 RESPONSE LEDGER: duplicate task_id {tid} in "
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
                    f"Arm 1 protocol requires {expected!r}. Refusing to run.")
        responses[tid] = rec
    return responses


def load_decisions(path: Path) -> Dict[str, dict]:
    decisions: Dict[str, dict] = {}
    for rec in load_jsonl(path):
        tid = rec.get("task_id")
        if tid in decisions:
            raise SystemExit(
                f"CORRUPT ARM 1 DECISION LEDGER: duplicate task_id {tid} in "
                f"{path}.")
        decisions[tid] = rec
    return decisions


# ── Driver ────────────────────────────────────────────────────────────

def run(paths: Paths, client, frozen_records: List[dict], cap_usd: float,
        log: Callable) -> int:
    """Full Arm 1 pass. Return codes:
        0 = arm complete (164 responses + 164 valid decisions)
        1 = incomplete (infrastructure; resume later)
        3 = invalid verdict encountered (halted loudly, nothing resampled)
    """
    responses = validate_response_ledger(paths.responses)
    decisions = load_decisions(paths.decisions)
    spent = sum(r.get("estimated_cost_usd", 0.0) for r in responses.values())

    # A previously recorded invalid verdict halts the arm again on resume
    # without any evaluator call.
    for tid, dec in decisions.items():
        if dec.get("parse_status") != "valid":
            log("arm1_halt_invalid_verdict", task_id=tid, resumed=True)
            return 3

    log("arm1_run_start", total=len(frozen_records),
        responses=len(responses), decisions=len(decisions),
        carried_over_spend_usd=round(spent, 4), cost_cap_usd=cap_usd)

    for task in frozen_records:
        tid = task["task_id"]
        if tid in decisions:
            continue
        if tid in responses:
            # Frozen response exists but no decision: parse it now.
            # NEVER resample.
            rec = build_decision_record(task, responses[tid])
            append_jsonl(paths.decisions, rec)
            decisions[tid] = rec
            log("decision_written", task_id=tid, verdict=rec["verdict"],
                parse_status=rec["parse_status"], resumed_response=True)
            if rec["parse_status"] != "valid":
                log("arm1_halt_invalid_verdict", task_id=tid)
                return 3
            continue

        est = cost_usd(max(1, len(task["prompt"]) // 4),
                       acfg.EVALUATOR_MAX_COMPLETION_TOKENS)
        if spent + est > cap_usd:
            log("cost_cap_abort", task_id=tid, spent_usd=round(spent, 4),
                projected_usd=round(est, 4), cap_usd=cap_usd)
            return 1

        evaluator_prompt = build_evaluator_prompt(
            task["prompt"], task["candidate_code"])
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
        resp_rec = build_response_record(task, res, evaluator_prompt)
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
            # Malformed evaluator output: frozen, recorded, HALT LOUDLY.
            # No resampling, no binary assignment.
            log("arm1_halt_invalid_verdict", task_id=tid)
            return 3

    expected_ids = {t["task_id"] for t in frozen_records}
    complete = (set(responses) == expected_ids and set(decisions) == expected_ids
                and all(d.get("parse_status") == "valid"
                        for d in decisions.values()))
    log("arm1_run_end", responses=len(responses), decisions=len(decisions),
        expected=len(frozen_records), spent_usd=round(spent, 4),
        complete=complete)
    if not complete:
        print("\nINCOMPLETE ARM 1: re-run to resume. Frozen evaluator "
              "responses are never resampled.", file=sys.stderr)
        return 1
    print("\nArm 1 complete. Run validate_selfcheck.py to certify.",
          flush=True)
    return 0


def main(argv=None, client_factory=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--responses", type=Path, default=acfg.RESPONSES_PATH)
    ap.add_argument("--decisions", type=Path, default=acfg.DECISIONS_PATH)
    ap.add_argument("--failures", type=Path, default=acfg.FAILURES_PATH)
    ap.add_argument("--log", type=Path, default=acfg.RUN_LOG_PATH)
    ap.add_argument("--cap", type=float, default=acfg.ARM_COST_CAP_USD)
    args = ap.parse_args(argv)

    # ABORT BEFORE ANY API CALL if the Stage 0 corpus is not byte-exact.
    verify_source_ledger()
    frozen_records = load_frozen_records()
    if len(frozen_records) != root_config.EXPECTED_N_TASKS:
        raise SystemExit(
            f"ARM 1 ABORT: expected {root_config.EXPECTED_N_TASKS} frozen "
            f"records, got {len(frozen_records)}.")

    paths = Paths(responses=args.responses, decisions=args.decisions,
                  failures=args.failures, log=args.log)
    log = RunLogger(paths.log)
    factory = client_factory or make_client
    client = factory()
    try:
        return run(paths, client, frozen_records, args.cap, log)
    except FatalConfigError as exc:
        log("fatal_protocol_error", error=str(exc)[:500])
        print(f"\nFATAL: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
