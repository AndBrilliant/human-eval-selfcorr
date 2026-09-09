# Arm 2 Steelman Run Report

Run date: 2026-09-09 (UTC). Single uninterrupted run; one process, no resumes.

Local test result: 24/24 pass (tests/test_arm2_steelman.py, run before any evaluator API call)
Arm 2 process exit code: 0
Number of resumes: 0

Frozen Arm 2 response count: 164
Parsed decision count: 164
YES count: 152
NO count: 12
Invalid structured-response count: 0

Validator result: VALID ARM 2 STEELMAN: YES (exit 0; all mismatch counters 0)

Unique response models: ['gpt-5.4-2026-03-05']
Unique system fingerprints: ['null'] (genuinely absent from API responses)
Finish reason counts: {'stop': 164}

Transport retries: 0
Failure-record count: 0 (data/arm2_steelman_failures.jsonl does not exist)
Recorded Arm 2 API cost: $1.5539

SHA-256 arm2_steelman_responses.jsonl: 820c25fc3c7e6defcbb78e8a5124cb67c3aeca2f55076b3ae8fa2dda0d22619b
SHA-256 arm2_steelman.jsonl: baa69db31d4a64e0dcf97dfda1c95c4f37be37577809b32dc92c275d96697d70

Stage 0 frozen hash: a08167e369c3b3ec353f7f0bd84f843e7add0cb80f2ad4eb498fca6dc6c53aa3
Stage 0 baseline hash: bf52d7622141788c51b0c5a5870b225c28424f5f5cf834e0f5086ba13a827a04
Arm 1 responses hash: e27544967bf8f285f2fe9d96de3b81b236a26741edaeb607ab304f5312490bc0
Arm 1 decisions hash: 5d7ae85e6e44c7b6f0bcf90e3ba729ad09970071d78a326fbfb5b637056d4649

Previous artifact hashes unchanged: YES

Local zip SHA-256: recorded externally after archive finalization
Indigo zip SHA-256: recorded externally after archive finalization
Hash verification: performed after report finalization

## Operational events

None. No interruptions, no transport retries, no fatal errors, no malformed
structured responses, no cost-cap events. All 164 evaluator responses
parsed as valid structured treatments (CASE_AGAINST / CASE_FOR /
FINAL_VERDICT) on first parse; every response finished with
finish_reason="stop".

## Protocol notes

- Source corpus verified before any API call: data/frozen_candidates.jsonl
  hashed to the approved Stage 0 frozen-ledger SHA-256 (a08167e3...).
- The runner never read data/baseline.jsonl and never read any Arm 1
  result ledger. No ground-truth labels and no Arm 1 verdicts were joined.
  T0 remains the immutable Stage 0 correctness label.
- Arm 2 is evaluator-only: no correction or revision phase; the candidate
  evaluated was never changed.
- No evaluator statistics (acceptance rates, kappa, likelihood ratios,
  evidence bits, accuracy, cross-arm comparisons, significance) were
  computed in this report or anywhere else. Raw operational quantities
  only.
