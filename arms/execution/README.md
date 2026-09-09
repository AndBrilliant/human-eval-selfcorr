# Arm 3: Execution-Grounded Positive Control — Protocol

**Arm 3 is an execution-grounded positive control.**

**The external signal is derived from executing the same immutable
candidate against the HumanEval reference tests that define the benchmark
correctness label. Accordingly, Arm 3 is not interpreted as an
independent validation benchmark; it anchors the behavior of a selector
given direct external ground-truth information.**

**Arm 3 contains no correction or revision phase.**

**All three evaluator arms judge the identical frozen Stage 0 candidate.**

## Definition

For each of the 164 immutable Stage 0 frozen candidates, Arm 3 executes
the exact frozen candidate against the HumanEval reference tests (fresh
subprocess, 15 s timeout, sanitized environment — the Stage 0 mechanism)
and reduces the result to a binary treatment text that reveals only
PASS vs FAIL:

- pass → `EXECUTION RESULT: PASS\nThe candidate passed all reference tests.`
- candidate-caused failure → `EXECUTION RESULT: FAIL\nThe candidate failed at least one reference test.`
- timeout (15 s) → `EXECUTION RESULT: FAIL\nThe candidate did not complete within the execution timeout.`

The evaluator (same snapshot `gpt-5.4-2026-03-05`, temperature 0,
`reasoning_effort="none"`, `max_completion_tokens=16`, one fresh user
message) then receives the problem, the exact candidate, and the
treatment text, and answers YES or NO. It never sees test source,
assertion text, stack traces, failing inputs, exception details, repair
hints, or the T0 label.

This arm measures `A_execution` only. T0 never changes. The candidate is
never revised, retested after revision, or replaced.

## Two-phase architecture

**Phase A** (`run_execution_signals.py`): executes all 164 frozen
candidates locally and freezes `arm3_execution_signals.jsonl`. No API
calls, no ground-truth reads.

**Integrity gate** (`validate_execution_signals.py`): after all 164
signals are frozen and BEFORE any evaluator call, certify `execution PASS
iff baseline_correct == true` for all 164 tasks. On success it atomically
writes `data/arm3_execution_signal_certification.json` recording the
SHA-256 of the exact certified signal-ledger byte sequence; on any
failure it removes that artifact. `baseline.jsonl` may be read ONLY
inside the TWO dedicated certifier modules
(`validate_execution_signals.py` and `validate_execution.py`); the
evaluator runtime never reads it.

**Phase B** (`run_execution_evaluator.py`): **technically incapable of
proceeding** unless the CURRENT signal ledger has a successful matching
certification. `verify_phase_b_preflight()` enforces, before any client
construction or model request: approved Stage 0 hash, exactly 164 frozen
tasks, exact signal-ID set equality, full signal protocol/hash/mapping
consistency, certification artifact presence, exact certification
metadata (status "valid", 0 mismatches, 164/164, PASS 155 / FAIL 9),
approved source/baseline hashes in the certification, and
`execution_signals_sha256` equal to the CURRENT signal ledger's SHA-256.
The gate runs inside both `main()` (before the client factory) and
`run_evaluator()` (before any model request) — there is no programmatic
bypass. The runtime reads only the certification artifact, never
`baseline.jsonl`, Arm 1, or Arm 2 ledgers; the treatment comes only from
the frozen signal.

**Final certifier** (`validate_execution.py`): full ledger certification
including the Execution/T0 integrity comparison. No API calls, no
cross-arm statistics.

## Transactions

Phase A: candidate hash re-verified → execute → freeze signal (fsync).
Frozen signals are never re-executed on resume. Candidate timeout =
genuine FAIL outcome (never retried). Local test-runner infrastructure
failures retry the identical test (max 3 attempts); exhaustion leaves the
task unsignaled for resume.

Phase B: evaluator request → raw response + provenance → append + fsync
`arm3_execution_responses.jsonl` → ONLY THEN parse YES/NO → append +
fsync `arm3_execution.jsonl`. A frozen response is never resampled. A
malformed verdict (`parse_status="invalid_verdict"`) halts the arm loudly
with no resampling. Structural API malformation is fatal: no freeze, no
second model call.

## Decision rule

Same strict binary parser as Arm 1: strip surrounding whitespace,
uppercase, accept exactly `YES` (acceptance 1) or `NO` (acceptance 0).
Everything else is a malformed verdict.

## Files

| File | Purpose |
|------|---------|
| `arms/execution/config.py` | Protocol constants, treatment texts, prompt template, approved hashes. |
| `arms/execution/run_execution_signals.py` | Phase A (local execution only). |
| `arms/execution/validate_execution_signals.py` | Integrity gate (sole reader of `baseline.jsonl`). |
| `arms/execution/run_execution_evaluator.py` | Phase B (evaluator calls). |
| `arms/execution/validate_execution.py` | Final certifier. |
| `tests/test_arm3_execution.py` | Local regression suite (no API calls). |

| Ledger | Contents |
|--------|----------|
| `data/arm3_execution_signals.jsonl` | Frozen execution signals + provenance. |
| `data/arm3_execution_responses.jsonl` | Frozen raw evaluator responses + provenance. |
| `data/arm3_execution.jsonl` | Parsed decisions. No ground truth. |
| `data/arm3_execution_run_log.jsonl` | Event log. |
| `data/arm3_execution_failures.jsonl` | Explicit infrastructure-failure records. |

## Cost

Operational only: $2.50/$15.00 per 1M input/output tokens, cumulative
across resumes, default cap $10 (`--cap`). Local execution has no API
cost. Cost never modifies N.

## Usage (after approval, IN ORDER)

```bash
cd /home/Drew/benchmark_v2
python3 arms/execution/run_execution_signals.py        # Phase A (local)
python3 arms/execution/validate_execution_signals.py   # integrity gate -> certification artifact
python3 arms/execution/run_execution_evaluator.py      # Phase B (API; gated on certification)
python3 arms/execution/validate_execution.py           # certify
```
