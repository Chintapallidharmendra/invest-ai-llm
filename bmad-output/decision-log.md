# Decision Log — invest-ai-llm (formerly "Foundry Local AI")

A threaded, append-only record of decisions made across BMAD planning workflows.
Every later skill (brief, PRD, architecture, stories) appends here so the reasoning
behind the plan stays visible and consistent.

**How to use:** add a new entry at the top of the log (newest first). Never rewrite
or delete past entries — supersede them with a new entry that references the old one.

## Entry format

```
### YYYY-MM-DD — <short title>
- **Decision:** <what was decided>
- **Rationale:** <why; alternatives considered>
- **Made by:** <skill/workflow, e.g. bmad-init, prd, architecture>
- **Supersedes:** <link to prior entry, if any>
```

---

### 2026-10-08 — Waves 4–5 compiled (12 stories); waves 0–3 marked done
- **Decision:**
  - **Waves 0–3 (15 stories) marked `done`** in `epics.md` (all merged to `main` by 2026-10-08).
  - **12 stories compiled as `ready-for-dev`:**
    - wave 4: 2.2, 8.2, 1.9, 10.3, 10.4
    - wave 5: 2.3, 2.4, 2.5, 8.3, 7.2, 9.2, 3.1
  - **Parallel-safety conventions added:**
    - `auth`, `spaces` and `admin` `routes.py` mount every `routes_*.py` in their package, so wave 5 stories add endpoint files without editing a shared router (2.2, 8.2, 2.3).
    - `audit.registry.discover()` also imports `audit_events_*.py`, so per-story event files stay under the `check_models` CI check (2.2).
    - Private spaces are created by an `auth.user_created` core-event subscriber (8.2) that runs in 2.3's create-user transaction.
    - `spaces.rls.enable_space_rls()` is the single RLS policy helper for every later content migration (8.2).
  - **Carried-over follow-ups placed in stories:**
    - 2.2 owns start-up wiring (password-policy preload; `kek_loaded` and LLM readiness registration in api and worker).
    - 8.2 adds the `space_keys` / `object_keys` FKs deferred by 8.1.
    - 3.1 accepts only an empty selected set until 9.1 registers a document resolver (documents arrive in wave 6).
- **Scope check:** pairwise owned-scope overlap within each wave, scripted.
  - Result: 1 overlap (the empty `tests/sheets/__init__.py`), resolved by giving it to 10.3.
  - Wave 5's two migrations (7.2, 3.1) both revise `8_2_spaces`; the second to merge owns the `alembic merge` revision (noted in both stories). **0 unserialized conflicts.**
- **Rationale:**
  - Login, spaces/RLS and the pure engines (expressions, calculations) unblock the most later stories.
  - The spike runs on UAT in parallel and can adjust † thresholds before chat work (wave 6+).
- **Made by:** bmad-epics-and-stories (Create, waves 4–5)
- **Supersedes:** none (extends "Story sharding: epic map + waves 0–3 compiled")

### 2026-10-07 — New repo invest-ai-llm; prototype dropped; project renamed
- **Decision:**
  - All planning artifacts moved from `foundry_local/bmad-output/` to the new repo
    **`/Users/dharmendra/Work/research/invest-ai-llm`** (git initialised on `main`,
    nothing committed yet).
  - **The old prototype is not brought into the new repo.** It stays in `foundry_local`
    for reference.
  - **Story 1.1 LOCKED sections updated with owner confirmation:**
    - AC #1: no `prototype/` folder; `bmad-output/` left untouched;
    - prototype-move task and owned path removed;
    - Dev Note and Testing for AC #1 updated.
  - ADR-038's tree drops `prototype/`.
  - The project is **renamed from "Foundry Local AI" to "invest-ai-llm"** in config, PRD
    (v2.3), architecture (v2.3), project context, addendum, epics and research report.
    Archived files keep their original names as history.
- **Rationale:** owner wants a dedicated, clean repo for the application. Foundry Local
  is no longer part of the design.
- **Made by:** owner decision; bmad-epics-and-stories (Update of Story 1.1), bmad-architecture, bmad-prd
- **Supersedes:** Story 1.1 v1 AC #1 (prototype move).

