# Arm 3 Execution-Grounded Evaluator — Run Report (Phase B)

Run date: 2026-09-09 (UTC). Single uninterrupted production run; one
process, no resumes.

**Arm 3 is an execution-grounded positive control, not an independent
test benchmark.** The external signal was derived from executing the same
immutable candidates against the HumanEval reference tests that define
the benchmark correctness label.

## Production command (exact)

```
python3 arms/execution/run_execution_evaluator.py
```

Pre-run gates passed: all eight approved artifact hashes recomputed
exact; local suite 54/54 PASS; compilation checks pass; Phase B
preflight (approved frozen ledger → complete certified signal ledger →
hash-bound certification matching the current signal ledger → strong
response/decision ledger validation) succeeded BEFORE client
construction.

## Run timing / interruptions

- Start: 2026-09-09T05:45:49+00:00 (`arm3_run_start`)
- End: 2026-09-09T05:48:23+00:00 (`arm3_run_end`, complete=true)
- Process exit code: 0
- Interruptions/resumes: none (single `arm3_run_start` event)

## Raw corpus

- Raw evaluator responses: 164
- Parsed decisions: 164
- YES count: 149
- NO count: 15
- Invalid-verdict count: 0

## Operational quantities

- Transport retries: 0
- Failure records: 0 (`data/arm3_execution_failures.jsonl` does not exist)
- Total input tokens: 61,266
- Total output tokens: 656
- Total estimated API cost: $0.1630
- Unique response models: ['gpt-5.4-2026-03-05']
- Unique system fingerprints: ['null'] (genuinely absent from API responses)
- finish_reason counts: {'stop': 164}
- service_tier counts: {'default': 164}
- Request-ID uniqueness: 164 unique request IDs / 164 responses

## Integrity counters (all zero)

- Candidate-hash mismatches: 0
- Execution-signal mismatches: 0
- Execution-treatment mismatches: 0
- Prompt mismatches: 0
- Raw-response-hash mismatches: 0
- Parser/decision mismatches: 0
- Duplicate/missing/unexpected IDs: 0

## Final validator status

`python3 arms/execution/validate_execution.py` → exit 0:
**VALID ARM 3 EXECUTION POSITIVE CONTROL: YES**
(164 signals, 164 responses, 164 decisions; Execution PASS 155 / FAIL 9;
Execution/T0 mismatches 0; YES+NO=164; invalid verdicts 0.)

## Definitive hashes

- Arm 3 response ledger (`data/arm3_execution_responses.jsonl`):
  `18063d272afbbe51257f7d4bf5c944ebe2773821585614f59bc13dc3ebb1bf6b`
- Arm 3 decision ledger (`data/arm3_execution.jsonl`):
  `ada055c1779975ce11b74951d86aa1fe48f4c17bbe083e4277d99e432ed8c282`
- Execution-signal ledger (`data/arm3_execution_signals.jsonl`):
  `97e82cc88faabc2ddf43da1f72e25cd5e18a38ed5127c6a6b9e7b44e6c654369` (unchanged)
- Certification artifact
  (`data/arm3_execution_signal_certification.json`):
  `286d647be5062bb1d809262d67c1abb3aa562da7ae26f65e7468832b33e4e61f` (unchanged)

## Six prior definitive hashes (recomputed; UNCHANGED)

- Stage 0 frozen: `a08167e369c3b3ec353f7f0bd84f843e7add0cb80f2ad4eb498fca6dc6c53aa3`
- Stage 0 baseline: `bf52d7622141788c51b0c5a5870b225c28424f5f5cf834e0f5086ba13a827a04`
- Arm 1 responses: `e27544967bf8f285f2fe9d96de3b81b236a26741edaeb607ab304f5312490bc0`
- Arm 1 decisions: `5d7ae85e6e44c7b6f0bcf90e3ba729ad09970071d78a326fbfb5b637056d4649`
- Arm 2 responses: `820c25fc3c7e6defcbb78e8a5124cb67c3aeca2f55076b3ae8fa2dda0d22619b`
- Arm 2 decisions: `baa69db31d4a64e0dcf97dfda1c95c4f37be37577809b32dc92c275d96697d70`

## Confirmations

- No candidate was revised, corrected, repaired, retested after
  modification, or replaced. The object evaluated is the exact frozen
  Stage 0 candidate, unchanged.
- Phase B consumed ONLY the already-certified execution signals
  (`data/arm3_execution_signals.jsonl` + hash-bound certification); it
  never re-executed candidates, never read `baseline.jsonl`, and never
  read Arm 1 or Arm 2 evaluator outputs.
- No cross-arm statistical analysis was performed.

Local zip SHA-256: recorded externally after archive finalization
Indigo zip SHA-256: recorded externally after archive finalization
