# Arm 3 Execution-Grounded Positive Control — Build Report

Build date: 2026-09-09 (UTC). Arm 3 IMPLEMENTED AND LOCALLY TESTED ONLY.
Arm 3 has NOT been run. No model/API calls were made. The actual Arm 3
execution corpus (all 164 signal generations) has NOT been produced.

## Files created/changed

Created (all new; no prior artifact modified):
- `arms/execution/__init__.py`
- `arms/execution/config.py` — protocol constants, exact treatment texts, exact prompt template, approved hashes, certification-artifact path
- `arms/execution/run_execution_signals.py` — Phase A: local execution + signal freezing (no API)
- `arms/execution/validate_execution_signals.py` — integrity gate; writes the hash-bound certification artifact (one of two sanctioned `baseline.jsonl` readers)
- `arms/execution/run_execution_evaluator.py` — Phase B: evaluator calls (gated; strong cross-bound ledger validators)
- `arms/execution/validate_execution.py` — final certifier (second sanctioned `baseline.jsonl` reader; reuses canonical strong validators)
- `arms/execution/README.md` — protocol document (contains all four required statements verbatim)
- `tests/test_arm3_execution.py` — 54 local regression tests
- `reports/arm3_execution_build_report.md` — this file

Note: a `benchmark_v2/.gitignore` appeared in one intermediate archive as
part of unrelated repo initialization; it was never part of the approved
experimental tree and has been REMOVED. The `benchmark_v2/.git` directory
exists locally (the project is now the recreated GitHub repo
AndBrilliant/human-eval-selfcorr) and is EXCLUDED from the zip archive.

Changed: none. Stage 0, Arm 1, Arm 2 code/data/reports untouched.

## Exact execution treatment

The exact frozen candidate (hash re-verified immediately before
execution) runs against the HumanEval reference tests in a fresh
subprocess (15 s timeout, sanitized environment — the Stage 0 mechanism).
The result is reduced to exactly one of three treatment texts revealing
only PASS vs FAIL:

```
EXECUTION RESULT: PASS
The candidate passed all reference tests.
```

```
EXECUTION RESULT: FAIL
The candidate failed at least one reference test.
```

```
EXECUTION RESULT: FAIL
The candidate did not complete within the execution timeout.
```

Never shown: reference-test source, assertion text, stack traces,
expected values, failing inputs, exception details, repair hints, or the
T0 label. Candidate timeout is a genuine FAIL outcome, never retried.
Local test-runner infrastructure failures retry the identical test
(max 3 attempts); exhaustion leaves the task unsignaled for resume.

## Exact evaluator prompt (template; instantiated per task with the
## verbatim HumanEval prompt, exact frozen candidate_code, exact
## treatment text; stored per record)

```
You are checking a Python solution that you produced earlier for a programming problem.

Determine whether the candidate solution is fully correct for all inputs allowed by the problem specification.

You have been given the result of executing this exact candidate against the reference tests.

Do not modify, repair, rewrite, or improve the solution.
Do not propose an alternative solution.
Do not infer or invent any execution information beyond the result shown.
Evaluate the existing candidate only.

Programming problem:
---BEGIN PROBLEM---
{task_prompt}
---END PROBLEM---

Your previously produced solution:
---BEGIN CANDIDATE---
{candidate_code}
---END CANDIDATE---

Execution grounding:
---BEGIN EXECUTION RESULT---
{execution_treatment}
---END EXECUTION RESULT---

Is the candidate solution fully correct?

Answer with exactly YES or NO.
Do not output anything else.
```

## Exact request parameters

```
model = "gpt-5.4-2026-03-05"
temperature = 0
reasoning_effort = "none"
max_completion_tokens = 16
messages = [single user message]   (fresh context; no system message)
SDK max_retries = 0                (all retries explicit and logged)
timeout = 300.0
```

## Two-phase architecture (PATCHED after independent build review)

**Review issues found and fixed (round 1):**

