# benchmark_v2 — Stage 0: Immutable HumanEval Baseline

## Core experimental invariant

> **All evaluator conditions operate on the same immutable baseline candidate
> for each HumanEval task. Evaluator arms never regenerate baseline
> solutions.**

Stage 0 (this directory, current state) produces exactly one frozen GPT-5.4
candidate per HumanEval task and its objective correctness (T0). Later
evaluator arms (self-check, steelman/debate, execution-feedback — **not yet
implemented**) will consume `data/frozen_candidates.jsonl` /
`data/baseline.jsonl` read-only.

## Populations

- **Benchmark population: N = 164** (the canonical HumanEval tasks). This
  never changes. Infrastructure failures (API timeouts, rate limits,
  subprocess errors) are **not** experimental exclusions — they are retried,
  logged, and if unresolvable they leave the baseline *incomplete*, never
  *smaller*.
- **N1** = number of baseline-correct candidates (`baseline_correct: true`).
- **N0** = number of baseline-incorrect candidates (`baseline_correct: false`).
- **N0 + N1 = 164**, always. An incorrect candidate is valid experimental
  data and is never regenerated.

## Sampling statement

Each HumanEval task contributes exactly one successfully returned and
frozen candidate. Transient transport failures may cause the identical
logical request to be reissued; retries are infrastructure-driven and never
conditioned on candidate content. (We do **not** claim transport retries
are literally the same server-side model sample; `transport_attempts` is
recorded per task.)

## Pipeline and on-disk transaction boundary

```
HumanEval problem
      |
one GPT-5.4 candidate (pinned snapshot gpt-5.4-2026-03-05, temperature 0)
      |
deterministic extraction + SHA-256 hashing
      |
append + fsync  ->  data/frozen_candidates.jsonl      <-- FREEZE POINT
      |
ONLY THEN: HumanEval reference tests in a fresh subprocess
      |
scored record (derived from the frozen candidate)  ->  data/baseline.jsonl
```

Once a `task_id` exists in `frozen_candidates.jsonl`, **no code path calls
the model for that task again** — even if testing crashes, scoring is
interrupted, `baseline.jsonl` is absent, or the run is resumed later.
Testing consumes the frozen ledger; it never generates candidates. A test
infrastructure failure after freezing leaves the candidate frozen; resume
retries **testing only**.

The baseline is valid only when: frozen candidates = 164, scored
candidates = 164, identical task-ID sets, and every scored candidate hash
matches its frozen candidate hash.

## Files

| File | Purpose |
|------|---------|
| `generate_baseline.py` | Stage 0 driver: generation phase (freeze-first), then scoring phase. Resumable; exits non-zero unless both ledgers cover all 164 tasks. |
| `validate_baseline.py` | Certifies BOTH ledgers (coverage, hashes, protocol metadata, re-extraction, score consistency). No model calls, no test execution. |
| `config.py` | All constants: dataset path+hash, pinned snapshot, request parameters, retry policy, prices. |
| `common/dataset.py` | Canonical dataset loader; explicit `DatasetIntegrityError` (never `assert` — active under `python -O`). |
| `common/model_client.py` | GPT-5.4 client (key from `~/.keys/openai`, OpenAI chat completions). |
| `common/extraction.py` | Strictly non-repairing extraction (see below). |
| `common/execution.py` | Reference-test subprocess runner (see below). |
| `tests/test_stage0.py` | 23 local regression tests (no API calls). Run: `python3 tests/test_stage0.py`. |
| `data/frozen_candidates.jsonl` | Immutable generation ledger (written before any testing). |
| `data/baseline.jsonl` | Scored ledger, derived exclusively from frozen candidates. |
| `data/failures.jsonl` | Explicit infrastructure-failure records (never silent drops). |
| `data/run_log.jsonl` | Timestamped event log: every call, retry, freeze, test, and write. |

## Usage

```bash
cd /home/Drew/benchmark_v2
python3 generate_baseline.py                 # full run (resumable)
python3 generate_baseline.py --limit 3       # freeze at most 3 new candidates
python3 validate_baseline.py                 # certify; exit 0 iff VALID
python3 tests/test_stage0.py                 # local regression tests (no API)
```

## Generation (exact request parameters)

```python
client.chat.completions.create(
    model="gpt-5.4-2026-03-05",     # immutable snapshot, not a moving alias
    messages=[{"role": "user", "content": generation_prompt}],
    temperature=0,
    max_completion_tokens=8192,
    reasoning_effort="none",
    timeout=300.0,
)
```

- SDK built-in retries disabled (`max_retries=0`): every transport retry is
  explicit and logged.
- `reasoning_effort` is a first-class `create()` parameter in openai SDK
  1.75.0. The SDK's declared Literal lists only `low/medium/high`; `"none"`
  is sent verbatim (not enforced client-side). If the snapshot rejects it
  (HTTP 400), the run aborts loudly (`FatalConfigError`) **before any task
  is frozen** — no silent fallback.
- Generation prompt (template in `config.py`, stored verbatim per record):

