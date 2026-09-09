# Arm 3 Execution — Phase A Run Report

Run date: 2026-09-09 (UTC). Single uninterrupted run; one process, no resumes.

## Commands run (exact)

```
python3 tests/test_arm3_execution.py                  # 54/54 PASS (gate)
python3 -m py_compile arms/execution/*.py             # compilation check
python3 arms/execution/run_execution_signals.py       # Phase A (local execution only)
python3 arms/execution/validate_execution_signals.py  # integrity certifier
```

Phase B (`run_execution_evaluator.py`) was NOT run. No OpenAI client was
constructed. NO evaluator/model/API calls were made.

## Phase A timing

- Start: 2026-09-09T05:31:31+00:00 (`arm3_signals_start`, todo=164)
- End: 2026-09-09T05:31:36+00:00 (`arm3_signals_end`, complete=true)
- Exit code: 0

## Signal ledger results

- Total execution signals: 164
- PASS (execution_status=pass): 155
- FAIL (execution_status=fail): 9
- Timeout count: 0
- fail_assertion count: 9
- fail_error count: 0
- Infrastructure retries: 0
- Infrastructure failures: 0 (`data/arm3_execution_failures.jsonl` does not exist)
- Duplicate IDs: 0
- Missing IDs: 0
- Unexpected IDs: 0
- Candidate SHA mismatches (vs approved frozen ledger): 0
- Execution/T0 mismatches: 0

## Certification

- Certifier exit code: 0
- `EXECUTION-SIGNAL INTEGRITY: YES`
- certification_status: "valid"
- certification_status fields verified: expected_tasks=164,
  frozen_execution_signals=164, execution_pass=155, execution_fail=9,
  execution_t0_mismatches=0, source/baseline hashes approved,
  certified_at present.

## Definitive hashes

- execution signal ledger SHA-256
  (`data/arm3_execution_signals.jsonl`):
  `97e82cc88faabc2ddf43da1f72e25cd5e18a38ed5127c6a6b9e7b44e6c654369`
- certification artifact SHA-256
  (`data/arm3_execution_signal_certification.json`):
  `286d647be5062bb1d809262d67c1abb3aa562da7ae26f65e7468832b33e4e61f`
- The certification's `execution_signals_sha256` field equals the signal
  ledger SHA-256 above (hash-bound to the current ledger).

## Six prior definitive hashes (recomputed after the run; UNCHANGED)

- Stage 0 frozen: `a08167e369c3b3ec353f7f0bd84f843e7add0cb80f2ad4eb498fca6dc6c53aa3`
- Stage 0 baseline: `bf52d7622141788c51b0c5a5870b225c28424f5f5cf834e0f5086ba13a827a04`
- Arm 1 responses: `e27544967bf8f285f2fe9d96de3b81b236a26741edaeb607ab304f5312490bc0`
- Arm 1 decisions: `5d7ae85e6e44c7b6f0bcf90e3ba729ad09970071d78a326fbfb5b637056d4649`
- Arm 2 responses: `820c25fc3c7e6defcbb78e8a5124cb67c3aeca2f55076b3ae8fa2dda0d22619b`
- Arm 2 decisions: `baa69db31d4a64e0dcf97dfda1c95c4f37be37577809b32dc92c275d96697d70`

## Confirmations

- NO evaluator/model/API calls were made.
- Phase B was NOT run.
- No correction or revision occurred; every executed candidate was the
  exact hash-verified frozen Stage 0 candidate, unmodified.
- No operational interruptions or resumes occurred; no anomalies.
- The runtime never accessed Arm 1/Arm 2 verdicts during signal
  generation; baseline.jsonl was read only by the dedicated certifier
  after all 164 signals were frozen.

Local zip SHA-256: recorded externally after archive finalization
Indigo zip SHA-256: recorded externally after archive finalization
