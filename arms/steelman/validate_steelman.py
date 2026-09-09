#!/usr/bin/env python3
"""Certify Arm 2 (structured steelman / self-debate): BOTH arm ledgers
against the approved Stage 0 frozen candidate corpus.

Makes NO API calls. Uses the frozen candidate ledger only. NEVER reads
data/baseline.jsonl and NEVER reads any Arm 1 result ledger.

Certifies:
  - source frozen ledger sha256 == approved Stage 0 corpus hash
  - frozen Arm 2 responses: 164, no missing/duplicates/unexpected IDs
  - parsed Arm 2 decisions: 164, no missing/duplicates/unexpected IDs
  - per response record: candidate_sha256 matches the approved frozen
    ledger; evaluator_prompt recomputed byte-exactly; raw-response hash
    recomputed; protocol metadata matches the Arm 2 configuration
  - per decision record: candidate/raw hashes match; the deterministic
    structured parser rerun on the frozen raw response reproduces stored
    case_against, case_for, verdict, acceptance, and parse_status exactly
  - invalid structured responses: 0 for a valid arm
  - unique response models reported (mixed versions = failure)
  - unique system fingerprints reported (not a validity failure)

Does NOT calculate ground-truth metrics or cross-arm statistics.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import config as root_config
from arms.steelman import config as acfg
from arms.steelman.run_steelman import (
    RESPONSE_PROTOCOL_FIELDS,
    RESPONSE_REQUIRED_FIELDS,
    build_evaluator_prompt,
    load_frozen_records,
    load_jsonl,
    parse_structured,
    sha256_file,
    sha256_text,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--responses", type=Path, default=acfg.RESPONSES_PATH)
    ap.add_argument("--decisions", type=Path, default=acfg.DECISIONS_PATH)
    ap.add_argument("--frozen", type=Path, default=acfg.FROZEN_SOURCE_PATH)
    args = ap.parse_args()

    errors = []

    # ── Source corpus hash ────────────────────────────────────────────
    source_sha = sha256_file(args.frozen)
    if source_sha != acfg.APPROVED_FROZEN_SHA256:
        print(f"ARM 2 SOURCE ABORT: frozen ledger sha256 {source_sha} != "
              f"approved {acfg.APPROVED_FROZEN_SHA256}", file=sys.stderr)
        return 1
    frozen_records = load_frozen_records(args.frozen)
    frozen_by_id = {r["task_id"]: r for r in frozen_records}
    expected_ids = set(frozen_by_id)

    responses = load_jsonl(args.responses)
    decisions = load_jsonl(args.decisions)

    def id_stats(records):
        counts = {}
        for r in records:
            counts[r.get("task_id")] = counts.get(r.get("task_id"), 0) + 1
        dupes = sorted(t for t, n in counts.items() if n > 1)
        ids = set(counts)
        missing = sorted(expected_ids - ids, key=lambda t: int(t.split("/")[1]))
        unexpected = sorted(ids - expected_ids)
        return ids, dupes, missing, unexpected

    resp_ids, resp_dupes, resp_missing, resp_unexpected = id_stats(responses)
    dec_ids, dec_dupes, dec_missing, dec_unexpected = id_stats(decisions)
    n_dupes = len(resp_dupes) + len(dec_dupes)
    n_unexpected = len(resp_unexpected) + len(dec_unexpected)
    for t in resp_dupes + dec_dupes:
        errors.append(f"duplicate task_id {t}")
    for t in resp_unexpected + dec_unexpected:
        errors.append(f"unexpected task_id {t}")

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
        if r.get("candidate_sha256") != fr["candidate_sha256"]:
            cand_hash_mismatches += 1
            errors.append(f"{tid}: candidate_sha256 != approved frozen ledger")
        expected_prompt = build_evaluator_prompt(fr["prompt"], fr["candidate_code"])
        if r.get("evaluator_prompt") != expected_prompt:
            prompt_mismatches += 1
            errors.append(f"{tid}: evaluator_prompt != exact template output")
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
            errors.append(f"{tid}: decision raw-response hash != response ledger")
            continue
        # Rerun the deterministic structured parser on the frozen raw.
        ca, cf, v, a, ps = parse_structured(rr["raw_evaluator_response"])
        stored = (d.get("case_against"), d.get("case_for"), d.get("verdict"),
                  d.get("acceptance"), d.get("parse_status"))
        if stored != (ca, cf, v, a, ps):
            parsed_mismatches += 1
            errors.append(f"{tid}: stored decision != structured parser output")
        if d.get("parse_status") == "valid":
            if d.get("verdict") == "YES":
                n_yes += 1
            elif d.get("verdict") == "NO":
                n_no += 1
        else:
            n_invalid += 1

    # ── Summary ───────────────────────────────────────────────────────
    print(f"Expected frozen tasks: {root_config.EXPECTED_N_TASKS}")
    print(f"Frozen Arm 2 responses: {len(resp_ids)}")
    print(f"Parsed Arm 2 decisions: {len(dec_ids)}")
    print(f"Missing responses: {len(resp_missing)}")
    if resp_missing:
        print(f"  missing response ids: {', '.join(resp_missing)}")
    print(f"Missing decisions: {len(dec_missing)}")
    if dec_missing:
        print(f"  missing decision ids: {', '.join(dec_missing)}")
    print(f"Duplicate task IDs: {n_dupes}")
    print(f"Unexpected task IDs: {n_unexpected}")
    print(f"Approved Stage 0 source hash correct: YES")
    print(f"  ({source_sha})")
    print(f"Candidate-hash mismatches: {cand_hash_mismatches}")
    print(f"Evaluator-prompt mismatches: {prompt_mismatches}")
    print(f"Raw-response hash mismatches: {raw_hash_mismatches}")
    print(f"Structured-parser mismatches: {parsed_mismatches}")
    print(f"Protocol metadata mismatches: {protocol_mismatches}")
    print(f"Valid YES decisions: {n_yes}")
    print(f"Valid NO decisions: {n_no}")
    print(f"Invalid structured responses: {n_invalid}")
    print(f"YES + NO: {n_yes + n_no}")
    print(f"Unique response models: {unique_models}")
    fp_display = sorted((fp if fp is not None else "null") for fp in fingerprints)
    print(f"Unique system fingerprints: {fp_display}")

    valid = (
        not errors
        and resp_ids == expected_ids
        and dec_ids == expected_ids
        and len(expected_ids) == root_config.EXPECTED_N_TASKS
        and n_dupes == 0
        and n_unexpected == 0
        and cand_hash_mismatches == 0
        and prompt_mismatches == 0
        and raw_hash_mismatches == 0
        and parsed_mismatches == 0
        and protocol_mismatches == 0
        and n_invalid == 0
        and n_yes + n_no == root_config.EXPECTED_N_TASKS
    )
    print(f"VALID ARM 2 STEELMAN: {'YES' if valid else 'NO'}")
    if not valid and errors:
        print("\nErrors:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
    return 0 if valid else 1


if __name__ == "__main__":
    sys.exit(main())
