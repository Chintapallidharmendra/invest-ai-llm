# evals

Evaluation harness, eval sets and the synthetic confidential corpus (ADR-037).

- `corpus/` — synthetic test documents generator (Story 1.7); `make corpus` runs
  `corpus/Makefile.inc`. Generated files go to `corpus/build/` (git-ignored).
- Eval harness and eval set arrive in Story 1.8. Reports go to `reports/` (git-ignored).

UAT holds **synthetic documents only**. Never put real deal documents here.

## Running evals

The eval runner (Story 1.8) scores the eval set in `cases/eval/` against the PRD quality
gates (NFR-010). **Run it on UAT or a dev machine, against the synthetic corpus only.**
Reports are local files (`reports/<timestamp>/report.md` + `results.json`, git-ignored),
never Langfuse, and never production content.

Run everything from the repository root, with the backend environment:

```sh
make corpus                                                    # corpus build/ (Story 1.7)
uv run --project backend python -m evals.runner count          # cases per category vs minimums
uv run --project backend python -m evals.runner validate       # schema-check every case

# Model mode (model spike, Story 1.9): masked excerpts straight to an OpenAI-compatible
# endpoint. On UAT, run inside a container on the `inference` network.
uv run --project backend python -m evals.runner run --mode model \
    --target http://vllm-chat:8000/v1 --model chat

# API mode: the deployed app over its HTTP API, as a dedicated eval user.
EVAL_PASSWORD=... uv run --project backend python -m evals.runner run --mode api \
    --target https://localhost:8043 --username eval-user --insecure \
    --judge-url http://vllm-chat:8000/v1 --judge-model chat
```

- **Modes:**
  - *api* drives the app end to end: login, upload, wait for ingestion, select documents,
    chat over SSE, poll jobs, apply operation previews, download outputs. Endpoints the
    app does not have yet (404/501) mark cases `skipped: feature-unavailable`.
  - *model* has no retrieval. It inlines masked excerpts (cited units first, then the
    start of each document, up to `--budget-chars`). Cases marked `modes: [api]` (the
    50,000-row sheet, the long monthly tables, most summaries) are skipped there.
- **Masking:** model-mode prompts never contain planted identifiers. They are masked
  with `app.guardrails.identifiers` (Story 4.1) when available, otherwise with the
  manifest's planted values.
- **Scoring:**
  - Deterministic: citations (document + locator; a sheet range covers its cells),
    exact figures (Story 4.4 grounding when available), FR-030 not-found phrasing,
    declines, ungrounded numbers, and cell-by-cell reference outputs in
    `cases/eval/reference/`.
  - Summaries are scored 1-5 per `rubrics/summary.yaml` by an LLM judge (recorded in
    every report). A reviewer can override any result in `overrides.yaml`.
- **Gates** (`runner/gates.yaml`):
  - overall pass rate >= 85%;
  - >= 85% of summaries score >= 4/5;
  - 0 ungrounded figures;
  - 100% of applied operations match their reference.

  The exit code is 1 when an enabled gate fails (`--no-gates` reports without failing)
  and 2 on setup errors.
- **Changing cases:** the cases are generated from the corpus manifest, so facts always
  match it. Edit `runner/authoring.py`, run
  `uv run --project backend python -m evals.runner author`, and commit the regenerated
  `cases/eval/`.
- **Tests:** `uv run --project backend pytest evals/tests/runner` (schema, scorers, SSE,
  API mode against stubs, model mode against the fake LLM).