- **ISSUE 1 (BLOCKER):** Phase B verified only that 164 execution signals
  existed; the T0 integrity gate was documentary, not executable.
  **Fix:** `validate_execution_signals.py` now writes a hash-bound
  certification artifact
  (`data/arm3_execution_signal_certification.json`, atomic
  temp+fsync+rename) ONLY after successful certification of the complete
  164-task signal ledger, recording the SHA-256 of the exact certified
  ledger bytes. Any failed validation REMOVES the artifact.
  `verify_phase_b_preflight()` in `run_execution_evaluator.py` enforces
  the gate before any client construction or model request (approved
  Stage 0 hash; exactly 164 frozen tasks; exact signal-ID set equality;
  full signal consistency; artifact presence; exact certification
  metadata — status "valid", 0 mismatches, 164/164, PASS 155 / FAIL 9;
  approved source/baseline hashes; `execution_signals_sha256` equal to
  the CURRENT ledger SHA; non-empty `certified_at`). The gate runs in
  BOTH `main()` (before the client factory) and `run_evaluator()`
  (before any model request): Phase B is technically incapable of
  proceeding without a valid certification matching the current ledger,
  and there is no programmatic bypass to
  `client.chat.completions.create(...)`. The runtime reads only the
  certification artifact — it still never reads `baseline.jsonl`.
- **ISSUE 2:** signal-ledger completeness was length-based and unexpected
  task IDs were accepted. **Fix:** `validate_signal_ledger()` rejects
  unexpected task IDs, duplicates, `task_index` disagreement, and
  candidate-SHA disagreement; completeness is exact set equality. The
  ledger is self-validating for status/treatment consistency via the
  allowed mapping table.
- **Wording:** `baseline.jsonl` may be read ONLY inside the TWO
  dedicated certifier modules (`validate_execution_signals.py` and
  `validate_execution.py`). The evaluator runtime never reads it.

**Review issues found and fixed (round 2):**

- **ISSUE 1 (BLOCKER):** frozen evaluator responses were insufficiently
  bound to the certified experimental object — a stale/corrupt response
  for a legitimate task_id (altered candidate_sha256, task_index, or
  evaluator_prompt with internally valid hashes/protocol constants) was
  accepted and reused. **Fix:** `validate_response_ledger(path,
  frozen_by_id, signals)` now cross-binds every frozen response to the
  approved task, task_index, frozen candidate, certified signal,
  execution_status, exact treatment, exact current evaluator prompt,
  raw-response hash, protocol constants, AND the pinned response_model.
  Any violation: ABORT before any new API request; never resampled.
- **ISSUE 1B:** the decision ledger was weakly validated. **Fix:**
  `validate_decision_ledger(path, frozen_by_id, responses)` enforces
  expected IDs, matching frozen response, task_index/candidate/
  execution_status/raw-hash equality, exact protocol constants, and
  requires the strict parser rerun on the frozen raw response to
  reproduce the stored verdict/acceptance/parse_status exactly. A
  correctly stored invalid_verdict is legitimate frozen data (parser
  agrees) and still halts the arm with rc 3 and zero new API calls; a
  decision without a matching response is corruption and aborts.
- **ISSUE 2 (BLOCKER):** production `run_evaluator()` accepted a
  caller-supplied task list and could declare a 3-task subset "Arm 3
  complete". **Fix:** the `frozen_records` parameter is REMOVED;
  `run_evaluator(paths, client, cap_usd, log)` always obtains the full
  approved 164-task corpus from `verify_phase_b_preflight()` and
  "complete" can only ever mean all 164 approved tasks. Tests exercise
  partial work by prepopulating valid ledgers and leaving 1-3 tasks
  pending — no production subset bypass exists.
- **Startup ordering** is now: approved frozen ledger → complete
  certified signal ledger → matching hash-bound certification → strong
  response validation → strong decision validation → ONLY THEN new
  evaluator requests. `main()` performs all of it before client
  construction.
- **Final certifier** (`validate_execution.py`) now REUSES the canonical
  strong validators (no divergent second definition of a valid record):
  wrong task_index, unknown execution_test_status,
  timeout-with-generic-treatment, pass-with-fail-status, candidate
  mismatch, signal/response mismatch, response/prompt mismatch, and
  response/decision mismatch are all rejected by the same code that
  gates the runtime.
- **Wording:** the certification artifact is "hash-bound"
  (SHA-256-bound); no cryptographic signing is implied.
  `.gitignore` (present in one intermediate archive, never part of the
  approved tree) has been REMOVED.

Architecture (post-patch):

- **Phase A** (`run_execution_signals.py`): verify approved Stage 0 frozen
  hash (abort before any work on mismatch) → re-verify each candidate
  hash → execute locally → freeze signal record (fsync). NO API calls, NO
  ground-truth reads. Frozen signals are never re-executed on resume.
