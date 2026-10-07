# System Architecture: invest-ai-llm (Confidential Document Assistant)

**Document Version:** 2.3
**Date:** 2026-10-07
**Author:** Winston (Architect), via bmad-architecture (Update intent)
**Track:** BMad Method
**Status:** Approved for story creation
**Source PRD:** `bmad-output/prd.md` v2.0 (FR-001–FR-055, NFR-001–NFR-025)

> Single source of truth for cross-cutting technical decisions. Every story inherits the
> **LOCKED** rules below.
>
> **v2.0 follows the 2026-10-07 pivot** from a market-data assistant to a confidential
> document and Excel assistant for investment bankers. ADRs are never deleted:
> - Superseded ADRs keep a stub pointing to their replacement.
> - Full v1.1 text is in `archive/architecture-v1.1-market-data.md`.
> - Unchanged ADRs are carried over verbatim, with dated amendment notes where needed.

---

## Table of Contents
1. System Overview · 2. Architecture Pattern · 3. Architecture Decision Records ·
4. Component Design · 5. Data Model · 6. API Specifications · 7. FR / NFR Coverage Matrix ·
8. Technology Stack · 9. Trade-off Analysis · 10. Deployment Architecture ·
11. Future Considerations · 12. Open Architecture Questions

---

## 1. System Overview

### Purpose
A fully self-hosted assistant for ~10 investment bankers. They upload confidential
documents (PDF, DOCX, PPTX) and workbooks (XLSX, CSV) into a private space or a deal
workspace. They then ask cited questions, get background summaries and comparisons,
extract tables to Excel, and transform workbooks with typed, previewed operations. It
runs on one NVIDIA A10 (24 GB) with **no internet egress**.

### Scope
**In scope (V1):**
- Admin-provisioned accounts with three roles (user, admin, compliance)
- Private spaces and deal workspaces with strict isolation (app filtering + Postgres RLS)
- Envelope encryption with per-object keys and crypto-shredding
- Ingestion pipeline: validation, Docling parsing, OCR, injection scan, identifier
  masking, chunking, hybrid index
- Chat over a selected document set with citations and figure grounding
- Background map-reduce summaries and comparisons
- Typed spreadsheet engine with preview → confirm → new workbook
- Watermarked expiring downloads; 30-day expiry and on-demand deletion
- Metadata-only tamper-evident audit; compliance break-glass
- Content-free observability; synthetic-corpus test suites as release gates

**Out of scope:** see PRD. Architecturally relevant:
- No market data or internet
- No code execution on user data
- No macros or password-protected files
- No email, SSO or MFA
- No high-availability cluster

### Architectural Drivers
1. **FR-041 / NFR-020: Information barriers.** Two-layer isolation: application
   `AccessContext` plus Postgres RLS, with no admin content path. Per-user model-cache
   salt (ADR-023, ADR-031).
2. **NFR-021 / NFR-022 / FR-053: Encryption and provable deletion.** Envelope encryption
   with per-object keys; crypto-shred; 30-day backup window; KEK outside DB backups
   (ADR-028, ADR-035).
3. **FR-014 / NFR-006: Hostile documents.** A sandboxed parser; injection scan at
   ingestion; content treated as data; no URLs or images in output; apply-on-confirm
   (ADR-026, ADR-027, ADR-025).
4. **FR-012 / NFR-005: High-risk identifiers.** Masked at ingestion, blocked in chat;
   unmasked text never reaches prompts, indexes, logs or traces.
5. **FR-036 / NFR-010: Grounded figures and citations.** Engine-rendered tables, held-back
   numeric spans, chunk-level provenance in summaries (ADR-025, ADR-029).
6. **NFR-001 / NFR-004 / NFR-024: One A10 for chat + background jobs.** Map-reduce
   summaries with ≤ 2 concurrent jobs, chat priority, 8k chat context (ADR-029, ADR-009, ADR-015).
7. **NFR-008 / NFR-023: Zero egress; no content in logs, traces or audit** (ADR-034, ADR-033, ADR-032).
8. **NFR-025: Spreadsheet and file safety.** Typed catalogue, safe expression grammar,
   rlimited execution, values-only outputs (ADR-030).

### Stakeholders & Constraints
- **Users:** ~10 bankers in deal teams; admin (IT, no content access); compliance.
- **Team:** one builder working with AI agents. Favour conventional tools and strong LOCKED rules.
- **Fixed:**
  - FastAPI + React/TS; vLLM on one A10 (INT4 via Marlin; no native FP8)
  - Open-weight models; zero egress; no email
  - 30-day retention; typed Excel operations only

---

## 2. Architecture Pattern

**Pattern:** **Modular monolith** (FastAPI api + worker from the same codebase), plus a
**sandboxed parser** container and two **vLLM** model servers, on Docker Compose on a
single host (ADR-038).

**Justification:**
- ~57 stories, one builder and ~10 users don't need microservices.
- The processes that are split out each have a security or runtime reason:
  - **parser:** untrusted-file handling with resource limits and no network;
  - **vLLM:** GPU inference;
  - **worker:** long jobs and schedules.

**Alternatives:** microservices (operational cost with no NFR demanding them); a single
process (untrusted parsing in the API process violates NFR-025 isolation).

---

## 3. Architecture Decision Records

| ADR | Title | Status | Drives |
|-----|-------|--------|--------|
| ADR-001 | Modular monolith with fixed module boundaries | Superseded by ADR-038 | — |
| ADR-002 | REST/JSON under `/api/v1` + SSE for chat | Accepted | FR-006, NFR-001 |
| ADR-003 | Response, error (RFC 9457) and SSE conventions | Accepted (amended by ADR-025) | all API FRs |
| ADR-004 | Naming conventions | Accepted (amended 2026-10-07) | all |
| ADR-005 | PostgreSQL 18 + pgvector; SQLAlchemy 2 async; Alembic; UUIDv7 | Accepted (amended 2026-10-07) | data FRs |
| ADR-006 | Server-side sessions; manual set-password links | Accepted | FR-001–003, FR-038, NFR-007 |
| ADR-007 | RBAC with two roles | Superseded by ADR-023 | — |
| ADR-008 | Frontend state: TanStack Query; no global store | Accepted (amended 2026-10-07) | FR-006–011, FR-055 |
| ADR-009 | LLM gateway to vLLM: chat + guard instances | Accepted (amended by ADR-029, ADR-031) | NFR-001, NFR-004, NFR-015 |
| ADR-010 | Guardrail pipeline v1 | Superseded by ADR-024 | — |
| ADR-011 | Output gating v1 | Superseded by ADR-025 | — |
| ADR-012 | Agent and tool contract v1 | Superseded by ADR-026 | — |
| ADR-013 | Market-data provider abstraction | **Retired** (pivot; no replacement) | — |
| ADR-014 | Public knowledge library | Superseded by ADR-027 | — |
| ADR-015 | Conversation context within ~8k tokens | Accepted | FR-007, NFR-004 |
| ADR-016 | Hash-chained audit v1 | Superseded by ADR-032 | — |
| ADR-017 | Background jobs: APScheduler + Postgres jobs | Accepted (amended 2026-10-07) | FR-046, FR-052 |
| ADR-018 | Valkey for rate limits and short caches | Accepted (amended 2026-10-07) | FR-037 |
| ADR-019 | Observability v1 (Langfuse) | Superseded by ADR-033 | — |
| ADR-020 | Deployment v1 (egress proxy) | Superseded by ADR-034 | — |
| ADR-021 | Configuration and secrets | Accepted (amended 2026-10-07) | NFR-009 |
| ADR-022 | Test and evaluation strategy v1 | Superseded by ADR-037 | — |
| ADR-023 | Space-scoped access control and isolation (RLS) | Accepted | FR-005, FR-039–041, FR-055, NFR-020 |
| ADR-024 | Guardrail pipeline v2 | Accepted | FR-012–017, FR-030, FR-037, NFR-003/005/006/013 |
| ADR-025 | Output gating, grounding and rendering v2 | Accepted | FR-006, FR-009, FR-015, FR-036, NFR-006 |
| ADR-026 | Agent and tool contract v2 (apply-on-confirm) | Accepted | FR-016, FR-023, FR-045–051, FR-055 |
| ADR-027 | Document ingestion pipeline (sandboxed parser, Docling, OCR, mask, scan) | Accepted | FR-012, FR-014, FR-042–049, NFR-024/025 |
| ADR-028 | Envelope encryption with per-object keys; crypto-shredding | Accepted | FR-053, NFR-021, NFR-022 |
| ADR-029 | Background summary and comparison jobs; GPU fairness | Accepted | FR-046, FR-047, NFR-001/004/024 |
| ADR-030 | Typed spreadsheet engine and file safety | Accepted | FR-023, FR-048–051, NFR-025 |
| ADR-031 | Per-user model-cache isolation (`cache_salt`) | Accepted | FR-041, NFR-020 |
| ADR-032 | Metadata-only hash-chained audit | Accepted | FR-031, FR-032, NFR-017, NFR-023 |
| ADR-033 | Observability without content (Jaeger, no Langfuse) | Accepted | NFR-016, NFR-023 |
| ADR-034 | Zero-egress deployment and network isolation | Accepted | NFR-008, NFR-009, NFR-025 |
| ADR-035 | Data lifecycle: retention, deletion, backups | Accepted | FR-052, FR-053, NFR-014, NFR-022 |
| ADR-036 | Compliance break-glass access | Accepted | FR-054 |
| ADR-037 | Test and evaluation strategy v2 (synthetic corpus) | Accepted | NFR-005/006/010/020/022/023/025 |
| ADR-038 | Module boundaries v2 | Accepted | NFR-015, NFR-019 |

---

### Carried-over ADRs (Accepted)

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

**Status:** Accepted (amended 2026-10-07)   **Drives:** all API FRs, FR-006, FR-012

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

**Amendment 2026-10-07 (by ADR-025/027):** SSE adds `plan` and `job` events; `sources` items become `{kind, document_id, locator}`; new problem codes `file_type_unsupported`, `macro_file_rejected`, `encrypted_file_rejected`, `file_too_large`, `zip_bomb_suspected`, `not_ready`. Not accessible → 404 (ADR-023).

---

### ADR-004: Naming conventions

**Status:** Accepted (amended 2026-10-07)   **Drives:** maintainability, parallel-story consistency

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

**Amendment 2026-10-07:** prefixes `md_`/`mf_`/`co_`/`kb_` are retired with the market data. Content tables use plain names (`documents`, `chunks`, `sheets`, `outputs`). **Encrypted columns end in `_enc`** (ADR-028). The tool list is in ADR-026. Audit event types gain domains `spaces.*`, `documents.*`, `sheets.*`, `outputs.*`, `lifecycle.*`, `compliance.*`.

---

### ADR-005: PostgreSQL 18 + pgvector; SQLAlchemy 2 async; Alembic; UUIDv7

**Status:** Accepted (amended 2026-10-07)   **Drives:** FR-008, FR-018–033, NFR-014, NFR-017

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

