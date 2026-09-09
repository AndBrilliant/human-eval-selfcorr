#!/usr/bin/env python3
"""Certify Arm 3 (execution-grounded positive control): execution-signal
ledger, evaluator-response ledger, and decision ledger against the
approved Stage 0 corpus.

Makes NO API calls. This final certifier MAY read data/baseline.jsonl for
the Execution/T0 integrity comparison only (the same reference-test
mechanism defines both; the comparison certifies the positive control).
It never reads Arm 1 or Arm 2 ledgers. It computes NO cross-arm
statistics.

Certifies (see the summary block): 164 signals, 164 responses, 164
decisions; no missing/duplicates/unexpected IDs; execution PASS/FAIL
counts; Execution/T0 mismatches == 0; candidate-hash, prompt,
response-hash, parser-rerun, and protocol-metadata checks; invalid
verdicts == 0; unique response models/fingerprints reported.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import config as root_config
from arms.execution import config as acfg
from arms.execution.run_execution_signals import (
    SIGNAL_PROTOCOL_FIELDS,
    SIGNAL_REQUIRED_FIELDS,
    load_frozen_records,
    load_jsonl,
    sha256_file,
    sha256_text,
)
from arms.execution.run_execution_evaluator import (
    RESPONSE_PROTOCOL_FIELDS,
    RESPONSE_REQUIRED_FIELDS,
    build_evaluator_prompt,
    parse_verdict,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--signals", type=Path, default=acfg.SIGNALS_PATH)
    ap.add_argument("--responses", type=Path, default=acfg.RESPONSES_PATH)
    ap.add_argument("--decisions", type=Path, default=acfg.DECISIONS_PATH)
    ap.add_argument("--frozen", type=Path, default=acfg.FROZEN_SOURCE_PATH)
    ap.add_argument("--baseline", type=Path, default=acfg.BASELINE_PATH)
    args = ap.parse_args()

    errors = []

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

    signals = load_jsonl(args.signals)
    responses = load_jsonl(args.responses)
    decisions = load_jsonl(args.decisions)
    baseline_records = load_jsonl(args.baseline)
    t0 = {r["task_id"]: r["baseline_correct"] for r in baseline_records}

    def id_stats(records):
        counts = {}
        for r in records:
            counts[r.get("task_id")] = counts.get(r.get("task_id"), 0) + 1
        dupes = sorted(t for t, n in counts.items() if n > 1)
        ids = set(counts)
        missing = sorted(expected_ids - ids, key=lambda t: int(t.split("/")[1]))
        unexpected = sorted(ids - expected_ids)
        return ids, dupes, missing, unexpected

    sig_ids, sig_dupes, sig_missing, sig_unexpected = id_stats(signals)
    resp_ids, resp_dupes, resp_missing, resp_unexpected = id_stats(responses)
    dec_ids, dec_dupes, dec_missing, dec_unexpected = id_stats(decisions)
    n_dupes = len(sig_dupes) + len(resp_dupes) + len(dec_dupes)
    n_unexpected = (len(sig_unexpected) + len(resp_unexpected)
                    + len(dec_unexpected))
    for t in sig_dupes + resp_dupes + dec_dupes:
        errors.append(f"duplicate task_id {t}")
    for t in sig_unexpected + resp_unexpected + dec_unexpected:
        errors.append(f"unexpected task_id {t}")

    # ── Execution-signal checks (+ T0 integrity) ──────────────────────
    sig_cand_mismatches = 0
    sig_protocol_mismatches = 0
    t0_mismatches = 0
    n_pass = 0
    n_fail = 0
    signals_by_id = {}
    for s in signals:
        tid = s.get("task_id")
        signals_by_id.setdefault(tid, s)
        for field in SIGNAL_REQUIRED_FIELDS:
            if field not in s:
                errors.append(f"{tid}: signal missing field {field!r}")
        if tid not in frozen_by_id:
            continue
        if s.get("candidate_sha256") != frozen_by_id[tid]["candidate_sha256"]:
            sig_cand_mismatches += 1
            errors.append(f"{tid}: signal candidate_sha256 != frozen ledger")
        for field, expected in SIGNAL_PROTOCOL_FIELDS.items():
            if s.get(field) != expected:
                sig_protocol_mismatches += 1
                errors.append(f"{tid}: signal {field}={s.get(field)!r} != "
                              f"protocol {expected!r}")
        # Treatment text must be exactly one of the three approved texts
        # and consistent with the recorded status.
        treatment = s.get("execution_treatment_text")
        if s.get("execution_status") == "pass":
            n_pass += 1
            if treatment != acfg.TREATMENT_PASS:
                sig_protocol_mismatches += 1
                errors.append(f"{tid}: pass signal with non-PASS treatment")
        elif s.get("execution_status") == "fail":
            n_fail += 1
            if treatment not in (acfg.TREATMENT_FAIL,
                                 acfg.TREATMENT_FAIL_TIMEOUT):
                sig_protocol_mismatches += 1
                errors.append(f"{tid}: fail signal with unknown treatment")
        else:
            sig_protocol_mismatches += 1
            errors.append(f"{tid}: unknown execution_status "
                          f"{s.get('execution_status')!r}")
        # Integrity certification vs Stage 0 T0 (this module only).
        if tid in t0:
            sig_pass = s.get("execution_status") == "pass"
            if sig_pass != (t0[tid] is True):
                t0_mismatches += 1
                errors.append(f"{tid}: execution_status="
                              f"{s.get('execution_status')} but Stage 0 "
                              f"baseline_correct={t0[tid]}")

    # ── Per-response checks ───────────────────────────────────────────
    cand_hash_mismatches = 0
    prompt_mismatches = 0
    raw_hash_mismatches = 0
    protocol_mismatches = 0
    response_models = set()
    fingerprints = set()
    resp_by_id = {}
    for r in responses:
        tid = r.get("task_id")
        resp_by_id.setdefault(tid, r)
        for field in RESPONSE_REQUIRED_FIELDS:
            if field not in r:
                errors.append(f"{tid}: response missing field {field!r}")
        if tid not in frozen_by_id:
            continue
        fr = frozen_by_id[tid]
        sig = signals_by_id.get(tid)
        if r.get("candidate_sha256") != fr["candidate_sha256"]:
            cand_hash_mismatches += 1
            errors.append(f"{tid}: candidate_sha256 != approved frozen ledger")
        if sig is None:
            prompt_mismatches += 1
            errors.append(f"{tid}: response has no matching execution signal")
        else:
            if r.get("execution_status") != sig.get("execution_status"):
                protocol_mismatches += 1
                errors.append(f"{tid}: response execution_status != signal")
            if r.get("execution_treatment_text") != \
                    sig.get("execution_treatment_text"):
                protocol_mismatches += 1
                errors.append(f"{tid}: response treatment != signal treatment")
            expected_prompt = build_evaluator_prompt(
                fr["prompt"], fr["candidate_code"],
                sig["execution_treatment_text"])
            if r.get("evaluator_prompt") != expected_prompt:
                prompt_mismatches += 1
                errors.append(f"{tid}: evaluator_prompt != exact template "
                              f"output")
        raw = r.get("raw_evaluator_response")
        if isinstance(raw, str) and \
                sha256_text(raw) != r.get("raw_evaluator_response_sha256"):
            raw_hash_mismatches += 1
            errors.append(f"{tid}: raw_evaluator_response hash mismatch")
        for field, expected in RESPONSE_PROTOCOL_FIELDS.items():
            if r.get(field) != expected:
                protocol_mismatches += 1
                errors.append(f"{tid}: {field}={r.get(field)!r} != "
                              f"protocol {expected!r}")
        response_models.add(r.get("response_model"))
        fingerprints.add(r.get("system_fingerprint"))

    unique_models = sorted(m for m in response_models if m is not None)
    if len(response_models) > 1:
        errors.append(f"multiple evaluator response_model values: "
                      f"{sorted(response_models)}")
    elif response_models and next(iter(response_models)) != acfg.EVALUATOR_MODEL:
        errors.append(f"evaluator response_model "
                      f"{next(iter(response_models))!r} != requested "
                      f"{acfg.EVALUATOR_MODEL!r}")

    # ── Per-decision checks ───────────────────────────────────────────
    parsed_mismatches = 0
    n_yes = 0
    n_no = 0
    n_invalid = 0
    for d in decisions:
        tid = d.get("task_id")
        if tid not in frozen_by_id:
            continue
        fr = frozen_by_id[tid]
        if d.get("candidate_sha256") != fr["candidate_sha256"]:
            parsed_mismatches += 1
            errors.append(f"{tid}: decision candidate_sha256 != frozen ledger")
        rr = resp_by_id.get(tid)
        if rr is None:
            parsed_mismatches += 1
            errors.append(f"{tid}: decision has no matching frozen response")
            continue
        if d.get("raw_evaluator_response_sha256") != \
                rr.get("raw_evaluator_response_sha256"):
            parsed_mismatches += 1
            errors.append(f"{tid}: decision raw-response hash != response "
                          f"ledger")
            continue
        if d.get("execution_status") != rr.get("execution_status"):
            parsed_mismatches += 1
            errors.append(f"{tid}: decision execution_status != response")
        v, a, ps = parse_verdict(rr["raw_evaluator_response"])
        if (d.get("verdict"), d.get("acceptance"), d.get("parse_status")) != \
                (v, a, ps):
            parsed_mismatches += 1
            errors.append(f"{tid}: stored decision != parser output")
        if d.get("parse_status") == "valid":
            if d.get("verdict") == "YES":
                n_yes += 1
            elif d.get("verdict") == "NO":
                n_no += 1
        else:
            n_invalid += 1

    # ── Summary ───────────────────────────────────────────────────────
    print(f"Expected frozen tasks: {root_config.EXPECTED_N_TASKS}")
    print(f"Frozen execution signals: {len(sig_ids)}")
    print(f"Execution PASS: {n_pass}")
    print(f"Execution FAIL: {n_fail}")
    print(f"Execution/T0 mismatches: {t0_mismatches}")
    print(f"Frozen evaluator responses: {len(resp_ids)}")
    print(f"Parsed Arm 3 decisions: {len(dec_ids)}")
    print(f"Missing execution signals: {len(sig_missing)}")
    if sig_missing:
        print(f"  missing signal ids: {', '.join(sig_missing)}")
    print(f"Missing evaluator responses: {len(resp_missing)}")
    if resp_missing:
        print(f"  missing response ids: {', '.join(resp_missing)}")
    print(f"Missing decisions: {len(dec_missing)}")
    if dec_missing:
        print(f"  missing decision ids: {', '.join(dec_missing)}")
    print(f"Duplicate IDs: {n_dupes}")
    print(f"Unexpected IDs: {n_unexpected}")
    print(f"Candidate-hash mismatches: "
          f"{cand_hash_mismatches + sig_cand_mismatches}")
    print(f"Prompt mismatches: {prompt_mismatches}")
    print(f"Response-hash mismatches: {raw_hash_mismatches}")
    print(f"Parsed-response mismatches: {parsed_mismatches}")
    print(f"Protocol metadata mismatches: "
          f"{protocol_mismatches + sig_protocol_mismatches}")
    print(f"Valid YES decisions: {n_yes}")
    print(f"Valid NO decisions: {n_no}")
    print(f"Invalid verdicts: {n_invalid}")
    print(f"YES + NO: {n_yes + n_no}")
    print(f"Unique response models: {unique_models}")
    fp_display = sorted((fp if fp is not None else "null") for fp in fingerprints)
    print(f"Unique system fingerprints: {fp_display}")

    valid = (
        not errors
        and sig_ids == expected_ids
        and resp_ids == expected_ids
        and dec_ids == expected_ids
        and len(expected_ids) == root_config.EXPECTED_N_TASKS
        and n_dupes == 0
        and n_unexpected == 0
        and t0_mismatches == 0
        and cand_hash_mismatches == 0
        and sig_cand_mismatches == 0
        and prompt_mismatches == 0
        and raw_hash_mismatches == 0
        and parsed_mismatches == 0
        and protocol_mismatches == 0
        and sig_protocol_mismatches == 0
        and n_invalid == 0
        and n_yes + n_no == root_config.EXPECTED_N_TASKS
    )
    print(f"VALID ARM 3 EXECUTION POSITIVE CONTROL: "
          f"{'YES' if valid else 'NO'}")
    if not valid and errors:
        print("\nErrors:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
    return 0 if valid else 1


if __name__ == "__main__":
    sys.exit(main())
