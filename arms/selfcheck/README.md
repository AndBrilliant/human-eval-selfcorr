# Arm 1: Pure Self-Check — Protocol

**Arm 1 evaluates the identical 164 immutable Stage 0 candidates. The
evaluator receives only the HumanEval problem and frozen candidate code in
a fresh context. It receives no execution result, reference test,
ground-truth label, or correction opportunity.**

**Arm 1 never changes the candidate. T0 remains the immutable Stage 0
correctness label.**

**The Arm 1 runner is deliberately unable to read `baseline.jsonl`. Ground
truth is joined only after evaluator data are frozen.**

## Definition

For each of the 164 frozen Stage 0 candidates, in a completely fresh API
context (single user message, no conversation history from generation or
between tasks), the same model snapshot (`gpt-5.4-2026-03-05`,
temperature 0, `reasoning_effort="none"`, `max_completion_tokens=16`) is
shown the HumanEval problem and the exact frozen candidate code,
explicitly identified as its own previously produced solution, and asked
only whether the candidate is fully correct. One binary YES/NO judgment.
No revision, no execution, no tests, no test outcomes, no corrected
solution.

This arm measures `A_selfcheck` only. It does not modify T0.

## Files

| File | Purpose |
|------|---------|
| `arms/selfcheck/config.py` | Arm 1 protocol constants (model, prompt template, paths, approved corpus hash). |
| `arms/selfcheck/run_selfcheck.py` | Evaluator runner (freeze-before-parse, resumable, blind to T0). |
| `arms/selfcheck/validate_selfcheck.py` | Certifier. No API calls; never reads `baseline.jsonl`. |
| `tests/test_arm1_selfcheck.py` | Local regression suite (no API calls). |

| Ledger | Contents |
|--------|----------|
| `data/arm1_selfcheck_responses.jsonl` | Immutable frozen raw evaluator responses + provenance. |
| `data/arm1_selfcheck.jsonl` | Parsed decisions (verdict, acceptance, parse_status). No ground truth. |
| `data/arm1_selfcheck_run_log.jsonl` | Event log. |
| `data/arm1_selfcheck_failures.jsonl` | Explicit transport-failure records. |

## Transaction

```
frozen Stage 0 candidate
    -> one evaluator API request
    -> raw response + provenance
    -> append + fsync arm1_selfcheck_responses.jsonl   (FREEZE POINT)
    -> ONLY THEN parse YES/NO
    -> append + fsync arm1_selfcheck.jsonl
```

Once a `task_id` exists in the response ledger, no code path queries the
evaluator for it again — under parse crash, machine crash, missing
decision ledger, failed validation, or resume.

## Decision rule

`A_selfcheck = 1` iff parsed verdict == YES; `0` iff NO. Parser: strip
surrounding whitespace, uppercase, accept only exactly `YES` or `NO`.
Anything else (`"YES."`, `"NO because..."`, `"MAYBE"`, JSON, empty,
multi-token) is `parse_status="invalid_verdict"`: the raw response stays
frozen, no binary A is assigned, the arm halts loudly, and the task is
never resampled.

## Retry semantics

Transport failures before a usable response (connection, timeout, rate
limit, retryable HTTP) may reissue the identical logical request — logged,
content-independent, `transport_attempts` recorded. Once a response is
returned: no retry for any reason (verdict content, malformed verdict,
finish_reason, length). Structurally malformed API responses (not exactly
one choice, empty `response_model`, non-str content) are a fatal protocol
error: no freeze, no second model call, run halts.

## Preflight abort

Before any evaluator call, the runner verifies that
`data/frozen_candidates.jsonl` hashes to the approved corpus SHA-256
(`a08167e3…`). Any mismatch: abort before any API call.

## Cost

Operational only: $2.50/$15.00 per 1M input/output tokens, cumulative
across resumes, default cap $10 (`--cap`). Cost never modifies N.

## Usage (after approval)

```bash
cd ~/benchmark_v2
python3 arms/selfcheck/run_selfcheck.py        # resumable
python3 arms/selfcheck/validate_selfcheck.py   # certify; exit 0 iff VALID
```