**Amendment 2026-10-07:** content tables have `space_id NOT NULL` with **RLS `ENABLE` + `FORCE`** (ADR-023). Content columns are AES-GCM ciphertext `bytea` (`_enc`, ADR-028). The data disk relies on Azure managed-disk default SSE (see ADR-028 amendment). `app_rw` is subject to RLS. Soft-delete is replaced by the delete pipeline (ADR-035). Market-data tables are removed.

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

### ADR-008: Frontend state: TanStack Query for server state; no global store

**Status:** Accepted (amended 2026-10-07)   **Drives:** FR-006–011, admin FRs, NFR-011

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

**Amendment 2026-10-07:** the markdown renderer allows **only `cite:` links**: no images, raw HTML or external URLs (ADR-025). New UI areas: spaces and workspace membership, upload with ingestion reports, the selected-set picker, a citation viewer (page image, slide text, sheet range), plan cards with Apply, job cards with progress, notifications, downloads, and the compliance console.

---

### ADR-009: LLM gateway to vLLM (OpenAI-compatible): chat + guard instances

**Status:** Accepted (amended 2026-10-07)   **Drives:** NFR-001, NFR-002, NFR-004, NFR-008, NFR-015

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

**Amendment 2026-10-07:** every request carries a per-user **`cache_salt`** (ADR-031) and a **priority** (chat above background jobs; `--scheduling-policy priority` if supported by the pinned version, ADR-029). Background summary map/reduce calls use the same `chat` model.

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

### ADR-017: Background jobs: worker process, APScheduler + Postgres job table

**Status:** Accepted (amended 2026-10-07)   **Drives:** FR-025, FR-028, NFR-014, NFR-017

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

**Amendment 2026-10-07:** job types are `ingest`, `summary`, `comparison`, `apply_ops`, `lifecycle`, `audit_verify`, `backup`, `user_purge`. Market-data refresh jobs are removed. LLM-heavy jobs take the GPU semaphore (ADR-029). Job payloads contain IDs and enums only (no content).

---

### ADR-018: Valkey for rate limits, upstream token buckets and short-lived cache

**Status:** Accepted (amended 2026-10-07)   **Drives:** FR-026, FR-037, NFR-018

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

**Amendment 2026-10-07:** upstream token buckets and the data-source flag cache are removed (ADR-013 retired). Valkey also holds per-user **upload quotas** and the **GPU job semaphore** (ADR-029). It still holds no content.

---

### ADR-021: Configuration and secrets

**Status:** Accepted (amended 2026-10-07)   **Drives:** NFR-009, NFR-015

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

**Amendment 2026-10-07:** secrets add the **KEK file** (root-only, never in DB backups), **backup key**, **cache-salt secret** and **audit HMAC key** (ADR-028/031/032). Settings add retention days (7–90, default 30), upload quotas and the break-glass two-person rule.

---

### Superseded and retired ADRs (full text in `archive/architecture-v1.1-market-data.md`)

- **ADR-001: Modular monolith with fixed module boundaries.** Status: **Superseded by ADR-038** (2026-10-07): pattern unchanged; module list replaced for documents, sheets, spaces, crypto, lifecycle and compliance.
- **ADR-007: RBAC with two roles.** Status: **Superseded by ADR-023** (2026-10-07): three roles + space-scoped access + RLS.
- **ADR-010: Guardrail pipeline order; fail closed.** Status: **Superseded by ADR-024** (2026-10-07): identifier gate changed to high-risk IDs only; scope categories for document work; access-check stage.
- **ADR-011: Streaming output gating, figure grounding and tool-rendered tables.** Status: **Superseded by ADR-025** (2026-10-07): grounding against documents/sheets; URL and image stripping.
- **ADR-012: Agent and tool contract.** Status: **Superseded by ADR-026** (2026-10-07): document/sheet tool set; apply-on-confirm.
- **ADR-014: Knowledge library.** Status: **Superseded by ADR-027** (2026-10-07): user documents, sandboxed parser, masking.
- **ADR-016: Hash-chained, append-only audit trail.** Status: **Superseded by ADR-032** (2026-10-07): payloads restricted to metadata by type.
- **ADR-019: Observability with Langfuse.** Status: **Superseded by ADR-033** (2026-10-07): content-free; Jaeger; Langfuse dropped.
- **ADR-020: Deployment and network isolation with egress proxy.** Status: **Superseded by ADR-034** (2026-10-07): zero egress; parser network.
- **ADR-022: Test and evaluation strategy.** Status: **Superseded by ADR-037** (2026-10-07): synthetic corpus; isolation, malicious-file, canary and deletion suites.
- **ADR-013: Market-data provider abstraction, stored copies, refresh and upstream discipline.** Status: **Retired** (2026-10-07): market data dropped by the pivot; no replacement. nselib/mftool/ACE/internal-DB adapters are not built.

---

### New ADRs (2026-10-07)

### ADR-023: Space-scoped access control and isolation (supersedes ADR-007)

**Status:** Accepted (2026-10-07)   **Drives:** FR-005, FR-039, FR-040, FR-041, FR-055, NFR-020

**Context:** Confidential deal material must never cross users or deals (information
barriers). One missed `WHERE` clause in any of dozens of data paths would be a breach.
Admins (IT) must not see content.

**Decision:**
- **Spaces:** `spaces(kind = private | workspace)`. Every user gets exactly one private
  space on creation. Workspaces have `space_members(role = owner | member)`.
- **Every content row carries `space_id NOT NULL`:** documents, chunks, tables, sheets,
  outputs, conversations, messages, jobs, notifications about objects.
  - Conversations also carry `owner_user_id`, so they stay **private to their creator**
    even inside a workspace.
  - Outputs also carry `owner_user_id`. Access requires creator **and** current membership.
- **A conversation is bound to one space.** Its selected documents (FR-055) must belong
  to that space, and outputs are written to that space.
- **Two enforcement layers:**
  1. **Application:** every content repository function takes an `AccessContext(user_id,
     role, space_ids, grant_id?)` built by the request dependency. Queries filter on it.
     Not accessible = 404.
  2. **Database backstop: PostgreSQL Row-Level Security** on all content tables. Each
     transaction begins with `SET LOCAL app.user_id = …` (and `app.grant_id` for
     break-glass). Policies allow rows whose `space_id` is in the user's memberships, plus
     the owner checks above. **With no context set, policies return no rows.** Tables use
     `FORCE ROW LEVEL SECURITY`, so even the table owner is subject to them. The app
     connects as `app_rw`, which is subject to RLS.
- **Admin and system code paths run without a user context**, so RLS gives them zero
  content rows by construction. Admin endpoints use metadata-only views
  (counts, sizes, statuses).
- Background jobs carry `user_id` + `space_id` and set the same context before touching data.
- Vector and keyword search always filter `space_id = ANY(:space_ids)`. pgvector HNSW
  uses iterative index scans so filtered queries stay complete.
- Membership is re-read on every request (no caching), so removal takes effect immediately.

**Consequences: LOCKED:**
- No content table without `space_id` and an RLS policy (migration review checklist).
- No repository method without `AccessContext`.
- No admin endpoint may join content tables.
- The isolation suite (NFR-020) must pass on every release.

**Alternatives:**
- App-level filtering only: one bug equals a breach.
- Separate database per deal: heavy operations for one builder; revisit if the number of
  workspaces grows large or Compliance demands physical separation.

**Revisit when:** Compliance requires physical separation per deal, or there are more
than ~200 workspaces.

---

### ADR-024: Guardrail pipeline v2 (supersedes ADR-010)

**Status:** Accepted (2026-10-07)   **Drives:** FR-012–FR-017, FR-030, FR-037, FR-041, FR-055, NFR-003, NFR-005, NFR-006, NFR-013

**Decision:** stages in fixed order inside `chat.service.handle_turn()`:

| # | Stage | Implementation | On hit / failure |
|---|---|---|---|
| 0 | Auth, CSRF, rate limit and quota | ADR-006, ADR-018 | 401/403/429 problem |
| 1 | **Chat identifier gate** | Presidio pattern recognisers for **high-risk IDs only** (PAN, Aadhaar incl. partial, bank account + context, demat, card/Luhn, UPI, passport, voter ID). No PERSON/EMAIL/PHONE blocking. | 422 `pii_detected` {types}; text discarded before persistence or logging |
| 2 | **Guard moderation** | Qwen3Guard-Gen on the user message (jailbreak and unsafe) | Template decline; audit category only |
| 3 | **Scope router** | Structured `chat` call → `{level, category}`. Categories: `doc_task`, `sheet_task`, `concept`, `howto`, `care_projection`, `care_legal`, `needs_internet`, `barrier`, `insider_misuse`, `off_topic`, `self_disclosure` | Level 3 → template decline (ADR-024 templates); `needs_internet` → "no internet access" template |
| 4 | **Access check** | Re-verify that the conversation's space membership and selected documents are accessible and `ready` (ADR-023) | 404/409 problem; fail closed |
| 5 | Constrained agent | ADR-026 | — |
| 6 | **Output gate** | ADR-025 | Regenerate once, else `replace` |
| 7 | **Final moderation** | Qwen3Guard on the full response | `replace` |

- **Documents are protected at ingestion (ADR-027):** high-risk IDs are masked and
  injection-flagged segments excluded. The agent only ever sees masked, unflagged text.
- **Fail closed:** any error or timeout in stages 1–4, 6 or 7 means refusal (503
  `safety_unavailable`, or `replace` + `withheld`). Missing RLS context also means no data.
- Latency: stages 1–2 ≤ 500 ms (NFR-003). Stage 3 counts toward TTFT.

**Consequences: LOCKED:**
- The order is fixed; stage 1 runs on raw input before any write.
- Declines use templates from `guardrails/templates/`.
- New categories require a PRD policy change.

**Alternatives:** masking chat input (rejected; users can rephrase, and blocking is
safer); blocking documents with identifiers (rejected by owner: masking keeps the tool
usable).

---

### ADR-025: Output gating, grounding and rendering v2 (supersedes ADR-011)

**Status:** Accepted (2026-10-07)   **Drives:** FR-006, FR-009, FR-015, FR-023, FR-030, FR-036, NFR-001, NFR-006, NFR-010

**Decision:**
1. **Tables come from engine data.** Document tables, sheet query results, operation
   previews and calculation results are emitted as `table` events built from tool
   results, never from model text. They are stored encrypted in `message_tables`.
2. **Hold-back gating as in ADR-011.** Spans without digits or `@` stream immediately.
   Spans with digits or `@` are held until checked for:
   - (a) high-risk identifier patterns: no unmasked IDs may ever be output;
   - (b) figure grounding.
3. **Grounding set** = numbers in this turn's retrieved chunks and tables (masked text),
   sheet cells returned by tools, operation outputs, calculation outputs, the user's
   message, and the current date. The normalisation and match rules are those of ADR-011.
