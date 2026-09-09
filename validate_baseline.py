#!/usr/bin/env python3
"""Certify the frozen Stage 0 baseline: BOTH ledgers.

Verifies, against data/frozen_candidates.jsonl and data/baseline.jsonl
(override with --frozen / --baseline):

  Dataset (hash-pinned, explicit exceptions — active under python -O):
    - exactly 164 expected task IDs, no missing/duplicates/unexpected.

  Per frozen candidate:
    - task_index matches canonical dataset order
    - stored prompt == canonical HumanEval prompt exactly
    - stored entry_point == canonical entry_point exactly
    - stored dataset_sha256 == configured dataset SHA256
    - generation_prompt == exact configured prompt for that task
    - generator_model == configured pinned snapshot
    - generator_temperature == 0
    - reasoning_effort == configured value
    - max_completion_tokens == configured value
    - sha256(raw_model_response) == raw_response_sha256
    - sha256(candidate_code) == candidate_sha256
    - re-running the deterministic extractor on raw_model_response
      reproduces the stored candidate_code and extraction_method exactly

  Per scored baseline record:
    - matching frozen candidate exists
    - candidate_code / candidate_sha256 exactly match the frozen record
    - baseline_correct is boolean
    - test_status is one of the allowed statuses
    - baseline_correct == true  iff  test_status == "pass"

  Model-version integrity:
    - all unique response_model values are reported; more than one unique
      value (or a value different from the requested snapshot) is a hard
      failure — the baseline must not silently mix model versions.

Exit code 0 iff VALID. This utility performs no generation, no model
calls, and no test execution.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
from common.dataset import DatasetIntegrityError, load_tasks
from common.extraction import extract_candidate


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_jsonl(path: Path, label: str, errors: list) -> list:
    records = []
    if not Path(path).is_file():
        errors.append(f"{label}: file not found: {path}")
        return records
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                errors.append(f"{label} line {lineno}: invalid JSON: {exc}")
    return records


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frozen", type=Path, default=config.FROZEN_PATH)
    ap.add_argument("--baseline", type=Path, default=config.BASELINE_PATH)
    ap.add_argument("--dataset", type=Path, default=config.DATASET_PATH)
    args = ap.parse_args()

    errors = []

    # ── Dataset (hard integrity checks via explicit exceptions) ──────
    try:
        tasks = load_tasks(args.dataset)
    except DatasetIntegrityError as exc:
        print(f"DATASET INTEGRITY FAILURE: {exc}", file=sys.stderr)
        return 1
    task_by_id = {t["task_id"]: t for t in tasks}
    expected_ids = set(task_by_id)

    frozen = load_jsonl(args.frozen, "frozen", errors)
    scored = load_jsonl(args.baseline, "baseline", errors)

    # ── ID coverage / duplicates ──────────────────────────────────────
    def id_stats(records):
        counts = {}
        for r in records:
            counts[r.get("task_id")] = counts.get(r.get("task_id"), 0) + 1
        dupes = sorted(t for t, n in counts.items() if n > 1)
        ids = set(counts)
        missing = sorted(expected_ids - ids, key=lambda t: int(t.split("/")[1]))
        unexpected = sorted(ids - expected_ids)
        return ids, dupes, missing, unexpected

    frozen_ids, frozen_dupes, frozen_missing, frozen_unexpected = id_stats(frozen)
    scored_ids, scored_dupes, scored_missing, scored_unexpected = id_stats(scored)
    n_dupes = len(frozen_dupes) + len(scored_dupes)
    for t in frozen_dupes:
        errors.append(f"frozen: duplicate task_id {t}")
    for t in scored_dupes:
        errors.append(f"baseline: duplicate task_id {t}")
    for t in frozen_unexpected:
        errors.append(f"frozen: unexpected task_id {t}")
    for t in scored_unexpected:
        errors.append(f"baseline: unexpected task_id {t}")

    # ── Per-record frozen checks ──────────────────────────────────────
    raw_hash_mismatches = 0
    cand_hash_mismatches = 0
    protocol_mismatches = 0
    response_models = set()
    system_fingerprints = set()
    frozen_by_id = {}
    for r in frozen:
        tid = r.get("task_id")
        frozen_by_id.setdefault(tid, r)
        if tid not in task_by_id:
            continue
        task = task_by_id[tid]
        if r.get("task_index") != task["task_index"]:
            protocol_mismatches += 1
            errors.append(f"{tid}: task_index {r.get('task_index')} != "
                          f"canonical {task['task_index']}")
        if r.get("prompt") != task["prompt"]:
            protocol_mismatches += 1
            errors.append(f"{tid}: stored prompt != canonical HumanEval prompt")
        if r.get("entry_point") != task["entry_point"]:
            protocol_mismatches += 1
            errors.append(f"{tid}: stored entry_point != canonical entry_point")
        if r.get("dataset_sha256") != config.DATASET_SHA256:
            protocol_mismatches += 1
            errors.append(f"{tid}: dataset_sha256 mismatch")
        expected_gen_prompt = config.GENERATION_PROMPT_TEMPLATE.format(
            prompt=task["prompt"])
        if r.get("generation_prompt") != expected_gen_prompt:
            protocol_mismatches += 1
            errors.append(f"{tid}: generation_prompt != configured template output")
        for field, expected in (
            ("generator_model", config.GENERATOR_MODEL),
            ("generator_temperature", config.GENERATOR_TEMPERATURE),
            ("reasoning_effort", config.REASONING_EFFORT),
            ("max_completion_tokens", config.MAX_COMPLETION_TOKENS),
        ):
            if r.get(field) != expected:
                protocol_mismatches += 1
                errors.append(f"{tid}: {field}={r.get(field)!r} != configured "
                              f"{expected!r}")
        raw = r.get("raw_model_response")
        code = r.get("candidate_code")
        if isinstance(raw, str) and sha256_text(raw) != r.get("raw_response_sha256"):
            raw_hash_mismatches += 1
            errors.append(f"{tid}: raw_response_sha256 mismatch (immutability "
                          f"violation)")
        if isinstance(code, str) and sha256_text(code) != r.get("candidate_sha256"):
            cand_hash_mismatches += 1
            errors.append(f"{tid}: candidate_sha256 mismatch (immutability "
                          f"violation)")
        if isinstance(raw, str):
            re_code, re_method = extract_candidate(raw)
            if re_code != code or re_method != r.get("extraction_method"):
                cand_hash_mismatches += 1
                errors.append(f"{tid}: deterministic re-extraction does not "
                              f"reproduce stored candidate_code/extraction_method")
        response_models.add(r.get("response_model"))
        system_fingerprints.add(r.get("system_fingerprint"))

    unique_models = sorted(m for m in response_models if m is not None)
    if len(response_models) > 1:
        errors.append(f"multiple unique response_model values: "
                      f"{sorted(response_models)} — baseline must not mix "
                      f"model versions")
    elif response_models and next(iter(response_models)) != config.GENERATOR_MODEL:
        errors.append(f"API returned response_model "
                      f"{next(iter(response_models))!r}, requested snapshot "
                      f"{config.GENERATOR_MODEL!r}")

    # ── Per-record scored checks ──────────────────────────────────────
    cand_score_mismatches = 0
    n_correct = 0
    n_incorrect = 0
    for r in scored:
        tid = r.get("task_id")
        if r.get("generation_status") != "success":
            errors.append(f"{tid}: baseline generation_status="
                          f"{r.get('generation_status')!r} (must be 'success')")
        if r.get("attempt_count") != 1:
            errors.append(f"{tid}: attempt_count={r.get('attempt_count')} (must be 1)")
        fr = frozen_by_id.get(tid)
        if fr is None:
            cand_score_mismatches += 1
            errors.append(f"{tid}: scored record has no matching frozen candidate")
        else:
            if r.get("candidate_sha256") != fr.get("candidate_sha256"):
                cand_score_mismatches += 1
                errors.append(f"{tid}: scored candidate_sha256 != frozen "
                              f"candidate_sha256")
            if r.get("candidate_code") != fr.get("candidate_code"):
                cand_score_mismatches += 1
                errors.append(f"{tid}: scored candidate_code != frozen "
                              f"candidate_code")
        bc = r.get("baseline_correct")
        ts = r.get("test_status")
        if not isinstance(bc, bool):
            errors.append(f"{tid}: baseline_correct missing or not boolean")
        if ts not in config.ALLOWED_TEST_STATUSES:
            errors.append(f"{tid}: test_status={ts!r} not in "
                          f"{config.ALLOWED_TEST_STATUSES}")
        if isinstance(bc, bool) and ts in config.ALLOWED_TEST_STATUSES:
            if bc != (ts == "pass"):
                cand_score_mismatches += 1
                errors.append(f"{tid}: baseline_correct={bc} inconsistent with "
                              f"test_status={ts!r}")
        if isinstance(bc, bool):
            if bc:
                n_correct += 1
            else:
                n_incorrect += 1

    # ── Summary ───────────────────────────────────────────────────────
    print(f"Expected HumanEval tasks: {config.EXPECTED_N_TASKS}")
    print(f"Frozen candidate records: {len(frozen_ids)}")
    print(f"Scored baseline records: {len(scored_ids)}")
    print(f"Missing candidates: {len(frozen_missing)}")
    if frozen_missing:
        print(f"  missing candidate ids: {', '.join(frozen_missing)}")
    print(f"Missing scores: {len(scored_missing)}")
    if scored_missing:
        print(f"  missing score ids: {', '.join(scored_missing)}")
    print(f"Duplicates: {n_dupes}")
    print(f"Raw-response hash mismatches: {raw_hash_mismatches}")
    print(f"Candidate hash mismatches: {cand_hash_mismatches}")
    print(f"Protocol metadata mismatches: {protocol_mismatches}")
    print(f"Candidate/score mismatches: {cand_score_mismatches}")
    print(f"Unique API response models: {unique_models}")
    # Reported for audit; differing fingerprints are NOT a validity failure.
    # null is permitted if genuinely absent.
    fp_display = sorted((fp if fp is not None else "null")
                        for fp in system_fingerprints)
    print(f"Unique system fingerprints: {fp_display}")
    print(f"Baseline correct: {n_correct}")
    print(f"Baseline incorrect: {n_incorrect}")
    print(f"N0 + N1: {n_correct + n_incorrect}")

    valid = (
        not errors
        and frozen_ids == expected_ids
        and scored_ids == expected_ids
        and n_dupes == 0
        and raw_hash_mismatches == 0
        and cand_hash_mismatches == 0
        and protocol_mismatches == 0
        and cand_score_mismatches == 0
    )
    print(f"VALID BASELINE: {'YES' if valid else 'NO'}")
    if not valid and errors:
        print("\nErrors:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
    return 0 if valid else 1


if __name__ == "__main__":
    sys.exit(main())
