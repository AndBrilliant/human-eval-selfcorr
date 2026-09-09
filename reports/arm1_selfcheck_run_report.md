# Arm 1 Self-Check Run Report

Run date: 2026-09-09 (UTC). Single uninterrupted run; one process, no resumes.

Local test result: 19/19 pass (tests/test_arm1_selfcheck.py, run before any evaluator API call)
Arm 1 process exit code: 0
Number of resumes: 0
Frozen evaluator response count: 164
Parsed decision count: 164
YES count: 152
NO count: 12
Invalid verdict count: 0
Validator result: VALID ARM 1 SELFCHECK: YES (exit 0; all mismatch counters 0)
Unique response models: ['gpt-5.4-2026-03-05']
Unique system fingerprints: ['null'] (genuinely absent from API responses)
Transport retries: 0
Failure-record count: 0 (data/arm1_selfcheck_failures.jsonl does not exist)
Recorded Arm 1 API cost: $0.1482

SHA-256 arm1_selfcheck_responses.jsonl: e27544967bf8f285f2fe9d96de3b81b236a26741edaeb607ab304f5312490bc0
SHA-256 arm1_selfcheck.jsonl: 5d7ae85e6e44c7b6f0bcf90e3ba729ad09970071d78a326fbfb5b637056d4649

Stage 0 frozen hash: a08167e369c3b3ec353f7f0bd84f843e7add0cb80f2ad4eb498fca6dc6c53aa3
Stage 0 baseline hash: bf52d7622141788c51b0c5a5870b225c28424f5f5cf834e0f5086ba13a827a04
Stage 0 hashes unchanged: YES

Local zip SHA-256: recorded externally after archive finalization
Indigo zip SHA-256: recorded externally after archive finalization
Hash verification: performed after report finalization

Indigo archive contains all Stage 0 + Arm 1 ledgers: YES (verified remotely after upload)

## Operational events

None. No interruptions, no transport retries, no fatal errors, no malformed
verdicts, no invalid verdicts, no cost-cap events. All 164 evaluator
responses parsed as valid binary verdicts on first parse.

## Protocol notes

- Source corpus verified before any API call: data/frozen_candidates.jsonl
  hashed to the approved Stage 0 frozen-ledger SHA-256 (a08167e3...).
- The runner never read data/baseline.jsonl; no ground-truth labels were
  joined. T0 remains the immutable Stage 0 correctness label.
- No evaluator statistics (acceptance rates, kappa, likelihood ratios,
  evidence bits, significance) were computed in this report or anywhere
  else. Raw operational quantities only.