4. **No external content in output.** The output gate strips any URL, e-mail-style link
   target, HTML, or markdown image/link syntax not matching the internal citation scheme
   `cite:<document_id>#<locator>`. The frontend renderer (ADR-008) renders only
   `cite:` links, never images, raw HTML or external URLs. CSP `img-src 'self'`
   blocks image beacons regardless.
5. **Citations** are emitted by the orchestrator from the chunks, tables and sheet ranges
   actually used, as `sources` items:
   `{kind: document|workbook|calculation, document_id, locator: "p.12" | "slide 4" | "Sheet1!B2:F20"}`.
6. **Masked placeholders** (`[PAN]`, `[AADHAAR]`, …) pass through unchanged. Any attempt
   to output a value matching a high-risk pattern next to a placeholder is withheld.

**SSE event set (amends ADR-003):** adds
- `plan` {preview_id, steps[], preview table}: operation plan awaiting user confirmation
- `job` {job_id, kind, status}: background summary or comparison started

The `sources` items use the shape above.

**Consequences: LOCKED:** no token reaches the client except through the output gate;
tabular tool results must be `TableResult`; the frontend renders only `cite:` links.

---

### ADR-026: Agent and tool contract v2 (supersedes ADR-012)

**Status:** Accepted (2026-10-07)   **Drives:** FR-016, FR-023, FR-045–FR-051, FR-055, NFR-006, NFR-025

**Decision:**
- PydanticAI agent; typed tools with bounded arguments. Tools receive `AccessContext`
  and the conversation's selected set **from the orchestrator, never from model
  arguments**. Any document ID the model passes is checked to be in the selected set.
- **Tool allow-list (V1):**

  | Tool | Purpose | FR |
  |---|---|---|
  | `list_selected_documents()` | Names, types, page and sheet counts of the selected set | FR-055 |
  | `search_documents(query, document_ids?)` | Hybrid retrieval within the selected set → chunks with locators | FR-045 |
  | `read_pages(document_id, from, to≤from+9)` | Bounded section read (masked text) | FR-045 |
  | `get_document_table(document_id, table_ref)` | Extracted table as `TableResult` | FR-045, FR-048 |
  | `start_summary(document_id, template, focus?)` | Enqueue background summary → `job` event | FR-046 |
  | `start_comparison(document_a, document_b, focus?)` | Enqueue background comparison → `job` event | FR-047 |
  | `extract_table_to_excel(document_id, table_ref)` | Create an XLSX output | FR-048 |
  | `describe_workbook(document_id)` | Sheet profiles | FR-049 |
  | `query_sheet(document_id, sheet, operations[])` | Read-only catalogue ops → `TableResult` (no output file) | FR-049 |
  | `preview_operations(document_id \| output_id, operations[])` | Validate + run on a sample → `plan` event | FR-050, FR-051 |
  | `calculate(metric, inputs)` | Decimal engine (growth, CAGR, margin, ratio, multiple, sum, mean) with input references | FR-023 |

- **Applying operations needs explicit user confirmation.** `apply` is **not** a model
  tool. The user clicks "Apply" on the `plan` card, which calls
  `POST /api/v1/operation-previews/{id}/applications` (ADR-030). This prevents injected
  instructions from creating outputs on their own.
- Limits: ≤ 6 tool calls per turn, ≤ 3 sequential rounds.
- Tools never call the network, the LLM, code, macros or formulas.
- Versioned system prompt encoding the PRD v2.0 Response Scope Policy.

**Consequences: LOCKED:** new capabilities are only new allow-listed tools + eval
cases; nothing writes outputs without a user confirmation event.

---

### ADR-027: Document ingestion pipeline (supersedes ADR-014)

**Status:** Accepted (2026-10-07)   **Drives:** FR-012, FR-014, FR-042–FR-049, NFR-005, NFR-006, NFR-024, NFR-025

**Decision:** ingestion is a worker job (ADR-017). Parsing runs in a dedicated **`parser`
container**: no network, CPU and memory limits (4 GB), read-only root, tmpfs scratch.
The worker sends it decrypted bytes over an internal socket and receives a structured
result. Steps:

1. **Validate** (API, before accepting the upload):
   - Extension + magic bytes; size and page/slide/sheet limits (FR-042).
   - **Reject:** macro-enabled files (OOXML containing `vbaProject.bin` or a macro
     extension); encrypted or password-protected files; OOXML zip with uncompressed
     size > 500 MB, ratio > 100:1 or > 10,000 entries (zip-bomb guard).
   - Encrypt the original with the object key (ADR-028) and store it. `status=pending`.
2. **Parse** (parser container):
   - PDF/DOCX/PPTX via **Docling**: text items with page/slide provenance, headings,
     tables (TableFormer), speaker notes, comments, document metadata.
   - Pages without a text layer → **OCR** via Docling's RapidOCR engine (CPU, English),
     with segments marked `ocr=true` (FR-044).
   - XLSX via **openpyxl** (`read_only=True, data_only=True`): cached values only, no
     formula evaluation, external links ignored, comments captured. CSV via Polars with
     encoding sniffing.
3. **Injection scan:**
   - Heuristic patterns (instruction-like phrases, URLs, "system prompt", role markers).
   - **Guard model** on every non-table text segment, batched (and on comments, notes and
     metadata).
   - Flagged segments are excluded from the index and from prompts, and listed in the
     ingestion report with locators (FR-014, FR-043).
4. **Mask:** Presidio high-risk recognisers on every segment, table cell and sheet cell.
   Matches are replaced with `[TYPE]`. Counts and locators go to the report (FR-012).
   Only masked text proceeds.
5. **Structure and chunk:** section-aware chunks of ~500 tokens that never cross pages
   or slides and keep locators. Tables become `doc_tables` (cells encrypted) with a
   markdown rendering chunk for retrieval. Sheets are profiled (header detection, column
   types, counts) and stored as **encrypted Parquet per sheet**.
6. **Index:** chunk text is encrypted (ADR-028). A keyword `tsvector` and a
   `bge-small-en-v1.5` embedding (CPU, fastembed) are stored for search; this is the
   documented plaintext-derivative exception (ADR-028).
7. **Finish:** `status = ready` or `ready_with_warnings`, the encrypted ingestion report,
   and an in-app notification. Failures → `failed` with a reason code. The job is
   idempotent and resumable. A document is never marked ready unless every step committed.

- **Viewer:** PDF pages are rendered to images on demand from the encrypted original
  (pypdfium2) for users with access. DOCX/PPTX show the extracted masked text of that
  page or slide. Sheets show the cited range.

**Consequences: LOCKED:**
- Unmasked text never leaves the parser → worker boundary except into the encrypted original.
- Indexes, prompts, logs and traces only ever see masked text.
- Only `documents.ingestion` calls the parser.

**Alternatives:** Unstructured.io (heavier, mixed licensing for some components); PyMuPDF
(AGPL); VLM parsing (needs GPU memory; parked as D-15).

**Revisit when:** parsing quality is below target on the eval corpus (consider
Granite-Docling on a second GPU), or ingestion exceeds NFR-024.

---

### ADR-028: Envelope encryption at rest with per-object keys; crypto-shredding

**Status:** Accepted (2026-10-07)   **Drives:** FR-053, NFR-021, NFR-022, NFR-014

**Decision:**
- **Key hierarchy:**
  - **KEK** (key-encryption key, 256-bit) from a root-only host file mounted as a Compose
    secret. It is **never stored in the database or in database backups**, and is
    escrowed offline by the product owner.
  - **Space key** (per space, random 256-bit), wrapped by the KEK, in `space_keys`.
  - **Object key** (per document, output and conversation), wrapped by the space key, in
    `object_keys`.
- **Cipher:** AES-256-GCM (`cryptography`), with **AAD = `space_id || object_id ||
  field`** so ciphertext cannot be moved between objects or spaces. Files are encrypted
  in 1 MB chunks (streaming), each with its own nonce.
- **Encrypted:** original files, extracted/masked segments and chunk text, tables, sheet
  Parquet, profiles, ingestion reports, conversation titles, summaries, messages,
  message tables, outputs, operation logs, feedback comments, document titles and file
  names, workspace code names.
- **Plaintext by necessity (documented exception):** `tsvector` keyword index and
  embeddings of **masked** chunk text, and structural metadata (IDs, sizes, counts,
  statuses, locators, timestamps). Both live only in the DB, which sits on a
  **Azure managed disk with default server-side encryption (SSE)** with RLS and network isolation.
- **Decryption** happens only in api, worker or parser memory; temp files go to tmpfs.
  Keys are unwrapped per request and kept in memory caches for at most 5 minutes.
- **Crypto-shredding:**
  - Deleting an object deletes its `object_keys` row and data rows and files.
  - Closing a space deletes its space key and all object keys.
  - Ciphertext that remains (dead tuples, WAL, backups) becomes unreadable once the
    wrapped key is gone **and** the backups that still contain it age out (≤ 30 days,
    ADR-035).
- **Rotation:** rotating the KEK re-wraps the space keys (no data re-encryption).
  Rotating a space key re-wraps its object keys.

**Consequences: LOCKED:**
- All content fields go through `crypto.encrypt_field/decrypt_field` with the right
  AAD; no plaintext content columns.
- New content fields need an encryption decision in review.
- `crypto` is the only module importing `cryptography`.

**Alternatives:**
- Disk encryption only: doesn't protect against DB dumps or enable per-object shredding.
- pgcrypto in SQL: keys would reach the DB server.
- External KMS/HSM or Vault: preferred long-term; not available now.

**Revisit when:** an HSM, KMS or Vault becomes available (move the KEK there), or
Compliance requires encrypted search.

**Accepted residual risk:** a root administrator of the single host can read the KEK
and memory. Mitigations are restricted host access, host login auditing and Azure SSE. A
HSM is the long-term fix.


**Amendment 2026-10-07 (Azure UAT host, no Key Vault):**
- **Disk layer:** the Azure managed OS and data disks' **default server-side encryption
  (SSE, platform-managed keys)** satisfies the disk-level part of NFR-021. LUKS is not
  required.
- **App-level envelope encryption stays the primary control.** SSE only protects
  against physical media access in the Azure datacentre, not against access from inside
  the VM.
- **No Key Vault:** the KEK stays a root-only file (`/etc/foundry/kek`, mode 0400) on
  the **OS disk**, mounted as a Compose secret, escrowed offline (AQ-6).
- **Temp disk** `/mnt` (Azure ephemeral resource disk) is **not covered by SSE** unless
  encryption-at-host is enabled. It must never hold content: no Docker data-root, no
  database, no files. Container scratch uses tmpfs.
- **Revisit when** Key Vault (or Managed HSM) becomes available: move the KEK there.
---

### ADR-029: Background summary and comparison jobs with GPU fairness

**Status:** Accepted (2026-10-07)   **Drives:** FR-046, FR-047, NFR-001, NFR-004, NFR-024

**Decision:**
- `start_summary` / `start_comparison` enqueue `jobs` rows (`type=summary|comparison`,
  `user_id`, `space_id`, `priority`). Job payloads hold only IDs and template names.
