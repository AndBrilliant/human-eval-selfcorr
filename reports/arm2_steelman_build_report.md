# Arm 2 Steelman Build Report

Build date: 2026-09-09 (UTC). Arm 2 IMPLEMENTED AND LOCALLY TESTED ONLY.
Arm 2 has NOT been run. No model/API calls were made.

## Files created/changed

Created (all new; no prior artifact modified):
- `arms/steelman/__init__.py`
- `arms/steelman/config.py` — protocol constants, approved corpus hash, exact prompt template
- `arms/steelman/run_steelman.py` — evaluator runner (freeze-before-parse, resumable, blind)
- `arms/steelman/validate_steelman.py` — certifier (no API calls)
- `arms/steelman/README.md` — protocol document
- `tests/test_arm2_steelman.py` — 24 local regression tests
- `reports/arm2_steelman_build_report.md` — this file

Changed: none. Stage 0 code/data, Arm 1 code/data/report untouched.

## Exact Arm 2 prompt (template; instantiated per task with the verbatim
## HumanEval prompt and exact frozen candidate_code; stored per record)

```
You are critically evaluating a Python solution that you produced earlier for a programming problem.

Your task is to determine whether the candidate is fully correct for all inputs allowed by the specification.

Perform a structured adversarial evaluation before giving your verdict.

First, make the strongest specific case that the candidate is WRONG. Look actively for edge cases, specification mismatches, incorrect assumptions, arithmetic or logical errors, and cases where apparently plausible code would fail.

Second, make the strongest specific case that the candidate is CORRECT. Check whether the apparent concerns actually apply and whether the implementation satisfies the complete specification.

Finally, decide which case survives scrutiny.

You are evaluating the existing candidate only.

Do not modify, repair, rewrite, or improve the candidate.
Do not provide corrected code or an alternative implementation.
Do not execute the code.
Do not assume access to unit tests, reference answers, execution results, ground-truth labels, or any information not shown below.

Programming problem:
---BEGIN PROBLEM---
{task_prompt}
---END PROBLEM---

Your previously produced solution:
---BEGIN CANDIDATE---
{candidate_code}
---END CANDIDATE---

Respond using exactly this structure:

CASE_AGAINST:
<your strongest case that the candidate is wrong>

CASE_FOR:
<your strongest case that the candidate is correct>

FINAL_VERDICT: YES

Replace YES with NO if the candidate is not fully correct.

The final line must be exactly FINAL_VERDICT: YES or FINAL_VERDICT: NO.
```

## Exact request parameters

```
model = "gpt-5.4-2026-03-05"
temperature = 0
reasoning_effort = "none"
max_completion_tokens = 2048
messages = [single user message]   (no system message; fresh context per task)
SDK max_retries = 0                (all retries explicit and logged)
timeout = 300.0
```

## Parser grammar

Valid response, in order: exactly one `CASE_AGAINST:` header line;
non-empty content; exactly one `CASE_FOR:` header line; non-empty
content; exactly one `FINAL_VERDICT: YES|NO` line as the FINAL
non-whitespace line (optional surrounding whitespace on that line only).
Rejected (parse_status="invalid_structure", no binary A, arm halts,
never resampled): missing/duplicated headers, reversed order, empty
sections, `FINAL_VERDICT: YES.`, bare `YES`, `VERDICT: YES`, JSON,
content after the verdict, multiple verdict lines, empty response.
Section texts are stored exactly as parsed (outer whitespace stripped;
never rewritten or summarized).

## Freeze transaction

```
approved frozen Stage 0 candidate
    -> one evaluator API request
    -> raw response + provenance
    -> append + fsync data/arm2_steelman_responses.jsonl   (FREEZE POINT)
    -> ONLY THEN parse structured response
    -> append + fsync data/arm2_steelman.jsonl
```

Once a task_id exists in the response ledger, no code path queries the
evaluator for it again — under parse crash, machine crash, missing
decision ledger, failed validation, or resume. Structural API metadata
malformation = fatal protocol error (1 call, no freeze). Transport
failures pre-response retry identically (logged, transport_attempts
recorded). No content-conditioned resampling of any kind.

## Blindness confirmations

- `data/baseline.jsonl` is NOT read: confirmed. Arm 2 source contains no
  operational reference to it (grep-verified); runner succeeds in
  environments where it is absent.
- Arm 1 result ledgers are NOT read: confirmed. Arm 2 source contains no
  reference to `arm1_selfcheck` or `A_selfcheck` (grep-verified); runner
  succeeds with Arm 1 ledgers absent.
- Runner input is ONLY `data/frozen_candidates.jsonl`, pre-verified
  against the approved Stage 0 SHA-256
  (a08167e369c3b3ec353f7f0bd84f843e7add0cb80f2ad4eb498fca6dc6c53aa3)
  BEFORE any client construction or API call.
- Evaluator prompts contain no T0, tests, execution results, or Arm 1
  verdicts (regression-tested).

## Local test results

- Arm 2 suite: 24/24 pass (`python3 tests/test_arm2_steelman.py`)
- Arm 1 suite: 19/19 pass (regression, unchanged)
- Stage 0 suite: 29/29 pass (regression, unchanged)
- Compilation checks pass.

Coverage: source-hash abort before client construction, candidate-hash
abort, 163-cannot-validate, T0/Arm-1 blindness, exact request parameters,
parser valid cases (YES/NO/whitespace variants, exact section extraction),
parser invalid cases (12 grammars), freeze-before-parse crash+resume
(call count stays 1, identical hash reused), malformed-treatment
non-resampling (1 call, halt rc=3, resume still 1 call), structural API
errors (1 call, fatal), transport retry (1 frozen response,
transport_attempts=2), validator accept/reject including tampered section
text.

## API call confirmation

NO model/API calls were made. Arm 2 was NOT run. No smoke subset, no
--limit 1, no evaluator request of any kind.

## Immutability verification (post-build)

- Stage 0 frozen: a08167e369c3b3ec353f7f0bd84f843e7add0cb80f2ad4eb498fca6dc6c53aa3 (unchanged)
- Stage 0 baseline: bf52d7622141788c51b0c5a5870b225c28424f5f5cf834e0f5086ba13a827a04 (unchanged)
- Arm 1 responses: e27544967bf8f285f2fe9d96de3b81b236a26741edaeb607ab304f5312490bc0 (unchanged)
- Arm 1 decisions: 5d7ae85e6e44c7b6f0bcf90e3ba729ad09970071d78a326fbfb5b637056d4649 (unchanged)
- Arm 1 run report: reports/arm1_selfcheck_run_report.md (unchanged)

## Unresolved blockers

None.