### 2026-10-07 — Story sharding: epic map + waves 0–3 compiled
- **Decision:**
  - `epics.md` maps **71 stories** across Epics 1, 2, 3, 4, 7, 8, 9, 10 (5 and 6
    retired), with 13 build waves and a real-data gate.
  - **15 stories compiled as `ready-for-dev`** (waves 0–3): 1.1–1.8, 1.10, 1.11, 2.1,
    4.1, 4.4, 7.1, 8.1.
  - Story 7.1 moved from wave 2 to wave 3, because it registers its verify cron with the
    1.11 scheduler.
  - **Parallel-safety conventions:**
    - dependencies declared once in 1.1;
    - auto-discovered routers and frontend routes;
    - per-module settings, audit events and readiness checks;
    - one migration file per story;
    - compose include files owned by their feature stories.
- **Scope check:** the plugin's shared `scope-conflict-check.sh` is not installed, so an
  equivalent pairwise overlap check was run. Result: 8 overlaps, all serialized by
  Blocked-by links; **0 unserialized conflicts**.
- **Rationale:** foundation and confidentiality primitives (crypto, audit, identifiers,
  grounding) first; later waves compile after these, so Learnings carry forward.
- **Made by:** bmad-epics-and-stories (Create)
- **Supersedes:** none

### 2026-10-07 — Port 8043; interim local backups
- **Decision:**
  - The inbound range is **8000–8050**, so Caddy serves HTTPS on **8043**.
  - Until IT provides off-host storage, the app writes **encrypted nightly backups to
    `/data/backups/` and keeps 2 days**. That's on the persistent data disk, outside the
    repo (git-ignored), never on `/mnt` or the OS disk.
  - The backup target is a configuration switch (`local` | `azure_blob`).
- **Rationale:** owner. Backups must not block development. UAT holds only synthetic data (Q23).
- **Accepted limitation:** NFR-014 (recovery after VM loss) is not met in interim mode.
- **Blocking condition:** off-host backups (Q25) must be live before any real deal document.
- **Made by:** bmad-architecture (v2.2), bmad-prd (v2.2)
- **Supersedes:** "Caddy on 8443" in the UAT network entry.

### 2026-10-07 — UAT data rule and network facts
- **Decision:**
  - **Q23:** the UAT VM uses the **synthetic test corpus only**. Real deal documents
    wait until NSG egress deny (already in place), private-storage backups, KEK escrow
    and Compliance sign-off are complete. Then this VM is promoted, or production is
    built from the same runbook.
  - **Q24 (partial):** outbound internet is already blocked. Only an inbound port range
    starting at 8000 is exposed, so **Caddy serves HTTPS on 8443** (later corrected to 8043, see next entry).
    All other services stay internal.
- **Open:** the exact upper bound of the port range; the backup storage account.
- **Made by:** bmad-architecture (amendment to ADR-034)
- **Supersedes:** Caddy on port 443 (ADR-020/034).

### 2026-10-07 — Azure UAT host: no Key Vault; default Azure disk encryption
- **Decision:**
  - The host is an **Azure VM (UAT)**. **Key Vault is not used** (not readily
    available), so the KEK stays a root-only file on the OS disk, escrowed offline.
  - **Default Azure server-side encryption** on managed disks satisfies NFR-021's
    disk-layer encryption. LUKS is not required. App-level envelope encryption
    (ADR-028) remains the primary control.
  - `/mnt` (ephemeral temp disk, not covered by SSE) holds **no content**.
  - Docker data-root moves to the persistent data disk.
  - Zero egress via an **NSG outbound deny-Internet** rule + host firewall, with Azure
    default outbound access disabled.
  - Backups are app-level (encrypted) to a **private Azure Storage account** with a
    30-day lifecycle. **No whole-VM backups that capture the KEK.**
- **Rationale:** owner input. Key Vault would be stronger, but its absence is mitigated
  by the root-only KEK file, offline escrow and restricted host access. It remains a
  revisit trigger.
- **Proposed (pending Q23):** UAT uses the synthetic corpus only until production
  controls and Compliance sign-off are in place.
- **Made by:** bmad-architecture (UPDATE v2.1), bmad-prd (v2.1)
- **Supersedes:** LUKS requirement (ADR-028 v2.0), "managed key vault" suggestion in the
  host-specs entry.

### 2026-10-07 — Host specs verified (nbfcuatyolomlapp01)
- **Facts (from host commands):**
  - **36 vCPU, 432 GiB RAM**, no swap.
  - GPU **NVIDIA A10-24Q** (vGPU profile, full 24 GB); driver 570.211.01; CUDA 12.8.
  - Disks:
    - root `/` 62 GB (32 GB free)
    - `/data` 251 GB ext4 (persistent data disk)
    - `/mnt` 1.4 TB ext4
  - **No disk shows LUKS encryption.**
