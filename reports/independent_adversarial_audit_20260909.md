# Independent Adversarial Audit — 2026-09-09

**Nature of this document:** this is an **independent AI-assisted
adversarial audit**. It is NOT peer review, NOT journal review, and NOT
external expert review.

- **Audited commit:** `d997a64f29d66bbfffbfdba8f5ef6a562f033878`
- **Verdict:** MINOR REVISION
- **Retraction-worthy findings:** 0
- **Major findings:** 0
- **Minor findings:** 3
- **Editorial findings:** 1

## Scope and limitations (recorded honestly)

The audit independently recomputed empirical quantities from the
preserved corpus (the frozen ledgers in `data/`). Due to its connector
environment, the audit did **not** literally execute every local
regression command or byte-hash every large file itself. Its empirical
conclusions are based on independent reconstruction from the preserved
artifacts, not on re-execution of the full local harness.

## Audit summary (as supplied by the auditor)

> The audit verdict was MINOR REVISION: 0 retraction-worthy findings,
> 0 major-revision findings. The empirical corpus and statistics
> survived independent recomputation.

### Findings

The three MINOR findings concern the **manuscript**, not the benchmark
repository:

1. **Minor 1 — claim scope:** causal language about fresh-context
   evaluation reducing correlated error.
2. **Minor 2 — claim scope:** causal attribution of the steelman
   improvement to reduced latent shared error.
3. **Minor 3 — notation scope:** overly narrow formal correctness
   notation `G = H*(X)`.

The one EDITORIAL finding concerned wording strength around Stage-0
candidate generation history (addressed by the README clarification
documenting the HumanEval/85 interruption).

## Repository position on the findings

These are manuscript claim-scope revisions. No benchmark data or
executable code was changed in response to them. The repository
demonstrates observed evaluator behavior; it does not attempt to prove
the latent theoretical mechanism.

## What this audit does NOT establish

- It is not independent peer review and confers no expert validation.
- It does not formally verify the harness.
- It does not certify the manuscript's theoretical claims.
