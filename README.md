# HumanEval Self-Correction Benchmark (benchmark_v2)

**Status: COMPLETE.** This repository is the preserved reproducibility
artifact for a finished experiment. Nothing here needs to be regenerated
to audit the reported results.

## What this benchmark is

The benchmark evaluates whether different evaluator configurations
recognize the correctness of **the same frozen object**: one immutable
GPT-5.4 candidate solution per HumanEval task.

- **N = 164** canonical HumanEval tasks.
- **Stage 0 contains exactly one persisted frozen candidate per task.**
  Each successfully returned candidate used by the experiment was frozen
  immediately upon return, before reference-test scoring. One operational
  exception is documented for **HumanEval/85**: a request was initiated
  before a process interruption but no response was persisted; on resume
  the still-unfrozen task's identical logical request was reissued. No
  candidate content from the interrupted request was observed or used
  for selection (see the operational note in
  [`REPRODUCIBILITY.md`](REPRODUCIBILITY.md)).
- Stage-0 reference-test scoring (generation correctness **T0**):
  **155 correct candidates, 9 incorrect candidates** (N0 + N1 = 164).
- **All three evaluator arms judge the SAME immutable candidate.** No
  evaluator arm regenerates, repairs, or revises candidate code.

## Core methodological distinctions

Three different things are never conflated here:

1. **Generation correctness (T0)** — whether the frozen candidate passes
   the HumanEval reference tests. Determined once in Stage 0, before any
   evaluator analysis. Immutable.
2. **Evaluator acceptance (A)** — whether an evaluator configuration
   *judges* the frozen candidate to be correct (YES/NO). This is what
   varies across arms.
3. **Actual correction/revision** — modifying candidate code after
   evaluation. **There is NO correction/revision experiment in
   benchmark_v2.** No arm has any code path that alters a candidate.

The benchmark measures *recognition* of correctness, not repair.

## The three completed evaluator conditions

Each arm evaluated all 164 frozen candidates in fresh, independent
contexts using the same model snapshot (`gpt-5.4-2026-03-05`,
temperature 0, `reasoning_effort="none"`).

### Arm 1 — pure self-check

The model sees the problem and its own frozen candidate and answers one
binary question: is it fully correct?

| | accepted | rejected |
|---|---|---|
| **incorrect candidates (N0=9)** | 6/9 | 3/9 |
| **correct candidates (N1=155)** | 146/155 | 9/155 |

### Arm 2 — structured steelman (self-debate)

The model must produce a strongest case that the candidate is WRONG, a
strongest case that it is CORRECT, then a final binary verdict.

| | accepted | rejected |
|---|---|---|
| **incorrect candidates (N0=9)** | 3/9 | 6/9 |
| **correct candidates (N1=155)** | 149/155 | 6/155 |

Self-check and steelman each issued **152 YES / 12 NO** overall — so the
steelman improvement (6→3 false acceptances, 9→6 false rejections) is
**better discrimination, not simply a more skeptical threshold**.

### Arm 3 — execution-grounded positive control

The evaluator additionally receives a binary execution signal
(`EXECUTION RESULT: PASS` / `FAIL`) produced by executing the exact
frozen candidate against the HumanEval reference tests.

| | accepted | rejected |
|---|---|---|
| **incorrect candidates (N0=9)** | 0/9 | 9/9 |
| **correct candidates (N1=155)** | 149/155 | 6/155 |

**Arm 3 is an oracle-grounded positive control.** Its PASS/FAIL signal
is produced using the same HumanEval reference-test semantics that
define Stage-0 correctness. Therefore **Arm 3 is NOT an independent
validation benchmark** — it anchors the externally grounded endpoint of
the evaluation spectrum. The candidate remains immutable and cannot be
revised; execution did not "correct" any candidate code — it improved
**evaluator classification** only. Arm 3 is not a correction condition.

## Reproducibility

The repository contains the complete preserved experiment:

- immutable frozen candidates (`data/frozen_candidates.jsonl`);
- Stage-0 reference-test labels (`data/baseline.jsonl`);
- exact evaluator prompts (stored verbatim per record);
- raw model responses (frozen before parsing, per arm);
- parsed evaluator decisions (per arm);
- execution signals and their hash-bound certification;
- run logs, validators, regression tests, and run reports.

**Reproducing the reported experiment does NOT require regenerating GPT
outputs. The preserved corpus IS the experiment.** A researcher can
independently audit the stored corpus, recompute hashes and statistics,
and inspect the harness. New API generation would be a *replication*,
not a reproduction of the original realized sample.

See [`REPRODUCIBILITY.md`](REPRODUCIBILITY.md) for the auditor-oriented
guide and [`MANIFEST.sha256`](MANIFEST.sha256) for the definitive
artifact hashes.

## Independent audit

An adversarial fresh-model (AI-assisted) audit of commit `d997a64`
independently reconstructed the empirical results from the preserved
corpus and found no major empirical failure (0 retraction-worthy,
0 major findings), while identifying several manuscript claim-scope
issues that belong to the paper, not this repository. Full provenance
and scope caveats:
[`reports/independent_adversarial_audit_20260909.md`](reports/independent_adversarial_audit_20260909.md).

## Layout

```
config.py / generate_baseline.py / validate_baseline.py   # Stage 0
common/                  # shared neutral utilities (dataset, client, extraction, execution)
arms/selfcheck/          # Arm 1: pure self-check
arms/steelman/           # Arm 2: structured steelman / self-debate
arms/execution/          # Arm 3: execution-grounded positive control
data/                    # all frozen ledgers (immutable)
reports/                 # run/build reports written at execution time
tests/                   # local regression suites (no API calls)
MANIFEST.sha256          # definitive artifact hashes
REPRODUCIBILITY.md       # auditor guide
```

## Safe audit commands (NO API calls)

```bash
python3 validate_baseline.py                    # certify Stage 0 corpus
python3 arms/selfcheck/validate_selfcheck.py    # certify Arm 1
python3 arms/steelman/validate_steelman.py      # certify Arm 2
python3 arms/execution/validate_execution.py    # certify Arm 3
python3 tests/test_stage0.py                    # 29 local tests
python3 tests/test_arm1_selfcheck.py            # 19 local tests
python3 tests/test_arm2_steelman.py             # 24 local tests
python3 tests/test_arm3_execution.py            # 54 local tests
sha256sum -c MANIFEST.sha256                    # verify artifact integrity
```

⚠️ The `run_*.py` / `generate_baseline.py` production entry points make
real API calls and write to `data/`. They are preserved for inspection
and replication, NOT needed for reproduction of the reported results.

## License

This work is licensed under the **Creative Commons
Attribution-NonCommercial-ShareAlike 4.0 International License**
(CC BY-NC-SA 4.0). See [`LICENSE`](LICENSE).

- **Attribution required** — you must give appropriate credit.
- **Non-commercial use only** — commercial use is not permitted.
- **Share-alike** — derivatives must be distributed under the same
  license.

https://creativecommons.org/licenses/by-nc-sa/4.0/
