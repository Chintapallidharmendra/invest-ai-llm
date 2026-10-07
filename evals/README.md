# evals

Evaluation harness, eval sets and the synthetic confidential corpus (ADR-037).

- `corpus/` — synthetic test documents generator (Story 1.7); `make corpus` runs
  `corpus/Makefile.inc`. Generated files go to `corpus/build/` (git-ignored).
- Eval harness and eval set arrive in Story 1.8. Reports go to `reports/` (git-ignored).

UAT holds **synthetic documents only**. Never put real deal documents here.
