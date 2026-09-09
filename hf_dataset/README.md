---
license: cc-by-nc-sa-4.0
task_categories:
  - text-generation
tags:
  - HumanEval
  - LLM evaluation
  - self-correction
  - benchmark
size_categories:
  - n<1K
---

# HumanEval Self-Correction Benchmark v2

**A fixed-candidate, paired benchmark of LLM correctness recognition
under pure self-check, structured steelman/self-debate, and
execution-grounded positive-control evaluation.**

Companion artifact for the paper:

> **Limits of Self-Correction in LLMs:
> An Information-Theoretic Analysis of Correlated Errors**

## Key facts

- **N = 164** canonical HumanEval tasks.
- **Stage-0 correct = 155, Stage-0 incorrect = 9** (reference-test
  correctness, T0).
- **The same immutable candidate is judged across all evaluator
  conditions.** No repair or revision occurs anywhere.
- One row per task in `data/benchmark.jsonl` (164 rows), containing the
  Stage-0 label, the frozen candidate (with SHA-256), and all three
  arms' verdicts/acceptances.

## Evaluator conditions

| Arm | Condition | Wrong accepted | Correct accepted |
|-----|-----------|----------------|------------------|
| 1 | Pure self-check | 6/9 | 146/155 |
| 2 | Structured steelman / self-debate | 3/9 | 149/155 |
| 3 | Execution-grounded positive control | 0/9 | 149/155 |

**Arm 3 is an oracle-grounded positive control — NOT independent
validation.** Its binary PASS/FAIL signal comes from executing the same
immutable candidate against the HumanEval reference tests that define
the correctness label. It anchors the externally grounded endpoint of
the evaluation spectrum; the candidate itself is never modified.

## Provenance

- **Canonical source and full provenance (raw ledgers, run logs,
  validators, source code, historical reports):**
  https://github.com/AndBrilliant/human-eval-selfcorr
- **Generated from Git commit:**
  `d997a64f29d66bbfffbfdba8f5ef6a562f033878` (plus documentation-only
  remediation; no experimental artifact changed after that commit —
  verify with `MANIFEST.sha256`, included here).
- This mirror is for discovery and convenient analysis. GitHub remains
  the canonical benchmark repository.

## Source benchmark attribution

The underlying tasks come from **HumanEval** (Chen et al., 2021,
"Evaluating Large Language Models Trained on Code",
arXiv:2107.03374), distributed under the **MIT License**
(https://github.com/openai/human-eval).

## License of this artifact

CC BY-NC-SA 4.0 (attribution required, non-commercial use only,
share-alike) — see the canonical repository's `LICENSE` file.
