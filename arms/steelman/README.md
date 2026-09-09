# Arm 2: Structured Steelman / Self-Debate — Protocol

**Arm 2 evaluates the same 164 immutable Stage 0 candidates as Arm 1, but
requires a structured adversarial self-evaluation consisting of a
strongest case against correctness, a strongest case for correctness, and
a final binary verdict.**

**Arm 2 has no correction or revision phase.**

**The candidate evaluated is never changed. T0 remains the immutable
Stage 0 label.**

**Arm 2 receives no Arm 1 judgment, no ground truth, no reference tests,
and no execution results.**

## Definition

For each of the 164 frozen Stage 0 candidates, in a fresh API context
(single user message, no history from generation, Arm 1, or earlier Arm 2
tasks), the same model snapshot (`gpt-5.4-2026-03-05`, temperature 0,
`reasoning_effort="none"`, `max_completion_tokens=2048`) is shown the
HumanEval problem and the exact frozen candidate code, explicitly
identified as its own previously produced solution, and must produce:

```
CASE_AGAINST:
<strongest case that the candidate is wrong>

CASE_FOR:
<strongest case that the candidate is correct>

FINAL_VERDICT: YES|NO
```

This arm measures `A_steelman` only. It does not modify T0. No corrected
code is requested, persisted, or applied — this arm is evaluator-only,
NOT evaluator-plus-correction.

## Structured-output validity is part of the treatment

A valid returned response must contain exactly one `CASE_AGAINST:` header,
exactly one `CASE_FOR:` header (in that order), non-empty content in both
sections, and exactly one final line `FINAL_VERDICT: YES` or
`FINAL_VERDICT: NO` as the final non-whitespace line. Rejected: missing or
duplicated headers, reversed order, empty sections, `FINAL_VERDICT: YES.`,
bare `YES`, `VERDICT: YES`, JSON, content after the verdict, multiple
verdict lines, empty response.

An invalid structured response is experimental data: the raw response
stays frozen, `parse_status="invalid_structure"` is recorded, no binary
A is assigned, the arm halts loudly, and the task is NEVER resampled.
The parser is never loosened after observing a real response.

## Transaction

```
approved frozen Stage 0 candidate
    -> one evaluator API request
    -> raw response + provenance
    -> append + fsync arm2_steelman_responses.jsonl   (FREEZE POINT)
    -> ONLY THEN parse structured response
    -> append + fsync arm2_steelman.jsonl
```

Once a `task_id` exists in the response ledger, no code path queries the
evaluator for it again — under parse crash, machine crash, missing
decision ledger, failed validation, or resume.

## Retry semantics

Genuine pre-response transport failures may reissue the identical logical
request (logged, `transport_attempts` recorded). Once a response is
returned: no retry for any reason (verdict, reasoning, malformed
structure, finish_reason, length). Structurally malformed API metadata
(multiple choices, empty `response_model`, non-str content) is a fatal
protocol error: no freeze, no second model call, run halts.

## Blindness

The runner reads ONLY `data/frozen_candidates.jsonl` (verified against
the approved Stage 0 SHA-256 `a08167e3…` before any API call; mismatch =
abort). It never reads `data/baseline.jsonl` and never reads any Arm 1
result ledger.

## Files

| File | Purpose |
|------|---------|
| `arms/steelman/config.py` | Arm 2 protocol constants (model, prompt template, paths, approved corpus hash). |
| `arms/steelman/run_steelman.py` | Evaluator runner (freeze-before-parse, resumable, blind). |
| `arms/steelman/validate_steelman.py` | Certifier. No API calls; reads only the frozen Stage 0 ledger. |
| `tests/test_arm2_steelman.py` | Local regression suite (no API calls). |

| Ledger | Contents |
|--------|----------|
| `data/arm2_steelman_responses.jsonl` | Immutable frozen raw evaluator responses + provenance. |
| `data/arm2_steelman.jsonl` | Parsed decisions (case_against, case_for, verdict, acceptance, parse_status). No ground truth. |
| `data/arm2_steelman_run_log.jsonl` | Event log. |
| `data/arm2_steelman_failures.jsonl` | Explicit transport-failure records. |

## Cost

Operational only: $2.50/$15.00 per 1M input/output tokens, cumulative
across resumes, default cap $10 (`--cap`). Cost never modifies N.

## Usage (after approval)

```bash
cd /home/Drew/benchmark_v2
python3 arms/steelman/run_steelman.py        # resumable
python3 arms/steelman/validate_steelman.py   # certify; exit 0 iff VALID
```