- **Concurrency:** at most **2 LLM-heavy jobs** at once (a Valkey semaphore), queued
  first in, first out with **round-robin across users**, so one user's 10 documents don't
  block others.
- **Chat stays first:** chat requests are sent to vLLM with higher priority than job
  requests (vLLM priority scheduling; verify in the spike). If priority scheduling is
  unavailable, a job's map steps pause while chat queue depth exceeds 6.
- **Summary = map-reduce with citations:**
  - **Map:** the document's sections are grouped into ~6k-token windows, page-bounded.
    For each window the model writes template-specific notes; every note carries the
    chunk IDs it came from (structured output).
  - **Reduce:** merge notes per template section, hierarchically when notes exceed
    ~8k tokens. Chunk IDs are preserved.
  - **Ground:** figures are checked against the cited chunks (ADR-025 rules). Ungrounded
    bullets are dropped and listed as "couldn't verify".
  - **Store:** the encrypted summary is posted to the conversation as an assistant
    message with `sources`, and a notification is sent.
- **Comparison:**
  1. Align sections of documents A and B (heading match, then embedding similarity).
  2. Run a text diff on aligned pairs (difflib), and extract figures from both.
  3. The model explains changes per pair, citing both locators.
  4. Changed figures become a `TableResult` built from the extracted values.
- Progress (sections done / total) is in `jobs.progress`. The frontend polls
  `GET /jobs/{id}` every 3 s while the job is visible.
- Model context: map windows ~6k tokens + prompt, within `--max-model-len 16384`.

**Consequences: LOCKED:** summaries and comparisons never run in the request path;
every summary bullet keeps chunk-level provenance.

**Revisit when:** the summary p95 for 100 pages exceeds 10 min (NFR-024), or chat
TTFT degrades while jobs run.

---

### ADR-030: Typed spreadsheet operation engine and file safety

**Status:** Accepted (2026-10-07)   **Drives:** FR-023, FR-048–FR-051, NFR-024, NFR-025

**Decision:**
- **Representation:** each sheet is a Polars DataFrame persisted as encrypted Parquet
  (ADR-027), with a profile of columns, types, header row and counts.
- **Operations:** a Pydantic discriminated union, one class per catalogue operation
  (addendum FR-050 list). Each is validated against the profile: columns exist, types
  are compatible, cardinality limits respected (pivot ≤ 1,000 columns).
- **Calculated columns** use a **safe expression grammar**:
  - arithmetic, comparisons, `IF`, `ROUND`, `ABS`, `MIN`, `MAX`, column references;
  - parsed with a small hand-written or Lark grammar into Polars expressions;
  - **never `eval`**, no attribute access, no function calls outside the list.
- **Execution:** catalogue operations run in a `ProcessPoolExecutor` (in api for
  previews of up to 1,000 sampled rows, in the worker for full applies), with per-task
  `setrlimit`:
  - CPU ≤ 30 s, address space ≤ 2 GB
  - output ≤ 5M cells, wall-clock timeout 45 s

  On breach the operation is aborted with a clear message (NFR-025).
- **Preview → confirm → apply:**
  1. `preview_operations` stores an `operation_previews` row (ops JSON, input reference,
     first 20 rows) and emits `plan`.
  2. The user confirms (`POST /operation-previews/{id}/applications`).
  3. A full run writes a **new output version** (XLSX via openpyxl) with: result sheets;
     an **`Operations`** sheet (steps, parameters, timestamp); a **`Notes`** sheet
     (sources, confidentiality watermark: user, time, workspace code name).
  4. Chained operations (FR-051) use the latest output as input. `outputs.version_of_id`
     links the versions.
- **Read-only queries** (`query_sheet`) use the same engine and return a `TableResult`
  with a sheet!range locator for grounding.
- **Calculations** (`calculate`) use `Decimal` with explicit input references.
- **Formulas:** cached values only. Cells with formulas but no cached value are reported.
  Formulas are never written into outputs: outputs contain values only, which avoids
  formula injection when the file is opened. As further defence, text cells starting
  with `= + - @` are prefixed with `'`.

**Consequences: LOCKED:** no code generation or execution on user data; every output is
a new file with an Operations log; values-only outputs.

**Revisit when:** catalogue coverage falls below ~80% of real requests in pilot logs
(category counts only); then consider D-11 (sandboxed Python).

---

### ADR-031: Per-user model-cache isolation (`cache_salt`)

**Status:** Accepted (2026-10-07)   **Drives:** FR-041, NFR-020

**Decision:**
- Every request from the `llm` gateway to vLLM (chat and guard) includes
  `cache_salt = base64url(HMAC-SHA256(salt_secret, user_id))`.
- Background jobs use the job owner's salt. The scope router and guard calls use the
  same per-user salt.
- `salt_secret` is a Compose secret.
- Consequence: prefix caching is effective within a user's own conversation turns but
  not across users. The system prompt is recomputed once per user session.
- The spike measures the TTFT impact (NFR-001) and confirms that the pinned vLLM version
  honours `cache_salt`. If it doesn't, **disable prefix caching** (`--no-enable-prefix-caching`)
  rather than share it.

**Consequences: LOCKED:** the `llm` gateway refuses to send a request without a salt
(except health checks).

---

### ADR-032: Metadata-only, hash-chained audit (supersedes ADR-016)

**Status:** Accepted (2026-10-07)   **Drives:** FR-017, FR-031, FR-032, FR-054, NFR-017, NFR-023

**Decision:**
- The hash chain, partitioning, immutability (no UPDATE/DELETE grant, trigger), daily
  verification and retention anchors are **as in ADR-016**.
- **Payloads are metadata only, enforced by type.** Each event type has a Pydantic model
  whose fields are restricted to: UUIDs, enums, integers, booleans, timestamps,
  durations, model/prompt version strings from an allow-list, and **keyed hashes**
  (HMAC-SHA256 with an audit key; not plain hashes, which would allow guessing-based
  confirmation of content). **No free-text `str` fields are allowed.** A CI check
  rejects audit models with unconstrained strings.
- No titles, file names, workspace names, prompts, answers or error messages in the audit.
- Compliance reads the audit (FR-032). Admin cannot (ADR-023 roles).
- Break-glass events (`compliance.grant_*`, `compliance.content_viewed`) carry a `high`
  priority flag.

**Consequences: LOCKED:** `audit.writer.record(EventModel)` is the only entry point,
and event models follow the metadata-only rule.

---

### ADR-033: Observability without content (supersedes ADR-019)

**Status:** Accepted (2026-10-07)   **Drives:** NFR-012, NFR-016, NFR-023

**Decision:**
- Correlation IDs as in ADR-019.
- **Logs:** structlog JSON with a **field allow-list** (timestamp, level, correlation_id,
  user_id, space_id, module, event code, latency_ms, counts, status codes).
  - Exceptions are logged as `type + module + code location + error code`.
  - **Exception messages are not logged**, because parser and DB errors can embed content.
  - Request and response bodies are never logged.
  - The PII and canary scrubber stays as a final net.
- **Traces:** OpenTelemetry spans with an attribute allow-list (no prompts, outputs or
  document text), exported to **Jaeger all-in-one** (Apache-2.0) under the optional
  Compose profile `observability`, with short retention (7 days).
  **Langfuse is dropped:** its value is prompt/response capture, which conflicts with
  confidentiality.
- **Eval and quality runs** use only the **synthetic test corpus** in staging (ADR-037),
  and write reports as files. No production content is ever used for evals.
- Metrics, `/healthz` and `/readyz` as in ADR-019. Readiness includes the parser
  container and the KEK being loaded.

**Consequences: LOCKED:** use `core/obs.py` helpers; new span attributes must be
allow-listed; the canary suite runs every release.

---

### ADR-034: Zero-egress deployment and network isolation (supersedes ADR-020)

**Status:** Accepted (2026-10-07)   **Drives:** NFR-008, NFR-009, NFR-012, NFR-013, NFR-019, NFR-025

**Decision:** as ADR-020, **minus the egress proxy**. The running system has **no
outbound internet route**.

| Network | Members |
|---|---|
| `edge` | Caddy only (8043; inside the exposed 8000–8050 range) |
| `app` (internal) | caddy, api, worker, postgres, valkey, jaeger* |
| `inference` (internal) | api, worker, vllm-chat, vllm-guard |
| `parsing` (internal) | worker, parser |

- **Host firewall:** default-deny outbound. The only allowed internal destination is
  the off-host backup target (ADR-035). DNS for external names is not configured in
  containers.
- **Offline bundles:** vLLM model weights, guard model, `bge-small-en-v1.5`, Docling
  layout/TableFormer and RapidOCR models, spaCy model and Presidio configs are
  pre-staged under `/models` (read-only mounts), with `HF_HUB_OFFLINE=1` and
  `DOCLING_ARTIFACTS_PATH` set.
- **Updates:** container images and models arrive by `docker save` / `load` on approved
  media or an internal registry, following a documented maintenance procedure with
  checksum verification (runbook).
- TLS, CSP (`default-src 'self'; img-src 'self' data:; connect-src 'self'`), HSTS, the
  Caddy static SPA and restart policies are as in ADR-020.
- The **parser** container runs as non-root with a read-only root filesystem, tmpfs
  `/tmp`, `mem_limit: 4g`, `cpus` limit, no network except `parsing`, and seccomp
  default.

**Consequences: LOCKED:** no code may assume internet access; any new external
dependency must be bundled offline.


**Amendment 2026-10-07 (Azure VM):**
- **Zero egress** is enforced at two layers:
  1. The **Azure Network Security Group** has an outbound rule *deny Internet*, allowing
     only the VNet and the backup storage private endpoint.
  2. Host nftables default-deny.
- Azure "default outbound access" must be disabled for the subnet. Instance metadata
  (169.254.169.254) is allowed only to the host OS, not to containers.
- **Docker `data-root`** moves to the persistent data disk (`/data/docker`). The root
  disk (62 GB) is too small, and `/mnt` is ephemeral.

**Amendment 2026-10-07 (UAT network facts):**
- Outbound internet is **already blocked** at the Azure network level, which satisfies
  NFR-008. The host firewall stays as defence in depth.
- Only the inbound port range **8000–8050** is exposed. **Caddy listens on `8043`**
  (inside the range) instead of 443. Users reach `https://<host>:8043`. Ports
  8000–8050 must not be bound by any other service.
- No other service binds to an exposed port. vLLM, Postgres, Valkey, parser and Jaeger
  stay on internal Docker networks.
- TLS, HSTS and `__Host-` cookies all work on a non-standard port.
---

### ADR-035: Data lifecycle: retention, deletion and backups

**Status:** Accepted (2026-10-07)   **Drives:** FR-003, FR-008, FR-052, FR-053, NFR-014, NFR-022

**Decision:**
- **Expiry fields:** every content root (document, output, conversation) has
  `expires_at = created_at + retention_days` (setting, default 30, allowed 7–90).
  Derived rows inherit through their root. A workspace owner can extend once by 30 days
  (`retention_extended_at`).