- **Assessment:**
  - CPU and RAM are well above the recommendation (16 cores / 64 GB). The GPU and driver
    meet the vLLM prerequisites (R570+, CUDA 12.8).
  - Disk layout needs changes:
    1. Docker data-root must move off the small root disk.
    2. `/mnt` matches an Azure VM's **temporary (ephemeral) disk** (the VM shape matches
       Standard_NV36ads_A10_v5: 36 vCPU, 440 GiB, 1× A10, 1,440 GiB temp disk). It must
       hold **no persistent data**: scratch only.
    3. Persistent data (DB, encrypted files, models, Docker) goes on `/data`, or on a
       larger attached managed disk; recommend ≥ 512 GB.
    4. At-rest encryption (NFR-021) still needs a decision: LUKS on the data disk, or the
       cloud provider's host-level encryption with customer-managed keys.
- **Inferred, to confirm:** the host is a cloud VM (likely Azure) and, by its name, a
  **UAT** environment. If confirmed:
  - "self-hosted" means inside the firm's own cloud tenant;
  - zero egress is enforced with network security rules;
  - a managed key vault could hold the KEK, which would remove ADR-028's main residual risk;
  - the production host is still to be identified.
- **Made by:** bmad-architecture (input to AQ-3)
- **Supersedes:** AQ-3 sizing assumption.

### 2026-10-07 — PRD v2.0 and Architecture v2.0 (post-pivot)
- **Decision (PRD v2.0):** 55 FR IDs, 42 active (25 Must / 14 Should / 3 Could); 13
  retired as Won't; NFR-020–025 added, NFR-018 retired. New epics:
  - EPIC-008 Workspaces, Isolation & Encryption
  - EPIC-009 Document Processing
  - EPIC-010 Excel Processing

  EPIC-005/006 retired. About 57 active story sketches. Must share is exactly 60%: no
  new Musts without demoting one.
- **Decision (Architecture v2.0).** Superseded: ADR-001→038, 007→023, 010→024,
  011→025, 012→026, 014→027, 016→032, 019→033, 020→034, 022→037. ADR-013 retired.
  ADR-003/004/005/008/009/017/018/021 amended. New ADRs:
  - **ADR-023:** spaces (private + deal workspaces); every content row has `space_id`; two-layer isolation: repository `AccessContext` + **Postgres RLS (FORCE; no context = no rows)**; admin paths carry no user context; 404 for not-accessible.
  - **ADR-024:** guardrails v2: rate limit → **high-risk ID block in chat** → guard → scope router (document-work categories, barrier, insider misuse, needs-internet) → access check → agent → output gate → final moderation; fail closed.
  - **ADR-025:** output v2: engine-rendered tables; hold-back gating; grounding against chunks, sheets, operation and calc outputs; **strip all URLs, images and HTML**, only `cite:` links rendered; new SSE events `plan` and `job`.
  - **ADR-026:** tools v2 (11 typed tools scoped to the selected set); **applying operations requires a user click**, not a model tool.
  - **ADR-027:** ingestion: validate (reject macro, encrypted, zip-bomb files) → **sandboxed parser container** (Docling + RapidOCR + openpyxl data-only; no network, 4 GB) → guard injection scan → **mask high-risk IDs** → chunk, tables, sheets → encrypt + hybrid index.
  - **ADR-028:** envelope encryption: KEK (host file, not in DB backups, escrowed) → space key → object key; AES-256-GCM with AAD; crypto-shredding. Documented plaintext exception: `tsvector` + embeddings of masked text on a LUKS volume.
  - **ADR-029:** map-reduce summaries and comparisons as jobs; ≤ 2 concurrent, round-robin per user; chat priority.
  - **ADR-030:** typed Polars operation engine; safe expression grammar; rlimited process pool; preview → confirm → new workbook with Operations and Notes sheets; values-only outputs; formula-injection escaping.
  - **ADR-031:** per-user vLLM `cache_salt`; disable prefix caching if unsupported.
  - **ADR-032:** audit payloads restricted by type to IDs, enums, numbers and keyed hashes (no free text).
  - **ADR-033:** content-free logs and traces (field allow-lists; no exception messages); Jaeger optional; **Langfuse dropped**.
  - **ADR-034:** zero egress (default-deny), offline model bundles, offline update procedure.
  - **ADR-035:** 30-day expiry + delete pipeline (shred keys, delete rows and files, nightly VACUUM); minimal WAL; **30 rolling daily encrypted backups**, no monthly copies.
  - **ADR-036:** compliance break-glass (RLS grant clause, read-only, ≤ 24 h, every view audited, user notified).
  - **ADR-037:** synthetic confidential corpus only; isolation, malicious-file, canary and deletion suites as gates.
  - **ADR-038:** module list v2 (crypto, spaces, documents, sheets, outputs, jobs, lifecycle, compliance + separate parser).
