# invest-ai-llm

A secure, fully self-hosted AI assistant for investment bankers: summarise, question and
compare confidential documents (PDF, Word, PowerPoint, Excel/CSV) and process Excel
workbooks from plain-English prompts. An open-source LLM runs on our own GPU via vLLM;
no data leaves the server.

Planning artefacts (PRD, architecture, epics, stories, decision log) live in
[`bmad-output/`](bmad-output/). Start with
[`project-context.md`](bmad-output/project-context.md) and
[`architecture.md`](bmad-output/architecture.md).

## Layout (ADR-038)

```
backend/            FastAPI API + worker (Python 3.13, uv)
  app/
    core/           config, db (RLS context helper), errors, ids, time, obs
    crypto/         key hierarchy, field/file encryption, shredding      (no fastapi)
    auth/           users, sessions, links, roles
    spaces/         spaces, membership, AccessContext, RLS policies
    chat/           conversations, messages, SSE, turn orchestration
    assistant/      agent, prompts, tool registry, context builder       (no fastapi)
    guardrails/     identifier gate, guard client, scope router, grounding (no fastapi)
    llm/            vLLM gateway, profiles, cache_salt, priorities       (no fastapi)
    documents/      upload, ingestion, chunks, tables, retrieval         (fastapi only in routes)
    sheets/         sheet storage, operation catalogue + engine          (no fastapi)
    outputs/        output files, versions, watermarking, download tokens
    jobs/           job queue, scheduler, summary/comparison/lifecycle jobs
    lifecycle/      expiry, delete pipeline, backups trigger
    audit/          metadata-only writer, chain, verify, queries
    compliance/     break-glass grants, compliance viewer
    admin/          users, settings, overview (metadata only), alerts
    api/            router assembly, middleware, health
    worker/         worker entrypoint
  migrations/versions/   Alembic revisions (one per story)
  tests/
parser/             separate parsing container (Docling, OCR, openpyxl); no DB access
frontend/           React 19 + TypeScript SPA (pnpm, Vite, Tailwind 4, shadcn/ui)
deploy/             Docker Compose, zero-egress single-host deployment
evals/              eval harness, eval sets, synthetic corpus
bmad-output/        planning artefacts (not code)
```

Module boundaries are enforced by import-linter (`backend/.importlinter`):

- no `fastapi`/`starlette` in `crypto`, `assistant`, `guardrails`, `llm`, `sheets`, or in
  `documents` outside `documents.routes`
- only `app.crypto` imports `cryptography`; only `app.llm` imports `openai`
- domain modules never import `app.api`, `app.admin` or `app.chat`

## Getting started

Requirements: Python 3.13, [uv](https://docs.astral.sh/uv/), Node 22 LTS,
[pnpm](https://pnpm.io/), gitleaks, pre-commit.

```sh
(cd backend && uv sync)
(cd parser && uv sync)
(cd frontend && pnpm install --frozen-lockfile)
pre-commit install

make lint typecheck test
```

Make targets: `lint`, `typecheck`, `test`, `typegen`, `corpus`, `up`, `down`. Targets whose
tooling arrives in later stories print "not available yet".

**Dependencies are declared once** (Story 1.1) in `backend/pyproject.toml`,
`parser/pyproject.toml` and `frontend/package.json`. Feature stories must not edit them.

**No secrets and no real documents in this repo.** `/data/`, backups, `*.kek`, `.env` and
`deploy/secrets/*` are git-ignored, and gitleaks runs on every commit.