- **Lifecycle worker job (hourly):**
  - (a) in-app warnings at T-7 days;
  - (b) for expired roots, run the **delete pipeline**.
- **Delete pipeline** (also used for on-demand delete and workspace close):
  1. Mark `deleted_at` (hidden immediately via RLS/app filters).
  2. Delete the object keys (crypto-shred).
  3. Delete chunk, table, sheet, message and output rows and the encrypted files.
  4. Remove previews and download tokens.
  5. Record the audit event (metadata).
  6. Run `VACUUM` on affected tables in the nightly window so dead tuples holding
     plaintext index derivatives are reclaimed.

  Steps 1–2 complete synchronously within seconds. The rest finishes ≤ 15 min (NFR-022).
- **WAL:** `wal_keep_size` is minimal; no long-term WAL archiving.
- **Backups:** nightly `pg_dump` (custom format) + rsync of the encrypted file store,
  both encrypted with a separate backup key, pushed to the off-host target.
  - **Retention: 30 rolling daily backups, no monthly copies.** Within 30 days of
    deletion no backup holds the wrapped object key, satisfying NFR-022.
  - **The KEK is not in backups.** It is escrowed separately (ADR-028).
  - Restore drill quarterly (RPO 24 h / RTO 4 h).
- **Deactivated users (FR-003):** their content follows normal expiry. An admin can
  trigger "delete all content of user X" (an audited job) without seeing any content.

**Consequences: LOCKED:** every new content table defines its root and joins the delete
pipeline; no long-lived copies outside this pipeline (no exports retained beyond 24 h).


**Amendment 2026-10-07 (Azure):**
- **Backups:** application-level backups (encrypted `pg_dump` + encrypted file store
  sync) go to a **private Azure Storage account** (private endpoint, no public access),
  with lifecycle rules deleting blobs after 30 days.
- **Do not use whole-VM Azure Backup or disk snapshots** for this VM, or if IT requires
  it, use selective-disk backup **excluding the OS disk that holds the KEK** with
  retention ≤ 30 days. Whole-VM snapshots would capture the KEK alongside the data and
  keep it beyond the deletion window (NFR-022).

**Amendment 2026-10-07 (interim backups on UAT):**
- Until IT provides the private storage account, nightly encrypted backups (`pg_dump` +
  file-store sync, encrypted with the backup key) are written to
  **`/data/backups/`** on the persistent data disk, keeping **2 days**.
  - This is outside the code repository and never committed to git; the path is
    git-ignored as a safeguard.
  - Never on `/mnt` (ephemeral) or the OS disk (where the KEK lives).
- **Limitation:** same-VM backups don't survive loss of the VM or its data disk, so
  NFR-014 (RPO 24 h / RTO 4 h after host loss) is **not met** in this interim mode.
  That's acceptable on UAT because it holds only the synthetic corpus (Q23).
- **Blocking condition:** off-host backups to the IT-provided storage (30-day lifecycle)
  must be live **before any real deal document is uploaded**.
- The backup job is configurable (`APP_BACKUP_TARGET=local|azure_blob`,
  `APP_BACKUP_RETENTION_DAYS`), so switching later is a configuration change.
---

### ADR-036: Compliance break-glass access

**Status:** Accepted (2026-10-07)   **Drives:** FR-005, FR-054, FR-032, NFR-020

**Decision:**
- `break_glass_grants` row fields:
  - `requester_id` (compliance), `approver_id` (optional; required if the two-person
    setting is on, addendum Q17)
  - `case_ref`, `reason_enc` (encrypted with a compliance-space key), `target_type`
    (user | space | object) and `target_id`
  - `starts_at`, `expires_at` (≤ 24 h), `status`
- **RLS** policies include a clause permitting **SELECT** (never write) on target-scoped
  content when the transaction sets `app.grant_id` to an active grant held by the
  current compliance user.
- **Compliance viewer:** a separate read-only UI under `/compliance/grants/{id}`. Every
  object opened records `compliance.content_viewed` (high priority). No downloads unless
  the grant has `allow_export=true`; exports are watermarked "Compliance review".
- When the grant ends, the affected user gets an in-app notice unless the grant records
  an exception flag (with case_ref).

**Consequences: LOCKED:** break-glass is the only path by which a non-member can read
content.

---

### ADR-037: Test and evaluation strategy v2 (supersedes ADR-022)

**Status:** Accepted (2026-10-07)   **Drives:** NFR-005, NFR-006, NFR-010, NFR-020, NFR-022, NFR-023, NFR-025

**Decision (strategy only):**
- Unit and integration tests as in ADR-022 (fake LLM server, real Postgres/Valkey with RLS enabled).
- **Synthetic confidential corpus** in `evals/corpus/`: fabricated CIMs, term-sheet
  versions, SPAs, decks and financial workbooks with planted identifiers, injections and
  canaries. **Real deal documents are never used in tests, evals or staging.**
- Release-gate suites (addendum v2.0 sizes): identifier (chat + files), red-team
  (≥ 50 document-embedded), **isolation (≥ 100)**, eval (≥ 80, summary rubric), Excel
  reference outputs (40), **malicious files (30)**, **canary** (logs, traces, audit,
  errors) and **deletion** (DB, files, index, restored backup).
- Load test: 10 chat users + 2 summary jobs, 30-min soak (NFR-001/004).
- Fault injection: guard down, parser down, KEK missing, RLS context missing; expect
  refusal (NFR-013).
- Any model, prompt, guard, parser or operation-catalogue change runs all suites before release.

---

### ADR-038: Module boundaries v2 (supersedes ADR-001)

**Status:** Accepted (2026-10-07)   **Drives:** NFR-015, NFR-019; parallel stories

**Decision:** modular monolith as ADR-001 (pattern unchanged). Module list:
```
backend/app/
  core/        config, db (RLS context helper), errors, ids, time, obs
  crypto/      key hierarchy, field/file encryption, shredding           (NO fastapi imports)
  auth/        users, sessions, links, roles
  spaces/      spaces, membership, AccessContext, RLS policies (migrations)
  chat/        conversations, selected set, messages, SSE, turn orchestration
  assistant/   agent, prompts, tool registry, context builder            (NO fastapi imports)
  guardrails/  identifier gate, guard client, scope router, output gate, grounding, templates (NO fastapi)
  llm/         vLLM gateway, profiles, cache_salt, priorities             (NO fastapi imports)
  documents/   upload validation, ingestion orchestration, chunks, tables, retrieval, viewer (NO fastapi in core logic)
  sheets/      sheet storage, profiles, operation catalogue + engine, calculations, previews (NO fastapi imports)
  outputs/     output files, versions, watermarking, download tokens
  jobs/        job queue, scheduler entry, summary/comparison/lifecycle jobs
  lifecycle/   expiry, delete pipeline, backups trigger
  audit/       metadata-only writer, chain, verify, queries
  compliance/  break-glass grants, compliance viewer routers
  admin/       users, settings, overview (metadata only), alerts
  api/         router assembly, middleware, health
parser/        separate container entrypoint (Docling, OCR, openpyxl); no app DB access
frontend/ · deploy/ · evals/ (incl. corpus/)   # no prototype/ (amended 2026-10-07: new clean repo)
```
Dependency rules as ADR-001. Only `crypto` imports `cryptography`. Only `llm` talks to
vLLM. Only `documents.ingestion` talks to `parser`. Only `spaces` defines
`AccessContext`. import-linter enforced.

---

## 4. Component Design

### Component Overview
```
Browser (React SPA) ── HTTPS (cookie session + CSRF) ─► Caddy (TLS, CSP, static SPA)
                                                         │
                                         ┌───────────────▼────────────────┐
                                         │ API (FastAPI)                   │
                                         │ auth · spaces · chat (SSE)      │
                                         │ guardrails · assistant · llm    │
                                         │ documents(read) · sheets(preview)│
                                         │ outputs · admin · compliance    │
                                         │ audit · crypto                  │
                                         └──┬────────────┬────────────┬────┘
                     inference (internal)    │            │ app        │
        ┌───────────────┐  ┌──────────────┐ │   ┌────────▼─────┐  ┌───▼────┐
        │ vllm-chat GPU │  │ vllm-guard GPU│◄┘   │ PostgreSQL 18│  │ Valkey │
        └───────▲───────┘  └──────▲───────┘     │ + pgvector    │  └───▲────┘
                │                 │             │ (RLS, SSE)    │      │
        ┌───────┴─────────────────┴──────┐      └───────▲───────┘      │
        │ Worker: jobs (ingestion, summary,│─────────────┘─────────────┘
        │ comparison, apply ops, lifecycle,│
        │ audit verify, backups)           │── parsing (internal) ──► Parser container
        └──────────────────────────────────┘                          (Docling, OCR, openpyxl;
                                                                       no network, rlimits)
Encrypted file store /data (Azure SSE managed disk) · Jaeger (optional profile) · off-host backup target
```

### Turn sequence (document Q&A)
1. `POST /conversations/{id}/messages` → stage 0 (auth, CSRF, rate limit) → stage 1
   (identifier gate) → stage 2 (guard) → stage 3 (scope router) → stage 4 (access check
   on the conversation space + selected set).
2. Persist the user message (encrypted). Open SSE: `meta`, `status`.
3. Agent with tools (`search_documents`, `read_pages`, `get_document_table`,
   `query_sheet`, `calculate`, `preview_operations`, `start_summary` …), each with an
   `AccessContext` + selected-set check. Tabular results → `table`. Previews → `plan`.
   Background starts → `job`.
4. Tokens → output gate (hold-back + identifier + grounding + URL strip) → `delta`.
5. Final moderation. Then `sources`, persist the assistant message (encrypted), write
   the metadata audit event, `done`.

### Ingestion sequence
Upload (API: validate, encrypt the original, `pending`) → worker job → parser (Docling /
OCR / openpyxl) → worker: injection scan (guard) → mask → chunk/tables/sheets → encrypt
+ index → `ready` + report + notification.