- **Rationale:** confidentiality drivers from the pivot (information barriers,
  provable deletion, hostile documents, zero egress).
- **Made by:** bmad-prd (UPDATE v2.0) + bmad-architecture (UPDATE v2.0)
- **Supersedes:** PRD v1.1, Architecture v1.1 (archived)

### 2026-10-07 — PIVOT: confidential document & Excel assistant for investment bankers
- **Decision:** The product changes direction.
  - **Removed:** market data (nselib, mftool, ACE, internal market-data DB feeds), the
    public IIFL knowledge library and public-filings Q&A.
  - **New focus:** an assistant for **~10 investment bankers** to **summarise, query and
    compare their own confidential documents** (PDF, Word, PowerPoint, Excel/CSV) and to
    **process Excel workbooks from prompts**.
  - **Top risk:** privacy and confidentiality.
- **Owner accepted all recommendations:**
  1. **Visibility:** private to the uploader by default, plus optional **deal workspaces** with named members. The IT admin cannot read content; a separate Compliance role has logged access.
  2. **Personal identifiers:** business content is allowed (client names, financials). High-risk personal IDs (PAN, Aadhaar, bank/demat account, card, UPI, passport, voter ID) are **masked in documents before the model sees them**, and **blocked when typed into chat**.
  3. **Retention:** files and everything derived from them are **auto-deleted after 30 days**; users can permanently delete at any time. The audit trail keeps **metadata only**, for 5 years.
  4. **Excel:** **typed spreadsheet operations** in V1 (filter, sort, group/aggregate, pivot, join, calculated columns, table extraction, deterministic finance calculations). Sandboxed code execution is deferred.
  5. **Formats:** PDF, DOCX, PPTX, XLSX/CSV.
- **Assumed defaults (owner to correct):** OCR for scanned PDFs included as a Should;
  documents up to ~300 pages; long-document summaries run as **background jobs (minutes)**.
- **Rationale:** Owner direction. Confidential deal material is UPSI by nature, so the
  design moves from "keep PII and UPSI away" to **contain and isolate**:
  - isolation between users and deals, including per-user model cache salts;
  - app-level encryption at rest, with per-workspace keys enabling crypto-shredding;
  - metadata-only audit and traces;
  - zero internet egress (no market data any more);
  - injection-safe handling of counterparty documents;
  - watermarked, expiring exports.
- **Kept:** FastAPI + React, vLLM on the A10 (~10 users), sessions + admin-shared links,
  fail-closed guardrails, figure grounding (now against document and sheet data),
  read-only/typed tools, rate limits.
- **Made by:** owner decision, recorded via bmad-prd
- **Supersedes:** all market-data decisions (2026-10-05 research entry "data" parts;
  2026-10-06 "V1 capacity and market-data strategy" data parts; "No email…
  post-V1 data = ACE or internal DB" data part), PRD v1.1 scope (archived at
  `archive/prd-v1.1-market-data.md`), architecture v1.1 ADR-013/014 and related
  (archived at `archive/architecture-v1.1-market-data.md`).

### 2026-10-07 — Document PII policy confirmed (AQ-1 / Q12)
- **Decision:** For uploaded documents, **reject** any document containing high-risk
  identifiers (PAN, Aadhaar, bank or demat account number, card number, UPI ID,
  passport, voter ID) and report the type(s) and page(s). **Mask** email addresses and
  phone numbers in the indexed text. **Allow** director and officer names (public
  figures in public documents). User chat messages keep the stricter FR-012 rule
  (block on any detected PII).
- **Rationale:** Owner agreed. Strict rejection of any PII would block every annual
  report, which always lists company-secretary contact details and directors.
  High-risk identifiers stay a hard block.
