# System Architecture: Foundry Local AI (Secure Finance Chat Assistant)

**Document Version:** 1.1
**Date:** 2026-10-06
**Author:** Winston (Architect), via bmad-architecture
**Track:** BMad Method
**Status:** Approved for story creation
**Source PRD:** `bmad-output/prd.md` v1.1 (38 FR IDs incl. FR-004 Won't; 19 NFRs)

> This is the single source of truth for cross-cutting technical decisions. Every
> story compiled by bmad-epics-and-stories inherits the **LOCKED** decisions recorded
> here. Inputs: `prd.md` v1.0, `project-context.md`, `research-report.md` v1.4,
> `decision-log.md`, `addendum.md` v1.0.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Architecture Pattern](#2-architecture-pattern)
3. [Architecture Decision Records](#3-architecture-decision-records)
4. [Component Design](#4-component-design)
5. [Data Model](#5-data-model)
6. [API Specifications](#6-api-specifications)
7. [FR / NFR Coverage Matrix](#7-fr--nfr-coverage-matrix)
8. [Technology Stack](#8-technology-stack)
9. [Trade-off Analysis](#9-trade-off-analysis)
10. [Deployment Architecture](#10-deployment-architecture)
11. [Future Considerations](#11-future-considerations)
12. [Open Architecture Questions](#12-open-architecture-questions)

---

## 1. System Overview

### Purpose
An internal, self-hosted chat assistant for ~10 non-technical finance users. It answers
questions about IIFL / IIFL Finance, NSE stocks and indices, and Indian mutual funds,
using read-only data tools and a library of public documents. An open-weight LLM serves
it on one NVIDIA A10 (24 GB). Layered guardrails keep PII away from the model and keep
answers in scope, grounded and auditable.

### Scope
**In Scope (V1):**
- Admin-provisioned accounts with manually shared one-time set-password links; session login; two roles
- Streaming chat with conversation history, sources and as-of times, starter prompts and ratings
- Guardrail pipeline:
  - input: PII block, safety/jailbreak check, scope routing
  - output: hold-back gating, figure grounding, final moderation
  - fail closed if any safety check is unavailable
- Market/MF/company data tools over stored, scheduled copies (nselib, mftool), with a provider abstraction
- Public document library: upload, scan, index, retrieve with page citations
- Hash-chained audit trail, admin console (users, documents, data sources, audit, overview, settings)
- Evaluation, PII and red-team suites as release gates

**Out of Scope:** see PRD "Out of Scope". Architecturally relevant exclusions:
- No outbound email.
- No third-party LLM APIs.
- No real-time tick data.
- No write-capable tools.
- No SSO/MFA.
- No charts.
- Single host, so no high-availability cluster.

### Architectural Drivers
The NFRs that most constrain the design:

1. **NFR-005 / FR-012: PII never reaches the model or storage.** Forces a deterministic PII gate *before* any model call or persistence. Blocked text is never written anywhere, so it applies to logs, traces and audit too.
2. **NFR-013: Fail closed.** Every safety dependency (guard model, PII engine) sits on the request path with readiness checks. No "skip if unavailable" code path may exist.
3. **NFR-010 / FR-036 / FR-030: Grounded figures.** Numbers must come from tools or documents. This drives two decisions: tables are rendered from tool data, not model text (ADR-011), and every figure in model prose is checked before display.
4. **NFR-001 / NFR-004: Latency and capacity on one A10.** TTFT p95 ≤ 3 s (≤ 8 s with tools) for 10 concurrent users. This drives the GPU memory split, prefix caching, the ~8k context cap and streaming with *hold-back gating* rather than buffering whole answers.
5. **NFR-015: Swappable model and data source.** The LLM sits behind an OpenAI-compatible gateway and data behind a `MarketDataProvider` protocol. Required by the post-V1 move to ACE or internal database feeds.
6. **NFR-017 / FR-031: Tamper-evident audit, 5-year retention.** Drives the hash-chained, append-only audit store and DB-role permissions.
7. **NFR-008 / NFR-018: No egress except allow-listed data sources; disciplined upstream access.** Drives network isolation, an egress proxy, offline model loading and the scheduled data store.

### Stakeholders & Constraints (from project-context.md)
- **Users:** ~10 finance staff (desktop browsers, internal network/VPN), 1–2 admins, Compliance as reviewer.
- **Team:** a single builder working with AI agents, so prefer few moving parts, conventional tools and strong conventions.
- **Fixed constraints:**
  - FastAPI + React/TypeScript
  - vLLM on one A10 24 GB (Ampere: INT4 via Marlin; no native FP8 compute)
  - Open-weight models only
  - No third-party AI APIs
  - No email in V1
  - V1 data from open-source libraries under the accepted NSE ToU mitigations

---

## 2. Architecture Pattern

**Pattern:** **Modular monolith** (one FastAPI application with strict internal module
boundaries), plus one background **worker** process from the same codebase and two
**model-serving** processes (vLLM). Deployed with Docker Compose on a single host.

**Justification:**
- The BMad Method scale (~46 stories), a single builder and ~10 users don't justify
  microservices (REFERENCE rubric: simplest option that clears the bar).
- The only processes split out are those with genuinely different runtime profiles: GPU
  inference (vLLM), and scheduled or long-running jobs (worker: data refresh, document
  ingestion). Everything else shares one deployable unit, one database and one set of
  conventions.
- Module boundaries (ADR-001, §4) keep the `assistant` and `guardrails` modules free of
  web-framework imports. That keeps the later option open of splitting inference
  orchestration into its own service (research Finding 4).

**Alternatives considered:**
- **Microservices** (auth, chat, data, ingestion services): rejected. Operational cost
  (network contracts, deployments, tracing) is far beyond the benefit for one builder
  and 10 users. No NFR demands independent scaling.
- **Single process including jobs:** rejected. Data refresh and PDF ingestion would
  compete with request handling, and long jobs would block restarts.

**Application:** see §4 for modules and the dependency rules between them.

---

## 3. Architecture Decision Records

| ADR | Title | Status | Drives |
|-----|-------|--------|--------|
| ADR-001 | Modular monolith with fixed module boundaries | Accepted | NFR-015, NFR-019 |
| ADR-002 | REST/JSON under `/api/v1` + SSE for chat streaming | Accepted | FR-006, NFR-001 |
| ADR-003 | Response, error (RFC 9457) and SSE event conventions | Accepted | all API FRs |
| ADR-004 | Naming conventions | Accepted | NFR-015 |
| ADR-005 | PostgreSQL 18 + pgvector; SQLAlchemy 2 async; Alembic; UUIDv7 | Accepted | FR-008, FR-028–031, NFR-014, NFR-017 |
| ADR-006 | Server-side sessions with opaque tokens; manual set-password links | Accepted | FR-001–003, FR-038, NFR-007 |
| ADR-007 | RBAC with two roles; deny by default; ownership checks | Accepted | FR-001, FR-003, FR-005, FR-008 |
| ADR-008 | Frontend state: TanStack Query for server state; no global store | Accepted | FR-006–011 |
| ADR-009 | LLM gateway to vLLM (OpenAI-compatible): chat + guard instances | Accepted | NFR-001, NFR-004, NFR-008, NFR-015 |
| ADR-010 | Guardrail pipeline order; fail closed | Accepted | FR-012–017, FR-034, FR-035, FR-037, NFR-003, NFR-005, NFR-006, NFR-013 |
| ADR-011 | Streaming output gating, figure grounding and tool-rendered tables | Accepted | FR-006, FR-009, FR-015, FR-023, FR-024, FR-030, FR-036, NFR-001 |
| ADR-012 | Agent and tool contract (PydanticAI, allow-list, typed args, tool output as data) | Accepted | FR-007, FR-014, FR-016, FR-018–023, FR-027, FR-029 |
| ADR-013 | Market-data provider abstraction, stored copies, refresh and upstream discipline | Accepted | FR-018–022, FR-025–027, NFR-015, NFR-018 |
| ADR-014 | Knowledge library: ingestion scanning, hybrid retrieval, page citations | Accepted | FR-012, FR-014, FR-028, FR-029 |
| ADR-015 | Conversation context management within ~8k tokens | Accepted | FR-007, NFR-004 |
| ADR-016 | Hash-chained, append-only audit trail | Accepted | FR-017, FR-031, FR-032, NFR-017 |
| ADR-017 | Background jobs: worker process, APScheduler + Postgres job table | Accepted | FR-025, FR-028, NFR-014 |
| ADR-018 | Valkey for rate limits, upstream token buckets and short-lived cache | Accepted | FR-037, NFR-018 |
| ADR-019 | Observability: structured logs, correlation IDs, OpenTelemetry → Langfuse | Accepted | NFR-016 |
| ADR-020 | Deployment and network isolation: Compose, Caddy TLS, egress proxy, offline models | Accepted | NFR-008, NFR-009, NFR-012, NFR-019 |
| ADR-021 | Configuration and secrets | Accepted | NFR-009, NFR-015 |
| ADR-022 | Test and evaluation strategy as release gates | Accepted | NFR-005, NFR-006, NFR-010, FR-013, FR-034–036 |

Each ADR's one-line summary is appended to `decision-log.md`.

---

### ADR-001: Modular monolith with fixed module boundaries

**Status:** Accepted   **Drives:** NFR-015, NFR-019; enables parallel stories

**Context:** One builder and many parallel agent-authored stories need clear ownership,
or modules will import each other ad hoc. The `assistant` code must stay
framework-independent so it can later move to its own service.

**Decision:** Monorepo layout:
```
backend/                      # Python 3.13, uv-managed, package "app"
  app/
    core/        config, db session, logging, errors, ids, time, security primitives
    auth/        users, sessions, set-password links, password policy, RBAC deps
    chat/        conversations, messages, SSE streaming endpoint, orchestration of a turn
    assistant/   agent definition, system prompts, tool registry, context builder  (NO fastapi imports)
    guardrails/  pii gate, guard-model client, scope router, output gate, grounding  (NO fastapi imports)
    llm/         LLM gateway (OpenAI-compatible client, model profiles)            (NO fastapi imports)
    marketdata/  provider protocol + adapters, store repositories, refresh jobs      (NO fastapi imports)
    knowledge/   document ingestion, chunking, embeddings, retrieval                 (NO fastapi imports)
    audit/       audit writer, hash chain, verification, queries
    admin/       admin routers (users, documents, data sources, audit, overview, settings)
    exports/     CSV/XLSX generation from stored tables
    api/         router assembly, middleware (correlation id, csrf, rate limit), health
    worker/      scheduler entrypoint, job runner
  migrations/    Alembic
  tests/
frontend/                     # React + TS + Vite
deploy/                       # compose files, Caddy config, vLLM launch configs, egress allow-list, runbook
evals/                        # eval set, PII suite, red-team suite, runners, reports
prototype/                    # existing Streamlit/Foundry prototype (moved from src/, not maintained)
```
Dependency rules: `api`/`admin`/`chat` → domain modules → `core`. Domain modules never
import `api`, `admin` or `chat`. Modules marked "NO fastapi imports" are plain Python
and receive dependencies by parameter.

**Consequences: LOCKED for all stories:**
- Code goes in the module that owns the concern, as listed above. Cross-module calls go
  through each module's public `service.py` (or `__init__` exports), never its
  repositories directly.
- An import-linter contract in CI enforces these rules.
- Easier: parallel stories with low merge conflict; later service split.
- Accepted cost: some boilerplate (service layers).

**Alternatives:** layer-first layout (`routers/`, `models/`, `services/`): rejected
because it scatters each feature across folders and raises conflict between stories.

**Revisit when:** inference orchestration needs separate scaling (more than one GPU
host), at which point `assistant` + `guardrails` + `llm` move to a separate service.

---

### ADR-002: REST/JSON under `/api/v1` + SSE for chat streaming

**Status:** Accepted   **Drives:** FR-006, NFR-001, all admin FRs

**Context:** One first-party SPA client. Chat needs token streaming. A schema-first
contract lets frontend and backend stories proceed in parallel.

**Decision:**
- All HTTP APIs are **REST with JSON**, served under `/api/v1`. **No GraphQL, no WebSockets.**
- Chat replies stream as **Server-Sent Events** (`text/event-stream`) in the response to
  `POST /api/v1/conversations/{id}/messages`. The client consumes them with a
  fetch-based SSE reader (POST body plus cookies). Cancelling means the client aborts the
  request; the server detects the disconnect and cancels generation.
- **FastAPI's generated OpenAPI is the contract.** The frontend generates types with
  `openapi-typescript` into `frontend/src/api/schema.d.ts`; hand-written request types
  are not allowed. SSE event payloads are Pydantic models published in the OpenAPI
  `components` (via a schema-only endpoint) so they are generated too.
- Versioning: URL prefix `/api/v1`. Breaking changes need `/api/v2`, which is not
  expected in V1.

**Consequences: LOCKED:**
- Every endpoint has Pydantic request/response models.
- Streaming exists only in the chat-message endpoint.
- Long-running admin operations (document ingestion) return `202 Accepted` + a resource
  whose `status` the client polls.

**Alternatives:** WebSockets (bidirectional, not needed; harder with cookie auth and
proxies); GraphQL (overkill for one client).

**Revisit when:** a second client type (mobile, partner API) appears.

---

### ADR-003: Response, error and SSE event conventions

**Status:** Accepted   **Drives:** all API FRs, FR-006, FR-012

**Decision:**
- **Success responses:** the resource JSON itself (no envelope). Collections:
  `{"items": [...], "next_cursor": "<opaque>|null"}` with cursor pagination
  (`?cursor=&limit=`, default 20, max 100).
- **Errors:** RFC 9457 Problem Details, `Content-Type: application/problem+json`, with
  members `type`, `title`, `status`, `detail`, `instance`, plus extensions `code`
  (stable snake_case machine code) and `correlation_id`. Examples of `code`:
  `pii_detected` (422, plus `detected_types: [...]`), `rate_limited` (429, plus
  `retry_after_s`), `safety_unavailable` (503), `not_found`, `forbidden`,
  `validation_error` (422, plus `errors: [...]`), `csrf_failed` (403), `auth_required` (401).
- **Status codes:**
  - 200 OK, 201 created, 202 accepted (async), 204 no content
  - 400 malformed, 401 unauthenticated, 403 forbidden, 404 not found (also used for
    other users' resources, to avoid enumeration), 409 conflict, 422 validation/policy
  - 429 rate limit, 503 dependency or safety unavailable
- **Chat stream (SSE)** event types and payloads, with each `data:` a JSON object:
  `meta` {conversation_id, user_message_id, assistant_message_id} ·
  `status` {stage: "checking"|"thinking"|"fetching"|"searching", label} ·
  `delta` {text} · `table` {table_id, title, columns, rows, source, as_of, notes} ·
  `sources` {items:[{kind:"data"|"document", label, as_of|page}]} ·
  `replace` {text, reason_code} (the server withdrew streamed text) ·
  `done` {finish_reason: "stop"|"declined"|"cancelled"|"withheld", latency_ms} ·
  `error` {problem}.
  **Input-stage blocks (PII, rate limit, safety unavailable) are returned as normal HTTP
  problem responses before the stream starts**, never as SSE events.
- Time values in JSON are ISO-8601 UTC (`2026-10-06T09:15:00Z`). Display formatting
  (IST, DD-MMM-YYYY) happens only in the frontend. Money and figures in `table` rows are
  JSON numbers with explicit `unit` metadata in `columns` (e.g. `"INR_crore"`, `"pct"`).

**Consequences: LOCKED:** a single `ProblemException` + exception handler in
`core/errors.py`; no ad-hoc `{"error": ...}` shapes; the frontend has one problem parser
and one SSE reducer.

**Alternatives:** JSON:API envelope (verbose for one client); a custom error shape
(non-standard).

---

### ADR-004: Naming conventions

**Status:** Accepted   **Drives:** maintainability, parallel-story consistency

**Decision:**
- **DB:** tables are plural snake_case (`users`, `audit_events`). Columns are snake_case.
  PK `id` is UUIDv7, FKs are `<entity>_id`, timestamps `created_at`/`updated_at`
  (`timestamptz`). Market-data tables are prefixed `md_`, mutual funds `mf_`, company data
  `co_`, knowledge `kb_`.
- **JSON wire:** **snake_case** (matching Pydantic and DB). The frontend uses the
  generated snake_case types as-is (no camelCase conversion layer).
- **REST paths:** plural nouns, kebab-case for multiword (`/admin/data-sources`,
  `/set-password-links`). Actions that aren't CRUD are sub-resources
  (`POST /admin/users/{id}/session-revocations`).
- **Python:** modules snake_case, classes PascalCase, settings env vars `APP_` prefix
  (`APP_LLM_BASE_URL`).
- **TypeScript/React:** components PascalCase files (`ChatPane.tsx`), hooks `useX.ts`,
  other files camelCase.
- **Tools (LLM-facing):** verb_noun snake_case (`get_stock_quote`,
  `get_price_history`, `search_documents`).
- **Audit event types:** `<domain>.<action>` (`chat.message_answered`,
  `guard.pii_blocked`, `auth.login_failed`, `admin.user_created`).
- **Problem codes:** snake_case nouns or past participles (`pii_detected`).

**Consequences: LOCKED** for all stories. A linter rule (ruff + ESLint naming plugin)
covers what it can.

---

### ADR-005: PostgreSQL 18 + pgvector; SQLAlchemy 2 async; Alembic; UUIDv7

**Status:** Accepted   **Drives:** FR-008, FR-018–033, NFR-014, NFR-017

**Context:** Relational data (users, conversations, audit), time series (EOD prices,
NAVs) and vector search for documents. One builder means one database to secure and
back up.

**Decision:**
- **PostgreSQL 18** is the single primary store, with the **pgvector** extension for
  document embeddings. No separate vector DB.
- Access is **SQLAlchemy 2.x async ORM** with `asyncpg`. Migrations are **Alembic only**:
  every schema change is a migration, and autogenerate output must be reviewed.
- IDs are **UUIDv7** generated by the DB (`DEFAULT uuidv7()`). Time is `timestamptz`,
  stored UTC.
- **Two DB roles:** `app_rw` (normal CRUD on app tables; `INSERT, SELECT` only on
  `audit_events`) and `app_migrator` (DDL). Audit immutability is also enforced by a
  trigger that rejects `UPDATE`/`DELETE` on `audit_events`.
- Transactions: one transaction per request via a session dependency. Writes inside a
  streaming turn are committed at defined points: the user message before generation,
  the assistant message + audit event at `done`.
- Consistency: strong (single node). Referential integrity is enforced with FKs;
  soft-delete only where the PRD requires it (`conversations.deleted_at`).

**Consequences: LOCKED:** no raw SQL outside repositories (except reviewed performance
queries); no other datastore for app data; Valkey (ADR-018) is never the source of truth.

**Alternatives:** Qdrant/Chroma for vectors (second system; unnecessary below ~1M
vectors; research Finding: pgvector first); SQLModel (thinner docs for async + complex
relationships).

**Revisit when:** document chunks exceed ~1M, or filtered vector search p95 exceeds 300 ms.

---

### ADR-006: Server-side sessions with opaque tokens; manual set-password links

**Status:** Accepted   **Drives:** FR-001, FR-002, FR-003, FR-038, NFR-007

**Context:** ASVS L2 auth/session controls; immediate revocation (≤ 5 s); no email;
`fastapi-users` is in maintenance mode (research v1.1).

**Decision:**
- **Passwords:** Argon2id via `pwdlib[argon2]` (library defaults or stronger). Policy:
  ≥ 12 characters, rejected if in a bundled list of the top 100k breached passwords
  (local file, so no external lookup), no composition rules.
- **Sessions:** on login, generate a 256-bit random token (`secrets.token_urlsafe(32)`),
  store `sha256(token)` in `sessions` with `user_id`, `created_at`, `last_seen_at`,
  `expires_at` (absolute 12 h), `revoked_at`, `ip`, `user_agent`. Idle timeout is 60
  min, checked against `last_seen_at`, which is updated at most once per minute.
  **Every authenticated request looks up the session in Postgres**, so revocation is
  immediate.
- **Cookie:** `__Host-session` with `HttpOnly; Secure; SameSite=Strict; Path=/`. It is
  never readable by JS.
- **CSRF:** double-submit. A random `__Host-csrf` cookie (readable by JS) is set at
  login, and every state-changing request must send header `X-CSRF-Token` with a
  matching value. A check against `Origin`/`Sec-Fetch-Site` is added for defence in depth.
- **Login protection:** after 5 consecutive failures the account is locked for 15 min
  (counter on `users`). Responses are generic, and response time is equalised by
  hashing a dummy password for unknown users.
- **Set-password links (FR-001):** the admin action creates a `password_tokens` row with
  `sha256(token)`, `purpose` (`invite`|`reset`), `expires_at` = now + 24 h, `used_at`.
  The raw link `https://<host>/set-password#t=<token>` is returned **once** in the API
  response and never logged. The token sits in the URL fragment, so it doesn't reach
  server logs or Referer headers. Redeeming it requires the token + username + new
  password. Success marks it used, invalidates the user's other tokens, revokes all of
  the user's sessions and activates the user.
- **Logout** sets `revoked_at` and clears the cookies. **Password change (FR-038)**
  requires the current password and revokes all other sessions.
- Session IDs rotate on login (a new token is issued; any pre-auth cookie is discarded).

**Consequences: LOCKED:**
- All auth goes through `auth.deps.current_user` / `require_role`.
- No JWTs anywhere.
- No auth state in localStorage.
- Raw tokens are never persisted or logged.

**Alternatives:** JWT access/refresh (revocation complexity); fastapi-users
(maintenance mode); Django (owner chose FastAPI).

**Revisit when:** MFA (addendum D-01) or SSO (D-02) is added. Both slot in at
`auth.service.authenticate()`.

---

### ADR-007: RBAC with two roles; deny by default; ownership checks

**Status:** Accepted   **Drives:** FR-001, FR-003, FR-005, FR-008, FR-028, FR-032

**Decision:**
- Roles are `admin` and `user` (a column on `users`). Every router under `/api/v1/admin/*`
  is mounted with `Depends(require_role("admin"))` at router level. Endpoint-level
  exemptions are not allowed.
- **Deny by default:** every endpoint except `/auth/login`, `/auth/set-password` and
  `/healthz` requires an authenticated session.
- **Ownership:** a user can access only conversations, messages, exports and feedback
  where `user_id = current_user.id`. Queries filter by owner in the repository signature
  (`get_conversation(user_id, conversation_id)`), not after fetching. Not found and not
  owned both return 404.
- An admin cannot demote or deactivate themselves (FR-005), and the system keeps at
  least one active admin.
- Authorization failures emit audit events `auth.forbidden`.

**Consequences: LOCKED:** repositories for user-owned data require `user_id` as a parameter.

---

### ADR-008: Frontend state: TanStack Query for server state; no global store

**Status:** Accepted   **Drives:** FR-006–011, admin FRs, NFR-011

**Decision:**
- **React 19 + TypeScript (strict) + Vite.** Routing: React Router (data router).
- **Server state:** TanStack Query only. Query keys are centralised in
  `frontend/src/api/queryKeys.ts`. Mutations invalidate by key; no manual cache surgery
  except the chat stream.
- **Chat stream state:** one hook `useChatStream(conversationId)` with a reducer over the
  ADR-003 SSE events (`delta` appends, `replace` swaps text, `table`/`sources` attach,
  `done` finalises and invalidates the conversation query).
- **No Redux/Zustand.** Local UI state uses `useState`. Cross-cutting state (current user,
  CSRF token) comes from a `/auth/me` query plus React context.
- **UI:** Tailwind CSS + shadcn/ui (Radix primitives, accessible by default for
  WCAG 2.1 AA). Tables use TanStack Table.
- **Markdown** answers render with `react-markdown` + `rehype-sanitize`. Raw HTML is
  never rendered. Links are allowed only to the app's own document viewer and known
  filing URLs.
- An API client wrapper adds `X-CSRF-Token`, parses problem+json, and redirects to
  login on 401.

**Consequences: LOCKED:** no other state libraries; no hand-written API types (ADR-002);
all tables in answers are rendered from `table` events, never parsed from markdown.

---

### ADR-009: LLM gateway to vLLM (OpenAI-compatible): chat + guard instances

**Status:** Accepted   **Drives:** NFR-001, NFR-002, NFR-004, NFR-008, NFR-015

**Context:** The A10 must host the chat model and the guard model. The app must not
depend on model specifics (NFR-015). Qwen3's thinking mode conflicts with tool-call
parsing (research v1.3).

**Decision:**
- Two vLLM OpenAI-compatible servers on the A10 (Docker image `vllm/vllm-openai`, pinned
  tag chosen in the spike):
  - **chat** (default `Qwen3-14B` AWQ/GPTQ INT4; final choice from STORY-003):
    `--gpu-memory-utilization 0.78`, `--max-model-len 16384`, `--kv-cache-dtype fp8`,
    `--enable-prefix-caching`, `--max-num-seqs 16`, `--enable-auto-tool-choice
    --tool-call-parser hermes`, served name `chat`.
  - **guard** (`Qwen3Guard-Gen-0.6B`): `--gpu-memory-utilization 0.12`,
    `--max-model-len 4096`, served name `guard`.
- **`llm` module = the only code that talks to model servers.** It exposes
  `chat_completion(...)`, `stream_chat(...)` and `structured(...)` (JSON-schema-guided
  output via vLLM structured outputs), plus `guard_classify(...)`. Model names, base
  URLs and per-model "profiles" (stop tokens, `chat_template_kwargs:
  {"enable_thinking": false}`, parser assumptions) come from configuration. Agent code
  refers only to the logical names `chat` and `guard`.
- Timeouts: connect 2 s. Guard request total 3 s. Chat first token 10 s, then 60 s
  overall. One retry on connection errors only, never on content.
- Model weights are pre-downloaded to a host volume. Containers run with
  `HF_HUB_OFFLINE=1` on an internal network with no egress (ADR-020).

**Consequences: LOCKED:**
- No module other than `llm` imports `openai` or makes HTTP calls to model servers.
- No model names are hard-coded.
- Thinking mode is off unless a profile enables it after the spike.

**Alternatives:**
- Foundry Local: single-device design; replaced by owner decision.
- One vLLM process serving both models: vLLM serves one model per process.
- Running the guard on CPU via transformers: too slow for the 500 ms budget.

**Revisit when:** a second GPU or a larger card arrives (move the guard model to it,
raise context and concurrency), or the spike shows the 0.78/0.12 split OOMs.

---

### ADR-010: Guardrail pipeline order; fail closed

**Status:** Accepted   **Drives:** FR-012–017, FR-034, FR-035, FR-037, NFR-003, NFR-005, NFR-006, NFR-013

**Context:** Defence in depth with deterministic checks first, within a guard budget of
≤ 500 ms (input) and ≤ 300 ms (output overhead, p95). The response scope policy (PRD
Levels 1–3) must be enforced consistently and be testable.

**Decision:** every user message passes these stages **in this order**, inside
`chat.service.handle_turn()`:

| # | Stage | Implementation | On failure / hit |
|---|-------|----------------|------------------|
| 0 | Auth, CSRF, **rate limit** (FR-037) | session dep; Valkey sliding window 10/min, 300/day (settings) | 401/403/429 problem; audit `guard.rate_limited` |
| 1 | **PII gate** (FR-012) | `guardrails.pii`: Presidio AnalyzerEngine with built-in `IN_PAN`, `IN_AADHAAR`, `IN_PASSPORT`, `IN_VOTER`, `EMAIL_ADDRESS`, `CREDIT_CARD` (Luhn) + custom recognisers (bank account with context words, IFSC+account, UPI ID, Indian mobile, NSDL/CDSL demat IDs; partially masked Aadhaar counts too). spaCy `en_core_web_sm` for PERSON, used only in combination with an identifier. Benign allow-list: NSE symbols, ISINs, scheme codes. | **422 `pii_detected`** with `detected_types`; message text discarded **before any persistence or logging**; audit `guard.pii_blocked` {types} |
| 2 | **Safety/jailbreak** (FR-014) | `guardrails.guard`: Qwen3Guard-Gen prompt moderation (`Safety` + `Categories`) | Unsafe/jailbreak → standard decline (assistant message, `finish_reason: declined`); audit `guard.input_unsafe` {categories} |
| 3 | **Scope router** (FR-013, FR-015, FR-034, FR-035) | `guardrails.scope`: one structured call to `chat` with JSON schema `{level: 1|2|3, category: enum[...], needs_tools: bool}` over the latest message + a short context summary. Categories mirror PRD policy rows (`advice`, `forecast`, `upsi`, `personal_advice`, `customer_data`, `off_topic`, `self_disclosure`, `in_scope`, `in_scope_care`). | Level 3 → **deterministic decline template** per category (2–3 alternatives; UPSI/forecast offer published facts); no agent call; audit `guard.declined` {category} |
| 4 | **Constrained agent** | ADR-012 | — |
| 5 | **Output gating while streaming** | ADR-011 (PII + figure grounding + advice/forecast phrase rules on held-back spans) | Span withheld → regenerate once, else `replace`; audit `guard.output_*` |
| 6 | **Final moderation** | Qwen3Guard response moderation on the full answer at stream end | Unsafe → `replace` with safe message; audit `guard.output_unsafe` |

**Fail closed (NFR-013):** if the PII engine, guard model or scope router errors or
times out (guard 3 s), the turn ends with **503 `safety_unavailable`** (before
streaming) or a `replace` + `done{finish_reason:"withheld"}` (during streaming). No
code path calls the agent without stages 1–3 succeeding. `/readyz` reports not-ready
when the guard model or PII engine is unhealthy, and the UI shows "Assistant
temporarily unavailable".

**Latency accounting:** stages 1–2 count against NFR-003's 500 ms. Stage 3 is a short
structured generation whose system prefix is prefix-cached, with a target ≤ 400 ms; it
counts against NFR-001's TTFT (3 s / 8 s), not against NFR-003.

**Blocked-attempt counts (FR-017)** come from `audit_events` by `event_type`/category.
An in-app alert is raised when a user exceeds N blocks per day (setting).

**Consequences: LOCKED:**
- The stage order is fixed.
- Stage 1 always runs on raw input, before anything is written to the DB, logs or traces.
  The logging middleware must never log request bodies of the chat endpoint.
- Declines use templates from `guardrails/templates/`, not free model text.
- New scope categories require a PRD policy change.

**Alternatives:**
- Masking instead of blocking: rejected by PRD (Q3).
- LLM Guard: archived.
- NeMo Guardrails: 100–800 ms overhead plus Colang learning curve.
- Relying on the system prompt only: not testable or deterministic enough for a
  regulated firm.

**Revisit when:** PII recall is below 99% or false positives above 5% on the suite
(tune recognisers), or router accuracy on the scope suite is below 95% (consider a
fine-tuned small classifier).

---

### ADR-011: Streaming output gating, figure grounding and tool-rendered tables

**Status:** Accepted   **Drives:** FR-006, FR-009, FR-015, FR-023, FR-024, FR-030, FR-036, NFR-001, NFR-003, NFR-010

**Context:** Buffering whole answers for checking would break NFR-001. Streaming raw
model text could leak PII or ungrounded figures before any check runs. Tables of
figures are the most error-prone thing for a model to copy.

**Decision:**
1. **Tables come from tools, not the model.** When a tool returns tabular data, the
   orchestrator emits a `table` SSE event built directly from the tool result (with
   source, as-of, units, notes) and stores it in `message_tables`. The model is
   instructed to write narrative only and to refer to the table ("see the table
   above"). Exports (FR-024) are generated from `message_tables`, so exported figures
   are exactly the tool figures.
2. **Hold-back gating** (`guardrails.output_gate`): streamed text is tokenised into
   *spans*.
   - Spans without digits or `@` are released immediately, after a rolling regex check
     for advice/forecast phrases from a curated list.
   - Any span containing a digit or `@` is **held** until it ends (whitespace or
     punctuation outside a number), then checked:
     - (a) PII patterns (same recognisers as stage 1, regex subset);
     - (b) **figure grounding**.
   - Passing spans are released. On failure, nothing more is released, the generation is
     cancelled, and the turn is **regenerated once** with a corrective instruction. If
     that fails, a `replace` event sends an "I couldn't verify that figure" or safe
     fallback message.
3. **Figure grounding** (FR-036):
   - **Grounding set:** every number in this turn's tool results, the calculator outputs
     (FR-023), the cited document chunks, the user's message and the current date.
   - **Normalisation:** both sides are converted to a canonical value: ₹/Rs, commas,
     lakh/crore/million/billion, %, bps, and dates to ISO.
   - **Match rule:** a figure matches if its canonical value equals a grounding value
     within the display precision (rounding to the shown decimals).
   - **Exempt:** small counts ≤ 12 written without units, list ordinals, and period
     labels that match the request (e.g. "1 year", "Q2 FY26").
   - Each grounding failure is audited (`guard.figure_ungrounded`) and traced.
4. **Calculations** (FR-023) are a tool (`calculate_returns`), implemented with `Decimal`.
   The model never computes figures itself. The system prompt and grounding together
   enforce this.
5. **Sources** (FR-009): the orchestrator, not the model, emits the `sources` event from
   the tools and chunks actually used. The model's text cites by short label only.

**Consequences: LOCKED:**
- No component may forward model tokens to the client except through `output_gate`.
- Tool results that are tabular must return a `TableResult` (columns with units, rows,
  source, as_of).
- Exports must read from `message_tables`.

**Alternatives:** buffer the full answer (TTFT too high); stream unchecked and redact
afterwards (by then the user has seen the content); have the model generate tables
(transcription errors).

**Revisit when:** the grounding false-reject rate is above 3% on the eval set (tune
exemptions), or users report noticeable hold-back stutter.

---

### ADR-012: Agent and tool contract

**Status:** Accepted   **Drives:** FR-007, FR-014, FR-016, FR-018–023, FR-027, FR-029, FR-030

**Decision:**
- **PydanticAI** agent in `assistant/agent.py` using the `llm` gateway (OpenAI-compatible
  provider). Output is streamed text. Tool definitions are typed Python functions whose
  arguments are Pydantic models with explicit ranges (e.g. `period` enum `1W…5Y`, date
  bounds ≥ 2000-01-01 and ≤ today, `symbols` max 3 items, `limit` ≤ 10).
- **Tool allow-list (V1):**

  | Tool | FRs |
  |---|---|
  | `resolve_security(query)` | FR-018 |
  | `get_stock_quote(symbol)` | FR-018 |
  | `get_price_history(symbol, period\|from,to)` | FR-019 |
  | `get_index_performance(index, period)` | FR-020 |
  | `search_mutual_funds(query)` | FR-021 |
  | `get_mf_nav_history(scheme_code, period)` | FR-021 |
  | `compare_instruments(items, period)` | FR-022 |
  | `calculate_returns(series_ref\|values, method)` | FR-023 |
  | `get_company_results(symbol, periods≤4)` | FR-027 |
  | `get_corporate_actions(symbol)` | FR-027 |
  | `get_announcements(symbol, limit≤10)` | FR-027 |
  | `search_documents(query, company?)` | FR-029 |

  The registry is the only source of tools. Unknown tool names or invalid arguments are
  rejected and audited (`guard.tool_rejected`).
- **Tools are read-only.** They read only the local store (marketdata/knowledge
  repositories). **Tools never perform network I/O.** Upstream fetches happen in the
  provider layer (ADR-013), which tools call through a service that may trigger a
  rate-limited cache-miss fetch.
- **Tool output is data:** results are serialised as compact JSON inside a
  `<tool_result>` wrapper. Free text from documents and announcements was
  injection-scanned at ingestion (ADR-013/014), and is truncated (per-field cap 1,500
  chars) and labelled as quoted content.
- **Limits:** ≤ 4 tool calls per turn, ≤ 2 sequential rounds. Exceeding the limit ends
  with the "I couldn't complete that" template.
- **System prompt:** a versioned file `assistant/prompts/system_v{n}.md` that encodes the
  PRD Response Scope & Style Policy (levels, style rules, "never compute, never invent
  figures, say when you don't know"). The prompt version is recorded on every
  assistant message and audit event.
- **"Don't know" (FR-030):** if no tool or chunk returned relevant data, the model must
  use the "no source" template. This is also backed by grounding (ADR-011).

**Consequences: LOCKED:** new capabilities are added only as new allow-listed tools
with typed args, a story and eval cases. No tool may call `llm` (no nested model calls),
make network calls or write data.

**Alternatives:** LangGraph (heavier; revisit for multi-step workflows); raw OpenAI SDK
loop (more hand-rolled code).

---

### ADR-013: Market-data provider abstraction, stored copies, refresh and upstream discipline

**Status:** Accepted   **Drives:** FR-018–022, FR-025, FR-026, FR-027, NFR-015, NFR-018

**Decision:**
- `marketdata.providers.base.MarketDataProvider` is a `Protocol` with typed methods:
  `list_securities`, `eod_prices(symbol, from, to)`, `index_eod(index, from, to)`,
  `quote_snapshot(symbols)`, `company_results`, `corporate_actions`, `announcements`.
  `MutualFundProvider` has `list_schemes` and `nav_history(code, from, to)`.
  V1 adapters: `NselibProvider` and `MftoolProvider`. Post-V1 adapters (addendum
  D-04/D-09) are `AceProvider` and `InternalDbProvider`; for internal DB this means a
  read-only account and approved views only.
- **Stored copies** in Postgres (`md_*`, `mf_*`, `co_*` tables, §5). Tools read only
  from the store.
- **Refresh schedule (worker, IST):**

  | Data | When |
  |---|---|
  | Securities master | daily 07:00 |
  | EOD prices + indices | trading days 18:30 (retry 20:00) |
  | Quote snapshots for a **watchlist** (IIFL, IIFL Finance, configured indices and symbols queried in the last 7 days, cap 100) | every 15 min, 09:30–15:45 on trading days |
  | MF scheme list | daily 07:15 |
  | MF NAVs | 23:30 and 07:30 |
  | Results, actions, announcements | for watchlist companies, 3× per trading day |

  Holidays come from the provider's calendar.
- **Cache miss:** a symbol not in the store triggers at most one on-demand fetch through
  the provider, subject to the per-source token bucket (default 30 req/min). If the
  bucket is empty or the source is disabled, the tool returns "data temporarily
  unavailable" (FR-025).
- **Kill switch (FR-026):** `data_sources.enabled` is checked before every upstream call
  (cached in Valkey for 30 s, so a toggle takes effect in under a minute). Toggles are
  audited.
- **Staleness:** every stored row carries `as_of` and `fetched_at`. Tools return them.
  The orchestrator labels data older than its expected freshness ("as of 03-Oct-2026,
  latest available").
- **Upstream text** (announcement subjects and bodies) is PII-scanned and guard-scanned
  at ingestion. Items with flags are stored with `flagged=true` and excluded from tool
  results.
- **Metrics (NFR-018):** counters for stored-vs-upstream answers, upstream calls per
  source per minute, and refresh success/failure.

**Consequences: LOCKED:**
- Only `marketdata.providers.*` imports `nselib`, `mftool` or any data SDK.
- Outbound HTTP goes through the egress proxy (ADR-020).
- Adding a source = a new adapter + a `data_sources` row + config. Tools, chat and UI
  stay unchanged (NFR-015 demo: a `StubProvider`).

**Alternatives:** fetching live per question (violates NFR-018, slow); OpenBB
(company wound down; weak India coverage).

**Revisit when:** the ACE subscription or internal DB access is approved (Q10).

---

### ADR-014: Knowledge library: ingestion scanning, hybrid retrieval, page citations

**Status:** Accepted   **Drives:** FR-012, FR-014, FR-028, FR-029, FR-030

**Decision:**
- **Upload** (admin, PDF ≤ 50 MB) is stored on a host volume
  (`/data/documents/{id}.pdf`, not web-served directly), with a `kb_documents` row
  `status=pending`, and an ingestion job is queued (ADR-017).
- **Ingestion job:**
  1. Extract text per page with `pypdf` (BSD). Pages with no extractable text are
     flagged (no OCR in V1).
  2. **PII scan per page** under the document policy (FR-012, confirmed 2026-10-07):
     - High-risk identifiers (PAN, Aadhaar, bank/demat account, card, UPI, passport,
       voter ID) → **reject** the document (`status=rejected`, reason = types + page
       numbers).
     - Contact details (emails, phone numbers) are **masked** in the indexed text.
  3. **Injection scan:** each chunk goes through the guard model. Flagged chunks are
     excluded and the admin is notified in-app.
  4. **Chunking:** ~400 tokens with 50 overlap, never crossing pages, keeping
     `page_number`.
  5. **Embeddings** on CPU with **`BAAI/bge-small-en-v1.5`** (384-dim, MIT) via
     `fastembed` (ONNX, Apache-2.0); the model is bundled offline. Store in
     `kb_chunks.embedding vector(384)` with an HNSW index, plus `tsvector` for keyword
     search.
  6. `status=ready`. Deleting a document sets `deleted_at`, and retrieval excludes it
     immediately (≤ 5 min requirement easily met).
- **Retrieval** (`search_documents`): hybrid. The top 20 by cosine similarity and the
  top 20 by `ts_rank` are fused by Reciprocal Rank Fusion, and the top 5 chunks
  (≤ ~1,800 tokens) are returned with `{document_title, page_number, period}`.
- **Citations:** `sources` items are `{kind:"document", label: title, page}`. The
  document viewer endpoint serves the original PDF page to authenticated users.

**Consequences: LOCKED:** the embedding model and dimension are fixed (changing them
means re-embedding through a migration job). Retrieval goes only through
`knowledge.service.search`.

**Alternatives:** GPU embedding model served by vLLM (needs VRAM we don't have);
PyMuPDF (AGPL licence); dedicated vector DB (ADR-005).

**Revisit when:** scanned PDFs need OCR, or the library exceeds ~5,000 documents.

---

### ADR-015: Conversation context management within ~8k tokens

**Status:** Accepted   **Drives:** FR-007, NFR-004

**Decision:** `assistant.context.build()` assembles a working-context budget of
**8,192 tokens** (counted with the served model's tokenizer, which is bundled offline):

| Part | Budget |
|---|---|
| System prompt (versioned; prefix-cached) | ≤ 1,500 |
| Tool schemas | ≤ 1,000 |
| Rolling summary of older turns | ≤ 600 |
| Most recent turns, verbatim, newest first, as many as fit | ≤ 2,500 |
| Current turn: user message + tool results (tables summarised to the first 30 rows + aggregates) + retrieved chunks | ≤ 2,600 |

When history exceeds its budget, the oldest turns are folded into the rolling summary by
one `chat` call. The summary is stored on `conversations.summary` with `summary_upto_message_id`.
The answer itself is capped at `max_tokens=700`. `--max-model-len 16384` leaves headroom
for the tool round-trip.

**Consequences: LOCKED:** no component sends full conversation history to the model;
only `context.build()` constructs prompts.

---

### ADR-016: Hash-chained, append-only audit trail

**Status:** Accepted   **Drives:** FR-017, FR-031, FR-032, FR-033, NFR-016, NFR-017

**Decision:**
- Table `audit_events`, written only through `audit.writer.record(event)`. Each row:
  - `id`, `seq` (bigint, gap-free via a single-writer advisory lock), `occurred_at`
  - `correlation_id`, `actor_user_id`, `event_type`, `target_type`/`target_id`
  - `payload` (JSONB, schema per event type)
  - `prev_hash`, `hash = sha256(prev_hash || canonical_json(row without hash))`
- **Payload rules:** no raw PII ever. Blocked messages record only detected types.
  Chat events include the stored user message (already passed the PII gate), the final
  response, tools called with arguments, sources, model and prompt version, guard
  verdicts and per-stage latency.
- Immutability: the `app_rw` role has no UPDATE/DELETE, and a trigger rejects them
  (ADR-005). A daily `audit.verify` job recomputes the chain and raises an admin alert
  on mismatch.
- Retention: the table is partitioned by month. A retention job (default 60 months)
  drops whole partitions after first writing a **chain anchor** (last hash of the
  dropped partition) to `audit_anchors`.
- Admin search and export (FR-032) query by user, date range and type. Exports are
  themselves audited.

**Consequences: LOCKED:**
- Every FR that says "audited" calls `audit.writer.record` with a registered `event_type`.
- Event payload schemas are Pydantic models in `audit/events.py`.

**Alternatives:** an external WORM store or immudb (another system to run); a plain
table (not tamper-evident).

---

### ADR-017: Background jobs: worker process, APScheduler + Postgres job table

**Status:** Accepted   **Drives:** FR-025, FR-028, NFR-014, NFR-017

**Decision:**
- One `worker` container from the same image runs **APScheduler 3.x** (`AsyncIOScheduler`)
  for cron jobs: data refresh (ADR-013), audit verification and retention, backups
  trigger, session cleanup.
- On-demand jobs (document ingestion, audit export) go in a Postgres `jobs` table polled
  with `SELECT … FOR UPDATE SKIP LOCKED`. Fields: type, payload, status, attempts
  (max 3, exponential backoff), timestamps, error.
- Jobs are idempotent: refresh jobs upsert by natural key, and ingestion is keyed by
  document id.

**Consequences: LOCKED:** no Celery or arq; long work never runs inside API request
handlers.

**Alternatives:** Celery (broker + more ops); arq (Valkey-coupled, smaller ecosystem);
cron in the container (no retries or visibility).

---

### ADR-018: Valkey for rate limits, upstream token buckets and short-lived cache

**Status:** Accepted   **Drives:** FR-026, FR-037, NFR-018

**Decision:**
- **Valkey 8** (BSD-licensed Redis fork) is shared state across API workers and the
  worker. It holds:
  - per-user sliding-window rate limits (FR-037);
  - per-source upstream token buckets (NFR-018);
  - data-source enabled-flag cache (30 s TTL);
  - quote-response micro-cache (60 s).
- Valkey is **never** the source of truth and stores no PII. If Valkey is unavailable,
  rate limiting fails closed: chat returns 503 `rate_limit_unavailable`.

**Alternatives:** in-process limits (incorrect with multiple workers); Postgres counters
(write amplification on every request).

---

### ADR-019: Observability: structured logs, correlation IDs, OpenTelemetry → Langfuse

**Status:** Accepted   **Drives:** NFR-012, NFR-016, NFR-001–004 measurement

**Decision:**
- **Correlation ID:** generated by API middleware (`X-Correlation-ID`, UUIDv7), carried
  through the turn, tool calls, `llm` calls, audit events and job payloads.
- **Logs:** `structlog` JSON to stdout, collected by Docker. Fields: timestamp, level,
  correlation_id, user_id, module, event, latency_ms. **Bodies of chat requests and
  responses are never logged.** A log-scrubbing processor runs the PII regex subset as
  a final safety net.
- **Traces:** OpenTelemetry SDK spans for `turn`, `pii_gate`, `guard`, `scope_router`,
  `agent`, `tool:*`, `output_gate`, `final_moderation`, exported via OTLP to
  **self-hosted Langfuse** (MIT). Traces contain only post-PII-gate content. Langfuse
  also stores eval runs (ADR-022).
- **Metrics:** Prometheus-format `/metrics` on the API (internal network only): TTFT,
  tokens/s, stage latency, guard verdict counts, upstream call rates; plus vLLM's own
  `/metrics`.
- **Health:** `/healthz` (liveness) and `/readyz` (DB, Valkey, vLLM chat and guard, PII
  engine). An external probe checks `/readyz` every minute (NFR-012).
- Langfuse runs under a Compose **profile** `observability`, so it can be disabled if
  host resources are tight (§12 AQ-3). Logs and metrics remain.

**Consequences: LOCKED:** use the shared logger and tracer helpers from `core/obs.py`;
no `print`; every new external call gets a span.

---

### ADR-020: Deployment and network isolation

**Status:** Accepted   **Drives:** NFR-008, NFR-009, NFR-012, NFR-013, NFR-019

**Decision:** Docker Compose on the A10 host (§10). Networks:

| Network | Who is on it | Egress |
|---|---|---|
| `edge` | Caddy only, ports 443 (and 80 → 443) | — |
| `app` (internal) | caddy, api, worker, postgres, valkey, langfuse | none |
| `inference` (internal) | api, worker, vllm-chat, vllm-guard | none |
| `egress` | egress-proxy only | outbound |

- **Egress proxy** (Squid or tinyproxy) with a **domain allow-list** in
  `deploy/egress-allowlist.txt` (initially NSE and AMFI hosts used by nselib and
  mftool). The provider adapters in api and worker reach the internet only via
  `HTTPS_PROXY`. Nothing else has a route out.
- **TLS:** Caddy terminates TLS 1.2+ with a certificate from IIFL's internal CA (or
  Caddy's internal CA during the pilot), sets HSTS and a strict CSP
  (`default-src 'self'`), and serves the built SPA as static files. `/api/*` is proxied
  to api.
- **Models:** pre-downloaded into `/models` on the host (a runbook step done once, with
  a temporary egress exception). vLLM containers mount it read-only, with
  `HF_HUB_OFFLINE=1`.
- **Restart policy:** `unless-stopped` with health checks, so crashed components
  restart within the 2-minute budget (NFR-013).
- **Backups:** nightly `pg_dump` (custom format) + document volume rsync to an
  **off-host** target (§12 AQ-4), 14 daily + 12 monthly kept. A quarterly restore drill
  (NFR-014: RPO 24 h, RTO 4 h).

**Consequences: LOCKED:** no service publishes ports except Caddy; any new outbound
destination is an allow-list change reviewed in the decision log.

---

### ADR-021: Configuration and secrets

**Status:** Accepted   **Drives:** NFR-009, NFR-015

**Decision:**
- `pydantic-settings` classes in `core/config.py`, read from env vars (`APP_*`).
- Secrets come from Docker Compose **secrets** (files under `/run/secrets`): DB
  passwords, session pepper, Langfuse keys. `.env` holds non-secrets only.
- `gitleaks` runs in CI and pre-commit.
- Runtime-tunable business settings live in the `app_settings` table and are editable
  by admins: rate limits, starter prompts, block-alert threshold, watchlist, retention
  months.

**Consequences: LOCKED:** no secrets in code, images or `.env`; no settings read
directly from `os.environ` outside `core/config.py`.

---

### ADR-022: Test and evaluation strategy as release gates

**Status:** Accepted   **Drives:** NFR-005, NFR-006, NFR-010, FR-013, FR-034, FR-035, FR-036, NFR-001–004

**Decision (strategy only; execution is owned by the dev tooling):**
- **Unit tests** (pytest) per module. Required for `guardrails` (each recogniser, the
  grounding normaliser, output gate span logic), `auth` and `marketdata` calculations.
- **Integration tests** against a real Postgres + Valkey (Compose test profile), with a
  **fake LLM server** (an OpenAI-compatible stub returning scripted streams and tool
  calls) so tests are deterministic.
- **Suites in `evals/`** (YAML cases), run against a deployed stack with real models:
  - `pii/` ≥ 200 positive + ≥ 200 benign (NFR-005)
  - `redteam/` ≥ 100 (NFR-006)
  - `scope/` 60 answer + 60 decline (FR-013)
  - `upsi/` 30 (FR-034)
  - `forecast/` 30 (FR-035)
  - `grounding/` 30 (FR-036)
  - `eval/` ≥ 60 quality cases incl. multi-turn and unanswerable (NFR-010)

  The runner writes a report and pushes results to Langfuse.
- **Load test:** 10 virtual users, 30-minute soak with mixed eval prompts. It measures
  TTFT, tokens/s, stage latencies, errors and GPU memory (NFR-001–004).
- **Release gate:** a release (or a model/prompt/guard change) is blocked unless all
  suites meet their PRD thresholds. Results are linked in the decision log.
- **Frontend:** Vitest + Testing Library for the chat reducer and components;
  Playwright smoke tests for login → ask → table → export; automated axe scan of core
  screens (NFR-011).

**Consequences: LOCKED:** every story touching guardrails, tools or prompts adds or
updates suite cases. No model, prompt or guard change ships without a suite run.

---

## 4. Component Design

### Component Overview

```
Browser (React SPA)
   │  HTTPS (cookie session, CSRF header)        SSE stream for chat
   ▼
Caddy (TLS, static SPA, CSP) ──► API (FastAPI, uvicorn ×2 workers)
                                   ├─ api/ middleware: correlation-id, CSRF, rate limit, auth
                                   ├─ auth/ ── sessions, links, RBAC
                                   ├─ chat/ ── turn orchestration ──► guardrails/ (pii, guard, scope, output_gate, grounding)
                                   │                               └► assistant/ (agent, context, tools) ──► marketdata/ · knowledge/
                                   │                                                          └──────────► llm/ ──► vLLM chat / vLLM guard (A10)
                                   ├─ admin/ ─ users, documents, data sources, audit, overview, settings
                                   ├─ exports/ ─ CSV/XLSX from message_tables
                                   └─ audit/ ── hash-chained writer
Worker (same image) ── APScheduler + jobs table: refresh, ingestion, audit verify/retention, backups
   └─ marketdata providers ──► egress proxy (allow-list) ──► NSE / AMFI
PostgreSQL 18 + pgvector · Valkey 8 · Langfuse (optional profile)
```

### Turn sequence (FR-006, ADR-010/011)
1. `POST /conversations/{id}/messages` → auth, CSRF, rate limit (stage 0).
2. PII gate on the raw text (stage 1). On a hit: 422, nothing stored.
3. Guard moderation (stage 2) and scope router (stage 3). Level 3 → template decline.
4. Persist the user message. Open the SSE stream and emit `meta`, then `status`.
5. `context.build()` → agent run (streaming). Tool calls emit `status:fetching` and then `table`.
6. Tokens → `output_gate` → `delta` (with hold-back and grounding). On failure: one regeneration, else `replace`.
7. Final moderation of the full text. If unsafe: `replace`.
8. Emit `sources`. Persist the assistant message + tables. Audit event. Emit `done`.

### Component: Auth (`auth/`)
**Responsibility:** identities, credentials, sessions, set-password links, role checks.
**Interfaces Provided:** `/api/v1/auth/*`; `current_user`, `require_role` dependencies; `auth.service` for admin user ops.
**Interfaces Required:** core db, audit writer.
**Data Owned:** `users`, `sessions`, `password_tokens`.
**ADRs:** 006, 007.   **NFRs:** NFR-007 (ASVS L2 controls), NFR-009.

### Component: Chat (`chat/`)
**Responsibility:** conversations, messages, ratings, starter prompts, and turn orchestration including SSE.
**Interfaces Provided:** `/api/v1/conversations*`, `/api/v1/messages/{id}/feedback`, `/api/v1/starter-prompts`.
**Interfaces Required:** guardrails, assistant, audit, exports (table storage).
**Data Owned:** `conversations`, `messages`, `message_tables`, `message_sources`, `feedback`.
**ADRs:** 002, 003, 010, 011, 015.   **NFRs:** NFR-001/002 (streaming, prefix caching), NFR-011.

### Component: Guardrails (`guardrails/`)
**Responsibility:** PII gate, guard moderation, scope router, output gate, figure grounding, decline templates.
**Interfaces Provided:** `check_input(text, ctx) -> InputVerdict`, `OutputGate` (async stream transformer), `final_check(text)`.
**Interfaces Required:** llm (guard, chat structured), Presidio/spaCy (in-process).
**Data Owned:** none (verdicts go to audit); templates and recogniser config are files.
**ADRs:** 010, 011.   **NFRs:** NFR-003, NFR-005, NFR-006, NFR-013.

### Component: Assistant (`assistant/`)
**Responsibility:** agent definition, system prompt versions, tool registry, context building.
**Interfaces Provided:** `run_turn(ctx) -> AsyncIterator[AgentEvent]`.
**Interfaces Required:** llm, marketdata service, knowledge service.
**Data Owned:** prompt files; `conversations.summary` (written via chat repository).
**ADRs:** 012, 015.   **NFRs:** NFR-010, NFR-015.

### Component: LLM gateway (`llm/`)
**Responsibility:** the only client of the vLLM servers; model profiles; timeouts; tracing.
**Interfaces Provided:** `stream_chat`, `chat_completion`, `structured`, `guard_classify`.
**Data Owned:** none.   **ADRs:** 009.   **NFRs:** NFR-001, NFR-004, NFR-015.

### Component: Market data (`marketdata/`)
**Responsibility:** provider adapters, stored copies, refresh jobs, upstream discipline, kill switch.
**Interfaces Provided:** `marketdata.service` (read API used by tools), refresh job functions, admin data-source ops.
**Interfaces Required:** egress proxy, Valkey (buckets), guardrails (ingestion scans), audit.
**Data Owned:** `md_*`, `mf_*`, `co_*`, `data_sources`, `data_refresh_runs`.
**ADRs:** 013, 017, 018.   **NFRs:** NFR-015, NFR-018.

### Component: Knowledge (`knowledge/`)
**Responsibility:** document upload storage, ingestion pipeline, embeddings, hybrid retrieval, page serving.
**Interfaces Provided:** `knowledge.service.search`, admin document ops, `/api/v1/documents/{id}/pages/{n}`.
**Data Owned:** `kb_documents`, `kb_chunks`, files under `/data/documents`.
**ADRs:** 014, 017.   **NFRs:** NFR-010.

### Component: Audit (`audit/`)
**Responsibility:** tamper-evident event recording, verification, retention, search and export.
**Data Owned:** `audit_events`, `audit_anchors`.   **ADRs:** 016.   **NFRs:** NFR-017, NFR-016.

### Component: Admin (`admin/`)
**Responsibility:** admin-only routers: users, documents, data sources, audit search/export, overview, settings, alerts.
**Interfaces Required:** auth, knowledge, marketdata, audit, settings services.
**Data Owned:** `app_settings`, `admin_alerts`.   **ADRs:** 007.   **NFRs:** NFR-011.

### Component: Exports (`exports/`)
**Responsibility:** CSV/XLSX files from `message_tables` with source, as-of and an "Internal use only" note (`openpyxl`).
**ADRs:** 011.   **FRs:** FR-024, FR-032 (audit CSV).

### Component: Worker (`worker/`)
**Responsibility:** runs the scheduler and job runner; hosts no HTTP API except `/healthz`.
**ADRs:** 017.

### Component: Frontend (`frontend/`)
**Responsibility:** login and set-password pages, chat UI (stream reducer, tables, sources, export, ratings, starter prompts, history), admin console.
**ADRs:** 002, 003, 004, 008.   **NFRs:** NFR-011 (WCAG AA via Radix/shadcn, ≥ 1280 px desktop layout).

---

## 5. Data Model

> Governed by ADR-005 and ADR-004. All tables have `id uuid PK DEFAULT uuidv7()`,
> `created_at`, `updated_at timestamptz` unless noted. "→" means FK.

### Identity & access
- **users:** `username` (citext, unique), `email` (citext, unique), `role` (`admin`|`user`), `status` (`invited`|`active`|`locked`|`deactivated`), `password_hash` (nullable until set), `failed_login_count`, `locked_until`, `last_login_at`.
- **sessions:** `user_id`→users, `token_hash` (bytea, unique), `expires_at`, `last_seen_at`, `revoked_at`, `ip` (inet), `user_agent`. Index `(user_id) WHERE revoked_at IS NULL`.
- **password_tokens:** `user_id`→users, `token_hash` (unique), `purpose` (`invite`|`reset`), `expires_at`, `used_at`, `created_by`→users.

### Conversations
- **conversations:** `user_id`→users, `title`, `summary`, `summary_upto_message_id`, `deleted_at`. Index `(user_id, updated_at DESC) WHERE deleted_at IS NULL`.
- **messages:** `conversation_id`→conversations, `role` (`user`|`assistant`), `content` (text; post-PII-gate only), `status` (`complete`|`declined`|`withheld`|`cancelled`|`error`), `decline_category`, `model_name`, `prompt_version`, `correlation_id`, `latency_ms` (jsonb per stage), `token_usage` (jsonb).
- **message_tables:** `message_id`→messages, `title`, `columns` (jsonb: name, label, unit), `rows` (jsonb), `source_key`, `as_of`, `notes`.
- **message_sources:** `message_id`→messages, `kind` (`data`|`document`), `label`, `source_key`, `document_id`→kb_documents (nullable), `page_number`, `as_of`.
- **feedback:** `message_id`→messages (unique per user), `user_id`, `rating` (`up`|`down`), `comment` (PII-gated).

### Market, fund & company data (stored copies)
- **data_sources:** `key` (unique, e.g. `nse`, `amfi`), `provider_class`, `enabled`, `rate_limit_per_min`, `last_success_at`, `last_error_at`, `last_error`.
- **data_refresh_runs:** `source_key`, `job`, `started_at`, `finished_at`, `status`, `rows_upserted`, `error`.
- **md_securities:** `symbol` (unique), `isin`, `name`, `series`, `listing_status`, `aliases` (text[]; for name resolution, trigram index).
- **md_eod_prices:** PK (`symbol`, `trade_date`); `open`, `high`, `low`, `close`, `prev_close`, `volume`, `source_key`, `fetched_at`. `numeric(18,4)`.
- **md_quote_snapshots:** `symbol`, `last_price`, `change`, `change_pct`, `volume`, `as_of`, `source_key`, `fetched_at`. Latest-per-symbol view.
- **md_indices** / **md_index_eod:** `index_code`, `name`; PK (`index_code`, `trade_date`), OHLC, `source_key`.
- **mf_schemes:** `scheme_code` (unique), `name`, `amc`, `category`, `plan` (`direct`|`regular`), `option` (`growth`|`idcw`), `isin_growth`, `isin_reinvest`, `active`; trigram index on name.
- **mf_navs:** PK (`scheme_code`, `nav_date`), `nav numeric(18,4)`, `source_key`, `fetched_at`.
- **co_results:** `symbol`, `period_end`, `period_type` (`quarterly`|`annual`), `consolidated` (bool), `total_income`, `net_profit`, `eps`, `filing_date`, `filing_ref`, `raw` (jsonb), `source_key`. Unique (`symbol`, `period_end`, `period_type`, `consolidated`).
- **co_corporate_actions:** `symbol`, `action_type`, `ex_date`, `record_date`, `details`, `source_key`.
- **co_announcements:** `symbol`, `announced_at`, `subject`, `body_excerpt` (≤ 1,500 chars), `filing_ref`, `flagged` (bool), `source_key`.

### Knowledge
- **kb_documents:** `title`, `company` (`IIFL`|`IIFL_FINANCE`|other), `doc_type`, `period_label`, `file_path`, `sha256` (unique), `page_count`, `status` (`pending`|`processing`|`ready`|`rejected`|`failed`), `status_reason`, `uploaded_by`→users, `deleted_at`.
- **kb_chunks:** `document_id`→kb_documents, `page_number`, `chunk_index`, `text` (contact details masked), `tsv` (tsvector, GIN), `embedding vector(384)` (HNSW, cosine), `flagged` (bool).

### Platform
- **audit_events** (partitioned monthly; no `updated_at`): `seq` (bigint unique), `occurred_at`, `correlation_id`, `actor_user_id`, `event_type`, `target_type`, `target_id`, `payload` (jsonb), `prev_hash`, `hash`. Indexes `(actor_user_id, occurred_at)`, `(event_type, occurred_at)`.
- **audit_anchors:** `partition_name`, `last_seq`, `last_hash`, `dropped_at`.
- **jobs:** `type`, `payload` (jsonb), `status`, `attempts`, `run_after`, `last_error`, `correlation_id`.
- **app_settings:** `key` (unique), `value` (jsonb), `updated_by`. Holds starter prompts, rate limits, block alert threshold, watchlist, retention months, session timeouts.
- **admin_alerts:** `kind`, `payload`, `acknowledged_by`, `acknowledged_at`.
- **exports:** `user_id`, `message_table_id`→message_tables, `format` (`csv`|`xlsx`), `file_path`, `expires_at` (24 h).

### Storage Strategy
- **Primary store:** PostgreSQL 18 + pgvector (ADR-005).
- **Cache / shared counters:** Valkey 8 (ADR-018). Not a source of truth.
- **Files:** host volume `/data/documents` (originals) and `/data/exports` (24 h TTL, cleaned by the worker).
- **Retention and backup:**
  - Audit: 60 months by default, dropped by partition with anchors.
  - Conversations: retained with audit (soft delete hides them from the user).
  - Exports: 24 h.
  - Nightly off-host backup (ADR-020).

---

## 6. API Specifications

**Protocol:** REST/JSON, SSE for chat (ADR-002).   **AuthN:** session cookie + CSRF header (ADR-006).   **Errors:** RFC 9457 (ADR-003).   **Versioning:** `/api/v1`.

### Auth (public endpoints marked †)
| Method & path | Purpose | Notes |
|---|---|---|
| `POST /api/v1/auth/login` † | Username + password → sets `__Host-session`, `__Host-csrf` | 401 generic; lockout per FR-002 |
| `POST /api/v1/auth/logout` | Revoke current session | 204 |
| `GET /api/v1/auth/me` | Current user {id, username, role} + csrf token | 401 if no session |
| `POST /api/v1/auth/set-password` † | {token, username, new_password} | 204; 400 `token_invalid` (generic); revokes sessions |
| `POST /api/v1/auth/change-password` | {current_password, new_password} | 204; revokes other sessions |

### Chat
| Method & path | Purpose | Notes |
|---|---|---|
| `GET /api/v1/conversations` | List own conversations | cursor pagination |
| `POST /api/v1/conversations` | Create | 201 |
| `GET /api/v1/conversations/{id}` | Conversation + messages + tables + sources | 404 if not owner |
| `PATCH /api/v1/conversations/{id}` | Rename | |
| `DELETE /api/v1/conversations/{id}` | Hide from view (soft delete) | 204; audited |
| `POST /api/v1/conversations/{id}/messages` | Send message → **SSE stream** | 422 `pii_detected`, 429 `rate_limited`, 503 `safety_unavailable` before stream; NFR-001 |
| `PUT /api/v1/messages/{id}/feedback` | {rating, comment?} | comment PII-gated |
| `GET /api/v1/starter-prompts` | List | from app_settings |
| `POST /api/v1/exports` | {message_table_id, format} → 201 {id, download_url} | audited |
| `GET /api/v1/exports/{id}/download` | File | owner only; 24 h |
| `GET /api/v1/documents/{id}/pages/{n}` | Render cited page (PDF page or text) | authenticated |

### Admin (`require_role("admin")` at router level)
| Method & path | Purpose |
|---|---|
| `GET/POST /api/v1/admin/users` | List / create user. POST returns `{user, set_password_link}` (shown once) |
| `PATCH /api/v1/admin/users/{id}` | Change role or status (activate/deactivate/unlock) |
| `POST /api/v1/admin/users/{id}/set-password-links` | Re-issue link (invalidates previous; revokes sessions on use) |
| `POST /api/v1/admin/users/{id}/session-revocations` | Revoke all sessions (≤ 5 s effect) |
| `GET/POST /api/v1/admin/documents` | List / upload (multipart; 202 + status) |
| `GET/DELETE /api/v1/admin/documents/{id}` | Status / remove |
| `GET /api/v1/admin/data-sources` · `PATCH /api/v1/admin/data-sources/{key}` | Health; enable/disable (FR-026) |
| `GET /api/v1/admin/audit-events` | Filter by user, date range, type (FR-032) |
| `POST /api/v1/admin/audit-exports` | 202 → job → download (≤ 10,000 rows) |
| `GET /api/v1/admin/overview?days=7\|30` | Usage, blocks by category, ratings, source health (FR-017, FR-033) |
| `GET/PUT /api/v1/admin/settings` | Rate limits, starter prompts, alert threshold, watchlist, retention |
| `GET /api/v1/admin/alerts` · `POST /api/v1/admin/alerts/{id}/acknowledgements` | In-app alerts |

### Platform
`GET /healthz` (liveness), `GET /readyz` (dependencies incl. safety), `GET /metrics` (internal network only).

**Rate limits:** chat per FR-037 (Valkey). Login: 10 per minute per IP plus account lockout. Admin endpoints: 60 per minute per admin.

---

## 7. FR / NFR Coverage Matrix

| ID | Type | Requirement | Component(s) | ADR(s) | Status |
|----|------|-------------|--------------|--------|--------|
| FR-001 | FR | Admin creates user; manual one-time link | auth, admin, frontend | 006, 007, 016 | Addressed |
| FR-002 | FR | Sign in / out, lockout | auth, frontend | 006 | Addressed |
| FR-003 | FR | Deactivate, revoke sessions ≤ 5 s | auth, admin | 006, 007, 016 | Addressed |
| FR-004 | FR | Email reset | — | — | Deferred (Won't in V1; no email; D-10) |
| FR-005 | FR | Two roles | auth | 007 | Addressed |
| FR-006 | FR | Streamed answers, stop, status | chat, frontend | 002, 003, 008, 011 | Addressed |
| FR-007 | FR | Follow-up context | assistant | 012, 015 | Addressed |
| FR-008 | FR | Conversation history | chat, frontend | 005, 007, 008 | Addressed |
| FR-009 | FR | Source + as-of | chat, assistant, marketdata | 011, 013 | Addressed |
| FR-010 | FR | Starter prompts (admin-configurable) | chat, admin | 021 | Addressed |
| FR-011 | FR | Ratings | chat | 005, 010 | Addressed |
| FR-012 | FR | PII block before model | guardrails, knowledge | 010, 014 | Addressed |
| FR-013 | FR | Out-of-scope declines | guardrails | 010, 022 | Addressed |
| FR-014 | FR | Manipulation refused (incl. tool/doc text) | guardrails, assistant, knowledge | 010, 012, 014 | Addressed |
| FR-015 | FR | Response screening; no advice | guardrails | 010, 011 | Addressed |
| FR-016 | FR | Read-only, allow-listed tools | assistant | 012 | Addressed |
| FR-017 | FR | Blocked-attempt counts + in-app alert | audit, admin | 010, 016 | Addressed |
| FR-018 | FR | Stock quote | assistant, marketdata | 012, 013 | Addressed |
| FR-019 | FR | Price history | assistant, marketdata | 011, 012, 013 | Addressed |
| FR-020 | FR | Index performance | assistant, marketdata | 012, 013 | Addressed |
| FR-021 | FR | MF NAV + returns | assistant, marketdata | 012, 013 | Addressed |
| FR-022 | FR | Comparison | assistant | 011, 012 | Addressed |
| FR-023 | FR | Deterministic calculations | assistant | 011, 012 | Addressed |
| FR-024 | FR | Export CSV/XLSX | exports, frontend | 011 | Addressed |
| FR-025 | FR | Freshness, graceful failure | marketdata, worker | 013, 017 | Addressed |
| FR-026 | FR | Data-source kill switch | marketdata, admin | 013, 018 | Addressed |
| FR-027 | FR | Results, actions, announcements | assistant, marketdata | 012, 013 | Addressed |
| FR-028 | FR | Document library management | knowledge, admin, worker | 014, 017 | Addressed |
| FR-029 | FR | Document answers with page citations | knowledge, assistant | 011, 014 | Addressed |
| FR-030 | FR | Say "don't know" | assistant, guardrails | 011, 012 | Addressed |
| FR-031 | FR | Audit trail | audit | 016 | Addressed |
| FR-032 | FR | Audit search/export | audit, admin, worker | 016, 017 | Addressed |
| FR-033 | FR | Usage overview | admin | 016, 019 | Addressed |
| FR-034 | FR | UPSI declines | guardrails | 010, 022 | Addressed |
| FR-035 | FR | Forecast refusals | guardrails | 010, 011, 022 | Addressed |
| FR-036 | FR | Figure grounding | guardrails | 011 | Addressed |
| FR-037 | FR | Per-user rate limit | api, guardrails | 010, 018 | Addressed |
| FR-038 | FR | Change own password | auth | 006 | Addressed |
| NFR-001 | NFR | TTFT p95 ≤ 3 s / ≤ 8 s with tools (†) | llm, chat, guardrails | 009, 010, 011, 015 | Addressed (confirm in spike) |
| NFR-002 | NFR | ≥ 15 tok/s per stream (†) | vLLM chat | 009 | Addressed (confirm in spike) |
| NFR-003 | NFR | Guard overhead ≤ 500/300 ms | guardrails | 010, 011 | Addressed |
| NFR-004 | NFR | 10 concurrent, 8k context, 30-min soak | llm, assistant | 009, 015, 022 | Addressed |
| NFR-005 | NFR | PII recall ≥ 99%, FP ≤ 5%, 0 leakage | guardrails, obs | 010, 011, 019, 022 | Addressed |
| NFR-006 | NFR | ≥ 95% red-team; 0 non-allow-listed tools | guardrails, assistant | 010, 012, 022 | Addressed |
| NFR-007 | NFR | ASVS L2 auth/session | auth | 006, 007 | Addressed |
| NFR-008 | NFR | No egress to third-party AI | deploy | 009, 020 | Addressed |
| NFR-009 | NFR | TLS, secrets, DB not exposed | deploy, core | 020, 021 | Addressed |
| NFR-010 | NFR | ≥ 85% eval; 100% figures match | assistant, guardrails | 011, 012, 022 | Addressed |
| NFR-011 | NFR | Usability, WCAG AA | frontend | 008, 022 | Addressed |
| NFR-012 | NFR | 99% business-hours availability | deploy, obs | 019, 020 | Addressed (single host; see §9) |
| NFR-013 | NFR | Fail closed; restart ≤ 2 min | guardrails, deploy | 010, 020 | Addressed |
| NFR-014 | NFR | Backups RPO 24 h / RTO 4 h | deploy, worker | 017, 020 | Addressed (off-host target AQ-4) |
| NFR-015 | NFR | Swappable model and data source | llm, marketdata | 001, 009, 013 | Addressed |
| NFR-016 | NFR | Correlation IDs, traces, PII-free logs | obs, all | 019 | Addressed |
| NFR-017 | NFR | Audit retention + integrity | audit | 005, 016 | Addressed |
| NFR-018 | NFR | Market-data usage discipline | marketdata | 013, 018, 020 | Addressed |
| NFR-019 | NFR | One-command deploy + runbook | deploy | 020 | Addressed |

### Detailed NFR notes (per driver)
- **NFR-005 (PII):**
  - Input is gated before persistence and logging (ADR-010).
  - Output spans containing digits or `@` are checked before release (ADR-011).
  - Uploaded documents and upstream text are scanned at ingestion (ADR-013/014).
  - Logs are scrubbed as a final net (ADR-019).
  - Four independent places, so a recogniser gap must slip past all of them to leak.
- **NFR-013 (fail closed):**
  - Safety dependencies are on the request path, with timeouts that end in refusal.
  - `/readyz` gates the UI.
  - Valkey failure also fails closed for chat.
  - Fault-injection tests are in ADR-022.
- **NFR-001/004 (latency and capacity):**
  - Prefix caching of system prompt + tool schemas.
  - Scope router ≤ 400 ms.
  - Hold-back gating delays only numeric spans.
  - Context capped at 8k tokens; `max-num-seqs 16`.
  - Budget per turn ≈ 50 ms PII + ≤ 250 ms guard + ≤ 400 ms router + prefill/first token (target ≤ 1.5 s) → ~2.2 s typical without tools. Tools add a round-trip: ~1–2 s model + < 100 ms local DB reads.
  - The spike validates these numbers.
- **NFR-012 (availability):** single host, so availability depends on host uptime.
  Mitigations are auto-restart, readiness checks, quick restore and the runbook. 99%
  of business hours allows ~2 h downtime per month, achievable without HA.

---

## 8. Technology Stack

| Layer | Choice | Version (pinned in lockfiles at implementation) | Rationale (→ driver) | ADR |
|-------|--------|---------|----------------------|-----|
| Frontend | React + TypeScript (strict) + Vite | React 19, TS 5.x | Owner decision; rich tables; large ecosystem | 008 |
| UI kit | Tailwind CSS + shadcn/ui (Radix) + TanStack Table | Tailwind 4 | Accessible primitives → NFR-011 | 008 |
| FE data | TanStack Query; React Router; openapi-typescript | Query v5, Router v7 | Typed contract → parallel stories | 002, 008 |
| Backend | FastAPI on uvicorn; Pydantic v2 | Python 3.13 | Async streaming → NFR-001; owner decision | 001, 002 |
| ORM / migrations | SQLAlchemy 2 async + asyncpg; Alembic | — | Mature async ORM; reviewed migrations | 005 |
| Auth crypto | pwdlib[argon2], `secrets`, hashlib | — | Argon2id; no custom crypto → NFR-007 | 006 |
| Agent | PydanticAI (OpenAI-compatible provider) | — | Typed tools; vLLM support → NFR-015 | 012 |
| LLM serving | vLLM OpenAI-compatible server, Docker image | pinned in spike | Continuous batching on A10 → NFR-004 | 009 |
| Chat model | Qwen3-14B INT4 (AWQ/GPTQ) — default | spike decides | Fits 24 GB with KV for 10 users | 009 |
| Guard model | Qwen3Guard-Gen-0.6B | — | Fast safety/jailbreak classifier → NFR-003/006 | 009, 010 |
| PII | Microsoft Presidio + spaCy `en_core_web_sm` + custom India recognisers | — | Deterministic, India entities built in → NFR-005 | 010 |
| Embeddings | fastembed + BAAI/bge-small-en-v1.5 (CPU) | 384-d | No VRAM needed; MIT/Apache | 014 |
| PDF text | pypdf | — | BSD licence (avoid AGPL PyMuPDF) | 014 |
| Database | PostgreSQL + pgvector | PG 18, pgvector 0.8+ | One store incl. vectors; native `uuidv7()` | 005 |
| Shared state | Valkey | 8.x | BSD; rate limits/buckets → FR-037, NFR-018 | 018 |
| Jobs | APScheduler + Postgres jobs table | APScheduler 3.x | No extra broker | 017 |
| Market data | nselib (Apache-2.0), mftool | — | V1 decision; behind provider protocol | 013 |
| Exports | openpyxl, csv | — | XLSX/CSV with metadata → FR-024 | 011 |
| Observability | structlog, OpenTelemetry SDK, Langfuse (self-hosted, MIT), Prometheus metrics | — | PII-free traces; eval tracking → NFR-016, NFR-010 | 019 |
| Edge | Caddy | 2.x | Simple TLS + static + reverse proxy → NFR-009 | 020 |
| Egress | Squid (or tinyproxy) with domain allow-list | — | Enforces NFR-008/018 | 020 |
| Packaging | Docker Compose; uv; pnpm | — | One-command deploy → NFR-019 | 020 |
| Quality | pytest, import-linter, ruff, mypy; Vitest, Playwright, axe; gitleaks | — | Gates in ADR-022 | 001, 021, 022 |

**Alternatives considered:** see each ADR. Key rejections:
- Django (owner chose FastAPI)
- Foundry Local (single-device runtime)
- fastapi-users (maintenance mode)
- LLM Guard (archived)
- Celery/arq (more moving parts)
- Qdrant (unneeded at this scale)
- PyMuPDF (AGPL)
- JWT (revocation)

---

## 9. Trade-off Analysis

### Trade-off: Streaming with hold-back vs buffered answers
**Decision:** hold-back gating (ADR-011).
**Options:** (A) buffer the full answer and check it: safest, but TTFT equals the full
generation time (≈ 10–30 s); (B) stream unchecked: fastest, but unsafe; (C) stream
non-numeric spans immediately and hold numeric/`@` spans until checked.
**Rationale:** C meets NFR-001 and enforces FR-036 and PII on every figure. Residual
risk: advice or forecast phrasing without digits can stream before final moderation.
That is mitigated by the scope router (most such requests are declined before the
agent runs), the phrase rules, and `replace` at the end.
**Accepted:** Benefit: fast and grounded. Cost: rare visible text replacement. Mitigation: UI shows "Answer withdrawn for review" clearly.
**Revisit when:** `replace` events exceed 1% of answers.

### Trade-off: Extra scope-router call vs prompt-only scope
**Decision:** a separate structured router call (ADR-010).
**Rationale:** deterministic, testable declines for advice, UPSI and forecasts in a
regulated setting, at a cost of ≤ 400 ms per turn (cached prefix).
**Revisit when:** the TTFT budget is missed in the spike. Then fold the router into the
guard stage or use a fine-tuned small classifier.

### Trade-off: Block (never mask) PII
**Decision:** block (PRD Q3, ADR-010).
**Accepted:** occasional user friction (≤ 5% false positives) in exchange for zero
reliance on masking completeness.

### Trade-off: Single host vs high availability
**Decision:** single A10 host.
**Rationale:** budget and pilot scale. NFR-012 is 99% of business hours, not 99.9%.
**Accepted:** host maintenance means downtime. Mitigation: maintenance windows outside
business hours, restore drill, runbook.
**Revisit when:** users exceed ~10, or the availability target rises above 99%.

### Trade-off: Stored copies vs live data
**Decision:** stored, scheduled copies plus a rate-limited fetch on cache miss (ADR-013).
**Accepted:** data is EOD or 15-minute snapshots, not live. That matches V1 and reduces
NSE ToU exposure.
**Revisit when:** ACE (licensed real-time) is subscribed.

### Trade-off: Postgres for everything (incl. vectors, jobs, audit)
**Decision:** one database.
**Accepted:** less specialised performance. Plenty for ~10 users and thousands of
chunks. Benefit: one thing to secure, back up and restore.

### Trade-off: Langfuse footprint
**Decision:** Langfuse under an optional Compose profile.
**Cost:** it adds ClickHouse, MinIO and Postgres containers, so roughly 4–8 GB RAM and
several CPU cores on the host. That host also runs embeddings, Presidio and Postgres.
**Mitigation:** disable the profile if host resources are short (AQ-3); logs and
metrics remain.

---

## 10. Deployment Architecture

### Environments
- **Development (builder's Mac):** frontend, API and worker run locally (uv, pnpm).
  Postgres and Valkey run in Docker. `APP_LLM_BASE_URL` points to the **vLLM dev
  instances on the A10 host** through an SSH tunnel (research v1.2/1.3: the Mac cannot
  run vLLM). Integration tests use the fake LLM server.
- **Staging:** the same Compose stack on the A10 host under a separate project name and
  port, sharing the vLLM servers. It uses a separate database. Suites run here before
  promotion.
- **Production (pilot):** the Compose stack on the A10 host.

The A10 hosts both staging and production. vLLM is shared, so GPU capacity is shared too.
Run load tests outside business hours.

### Topology
```
            Users (internal network / VPN)
                     │ HTTPS 443
               ┌─────▼─────┐
               │   Caddy   │  TLS (internal CA), static SPA, CSP, /api proxy
               └─────┬─────┘
          app network│(internal)
   ┌────────┬────────┼─────────┬──────────┬──────────────┐
   │  api   │ worker │ postgres│  valkey  │ langfuse*    │
   └───┬────┴───┬────┴─────────┴──────────┴──────────────┘
       │ inference network (internal)
   ┌───▼────────────┐  ┌──────────────┐
   │ vllm-chat (GPU)│  │vllm-guard(GPU)│   one NVIDIA A10 24 GB (0.78 / 0.12)
   └────────────────┘  └──────────────┘
       │ (api, worker only) HTTPS_PROXY
   ┌───▼──────────┐ egress allow-list → NSE, AMFI
   │ egress-proxy │
   └──────────────┘
   * optional profile "observability"
```

### Host prerequisites (from research v1.3)
- Linux with NVIDIA driver R570+ (CUDA 12.8+), Docker and the NVIDIA Container Toolkit.
- ≥ 64 GB RAM and ≥ 8 CPU cores recommended (Presidio, embeddings, Postgres, Langfuse).
- ≥ 200 GB SSD for models (~50 GB), database, documents and backups staging.
- To be confirmed: AQ-3.

### Strategy
- **Deployment method:** recreate deployment via `docker compose up -d` with pinned
  image tags. Brief downtime is acceptable outside business hours. DB migrations run as
  a one-shot `migrate` service before `api` starts.
- **Model changes:** start the new vLLM container on staging, run all suites, then swap
  the production service tag. Every model or prompt change is recorded in the decision
  log with the suite results.
- **Rollback:** previous image tags are kept. Migrations are written to be
  backward-compatible for one release. DB restore from the nightly backup is the last
  resort.
- **Scaling path:** vertical (bigger GPU) or a second GPU for the guard model and more
  context. API workers are stateless (sessions in DB, counters in Valkey), so adding
  uvicorn workers is a configuration change.

---

## 11. Future Considerations

**Anticipated changes:**
- **Near (post-V1):**
  - ACE full subscription or internal DB feeds → `AceProvider` / `InternalDbProvider`
    (ADR-013); internal tables gated by AQ/Q10 classification.
  - MFA (TOTP) in `auth.service` (D-01).
  - Charts from `message_tables` (D-03): the data is already structured.
- **Medium:**
  - SSO via OIDC (D-02).
  - Real-time quotes via a licensed feed (D-05).
  - Confidential documents with per-document ACLs (D-06): `kb_documents` gains an
    access-group column, and retrieval filters by the user's groups.
  - Email (D-10) once a relay exists: FR-004 revived.
- **Long:**
  - More users → second GPU / larger card, split `assistant`+`guardrails`+`llm` into an
    inference service (ADR-001 boundary).
  - Fine-tuned scope classifier.
  - Multi-language.

**Scalability path:** ~10 concurrent users on one A10 (14B INT4, 8k context) → ~20–30
with an L40S/A100-class GPU or two A10s (guard on the second) → more GPUs or bigger
models behind the same `llm` gateway.

**Revisit triggers (aggregated):**
- PII recall < 99% or FP > 5% (ADR-010)
- Scope router accuracy < 95% or TTFT budget missed (ADR-010)
- `replace` > 1% of answers (ADR-011)
- Grounding false-rejects > 3% (ADR-011)
- Guard/chat memory split OOM in soak (ADR-009)
- > 1M chunks or vector p95 > 300 ms (ADR-005)
- > ~10 concurrent users or availability target > 99% (§9)
- ACE/internal DB approved (ADR-013)
- A second client type (ADR-002)

---

## 12. Open Architecture Questions

| # | Question | Proposed answer | Impact if different | Owner |
|---|----------|-----------------|---------------------|-------|
| AQ-1 | **Document PII policy.** ~~Open~~ **Resolved 2026-10-07:** reject on high-risk identifiers; mask emails/phones; allow director/officer names. PRD FR-012/FR-028 updated (v1.1). | As proposed | — | Product owner (confirmed) |
| AQ-2 | Is the chosen default (Qwen3-14B INT4, guard 0.6B, split 0.78/0.12) confirmed by the spike? | Decided by STORY-003; record in the decision log | Model profile, NFR-001/002 thresholds | Builder |
| AQ-3 | A10 host CPU, RAM and disk specs | ≥ 8 cores, ≥ 64 GB RAM, ≥ 200 GB SSD recommended | If smaller: disable the Langfuse profile; check embedding speed | Builder |
| AQ-4 | Off-host backup target (NAS / internal object storage) | Any internal target reachable via rsync or S3-compatible API | NFR-014 cannot be met with on-host-only backups | Product owner / IT |
| AQ-5 | TLS certificate source | IIFL internal CA certificate for the host name; Caddy internal CA only during the pilot setup | Browser trust warnings | IT |

---

## Appendix

### Glossary
| Term | Definition |
|------|------------|
| Turn | One user message and the assistant's response, including all guard stages and tool calls |
| Hold-back gating | Releasing streamed text immediately except spans containing digits or `@`, which are checked first |
| Grounding set | All numbers available from this turn's tool results, calculations, cited chunks, user message and current date |
| Stored copy | Locally persisted market/fund/company data refreshed on schedule |
| Fail closed | A safety dependency failure causes refusal, never bypass |
| Prefix caching | vLLM reuse of KV cache for identical prompt prefixes (system prompt, tool schemas, earlier turns) |
| Chain anchor | Last hash of an audit partition recorded before the partition is dropped at retention |

### References
- PRD: `bmad-output/prd.md` v1.0 · Addendum: `bmad-output/addendum.md` v1.0
- Research: `bmad-output/research-report.md` v1.4
- Decision log: `bmad-output/decision-log.md`
- Project context: `bmad-output/project-context.md`

### Document History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-10-06 | Winston (Architect) via bmad-architecture | Initial architecture: 22 ADRs, components, data model, API, coverage matrix for FR-001–038 and NFR-001–019 |
| 1.1 | 2026-10-07 | Winston (Architect) via bmad-architecture | AQ-1 resolved (document PII policy confirmed); ADR-014 no longer pending; status approved |

---

**END OF DOCUMENT**. Ready for handoff to `bmad-epics-and-stories`.
