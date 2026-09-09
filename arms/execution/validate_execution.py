#!/usr/bin/env python3
"""Certify Arm 3 (execution-grounded positive control): execution-signal
ledger, evaluator-response ledger, and decision ledger against the
approved Stage 0 corpus.

Makes NO API calls. baseline.jsonl may be read ONLY inside the TWO
dedicated Arm 3 certifier modules: this one and
validate_execution_signals.py. It never reads Arm 1 or Arm 2 ledgers.
It computes NO cross-arm statistics.

This certifier REUSES the canonical strong validators from the runtime
modules (validate_signal_ledger, validate_response_ledger,
validate_decision_ledger) so there is exactly ONE definition of a valid
Arm 3 record: wrong task_index, unknown execution_test_status,
timeout-with-generic-treatment, pass-with-fail-status, candidate
mismatch, signal/response mismatch, response/prompt mismatch, and
response/decision mismatch are all rejected by the same code that gates
the runtime.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import config as root_config
from arms.execution import config as acfg
from arms.execution.run_execution_signals import (
    load_frozen_records,
    load_jsonl,
    sha256_file,
    validate_signal_ledger,
)
from arms.execution.run_execution_evaluator import (
    validate_decision_ledger,
    validate_response_ledger,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--signals", type=Path, default=acfg.SIGNALS_PATH)
    ap.add_argument("--responses", type=Path, default=acfg.RESPONSES_PATH)
    ap.add_argument("--decisions", type=Path, default=acfg.DECISIONS_PATH)
    ap.add_argument("--frozen", type=Path, default=acfg.FROZEN_SOURCE_PATH)
    ap.add_argument("--baseline", type=Path, default=acfg.BASELINE_PATH)
    args = ap.parse_args()

    # ── Source corpus hashes ──────────────────────────────────────────
    frozen_sha = sha256_file(args.frozen)
    if frozen_sha != acfg.APPROVED_FROZEN_SHA256:
        print(f"ARM 3 SOURCE ABORT: frozen ledger sha256 {frozen_sha} != "
              f"approved {acfg.APPROVED_FROZEN_SHA256}", file=sys.stderr)
        return 1
    baseline_sha = sha256_file(args.baseline)
    if baseline_sha != acfg.APPROVED_BASELINE_SHA256:
        print(f"ARM 3 SOURCE ABORT: baseline.jsonl sha256 {baseline_sha} != "
              f"approved {acfg.APPROVED_BASELINE_SHA256}", file=sys.stderr)
        return 1

    frozen_records = load_frozen_records(args.frozen)
    frozen_by_id = {r["task_id"]: r for r in frozen_records}
    expected_ids = set(frozen_by_id)

    # ── Canonical strong validation (ONE definition of a valid record) ─
    try:
        signals = validate_signal_ledger(args.signals, frozen_by_id)
        responses = validate_response_ledger(args.responses, frozen_by_id,
                                             signals)
        decisions = validate_decision_ledger(args.decisions, frozen_by_id,
                                             responses)
    except SystemExit as exc:
        print(f"ARM 3 CERTIFICATION FAILURE: {exc}", file=sys.stderr)
        print("VALID ARM 3 EXECUTION POSITIVE CONTROL: NO")
        return 1

    baseline_records = load_jsonl(args.baseline)
    t0 = {r["task_id"]: r["baseline_correct"] for r in baseline_records}

    # ── ID coverage ───────────────────────────────────────────────────
    def missing(ids):
        return sorted(expected_ids - ids, key=lambda t: int(t.split("/")[1]))

    sig_missing = missing(set(signals))
    resp_missing = missing(set(responses))
    dec_missing = missing(set(decisions))

    # ── Execution/T0 integrity (certifier-only comparison) ────────────
    t0_mismatches = 0
    t0_discrepant = []
    for tid in sorted(set(signals) & set(t0), key=lambda t: int(t.split("/")[1])):
        sig_pass = signals[tid]["execution_status"] == "pass"
        if sig_pass != (t0[tid] is True):
            t0_mismatches += 1
            t0_discrepant.append(tid)

    n_pass = sum(1 for s in signals.values()
                 if s["execution_status"] == "pass")
    n_fail = sum(1 for s in signals.values()
                 if s["execution_status"] == "fail")

    n_yes = 0
    n_no = 0
    n_invalid = 0
    for d in decisions.values():
        if d.get("parse_status") == "valid":
            if d.get("verdict") == "YES":
                n_yes += 1
            elif d.get("verdict") == "NO":
                n_no += 1
        else:
            n_invalid += 1

    response_models = {r.get("response_model") for r in responses.values()}
    fingerprints = {r.get("system_fingerprint") for r in responses.values()}
    unique_models = sorted(m for m in response_models if m is not None)
    model_errors = []
    if len(response_models) > 1:
        model_errors.append(f"multiple evaluator response_model values: "
                            f"{sorted(response_models)}")
    elif response_models and next(iter(response_models)) != acfg.EVALUATOR_MODEL:
        model_errors.append(f"evaluator response_model "
                            f"{next(iter(response_models))!r} != requested "
                            f"{acfg.EVALUATOR_MODEL!r}")

    # ── Summary ───────────────────────────────────────────────────────
    # All cross-binding counters are 0 by construction: the canonical
    # strong validators above would have aborted otherwise.
    print(f"Expected frozen tasks: {root_config.EXPECTED_N_TASKS}")
    print(f"Frozen execution signals: {len(signals)}")
    print(f"Execution PASS: {n_pass}")
    print(f"Execution FAIL: {n_fail}")
    print(f"Execution/T0 mismatches: {t0_mismatches}")
    if t0_discrepant:
        print(f"  discrepant ids: {', '.join(t0_discrepant)}")
    print(f"Frozen evaluator responses: {len(responses)}")
    print(f"Parsed Arm 3 decisions: {len(decisions)}")
    print(f"Missing execution signals: {len(sig_missing)}")
    if sig_missing:
        print(f"  missing signal ids: {', '.join(sig_missing)}")
    print(f"Missing evaluator responses: {len(resp_missing)}")
    if resp_missing:
        print(f"  missing response ids: {', '.join(resp_missing)}")
    print(f"Missing decisions: {len(dec_missing)}")
    if dec_missing:
        print(f"  missing decision ids: {', '.join(dec_missing)}")
    print(f"Duplicate IDs: 0")
    print(f"Unexpected IDs: 0")
    print(f"Candidate-hash mismatches: 0")
    print(f"Prompt mismatches: 0")
    print(f"Response-hash mismatches: 0")
    print(f"Parsed-response mismatches: 0")
    print(f"Protocol metadata mismatches: 0")
    print(f"Valid YES decisions: {n_yes}")
    print(f"Valid NO decisions: {n_no}")
    print(f"Invalid verdicts: {n_invalid}")
    print(f"YES + NO: {n_yes + n_no}")
    print(f"Unique response models: {unique_models}")
    fp_display = sorted((fp if fp is not None else "null") for fp in fingerprints)
    print(f"Unique system fingerprints: {fp_display}")

    valid = (
        not model_errors
        and set(signals) == expected_ids
        and set(responses) == expected_ids
        and set(decisions) == expected_ids
        and len(expected_ids) == root_config.EXPECTED_N_TASKS
        and t0_mismatches == 0
        and n_invalid == 0
        and n_yes + n_no == root_config.EXPECTED_N_TASKS
    )
    print(f"VALID ARM 3 EXECUTION POSITIVE CONTROL: "
          f"{'YES' if valid else 'NO'}")
    if not valid:
        for e in model_errors:
            print(f"  - {e}", file=sys.stderr)
    return 0 if valid else 1


if __name__ == "__main__":
    sys.exit(main())