- **Made by:** bmad-architecture / bmad-prd (UPDATE: PRD v1.1, architecture v1.1)
- **Supersedes:** FR-012 v1.0 wording "documents containing PII are rejected".

### 2026-10-06 — Architecture v1.0: 22 ADRs accepted (architecture.md)
- **Decision:** One-line summaries (full records in `architecture.md` §3):
  - **ADR-001:** Modular monolith (FastAPI) + worker process + two vLLM servers. Fixed module boundaries; `assistant`/`guardrails`/`llm`/`marketdata`/`knowledge` have no FastAPI imports; import-linter enforced.
  - **ADR-002:** REST/JSON under `/api/v1`; chat streams via SSE on `POST /conversations/{id}/messages`. OpenAPI is the contract; TS types generated. No GraphQL or WebSockets.
  - **ADR-003:** Bare resource JSON; lists `{items, next_cursor}`; errors are RFC 9457 problem+json with a `code`. Fixed SSE event set (`meta`, `status`, `delta`, `table`, `sources`, `replace`, `done`, `error`). Input-stage blocks are HTTP problems, not SSE.
  - **ADR-004:** snake_case DB and JSON wire; plural tables with `md_`/`mf_`/`co_`/`kb_` prefixes; kebab-case paths; `verb_noun` tool names; `<domain>.<action>` audit event types.
  - **ADR-005:** PostgreSQL 18 + pgvector as the only primary store; SQLAlchemy 2 async + Alembic; UUIDv7; timestamptz UTC. App DB role cannot UPDATE/DELETE audit.
  - **ADR-006:** Server-side sessions with opaque hashed tokens in `__Host-` cookies (HttpOnly, Secure, SameSite=Strict); double-submit CSRF; Argon2id; lockout after 5 failures; 24 h single-use set-password links in the URL fragment, shown once. No JWTs.
  - **ADR-007:** RBAC `admin`/`user`; deny by default; admin routers guarded at router level; ownership filters in repository signatures; 404 for not-owned.
  - **ADR-008:** React 19 + TS + Vite; TanStack Query for server state; one SSE reducer hook; no Redux/Zustand; Tailwind + shadcn/ui; tables rendered from `table` events only.
  - **ADR-009:** `llm` gateway is the only client of vLLM (`chat` Qwen3-14B INT4 at 0.78, `guard` Qwen3Guard-0.6B at 0.12); thinking off; models offline; no hard-coded model names.
  - **ADR-010:** Fixed guardrail order: rate limit → PII gate (block, before persistence) → guard moderation → scope router (template declines) → agent → output gate → final moderation. **Fail closed** on any safety failure.
  - **ADR-011:** Tables come from tool data, not model text; hold-back gating checks digit/`@` spans for PII + figure grounding before release; one regeneration, else `replace`; calculations are a Decimal tool.
  - **ADR-012:** PydanticAI agent; 12 allow-listed read-only tools with typed bounded args; tools never do network I/O or call the LLM; ≤ 4 tool calls per turn; versioned system prompt encoding the PRD scope policy.
  - **ADR-013:** `MarketDataProvider` protocol (nselib/mftool now; ACE/InternalDb later); tools read stored copies; IST refresh schedule + 15-min watchlist snapshots; rate-limited cache-miss fetch; kill switch; upstream text scanned at ingestion.
  - **ADR-014:** Document ingestion: pypdf → PII scan (reject high-risk IDs, mask contacts; pending AQ-1) → guard injection scan → 400-token page-bounded chunks → bge-small embeddings on CPU → hybrid pgvector + full-text with RRF → page citations.
  - **ADR-015:** 8,192-token working context with fixed budgets and a rolling summary; only `context.build()` constructs prompts.
  - **ADR-016:** Hash-chained, append-only `audit_events` (monthly partitions, daily verification, chain anchors at retention, no raw PII).
  - **ADR-017:** Worker with APScheduler 3.x + Postgres `jobs` table (SKIP LOCKED); no Celery/arq.
  - **ADR-018:** Valkey 8 for rate limits, upstream token buckets and short caches; never a source of truth; fails closed for chat.
  - **ADR-019:** structlog JSON logs with correlation IDs (no chat bodies, scrubber); OpenTelemetry → self-hosted Langfuse (optional profile); Prometheus metrics; `/healthz` + `/readyz`.
  - **ADR-020:** Docker Compose on the A10 host; internal networks; only Caddy exposed (TLS, CSP); Squid egress proxy with domain allow-list; offline model volume; nightly off-host backups.
  - **ADR-021:** pydantic-settings (`APP_*`); Compose secrets; admin-tunable settings in `app_settings`; gitleaks.
  - **ADR-022:** Unit + integration tests (fake LLM server) + eval/PII/red-team/scope/UPSI/forecast/grounding suites + 10-user soak, all as release gates for any model/prompt/guard change.