- **Integrity gate** (`validate_execution_signals.py`): after all 164
  signals are frozen and BEFORE any evaluator call, certifies
  `execution PASS iff baseline_correct == true` for all 164 (expected
  PASS 155 / FAIL 9 by construction) and writes the certification
  artifact; any disagreement or incompleteness: STOP, no artifact,
  Phase B cannot run. One of the two sanctioned `baseline.jsonl` readers.
- **Phase B** (`run_execution_evaluator.py`): gated by
  `verify_phase_b_preflight()` — cannot proceed unless the CURRENT
  signal ledger has a successful matching certification. Freeze-before-
  parse, resume, halt, and fatal-structural semantics identical to
  Arms 1-2.
- **Final certifier** (`validate_execution.py`): full certification
  including the Execution/T0 integrity comparison; no API calls; no
  cross-arm statistics. The second sanctioned `baseline.jsonl` reader.

## No correction/revision

There is NO code path that asks the model to fix the candidate, accepts
revised code, retests revised code, mutates candidate_code, changes
candidate_sha256, replaces T0, or changes the object being evaluated.
Grep-verified: the only occurrences of "repair"/"revision"/"corrected"
in `arms/execution/` are the prompt's prohibitions and documentation
stating their absence. If the evaluator says the candidate is wrong, that
is the end of the interaction.

## Execution/T0 integrity-check design

The treatment is built ONLY from the freshly frozen execution signal —
never from T0. T0 lookup exists solely in the two certifier modules, runs
after signal freezing, and gates Phase B. The evaluator request path has
no access to `baseline_correct`.

## Local test results (post-patch, round 2)

- Arm 3 suite: 54/54 pass (`python3 tests/test_arm3_execution.py`),
  including all 17 round-2 required tests: response binding (wrong
  candidate SHA / task_index / execution_status / treatment /
  evaluator_prompt / unexpected ID / wrong response_model → abort, zero
  new API calls); decision binding (no matching response, wrong
  candidate SHA, wrong raw SHA, parser mismatch → abort; correctly
  stored invalid_verdict → legitimate, rc 3 halt, zero new calls);
  production completeness (no subset parameter exists; full 164
  certified state completes with zero calls; 163-prepopulated + 1
  pending completes with exactly one call); final-certifier strength
  (timeout signal with generic treatment rejected; wrong signal
  task_index rejected).
- Regression: Stage 0 29/29, Arm 1 19/19, Arm 2 24/24 — all unchanged.
- Compilation checks pass.

Earlier coverage (pre-patch, all still passing): wrong source hash
abort; altered candidate hash abort; exact frozen candidate executed
(spy-verified); exact PASS/FAIL/timeout treatments; exception → FAIL
without detail leakage; infra retry policy; signal freeze-before-
evaluator and resume-without-re-execution; exact request parameters and
prompt hygiene; binary parser equivalence with Arm 1; invalid-verdict
freeze + halt + no resampling; structural API failure fatal after one
call; freeze-before-parse crash/resume; blindness to Arm 1/Arm 2 ledgers
and baseline.jsonl in runtime modules; final validator accept/reject.

## API call confirmation

NO model/API calls were made. Arm 3 was NOT run. No execution-signal
generation on the actual corpus. No smoke subset.

## Immutability verification (post-build)

- Stage 0 frozen: a08167e369c3b3ec353f7f0bd84f843e7add0cb80f2ad4eb498fca6dc6c53aa3 (unchanged)
- Stage 0 baseline: bf52d7622141788c51b0c5a5870b225c28424f5f5cf834e0f5086ba13a827a04 (unchanged)
- Arm 1 responses: e27544967bf8f285f2fe9d96de3b81b236a26741edaeb607ab304f5312490bc0 (unchanged)
- Arm 1 decisions: 5d7ae85e6e44c7b6f0bcf90e3ba729ad09970071d78a326fbfb5b637056d4649 (unchanged)
- Arm 2 responses: 820c25fc3c7e6defcbb78e8a5124cb67c3aeca2f55076b3ae8fa2dda0d22619b (unchanged)
- Arm 2 decisions: baa69db31d4a64e0dcf97dfda1c95c4f37be37577809b32dc92c275d96697d70 (unchanged)
- Arm 1/Arm 2 reports: untouched.

## Unresolved blockers

None.
