#!/usr/bin/env python3
"""Arm 3 PHASE A integrity certification: execution signal vs Stage 0 T0.

baseline.jsonl may be read ONLY inside the TWO dedicated Arm 3 certifier
modules: this one and validate_execution.py. The evaluator runtime never
reads it.

This module runs AFTER all 164 execution signals have been independently
generated and frozen, and BEFORE any Arm 3 evaluator API call. It
certifies the positive-control wiring:

    execution_status == "pass"  iff  Stage 0 baseline_correct == true
    execution_status == "fail"  iff  Stage 0 baseline_correct == false

for all 164 tasks.

On SUCCESS it atomically writes the certification artifact
(data/arm3_execution_signal_certification.json) recording the SHA-256 of
the exact signal-ledger byte sequence certified. Phase B refuses to
construct an API client unless that artifact exists, is valid, and
matches the CURRENT signal ledger.

On ANY validation failure it REMOVES the certification artifact, so a
failed run can never leave behind a certification that could authorize
Phase B for the failing/current ledger.

This module makes no API calls.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys
import tempfile
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

EXPECTED_PASS = 155
EXPECTED_FAIL = 9


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def write_certification_atomic(path: Path, cert: dict) -> None:
    """temp file -> flush -> fsync -> atomic rename."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(cert, indent=2) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def remove_certification(path: Path) -> None:
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass


def build_certification(signals_path: Path, n_pass: int, n_fail: int) -> dict:
    return {
        "schema_version": acfg.SCHEMA_VERSION,
        "experiment_version": acfg.EXPERIMENT_VERSION,
        "arm": acfg.ARM_NAME,
        "source_frozen_ledger_sha256": acfg.APPROVED_FROZEN_SHA256,
        "baseline_ledger_sha256": acfg.APPROVED_BASELINE_SHA256,
        "execution_signals_sha256": sha256_file(signals_path),
        "expected_tasks": root_config.EXPECTED_N_TASKS,
        "frozen_execution_signals": root_config.EXPECTED_N_TASKS,
        "execution_pass": n_pass,
        "execution_fail": n_fail,
        "execution_t0_mismatches": 0,
        "certification_status": "valid",
        "certified_at": utc_now(),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--signals", type=Path, default=acfg.SIGNALS_PATH)
    ap.add_argument("--frozen", type=Path, default=acfg.FROZEN_SOURCE_PATH)
    ap.add_argument("--baseline", type=Path, default=acfg.BASELINE_PATH)
    ap.add_argument("--certification", type=Path,
                    default=acfg.CERTIFICATION_PATH)
    args = ap.parse_args()

    errors = []

    # Source corpus hash.
    frozen_sha = sha256_file(args.frozen)
    if frozen_sha != acfg.APPROVED_FROZEN_SHA256:
        print(f"ARM 3 INTEGRITY ABORT: frozen ledger sha256 {frozen_sha} != "
              f"approved {acfg.APPROVED_FROZEN_SHA256}", file=sys.stderr)
        remove_certification(args.certification)
        return 1

    # Stage 0 scored baseline must be byte-exact too.
    baseline_sha = sha256_file(args.baseline)
    if baseline_sha != acfg.APPROVED_BASELINE_SHA256:
        print(f"ARM 3 INTEGRITY ABORT: baseline.jsonl sha256 {baseline_sha} "
              f"!= approved {acfg.APPROVED_BASELINE_SHA256}", file=sys.stderr)
        remove_certification(args.certification)
        return 1

    frozen_records = load_frozen_records(args.frozen)
    frozen_by_id = {r["task_id"]: r for r in frozen_records}
    try:
        signals = validate_signal_ledger(args.signals, frozen_by_id)
    except SystemExit as exc:
        print(f"ARM 3 INTEGRITY ABORT: {exc}", file=sys.stderr)
        remove_certification(args.certification)
        return 1

    baseline_records = load_jsonl(args.baseline)
    t0 = {r["task_id"]: r["baseline_correct"] for r in baseline_records}

    expected_ids = set(frozen_by_id)
    missing = sorted(expected_ids - set(signals),
                     key=lambda t: int(t.split("/")[1]))

    mismatches = []
    n_pass = 0
    n_fail = 0
    for tid in sorted(set(signals) & set(t0), key=lambda t: int(t.split("/")[1])):
        sig_pass = signals[tid]["execution_status"] == "pass"
        t0_true = t0[tid] is True
        if sig_pass != t0_true:
            mismatches.append(tid)
            errors.append(
                f"{tid}: execution_status={signals[tid]['execution_status']} "
                f"but Stage 0 baseline_correct={t0[tid]}")
    for tid, s in signals.items():
        if s["execution_status"] == "pass":
            n_pass += 1
        else:
            n_fail += 1

    print(f"Expected frozen tasks: {root_config.EXPECTED_N_TASKS}")
    print(f"Frozen execution signals: {len(signals)}")
    print(f"Missing execution signals: {len(missing)}")
    if missing:
        print(f"  missing ids: {', '.join(missing)}")
    print(f"Execution PASS: {n_pass}")
    print(f"Execution FAIL: {n_fail}")
    print(f"Execution/T0 mismatches: {len(mismatches)}")
    if mismatches:
        print(f"  discrepant ids: {', '.join(mismatches)}")

    valid = (not errors and not missing
             and len(signals) == root_config.EXPECTED_N_TASKS
             and set(signals) == expected_ids
             and n_pass == EXPECTED_PASS
             and n_fail == EXPECTED_FAIL)
    print(f"EXECUTION-SIGNAL INTEGRITY: {'YES' if valid else 'NO'}")
    if valid:
        cert = build_certification(args.signals, n_pass, n_fail)
        write_certification_atomic(args.certification, cert)
        print(f"Certification artifact written: {args.certification}")
        print(f"  execution_signals_sha256: "
              f"{cert['execution_signals_sha256']}")
        print("Phase B (evaluator calls) is cleared to proceed.")
        return 0
    # A failed validation must NOT leave behind a usable certification.
    remove_certification(args.certification)
    print("DO NOT run the Arm 3 evaluator phase.", file=sys.stderr)
    print(f"Certification artifact removed/absent: {args.certification}",
          file=sys.stderr)
    for e in errors:
        print(f"  - {e}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