- **Rationale:** Derived from PRD v1.0 drivers: PII never reaches the model, fail closed,
  grounded figures, latency on one A10, swappable model and data, tamper-evident audit,
  no egress.
- **Made by:** bmad-architecture (Create)
- **Supersedes:** "arq or Celery" (research v1.1) → APScheduler + Postgres jobs; "Redis" → Valkey (licence).

### 2026-10-06 — PRD v1.0 baselined: Q1–Q4 defaults confirmed
- **Decision:** The owner confirmed the four defaulted product decisions:
  - **Q1:** V1 chores = stock/index lookups, MF NAV + returns, company
    results/actions/announcements, export + deterministic calculations.
  - **Q2:** IIFL knowledge = filings via data tools (Must) + admin-uploaded public
    document library with citations (Should).
  - **Q3:** PII is blocked and explained, never masked.
  - **Q4:** User chat history + tamper-evident audit trail, 5-year configurable
    retention.

  The PRD is baselined as **v1.0** (37 FRs: 22 Must / 12 Should / 3 Could; FR-004
  Won't; 19 NFRs; 7 epics; 46 story sketches).
- **Rationale:** Owner confirmation. Remaining open questions (Q7 compliance
  acknowledgement, Q8 session timeouts, Q9 pilot users, Q10 internal-DB classification,
  Q11 link-sharing channel) do not block architecture.
- **Made by:** bmad-prd (UPDATE)
- **Supersedes:** the "defaulted, pending confirmation" status in the PRD v0.1 entry.

### 2026-10-06 — No email in V1; post-V1 data = ACE full subscription or internal DB
- **Decision:**
  - **No outbound email in V1.** The admin shares one-time set-password links manually
    (FR-001: shown once, 24 h, single-use, username check, re-issuable for forgotten
    passwords).
  - FR-004 (email reset) moves to **Won't** (ID kept). New FR-038 (Should): users change
    their own password while signed in. FR-017 alerts are in-app.
  - **Post-V1 data source:** Accord Fintech ACE **full subscription**, or feeds from
    **internal database tables**. Internal tables need a data-classification sign-off
    (published/non-sensitive only), read-only access and source attribution (addendum
    Q10, D-09).
- **Rationale:** Owner answers to addendum Q5/Q6.
  - Manual link sharing avoids an email dependency. Its risk (an intercepted link) is
    reduced by the short expiry, single use, the username check and auditing.
  - Internal tables could contain UPSI or customer PII, which would conflict with FR-034
    and FR-012, so they are gated before connection.
  - Must count is unchanged (22 of 37, 59%).
- **Made by:** bmad-prd (UPDATE, PRD v0.3)
- **Supersedes:** the 2026-10-06 "V1 capacity and market-data strategy" entry's
  post-V1 source (ACE only), and FR-004's v0.1 wording.

### 2026-10-06 — PRD v0.2: response scope policy + four guardrail requirements
- **Decision:** Added a **Response Scope & Style Policy** to the PRD with three levels
  (answer fully / answer with care / decline and redirect) and style rules: plain English,
  tables for figures, source + as-of on every figure, ₹ and lakh/crore, DD-MMM-YYYY.
  Names alone are not PII. Added FR-034 **UPSI declines (Must)**, FR-035 **price-prediction
  refusals (Must)**, FR-036 **figure grounding check (Must)** and FR-037 **per-user rate
  limit (Should)**, plus STORY-042–046.
- **Rationale:** Owner approved after reviewing the scope and guardrail summary.
  - UPSI: speculation on unpublished IIFL information would create insider-trading
    (SEBI PIT) exposure.
  - Forecasts: made explicit so they can be tested on their own.
  - Grounding: the highest-impact hallucination risk is wrong numbers.
  - Rate limit: protects the shared A10 and slows guardrail probing.
  - Must share is 22 of 37 (59%).
- **Made by:** bmad-prd (UPDATE)
- **Supersedes:** none (extends PRD v0.1 entry)

### 2026-10-06 — PRD v0.1 drafted: scope, priorities, defaulted product decisions
- **Decision:** PRD v0.1 has 33 FRs (19 Must / 11 Should / 3 Could), 19 NFRs and 7
  epics (~41 story sketches). EPIC-001 (model spike, eval set, skeleton) goes first.
  Four product decisions were **defaulted to the recommended option** pending owner
  confirmation (addendum Q1–Q4):
  1. V1 chores = stock/index lookups, MF NAV + returns, company results/actions/announcements, export + deterministic calculations.
  2. IIFL knowledge = filings via data tools (Must) + admin-uploaded public document library with citations (Should).
  3. PII policy = block and explain (no masking).
  4. Retention = user chat history + audit trail with a 5-year configurable default.
- **Rationale:** The owner declined the question form, so drafting went ahead with
  recommended defaults, flagged for review. Musts are limited to safety, trust and
  core chores to avoid priority inflation (58%). Document-library answers are Should
  because filings cover core company facts. Index performance and the data-source kill
  switch are Should because workarounds exist. NFR thresholds marked † (latency,
  tokens/s) are provisional until the A10 spike.
- **Made by:** bmad-prd
- **Supersedes:** none

### 2026-10-06 — V1 capacity (~10 users) and market-data strategy
- **Decision:** (1) V1 serves **~10 users** on the single A10. Conversation context is
  capped at ~8k tokens, with `--max-model-len` ≈ 16k and `--max-num-seqs` ≈ 16. (2) V1
  market data comes from **open-source libraries** (`nselib`, `mftool`) behind
  `MarketDataProvider`. After V1 we subscribe to **Accord Fintech ACE APIs** (ACE MF;
  likely also ACE Equity/Datafeed for equities and company data) via a new `AceProvider`.
- **Rationale:** Project owner's decision. A KV-cache estimate for Qwen3-14B with FP8 KV
  (~80 KiB/token, ~105k tokens in ~8 GiB) fits 10 concurrent 8k contexts. Open-source
  data is acceptable for a small internal V1. **Accepted risk:** NSE ToU prohibit
  automated collection without consent. Mitigations: internal only, no redistribution,
  EOD/delayed preferred, cached scheduled refreshes, rate limits, source attribution,
  and a per-provider kill switch. ACE is an authorised NSE/BSE vendor, so switching
  removes the risk.
- **Made by:** bmad-research (user decision, research-report v1.4)
- **Supersedes:** "production needs a licensed feed (IIFL XTS / NSE Data & Analytics)"
  in the 2026-10-05 research entry. The licensed feed is now ACE, post-V1.

### 2026-10-06 — GPU host: NVIDIA A10 24 GB
- **Decision:** V1 inference runs on the builder's **NVIDIA A10 (24 GB, Ampere CC 8.6)**
  with vLLM in Docker (`vllm/vllm-openai`). The chat model and the guard model share the
  card as two vLLM processes (proposed split 0.78 / 0.12 `--gpu-memory-utilization`).
  Model shortlist: **Qwen3-14B INT4 (AWQ/GPTQ, primary)**, Qwen3-8B (fallback),
  Qwen2.5-14B INT4 (baseline). gpt-oss-20b only if it runs stably on Ampere.
- **Rationale:** Available hardware. 24 GB comfortably fits a ~14B INT4 model plus KV
  cache plus a 0.6B guard. Ampere has no native FP8 compute (FP8 runs weight-only), and
  gpt-oss MXFP4 is officially Hopper+ (Ampere uses a fallback path with a known
  not-planned Marlin MoE issue). Qwen3 runs with thinking off for tool turns because of
  known reasoning/tool-parser issues. Final choice comes from the model-selection spike.
- **Made by:** bmad-research (user input, research-report v1.3)
- **Supersedes:** none (refines the 2026-10-06 vLLM entry)

### 2026-10-06 — V1 model runtime: vLLM (Foundry Local dropped)
- **Decision:** V1 serves models with **vLLM** (OpenAI-compatible server, tool calling
  via `--enable-auto-tool-choice` + a model-specific parser) on a central **Linux GPU
  host** inside our network. The guard model (Qwen3Guard / Granite Guardian) runs on the
  same host. Foundry Local is no longer part of V1.
- **Rationale:** Project owner's choice. vLLM batches requests continuously for multiple
  users, accepts any Hugging Face model, and is the production path we would have
  migrated to anyway, so this avoids a migration. Consequences: (1) a Linux GPU host
  (NVIDIA CC ≥ 7.5 / CUDA 12.8+, or AMD ROCm) is now a V1 prerequisite; (2) the
  builder's Mac (M2 Pro, 16 GB, macOS 14.4.1) cannot run vLLM (vllm-metal needs
  macOS 15+), so development uses a shared vLLM dev server, or a local
  OpenAI-compatible stand-in with all evals run against vLLM.