```
Complete the following Python programming problem. Write the complete solution in Python, including the function definition and any imports it requires.
Return only Python code. Do not include tests, examples, or explanations.

{HumanEval prompt field, verbatim}
```

The prompt contains no evaluator language, no self-critique instructions,
no execution results, no reference tests, no hints, no cross-arm content.

## Extraction rules (strictly non-repairing)

Invariant: the raw model response is preserved verbatim
(`raw_model_response` + `raw_response_sha256`).

- **Default**: `candidate_code = raw_model_response`, byte-identical;
  `extraction_method = "raw_exact"`.
- **Only permitted transformation**: if the entire non-whitespace response
  consists of **exactly one** Markdown fence (exactly two ` ``` ` markers),
  optionally tagged `python`/`py` (case-insensitive), unwrap that outer
  fence and use its interior exactly;
  `extraction_method = "outer_fence_unwrapped"`.

Never: function-scanning, prose deletion, leading-statement deletion
(constants/decorators/imports survive), first-of-multiple-fence selection,
fence concatenation, prompt prepending (no completion-style repair path),
reindent/dedent, logic normalization, syntax repair, or another model call.
A format-violating response is valid experimental output and fails
execution naturally. The extractor is a pure function of the raw response;
the validator re-runs it and requires identical output.

## Test mechanism (described precisely)

**HumanEval reference tests executed against the frozen candidate in a
fresh Python subprocess, with a 15-second per-task timeout:**

```
program = candidate_code + "\n\n" + test + "\n\ncheck(" + entry_point + ")\nprint('PASS')\n"
```

`baseline_correct = (returncode == 0 and stdout ends with "PASS")`.
`test_status ∈ {pass, fail_assertion, fail_error, timeout}`. **No
SAFE_HEADER, no custom AST policy, no model-visible test output during
Stage 0.** (The upstream `human-eval` package is not installed here; this
is the reference-test semantics run locally, not "the canonical HumanEval
harness".) A candidate **timeout is an incorrect solution**, not an
infrastructure exclusion, and is never retried. Only local spawn/IO
failures retry (identical test, max 3 attempts). The Python
executable/version used for testing is recorded per scored record
(`test_python_executable`, `test_python_version`).

## Infrastructure retry policy (never a new candidate)

- **Generation transport**: transient errors only (connection, timeout,
  rate limit, HTTP 408/409/425/429/5xx) reissue the identical logical
  request: max 8 attempts, exponential backoff (base 5 s, ×2, cap 300 s),
  full jitter, all logged. Recorded as `transport_attempts`. Non-retryable
  4xx aborts the whole run loudly. Exhaustion → explicit record in
  `failures.jsonl`; run ends INCOMPLETE.
- **Cost accounting** (operational, not a scientific exclusion rule):
  $2.50 / $15.00 per 1M input/output tokens. **Cumulative across resume
  runs**: spend in already-frozen records is summed, then new generation
  cost added; restarting never resets spend. Hard cap default $25 (`--cap`).

## Resume semantics

On startup, before anything expensive: validate `frozen_candidates.jsonl`
— reject malformed records, duplicate task IDs, raw/candidate hash
mismatches, and any protocol metadata that differs from the configured
experiment (model snapshot, temperature, reasoning_effort,
max_completion_tokens, schema/experiment versions, dataset hash). A frozen
task is never regenerated — including candidates that failed the reference
tests (incorrect code is valid data).

## Immutability and certification

Frozen: `raw_model_response`, `raw_response_sha256`, `candidate_code`,
`candidate_sha256`, `extraction_method`, plus all protocol metadata.
Scored: `baseline_correct`, `test_status`, `test_details` (derived only).
`validate_baseline.py` verifies per task: canonical prompt/entry_point
equality, `task_index` vs canonical order, exact `generation_prompt`
regeneration, both hashes, deterministic re-extraction, scored↔frozen
candidate equality, `baseline_correct == (test_status == "pass")`, allowed
`test_status` values, and model-version integrity (all unique
`response_model` values printed; more than one — or one different from the
requested snapshot — is a hard failure: the baseline must not silently mix
model versions). Summary output:

```
Expected HumanEval tasks: 164
Frozen candidate records: 164
Scored baseline records: 164
Missing candidates: 0
Missing scores: 0
Duplicates: 0
Raw-response hash mismatches: 0
Candidate hash mismatches: 0
Protocol metadata mismatches: 0
Candidate/score mismatches: 0
Unique API response models: [...]
Baseline correct: N1
Baseline incorrect: N0
N0 + N1: 164
VALID BASELINE: YES
```

(N0/N1 are computed, never hard-coded.)

## Differences from the old harness (`human-eval-selfcorr/`)

Reused (infrastructure only): dataset file, `~/.keys/openai` convention,
OpenAI SDK access pattern, the reference-test subprocess pattern.

Deliberately **not** carried over: the three evaluator arms (later stages);
the `ast_guard` blocklist and `SAFE_HEADER` preamble (measurement
confounds); silent exception swallowing; repairing extraction
(code-start scan, prompt prepending, first-fence selection); the moving
`gpt-5.4` alias; the stale $10/M output price; test-before-persist
ordering (candidates could be discarded by local test failures).
