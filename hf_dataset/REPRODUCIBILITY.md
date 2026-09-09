# REPRODUCIBILITY — Auditor's Guide

This document lets an independent researcher audit the completed
benchmark **without making any API calls**. The preserved corpus is the
experiment; regeneration is replication, not reproduction.

## Experimental invariants

- **N = 164** canonical HumanEval tasks.
- **The same frozen candidate is judged by every evaluator arm.** Arms
  never regenerate, repair, or revise candidates.
- **Candidate immutability after freeze.** Each Stage 0 candidate was
  extracted, hashed (SHA-256), and fsynced to
  `data/frozen_candidates.jsonl` immediately after the API response
  returned — before any test execution. No code path ever called the
  model again for a frozen task.
- **T0 determined before evaluator analysis.** Stage-0 correctness
  (`baseline_correct`) was scored once, frozen, and never modified.
  Evaluator runners (Arms 1-2) could not read it; Arm 3's evaluator
  consumed only a freshly executed binary PASS/FAIL signal, never the
  T0 label.
- **Evaluator raw responses frozen before parsing.** Each arm appends
  and fsyncs the raw response ledger before the YES/NO (or structured)
  parser runs. A frozen response is never resampled.
- **No correction/revision phase exists anywhere in the repository.**

## Corpus counts

```
Stage 0 (T0):
    correct = 155
    incorrect = 9

Arm 1 (pure self-check):
    wrong accepted   = 6      wrong rejected   = 3
    correct accepted = 146    correct rejected = 9

Arm 2 (structured steelman):
    wrong accepted   = 3      wrong rejected   = 6
    correct accepted = 149    correct rejected = 6

Arm 3 (execution-grounded positive control):
    wrong accepted   = 0      wrong rejected   = 9
    correct accepted = 149    correct rejected = 6
```

(Arm 1 and Arm 2 both produced 152 YES / 12 NO; Arm 3 produced
149 YES / 15 NO. All counts independently derivable by joining the arm
decision ledgers to `data/baseline.jsonl` on `task_id`.)

## Definitive artifact hashes (SHA-256)

Recomputed from the current files. Also stored in `MANIFEST.sha256`
(`sha256sum -c MANIFEST.sha256` to verify).

```
a08167e369c3b3ec353f7f0bd84f843e7add0cb80f2ad4eb498fca6dc6c53aa3  data/frozen_candidates.jsonl
bf52d7622141788c51b0c5a5870b225c28424f5f5cf834e0f5086ba13a827a04  data/baseline.jsonl
e27544967bf8f285f2fe9d96de3b81b236a26741edaeb607ab304f5312490bc0  data/arm1_selfcheck_responses.jsonl
5d7ae85e6e44c7b6f0bcf90e3ba729ad09970071d78a326fbfb5b637056d4649  data/arm1_selfcheck.jsonl
820c25fc3c7e6defcbb78e8a5124cb67c3aeca2f55076b3ae8fa2dda0d22619b  data/arm2_steelman_responses.jsonl
baa69db31d4a64e0dcf97dfda1c95c4f37be37577809b32dc92c275d96697d70  data/arm2_steelman.jsonl
97e82cc88faabc2ddf43da1f72e25cd5e18a38ed5127c6a6b9e7b44e6c654369  data/arm3_execution_signals.jsonl
286d647be5062bb1d809262d67c1abb3aa562da7ae26f65e7468832b33e4e61f  data/arm3_execution_signal_certification.json
18063d272afbbe51257f7d4bf5c944ebe2773821585614f59bc13dc3ebb1bf6b  data/arm3_execution_responses.jsonl
ada055c1779975ce11b74951d86aa1fe48f4c17bbe083e4277d99e432ed8c282  data/arm3_execution.jsonl
```

## Validation commands

### Safe audit commands — NO API calls, read-only on `data/`

```bash
# Ledger certifiers (recompute hashes, re-derive prompts, rerun parsers)
python3 validate_baseline.py                    # Stage 0: VALID BASELINE
python3 arms/selfcheck/validate_selfcheck.py    # Arm 1: VALID ARM 1 SELFCHECK
python3 arms/steelman/validate_steelman.py      # Arm 2: VALID ARM 2 STEELMAN
python3 arms/execution/validate_execution.py    # Arm 3: VALID ARM 3
```

⚠️ One certifier is audit-safe (no API calls, touches no other artifact)
but **not** non-mutating:
`python3 arms/execution/validate_execution_signals.py` re-certifies the
signal ledger and rewrites `data/arm3_execution_signal_certification.json`
with identical certified content but a fresh `certified_at` timestamp
(changing that file's SHA-256). Run it only if you accept the timestamp
refresh; restore byte-identical artifacts afterwards with
`git checkout -- data/arm3_execution_signal_certification.json`.

```bash
# Local regression suites (fake clients only; no network)
python3 tests/test_stage0.py                    # 29 tests
python3 tests/test_arm1_selfcheck.py            # 19 tests
python3 tests/test_arm2_steelman.py             # 24 tests
python3 tests/test_arm3_execution.py            # 54 tests

# Hash verification
sha256sum -c MANIFEST.sha256
```

### Production commands — make REAL API calls; NOT for reproduction

```bash
python3 generate_baseline.py                    # Stage 0 generation (API)
python3 arms/selfcheck/run_selfcheck.py         # Arm 1 evaluator (API)
python3 arms/steelman/run_steelman.py           # Arm 2 evaluator (API)
python3 arms/execution/run_execution_signals.py # Arm 3 Phase A (local execution)
python3 arms/execution/run_execution_evaluator.py  # Arm 3 evaluator (API)
```

These are preserved for inspection and for researchers who wish to run a
*replication* with their own credentials. They are **not** required — and
will refuse to regenerate anything already frozen — for auditing the
reported experiment.

## Operational note (transparency)

During Stage 0 baseline generation, the process was interrupted after
initiating the request for **HumanEval/85** but before a response was
persisted. On resume the task remained unfrozen and the identical
logical request was reissued. No candidate content from the interrupted
request was observed or used in deciding to reissue it. Every other task
contributes exactly one successfully returned and frozen candidate;
transport-level retries elsewhere (count: 0 for Stage 0 and all arms)
would likewise have been logged and never conditioned on candidate
content.