- **Made by:** bmad-research (user decision, research-report v1.2)
- **Supersedes:** "Foundry Local serves V1; vLLM later" in the 2026-10-05 research entry
  and the 2026-10-06 FastAPI + React entry's runtime line.

### 2026-10-06 — V1 tech stack: FastAPI + React
- **Decision:** V1 is built on **FastAPI** (SQLAlchemy 2 async + Alembic, PostgreSQL,
  pydantic-settings) and **React + TypeScript (Vite)**. The rest of the proposed stack is
  unchanged: PydanticAI, Foundry Local behind an OpenAI-compatible interface (vLLM later),
  Presidio + Qwen3Guard guardrails, Redis, and Langfuse/OpenTelemetry.
- **Rationale:** Project owner's choice. FastAPI is the AI-ecosystem default, async-native,
  and Pydantic-based like PydanticAI. React leaves room for rich financial UI.
  Consequence: auth and user admin are not built in. V1 uses Argon2 (`pwdlib`), opaque
  hashed session tokens in HttpOnly cookies + CSRF, login rate limiting, and an admin page
  in React with set-password emails. `fastapi-users` was rejected because it is in
  maintenance mode. Auth/admin becomes its own epic with security acceptance tests.
- **Made by:** bmad-research (user decision, research-report v1.1)
- **Supersedes:** the "Proposed stack" in the 2026-10-05 research entry (Django 5 +
  Django Ninja), and the Knox auth wording in "V1 scope, security posture, and auth
  approach".