### Components
| Component | Responsibility | Data owned | ADRs |
|---|---|---|---|
| **auth** | Users, sessions, links, roles | users, sessions, password_tokens | 006, 023 |
| **spaces** | Spaces, membership, `AccessContext`, RLS policy migrations | spaces, space_members | 023 |
| **crypto** | Key hierarchy, field/file encryption, shredding | space_keys, object_keys | 028 |
| **chat** | Conversations, selected set, messages, SSE turn orchestration | conversations, conversation_documents, messages, message_tables, message_sources, feedback | 002, 003, 015, 024, 025 |
| **guardrails** | Identifier gate, guard client, scope router, output gate, grounding, templates | — (files) | 024, 025 |
| **assistant** | Agent, prompts, tool registry, context builder | prompt files | 026, 015 |
| **llm** | vLLM gateway, profiles, `cache_salt`, priorities | — | 009, 029, 031 |
| **documents** | Upload validation, ingestion orchestration, chunks/tables, retrieval, viewer | documents, doc_segments, chunks, doc_tables, ingestion_reports | 027, 028, 023 |
| **parser** (container) | Untrusted-file parsing: Docling, OCR, openpyxl, CSV | none (stateless) | 027, 034 |
| **sheets** | Sheet storage/profile, operation catalogue + engine, safe expressions, calculations, previews/applies | sheets, operation_previews | 030 |
| **outputs** | Output files, versions, watermark, download tokens | outputs, download_tokens | 030, 035 |
| **jobs** | Queue, scheduler, summary/comparison/ingestion/apply/lifecycle jobs, GPU semaphore | jobs | 017, 029 |
| **lifecycle** | Expiry warnings, delete pipeline, backup trigger | — | 035 |
| **audit** | Metadata-only chained events, verify, queries | audit_events, audit_anchors | 032 |
| **compliance** | Break-glass grants, compliance viewer, audit search/export | break_glass_grants | 036, 032 |
| **admin** | User management, settings, counts-only overview, alerts | app_settings, admin_alerts | 023 |
| **frontend** | Login, spaces & workspaces, upload & reports, chat (selected set, citations viewer, plan cards, job cards), downloads, admin, compliance | — | 008, 025 |

---

## 5. Data Model

> ADR-005 conventions (UUIDv7 PK, `created_at`/`updated_at`). **`enc` = AES-256-GCM
> ciphertext (bytea) under the object's key (ADR-028).** Every content table has
> `space_id NOT NULL` + RLS (ADR-023).

### Identity, spaces, keys
- **users:** `username`, `email` (citext unique), `role` (`user`|`admin`|`compliance`), `status`, `password_hash`, `failed_login_count`, `locked_until`, `last_login_at`.
- **sessions**, **password_tokens:** as v1.1 (ADR-006).
- **spaces:** `kind` (`private`|`workspace`), `owner_user_id`, `code_name_enc`, `retention_days`, `retention_extended_at`, `closed_at`. Unique private space per user.
- **space_members:** PK (`space_id`, `user_id`), `role` (`owner`|`member`), `added_by`, `added_at`.
- **space_keys:** `space_id` (unique), `wrapped_key`, `kek_version`.
- **object_keys:** `object_type`, `object_id` (unique pair), `space_id`, `wrapped_key` (wrapped by the space key).

### Documents & sheets
- **documents:** `space_id`, `uploader_id`, `kind` (`pdf`|`docx`|`pptx`|`xlsx`|`csv`), `title_enc`, `filename_enc`, `size_bytes`, `content_hmac` (duplicate detection within the space), `page_count`, `slide_count`, `sheet_count`, `status` (`pending`|`processing`|`ready`|`ready_with_warnings`|`failed`|`rejected`), `status_code`, `ocr_page_count`, `masked_counts` (jsonb: type → count), `flagged_count`, `file_path` (encrypted file), `expires_at`, `deleted_at`.
- **ingestion_reports:** `document_id`, `space_id`, `report_enc` (locators of masked and flagged items, OCR pages, tables).
- **doc_segments:** `document_id`, `space_id`, `locator` (page/slide), `kind` (`text`|`heading`|`note`|`comment`|`metadata`), `text_enc` (masked), `ocr`, `flagged`.
- **chunks:** `document_id`, `space_id`, `locator_from`, `locator_to`, `chunk_index`, `text_enc` (masked), `tsv` (tsvector, GIN), `embedding vector(384)` (HNSW cosine), `flagged`. *(Plaintext-derivative exception: `tsv`, `embedding`; ADR-028.)*
- **doc_tables:** `document_id`, `space_id`, `locator`, `table_index`, `n_rows`, `n_cols`, `cells_enc` (masked), `caption_enc`.
- **sheets:** `document_id`, `space_id`, `sheet_index`, `name_enc`, `parquet_path` (encrypted), `profile_enc`, `row_count`, `col_count`, `formula_without_cache_count`.

### Conversations & outputs
- **conversations:** `space_id`, `owner_user_id`, `title_enc`, `summary_enc`, `summary_upto_message_id`, `expires_at`, `deleted_at`.
- **conversation_documents:** PK (`conversation_id`, `document_id`) (same `space_id` enforced).
- **messages:** `conversation_id`, `space_id`, `role`, `content_enc`, `status`, `decline_category`, `model_name`, `prompt_version`, `correlation_id`, `latency_ms`, `token_usage`.
- **message_tables:** `message_id`, `space_id`, `title_enc`, `columns_enc`, `rows_enc`, `source_locators` (jsonb of document_id + locator).
- **message_sources:** `message_id`, `space_id`, `kind`, `document_id`, `locator`.
- **feedback:** `message_id`, `user_id`, `space_id`, `rating`, `comment_enc`.
- **operation_previews:** `space_id`, `owner_user_id`, `input_ref` (document sheet or output), `ops` (jsonb, validated catalogue ops; no content), `preview_enc`, `expires_at` (1 h), `applied_output_id`.
- **outputs:** `space_id`, `owner_user_id`, `kind` (`xlsx`|`csv`|`docx`), `origin` (`operations`|`table_extract`|`summary`|`comparison`), `source_document_ids` (uuid[]), `version_of_id`, `file_path` (encrypted), `ops_log_enc`, `expires_at`, `deleted_at`.
- **download_tokens:** `output_id`, `user_id`, `token_hash`, `expires_at` (24 h), `used_count`.

### Platform
- **jobs:** `type` (`ingest`|`summary`|`comparison`|`apply_ops`|`lifecycle`|`audit_verify`|`backup`|`user_purge`), `user_id`, `space_id`, `payload` (IDs and enums only), `priority`, `status`, `progress` (jsonb counts), `attempts`, `run_after`, `error_code`.
- **notifications:** `user_id`, `space_id`, `kind`, `object_id`, `read_at` (no content; the UI fetches titles through access-checked endpoints).
- **audit_events**, **audit_anchors:** ADR-032 (metadata-only).
- **break_glass_grants:** ADR-036.
- **app_settings**, **admin_alerts:** as v1.1 (settings now include retention days, quotas, rate limits, starter prompts, two-person rule).

### Storage Strategy
- **Primary store:** PostgreSQL 18 + pgvector on an Azure managed data disk (default SSE), with RLS.
- **Files:** `/data/files/{space_id}/{object_id}` (chunked AES-GCM) on the Azure managed data disk (default SSE).
- **Cache:** Valkey (no content).
- **Retention:** 30 days for content (ADR-035); audit 5 years (metadata); backups 30
  rolling days, encrypted, off-host. The KEK is escrowed separately and never in backups.

---

## 6. API Specifications

**Protocol:** REST/JSON + SSE (ADR-002). **AuthN:** session cookie + CSRF (ADR-006).
**AuthZ:** role + `AccessContext` (ADR-023). **Errors:** RFC 9457 (ADR-003). Not
accessible means **404**.

### Auth
`POST /api/v1/auth/login`† · `POST /auth/logout` · `GET /auth/me` · `POST /auth/set-password`† · `POST /auth/change-password` (as v1.1).

### Spaces
| Method & path | Purpose |
|---|---|
| `GET /api/v1/spaces` | The user's private space + workspaces they belong to |
| `POST /api/v1/spaces` | Create a workspace {code_name} |
| `PATCH /api/v1/spaces/{id}` | Rename (owner) |
| `DELETE /api/v1/spaces/{id}` | Close the workspace → delete pipeline (owner; confirm token) |
| `GET/POST /api/v1/spaces/{id}/members` · `DELETE /api/v1/spaces/{id}/members/{user_id}` | Membership (owner) |
| `POST /api/v1/spaces/{id}/retention-extensions` | One-time +30 days (owner) |

### Documents
| Method & path | Purpose |
|---|---|
| `POST /api/v1/spaces/{id}/documents` (multipart) | Upload → 202 + document (status `pending`); validation errors 422 with `code` (`file_type_unsupported`, `macro_file_rejected`, `encrypted_file_rejected`, `file_too_large`, `zip_bomb_suspected`) |
| `GET /api/v1/spaces/{id}/documents` | List (title decrypted for members) |
| `GET /api/v1/documents/{id}` | Status, counts, expiry |
| `GET /api/v1/documents/{id}/report` | Ingestion report (FR-043) |
| `GET /api/v1/documents/{id}/view?locator=` | Page image / slide text / sheet range for citations |
| `POST /api/v1/documents/{id}/moves` | {target_space_id, confirm: true}: private → workspace only |
| `DELETE /api/v1/documents/{id}` | Permanent delete (FR-053) |

### Chat
| Method & path | Purpose |
|---|---|
| `GET/POST /api/v1/conversations` | List own / create {space_id} |
| `GET/PATCH/DELETE /api/v1/conversations/{id}` | Read / rename / permanent delete |
| `PUT /api/v1/conversations/{id}/documents` | Set the selected set {document_ids} (same space, ready) |
| `POST /api/v1/conversations/{id}/messages` | **SSE** turn (events per ADR-003 + ADR-025) |
| `PUT /api/v1/messages/{id}/feedback` | Rating |
| `GET /api/v1/starter-prompts` | Examples |

### Sheets, outputs, jobs, notifications
| Method & path | Purpose |
|---|---|
| `GET /api/v1/documents/{id}/sheets` | Sheet profiles |
| `GET /api/v1/operation-previews/{id}` | Plan + preview |
| `POST /api/v1/operation-previews/{id}/applications` | **User-confirmed apply** → 202 job → output |
| `GET /api/v1/outputs?space_id=` · `GET /api/v1/outputs/{id}` · `DELETE /api/v1/outputs/{id}` | Outputs and versions |
| `POST /api/v1/outputs/{id}/download-links` → `GET /api/v1/downloads/{token}` | 24 h watermarked download (FR-024) |
| `GET /api/v1/jobs/{id}` | Job status and progress (owner only) |
| `GET /api/v1/notifications` · `POST /api/v1/notifications/{id}/read` | In-app notices |