## Research: Financial market data libraries & V1 tech stack — 2026-10-05
- Mode: Create
- Types: Technical + Domain
- Key finding: Open-source NSE libraries (nselib, Apache-2.0) are fine for prototyping,
  but NSE's Terms of Use forbid automated scraping, so production needs a licensed feed
  (IIFL XTS API or NSE Data & Analytics). Foundry Local is designed for a single device,
  so the LLM runtime must stay swappable (vLLM later).
- Proposed stack (pending ADRs in architecture): Django 5 + Django Ninja, Django session
  auth with admin-provisioned users (Knox dropped; it was only an example), PydanticAI,
  Foundry Local behind an OpenAI-compatible interface, Presidio + Qwen3Guard guardrails,
  PostgreSQL (+pgvector later), Redis, React + TS, Langfuse/OpenTelemetry.
- Report: bmad-output/research-report.md
- Next skill: bmad-prd, then bmad-architecture

### 2026-10-05 — V1 scope, security posture, and auth approach
- **Decision:** V1 is a locally hosted, open-source LLM chat assistant for
  non-technical finance users. It answers IIFL / IIFL Finance queries and pulls NSE
  and open-source financial data for basic tasks. Security is the top constraint:
  sensitive data and PII must be blocked before it reaches the model, and the LLM
  must be guardrailed. Auth is Knox token authentication, with an admin who
  provisions users by username + email.
- **Rationale:** User direction. Knox is a deliberately simple V1 choice; SSO and
  MFA are deferred. Local inference keeps prompts off third-party APIs.
- **Made by:** bmad-init
- **Supersedes:** none

### 2026-10-05 — Track selected: bmad-method
- **Decision:** Initialized this project on the **bmad-method** track.
- **Rationale:** Scope is a local AI product built on Foundry Local (beyond the
  current Streamlit chat prototype), estimated at 10–30 stories, with one builder.
  The user reported compliance/security requirements, so the helper suggested
  Enterprise. We chose BMad Method instead: with one builder and no multi-team
  coordination, a separate Security/DevOps planning track isn't worth the overhead.
  Security and data-handling requirements will be written as NFRs in the PRD and as
  ADRs in the architecture. Revisit if a formal regime (e.g. SOC 2, HIPAA, GDPR
  audit) or real deployment/infra work appears; promoting to Enterprise is cheap.
- **Made by:** bmad-init
- **Supersedes:** none