### Admin (`admin` role; **no content**)
`GET/POST /admin/users` · `PATCH /admin/users/{id}` · `POST /admin/users/{id}/set-password-links` · `POST /admin/users/{id}/session-revocations` · `POST /admin/users/{id}/content-purges` (deletes a user's private-space content without viewing it; audited) · `GET/PUT /admin/settings` · `GET /admin/overview` (counts) · `GET /admin/alerts`.

### Compliance (`compliance` role)
`GET /compliance/audit-events` · `POST /compliance/audit-exports` · `POST /compliance/break-glass-grants` · `POST /compliance/break-glass-grants/{id}/approvals` · `GET /compliance/break-glass-grants/{id}/objects` · `GET /compliance/break-glass-grants/{id}/objects/{object_id}/view` (read-only; each view audited).

### Platform
`/healthz`, `/readyz` (DB, Valkey, vLLM chat+guard, parser, KEK loaded), `/metrics` (internal).

**Limits:**
- Chat 10/min, 300/day; uploads 50/day; 2 GB active storage per user.
- Login 10/min/IP; admin 60/min.
- Upload body ≤ 50 MB (Caddy `request_body max_size`).

---

## 7. FR / NFR Coverage Matrix

| ID | Type | Requirement | Component(s) | ADR(s) | Status |
|----|------|-------------|--------------|--------|--------|
| FR-001 | FR | Admin creates user; manual link | auth, admin | 006, 023 | Addressed |
| FR-002 | FR | Sign in/out, lockout | auth | 006 | Addressed |
| FR-003 | FR | Deactivate, revoke; content prompt | auth, admin, lifecycle | 006, 035 | Addressed |
| FR-004 | FR | Email reset | — | — | Deferred (Won't; no email) |
| FR-005 | FR | Three roles; admin no content | auth, spaces | 023, 032, 036 | Addressed |
| FR-006 | FR | Streamed answers | chat, frontend | 002, 003, 025 | Addressed |
| FR-007 | FR | Follow-ups | assistant | 015, 026 | Addressed |
| FR-008 | FR | History | chat | 005, 023, 035 | Addressed |
| FR-009 | FR | Citations + viewer | chat, documents | 025, 027 | Addressed |
| FR-010 | FR | Starter prompts | chat, admin | 021 | Addressed |
| FR-011 | FR | Ratings | chat | 028 | Addressed |
| FR-012 | FR | Block IDs in chat; mask in documents | guardrails, documents | 024, 027 | Addressed |
| FR-013 | FR | Off-topic declines | guardrails | 024, 037 | Addressed |
| FR-014 | FR | Injection resistance | documents, guardrails, assistant | 024, 026, 027 | Addressed |
| FR-015 | FR | Output screening; no URLs/images | guardrails, frontend | 025, 034 | Addressed |
| FR-016 | FR | Typed read-only tools; no code | assistant, sheets | 026, 030 | Addressed |
| FR-017 | FR | Block/flag counts | admin, audit | 032 | Addressed |
| FR-018 | FR | Stock quote | — | 013 (retired) | Retired (Won't) |
| FR-019 | FR | Price history | — | 013 (retired) | Retired (Won't) |
| FR-020 | FR | Index performance | — | 013 (retired) | Retired (Won't) |
| FR-021 | FR | MF NAV | — | 013 (retired) | Retired (Won't) |
| FR-022 | FR | Instrument comparison | — | 013 (retired) | Retired (Won't) |
| FR-023 | FR | Deterministic calculations | sheets | 026, 030 | Addressed |
| FR-024 | FR | Watermarked expiring downloads | outputs | 030, 035 | Addressed |
| FR-025 | FR | Market-data freshness | — | 013 (retired) | Retired (Won't) |
| FR-026 | FR | Data-source kill switch | — | 013 (retired) | Retired (Won't) |
| FR-027 | FR | Company filings | — | 013 (retired) | Retired (Won't) |
| FR-028 | FR | Public library | — | 014 → 027 | Retired (Won't) |
| FR-029 | FR | Library citations | — | 014 → 027 | Retired (Won't) |
| FR-030 | FR | "Not in documents" | assistant, guardrails | 025, 026 | Addressed |
| FR-031 | FR | Metadata-only audit | audit | 032 | Addressed |
| FR-032 | FR | Compliance audit search/export | compliance, audit | 032, 036 | Addressed |
| FR-033 | FR | Counts-only overview | admin | 032, 033 | Addressed |
| FR-034 | FR | UPSI decline | — | — | Retired (contained by 023) |
| FR-035 | FR | Forecast refusal | — | — | Retired (grounding 025) |
| FR-036 | FR | Figure grounding | guardrails, jobs | 025, 029 | Addressed |
| FR-037 | FR | Rate limits and quotas | api, guardrails | 018, 024 | Addressed |
| FR-038 | FR | Change password | auth | 006 | Addressed |
| FR-039 | FR | Private space | spaces | 023 | Addressed |
| FR-040 | FR | Deal workspaces | spaces, frontend | 023 | Addressed |
| FR-041 | FR | Isolation; no admin content | spaces, llm, all repos | 023, 031, 032 | Addressed |
| FR-042 | FR | Upload with validation | documents, parser | 027, 034 | Addressed |
| FR-043 | FR | Ingestion report | documents | 027 | Addressed |
| FR-044 | FR | OCR | parser | 027 | Addressed |
| FR-045 | FR | Q&A over selected documents | assistant, documents | 026, 027 | Addressed |
| FR-046 | FR | Background summaries | jobs, assistant | 029, 025 | Addressed |
| FR-047 | FR | Document comparison | jobs | 029 | Addressed |
| FR-048 | FR | Table extraction to Excel | documents, outputs | 026, 027, 030 | Addressed |
| FR-049 | FR | Workbook profile + Q&A | sheets | 027, 030 | Addressed |
| FR-050 | FR | Typed operations, preview → confirm | sheets, outputs | 026, 030 | Addressed |
| FR-051 | FR | Chained operations, versions | sheets, outputs | 030 | Addressed |
| FR-052 | FR | 30-day auto-deletion | lifecycle, jobs | 035 | Addressed |
| FR-053 | FR | Permanent deletion | lifecycle, crypto | 028, 035 | Addressed |
| FR-054 | FR | Break-glass | compliance | 036 | Addressed |
| FR-055 | FR | Selected document set | chat | 023, 026 | Addressed |
| NFR-001 | NFR | TTFT ≤ 3 s / 8 s with jobs running † | llm, chat, jobs | 009, 015, 029, 031 | Addressed (confirm in spike) |
| NFR-002 | NFR | ≥ 15 tok/s † | vLLM | 009 | Addressed (confirm in spike) |
| NFR-003 | NFR | Guard overhead | guardrails | 024 | Addressed |
| NFR-004 | NFR | 10 chats + 2 jobs soak | llm, jobs | 009, 029, 037 | Addressed |
| NFR-005 | NFR | Identifier recall ≥ 99% | guardrails, documents | 024, 027, 037 | Addressed |
| NFR-006 | NFR | Injection resistance | documents, guardrails | 025, 026, 027 | Addressed |
| NFR-007 | NFR | ASVS L2 | auth | 006 | Addressed |
| NFR-008 | NFR | Zero egress | deploy | 034 | Addressed |
| NFR-009 | NFR | TLS, secrets | deploy | 021, 034 | Addressed |
| NFR-010 | NFR | Output quality | assistant, sheets, jobs | 025, 029, 030, 037 | Addressed |
| NFR-011 | NFR | Usability, WCAG | frontend | 008 | Addressed |
| NFR-012 | NFR | 99% business hours | deploy, obs | 033, 034 | Addressed (single host; §9) |
| NFR-013 | NFR | Fail closed | guardrails, spaces, deploy | 023, 024, 037 | Addressed |
| NFR-014 | NFR | Backups RPO/RTO | lifecycle | 035 | Addressed (target AQ-4) |
| NFR-015 | NFR | Swappable model | llm | 009, 038 | Addressed |
| NFR-016 | NFR | Content-free observability | obs | 033 | Addressed |
| NFR-017 | NFR | Audit integrity | audit | 032 | Addressed |
| NFR-018 | NFR | Market-data discipline | — | 013 (retired) | Retired (Won't) |
| NFR-019 | NFR | One-command deploy | deploy | 034, 038 | Addressed |
| NFR-020 | NFR | Isolation suite 0 violations | spaces, llm | 023, 031, 037 | Addressed |
| NFR-021 | NFR | Encryption at rest | crypto | 028 | Addressed (documented index exception) |
| NFR-022 | NFR | Deletion guarantees | lifecycle, crypto | 028, 035 | Addressed |
| NFR-023 | NFR | No content in logs/traces/audit | obs, audit | 032, 033 | Addressed |
| NFR-024 | NFR | Processing performance † | parser, jobs, sheets | 027, 029, 030 | Addressed (benchmark in spike) |
| NFR-025 | NFR | Spreadsheet and file safety | parser, sheets | 027, 030, 034 | Addressed |

### Detailed NFR notes
- **NFR-020 (isolation):** three independent controls.
  1. Repository `AccessContext` filters.
  2. Postgres RLS with `FORCE`; no context means no rows.
  3. Per-user `cache_salt` on vLLM.
  The admin path has no user context, so it can't read content even through a bug in
  admin code. The ≥ 100-case suite exercises every route.
- **NFR-021/022 (encryption and deletion):** per-object keys allow precise
  crypto-shredding. The KEK lives outside DB backups. A 30-day backup window bounds
  recoverability. Plaintext derivatives (`tsv`, embeddings of masked text) are limited
  to the SSE-protected DB, removed by the delete pipeline and reclaimed by nightly VACUUM.
- **NFR-001/004 (GPU sharing):**
  - The KV cache (~105k tokens) is shared by 10 chats (≤ 8k each) and 2 map jobs (≤ ~7k each).
  - Chat has priority. Per-user `cache_salt` lowers prefix-cache reuse across users, so
    first-turn TTFT includes system-prompt prefill: ~1.5–2k tokens, ≈ 0.5–1 s on the A10
    (to measure).
- **NFR-025 (file safety):** macro and encrypted files rejected; zip-bomb limits;
  sandboxed parser (no network, 4 GB, read-only root); rlimited operation execution;
  values-only outputs with formula-injection escaping.

---

## 8. Technology Stack

| Layer | Choice | Rationale (→ driver) | ADR |
|---|---|---|---|
| Frontend | React 19 + TS + Vite; Tailwind + shadcn/ui; TanStack Query/Table; React Router; openapi-typescript; react-markdown + rehype-sanitize (`cite:` links only) | Owner decision; accessible UI; safe rendering → FR-015 | 008, 025 |
| Backend | FastAPI, Pydantic v2, uvicorn (Python 3.13) | Async SSE → NFR-001 | 002, 038 |
| DB | PostgreSQL 18 + pgvector 0.8+, RLS; SQLAlchemy 2 async + asyncpg; Alembic | One store, RLS isolation → NFR-020 | 005, 023 |
| Crypto | `cryptography` (AES-256-GCM, HKDF/HMAC); Azure managed-disk SSE (platform keys) | Envelope encryption → NFR-021/022 | 028 |
| Auth | pwdlib[argon2], opaque sessions | ASVS L2 → NFR-007 | 006 |
| Agent | PydanticAI (OpenAI-compatible) | Typed tools → FR-016 | 026 |
| Serving | vLLM (pinned in spike); chat Qwen3-14B INT4 (default); guard Qwen3Guard-Gen-0.6B | A10 fit; `cache_salt`; priority scheduling | 009, 029, 031 |
| Parsing | **Docling** (MIT) with RapidOCR; openpyxl (read-only, data-only); Polars CSV; pypdfium2 page rendering | Local multi-format parsing incl. tables/OCR → FR-042–044 | 027 |
| Identifiers | Presidio pattern recognisers (+ spaCy small for context) | Deterministic high-risk ID detection → NFR-005 | 024, 027 |
| Search | fastembed + bge-small-en-v1.5 (CPU) + Postgres FTS, RRF | No VRAM needed; hybrid → FR-045 | 027 |
| Spreadsheet engine | Polars; openpyxl writer; Lark (or hand-written) expression grammar | Typed, bounded, deterministic → FR-050, NFR-025 | 030 |
| Jobs | APScheduler 3.x + Postgres jobs table; Valkey semaphore | No extra broker; GPU fairness | 017, 029 |
| Shared state | Valkey 8 | Rate limits, quotas, semaphores | 018 |
| Observability | structlog (allow-list), OpenTelemetry → Jaeger (optional), Prometheus metrics | Content-free → NFR-023 | 033 |
| Edge & deploy | Caddy 2; Docker Compose; host nftables default-deny egress | Zero egress → NFR-008 | 034 |
| Quality | pytest, import-linter, ruff, mypy, Vitest, Playwright, axe, gitleaks; synthetic corpus | Release gates | 037 |

**Removed since v1.1:**
- nselib and mftool (pivot)
- Squid egress proxy (no egress)
- Langfuse (content capture conflicts with confidentiality)
- pypdf (replaced by Docling)

---

## 9. Trade-off Analysis

### Trade-off: Two isolation layers (app + RLS) vs app-only
**Decision:** both (ADR-023).
**Cost:** RLS policy maintenance and `SET LOCAL` discipline.
**Benefit:** one missed filter can no longer cause a breach, and admin code physically
can't read content.
**Revisit when:** never relaxed while deal data is held.

### Trade-off: Plaintext search derivatives vs fully encrypted search
**Decision:** keep `tsvector` and embeddings of **masked** text in plaintext inside the
SSE-encrypted DB disk (ADR-028).
**Options:** (a) as decided; (b) vector-only search with encrypted text (loses keyword
recall for names and numbers); (c) encrypted search schemes (immature and complex).
**Accepted risk:** a DB-level attacker could partially reconstruct text from these.
**Mitigation:** Azure SSE, RLS, network isolation, deletion + VACUUM, 30-day lifetime.
**Revisit when:** Compliance requires stronger guarantees.

### Trade-off: Typed operations vs code interpreter
**Decision:** typed catalogue (ADR-030; owner decision).
**Cost:** some requests unsupported. **Benefit:** no code execution risk; 100% reproducible results.
**Revisit when:** catalogue coverage is below ~80% (D-11).

### Trade-off: Per-user `cache_salt` vs shared prefix cache
**Decision:** isolate (ADR-031).
**Cost:** roughly 0.5–1 s extra prefill on a user's first turn. **Benefit:** no timing side channel between users.

### Trade-off: Map-reduce summaries in the background vs long-context single pass
**Decision:** map-reduce with ≤ 2 jobs (ADR-029).
**Rationale:** a 32k single pass uses about a third of the KV cache per request and
degrades chat. YaRN to 128k risks quality.
**Cost:** minutes, not seconds. **Benefit:** chat stays responsive, and every summary
bullet keeps its provenance.

### Trade-off: Single host vs HA
Unchanged from v1.1 (99% business-hours target; restore drill).
**Added residual risk:** the host root administrator can access keys in memory and the
KEK file. **Mitigation:** host access controls and audit; HSM later.

### Trade-off: Dropping Langfuse
**Decision:** Jaeger with metadata-only spans.
**Cost:** no prompt/response debugging in production. **Mitigation:** reproduce issues
on the synthetic corpus in staging.

---

## 10. Deployment Architecture

### Environments
- **Development (Mac):** api, worker, frontend and parser run locally (parser via Docker).
  Postgres (with RLS) and Valkey run in Docker. The LLM is reached through an SSH tunnel
  to the A10 dev vLLM. **Only the synthetic corpus is ever used in development.**
- **Staging:** a separate Compose project on the A10 host, sharing vLLM (separate salt
  secret), with its own DB, file store and KEK. Synthetic corpus only.
- **Production (pilot):** the Compose stack on the A10 host. Real deal data lives only here.

### Topology
```
Users (internal network/VPN) ─8043─► Caddy ─► api ─┬─► vllm-chat / vllm-guard (A10: 0.78 / 0.12)
                                                   ├─► postgres (RLS, Azure SSE disk) · valkey
worker ──► parser (no network, 4 GB, ro root) ─────┘
worker ──► off-host backup target (internal only; nightly, encrypted, 30-day rotation)
Host (Azure VM): NSG outbound deny + nftables default-deny · /models (ro, offline) · /data (persistent managed disk, SSE) · /mnt temp disk = scratch only · KEK file (root-only, OS disk, excluded from backups)
```

### Host prerequisites (update of AQ-3)
- Linux, NVIDIA driver R570+, Docker + NVIDIA Container Toolkit.
- **≥ 16 CPU cores and ≥ 64 GB RAM recommended.** Docling layout/table models and OCR
  are CPU-heavy, alongside embeddings, Presidio and Postgres.
- ≥ 512 GB persistent Azure managed data disk (models ~60 GB; data for 10 users × 2 GB quota plus derivatives; backup staging).

### Strategy
- Recreate deployment with pinned image tags. Migrations run in a one-shot `migrate`
  service (including RLS policies).
- Model, parser or catalogue changes go to staging first and pass all suites (ADR-037)
  before promotion. Each change is logged in the decision log.
- Rollback to the previous tags. Migrations stay backward-compatible for one release.
- **Offline update procedure** (ADR-034): images and models are loaded with checksum
  verification.
- **Key ceremony** at install: generate the KEK, escrow it offline, configure the backup
  key and salt secret. Documented in the runbook.

---

## 11. Future Considerations

- **Near:**
  - MFA (D-01).
  - Sandboxed Python for workbooks (D-11): a gVisor or Firecracker sandbox with no
    network, behind the same preview → confirm flow.
  - Generated decks and charts (D-12).
  - Password-protected files (D-13).
- **Medium:**
  - HSM, KMS or Vault for the KEK.
  - Second GPU: guard model, VLM parsing (D-15), longer contexts.
  - SSO (D-02).
  - Redline outputs (D-16).
- **Long:** more users and GPUs behind the `llm` gateway; separate inference service
  (ADR-038 boundary); per-deal physical databases if Compliance requires them.

**Revisit triggers (aggregated):**
- Isolation suite violation (blocker)
- Identifier recall < 99% or false positives > 5%
- Summary p95 > 10 min, or chat TTFT miss while jobs run
- Catalogue coverage < 80%
- `replace` events > 1%
- Ingestion slower than NFR-024
- Compliance demands physical separation or encrypted search
- An HSM becomes available

---

## 12. Open Architecture Questions

| # | Question | Proposed answer | Impact | Owner |
|---|---|---|---|---|
| AQ-1 | Document PII policy | **Resolved 2026-10-07, revised by the pivot:** mask high-risk IDs; business content allowed (decision log) | — | — |
| AQ-2 | Spike confirms the model, memory split, `cache_salt` and priority scheduling in the pinned vLLM | STORY-003 | Model profile; ADR-029/031 fallbacks | Builder |
| AQ-3 | Host CPU, RAM and disk | **Resolved 2026-10-07:** 36 vCPU and 432 GiB RAM (ample); A10-24Q vGPU with driver 570 / CUDA 12.8 (OK). **Actions:** move Docker data-root to persistent storage; never store data on `/mnt` (ephemeral temp disk); provision ≥ 512 GB persistent data disk; at-rest disk encryption = default Azure SSE (decided 2026-10-07) | Storage layout in the runbook (STORY-004) | Builder + IT |
| AQ-4 | Off-host backup target | Internal NAS or object store, reachable only from the host | NFR-014/022 | IT |
| AQ-5 | TLS certificate | Internal CA | Trust warnings | IT |
| AQ-6 | KEK custody: who holds the offline escrow, and the rotation schedule? | Product owner + one Compliance officer (split knowledge); annual rotation | Recovery and crypto-shredding assurance | Product owner + Compliance |
| AQ-8 | Is the host a cloud VM, and UAT or production? | **Resolved 2026-10-07:** Azure VM (UAT); no Key Vault (KEK file kept); default Azure SSE on managed disks; ADR-028/034/035 amended | — | — |
| AQ-9 | Will real deal documents be processed on this UAT VM during the pilot, and what is the production host? **Resolved 2026-10-07: synthetic corpus only on UAT until controls + sign-off; production built or promoted via the same runbook.** | Pilot on UAT with the **synthetic corpus only** until production-grade controls are confirmed (NSG egress deny, backups, KEK escrow, Compliance sign-off). Then either promote this VM to production or build the production VM from the same runbook | Where real data may live | Product owner + IT + Compliance |
| AQ-7 | Does Compliance accept the documented plaintext search-index exception (ADR-028)? | Accept with Azure SSE + RLS + 30-day lifetime | Else vector-only search (lower recall) | Compliance |

---

## Appendix

### Glossary
| Term | Definition |
|------|------------|
| AccessContext | Per-request object holding user, role, accessible space IDs and break-glass grant; required by every content repository |
| RLS | PostgreSQL Row-Level Security: database-enforced row filtering by session context |
| KEK / space key / object key | Envelope-encryption key hierarchy (ADR-028) |
| Crypto-shredding | Deleting data by destroying its key |
| `cache_salt` | vLLM per-request value that partitions the prefix cache |
| Map-reduce summary | Summarise windows (map), then merge (reduce), keeping chunk provenance |
| Plan card | UI card showing an operation plan + preview; the user confirms to apply |
| Locator | `p.12`, `slide 4`, `Sheet1!B2:F20` |
| Synthetic corpus | Fabricated confidential-style documents used for all tests and evals |

### References
- PRD v2.0 `bmad-output/prd.md` · Addendum v2.0 · Research v2.0 (Findings 7–10) · Decision log · Project context
- Archive: `archive/architecture-v1.1-market-data.md`

### Document History
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0–1.1 | 2026-10-06/07 | Winston | Market-data architecture (archived) |
| 2.0 | 2026-10-07 | Winston (Update) | Pivot: ADR-023–038 added; ADR-001/007/010/011/012/014/016/019/020/022 superseded; ADR-013 retired; ADR-003/004/005/008/009/017/018/021 amended; new components (spaces, crypto, parser, sheets, outputs, lifecycle, compliance), data model, API, coverage matrix for FR-001–055 / NFR-001–025 |
| 2.1 | 2026-10-07 | Winston (Update) | Azure UAT host facts: no Key Vault (KEK file kept), default Azure SSE replaces LUKS, NSG zero-egress, Docker data-root on persistent disk, `/mnt` scratch only, app-level backups to a private storage account with 30-day lifecycle, no whole-VM backups with the KEK; AQ-3/AQ-8 resolved, AQ-9 added |
| 2.2 | 2026-10-07 | Winston (Update) | Inbound range 8000–8050: Caddy on 8043. Interim local encrypted backups (`/data/backups`, 2 days) until IT storage; off-host backups required before real data |
| 2.3 | 2026-10-07 | Winston (Update) | Project renamed to invest-ai-llm (new repo); `prototype/` removed from the ADR-038 module tree |

---

---

**END OF DOCUMENT**. Ready for `bmad-epics-and-stories`.
