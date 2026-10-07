# Research Report: Financial Market Data Libraries & V1 Tech Stack

**Date:** 2026-10-05
**Research Type:** Combined (Technical + Domain)
**Mode:** Create
**Project:** invest-ai-llm (formerly "Foundry Local AI")
**Version:** 2.0
**Status:** Draft


> **⚠️ Update v2.0 (2026-10-07): product pivot.** The product is now a confidential
> document and Excel assistant for investment bankers (see `decision-log.md`).
> **Finding 1 (market-data libraries), the ACE and data-strategy notes, and the
> market-data rows of the stack table are superseded and kept for history only.**
> Findings 2–6 (vLLM on A10, guardrails, FastAPI, PydanticAI, regulation) still apply.
> New findings for document processing are in **"v2.0 Findings: Document Processing"**
> at the end of the Detailed Findings.

---

## Executive Summary

**Research Objective:** Identify open-source libraries and packages for pulling Indian
financial-market data (NSE first), and choose a V1 tech stack for a secure,
locally hosted LLM chat assistant for non-technical finance users. The stack should be
easy to learn now and leave room to optimise later.

**Key Findings:**
1. **Good open-source NSE libraries exist, but they scrape a site whose terms forbid scraping.**
   `nselib` (Apache-2.0) is the cleanest option and covers equities, derivatives,
   indices, corporate filings and financial results. However, NSE's Terms of Use prohibit
   automated data collection without written consent. These libraries are fine for
   prototyping, but production at a regulated firm needs a licensed source: NSE Data &
   Analytics, or a broker feed such as **IIFL's own XTS Market Data API**.
2. **vLLM is the V1 model runtime (updated v1.2).** Foundry Local is built for a single
   device, so V1 uses vLLM: it batches requests from many users, provides an
   OpenAI-compatible server and supports tool calling. It needs a **Linux GPU host**,
   which is now a V1 prerequisite. The app still talks to the model only through an
   **OpenAI-compatible endpoint**, so the runtime stays swappable.
3. **Guardrails must be layered, and one popular option is now dead.** LLM Guard
   (Protect AI) was **archived on 2026-07-09**. Use **Microsoft Presidio** (built-in
   recognisers for `IN_PAN` and `IN_AADHAAR`) as a deterministic PII gate before the
   model. Add a small open guard model (Qwen3Guard 0.6B/4B or Granite Guardian) for
   prompt-injection and safety checks, and allow only read-only tools.

**Bottom Line:** The V1 stack is Python on the backend and React on the frontend. It
uses **FastAPI** with SQLAlchemy 2 (async) + Alembic for the API (chosen by the project
owner on 2026-10-06, replacing the v1.0 Django recommendation). Auth and user admin are
built from small, proven pieces: Argon2 hashing, opaque revocable session tokens in
HttpOnly cookies, and an admin page in the React app. LLM logic uses **PydanticAI** and
stays independent of the web framework.
**PostgreSQL** is the database, with pgvector added later for IIFL document retrieval.
The UI is a **React + TypeScript** chat app. The model runs on **vLLM** on a Linux GPU
host behind an OpenAI-compatible interface (decided 2026-10-06; Foundry Local dropped). Market data goes
through a **`MarketDataProvider` interface**: `nselib` + `mftool` in development, and a
licensed feed (IIFL XTS or NSE Data & Analytics) in production. Every layer has a known
upgrade path, and V1 needs no exotic tools.

**Primary Recommendation:** Take this stack into `/bmad-architecture` as the proposed
baseline and record each major choice as an ADR. Before writing the PRD, resolve the
**data-licensing question** (who supplies production market data, and on what terms).
It is the biggest non-technical risk.

---

## Research Scope

### Objectives

**Primary Questions:**
1. Which open-source Python libraries can supply Indian (NSE/BSE/AMFI) market data, and
   what are their licences, coverage and maintenance health?
2. Is it legally safe to use those libraries in an internal finance product?
3. What backend, LLM-orchestration, guardrail, UI, storage and inference stack best fits
   a single builder, with a manageable learning curve and later optimisation paths?

**Secondary Questions:**
- Which Foundry Local models support tool calling?
- What does Indian regulation (SEBI AI/ML guidance, DPDP Act) imply for logging and PII?
- Should Knox be kept, or is there a better auth approach? (Knox was only an example.)

### Scope Boundaries

**In Scope:**
- Open-source and free market-data libraries for India, plus broker or licensed feeds as
  the production path
- Python web/API frameworks, LLM agent frameworks, guardrails, chat UIs, vector storage,
  inference runtimes and observability

**Out of Scope:**
- Paid terminal vendors (Bloomberg, Refinitiv): not open source, and not needed for V1
- Trading and order placement: excluded from V1 (see project-context non-goals)
- Detailed hardware sizing: depends on the deployment decision; belongs to architecture

**Time Frame:** State of the ecosystem as of October 2026
**Geographic Focus:** India (NSE, BSE, AMFI, SEBI)

---

## Methodology

### Research Approach

**Research Type:** Secondary (desk research)

**Methods Used:**
- About 25 WebSearch queries covering market-data libraries, licensing, frameworks,
  guardrails, inference runtimes and regulation
- Direct WebFetch of primary sources: Microsoft Learn (Foundry Local tool calling), the
  Foundry Local GA blog, the GitHub repos for `nselib` and `llm-guard`, PyPI
  (`jugaad-data`) and the OpenBB blog
- Cross-checked each key claim against at least two sources where possible

### Data Sources

**Primary Sources:**
1. NSE Terms of Use: https://www.nseindia.com/static/nse-terms-of-use (Official, High)
2. Microsoft Learn, Foundry Local tool calling (updated 2026-07-14) (Official, High)
3. Foundry Local GA blog, 2026-04-09 (Official, High)
4. GitHub: RuchiTanmay/nselib, protectai/llm-guard (Primary, High)
5. OpenBB blog "OpenBB belongs to everyone", 2026-08-25 (Primary, High)

**Secondary Sources:** comparison blogs and benchmarks (dev.to, open-techstack, Kanopy,
Particula, Spheron, Bizon), libraries.io and fossradar for project metadata, and law-firm
commentary (Lexology, IndiaCorpLaw) on SEBI guidance.

### Limitations

- The NSE Data Policy page timed out on direct fetch. Licensing conclusions rely on the
  Terms of Use and the Data Usage & Sharing Policy as summarised in search results.
- Benchmark numbers for vLLM, guard models and agent-framework token costs come from
  vendor-neutral blogs and single papers. Treat them as directional.
- The SEBI AI/ML guidance cited is a **2025 consultation paper** plus an intermediaries
  amendment. Confirm the final text with IIFL compliance.
- The exact Foundry Local model catalog changes over time. Confirm with
  `foundry model list` on the target machine.

---

## Detailed Findings

### Finding 1: Indian market-data libraries: coverage is good, licensing is the catch

**Summary:** Several maintained Python libraries wrap NSE's public website. They differ
mainly in **licence** and **coverage**. All of them rely on automated access to
nseindia.com, which NSE's terms prohibit without written consent.

| Library | Licence | Coverage | Health (Oct 2026) | Verdict |
|---|---|---|---|---|
| **nselib** | Apache-2.0 | Equity price/volume, bhavcopy, bulk/block deals, corporate actions, **financial results**, F&O and option chains, indices, India VIX, holidays, FII/DII | v2.5.x, ~180★, 1 core maintainer | **Recommend (dev/prototype)** |
| **NSEPython** | GPL-3.0 | Quotes, live indices, F&O, history | ~350★, active | Consider: copyleft licence |
| **jugaad-data** | "YOLO" (non-OSI) | Stocks, F&O, indices, **RBI data**, built-in cache, CLI | v0.35.9 (2026-09-23), active | **Avoid**: unclear licence |
| **NseIndiaApi** | — | Unofficial NSE API wrapper | v3.1.2 (2026-07-11) | Consider later |
| **nsepy** | — | Historical NSE | Old, superseded | Avoid |
| **mftool** | MIT [UNVERIFIED] | AMFI mutual-fund NAVs (latest and historical), scheme lists | Stable, no deps | **Recommend** |
| **yfinance** | Apache-2.0 | Global, NSE via `.NS` tickers | Active; rate-limited (~2k req/h), Yahoo data is **personal use only** | Fallback only |
| **OpenBB ODP v5** | Apache-2.0 | 100+ providers, weak native India coverage | Company **wound down Aug 2026**; now stewarded by OpenBQ / FINOS | Watch, not V1 |

**Production-grade / licensed sources:**
- **IIFL XTS Market Data API** (Symphony Fintech; official Python SDK
  `symphonyfintech/xts-pythonclient-api-sdk`): quotes, depth and historical candles,
  free for IIFL customers. This is the most natural licensed source, given the product
  is IIFL-focused.
- **NSE Data & Analytics**: NSE's official licensing arm for real-time, delayed and EOD
  data and redistribution.
- Other broker APIs (Zerodha Kite, Upstox, Angel SmartAPI) are possible alternatives
  [not deeply evaluated].
- **Accord Fintech ACE (chosen for post-V1, 2026-10-06):** an **authorised data vendor
  of BSE/NSE/MCX/NCDEX**, delivering by API and FTP. **ACE MF** covers all AMCs, 4,000+
  schemes, NAVs since inception, portfolios, returns, risk metrics and fund-manager
  data. **ACE Equity / ACE Datafeed** covers company financials, stock-market data,
  derivatives, corporate announcements, real-time and EOD. Note: ACE MF alone covers
  mutual funds. Equity prices and IIFL / IIFL Finance company data would need the ACE
  Equity / Datafeed products, so confirm scope when subscribing.

**Data strategy (decided 2026-10-06):**
- **V1 (~10 internal users):** open-source libraries behind `MarketDataProvider`:
  `nselib` (NSE equities, indices, corporate filings, results) and `mftool` (AMFI NAVs).
  **Accepted risk:** NSE's ToU forbid automated collection without consent. Mitigations:
  internal use only, no redistribution, EOD/delayed data preferred, aggressive caching
  with scheduled refreshes (no per-chat live scraping), polite rate limits, a source and
  timestamp on every answer, and a kill switch per provider. Recommend an informal
  compliance acknowledgement.
- **Post-V1:** subscribe to **ACE (Accord Fintech) APIs** and add an `AceProvider`. Thanks
  to the abstraction this is a new adapter plus configuration, with no change to app or
  agent code.

**Supporting Evidence:**
```
"[Users shall not conduct] any systematic or automated data collection activities
(including scraping, data mining, data extraction and data harvesting) ... without
express written consent."
Source: NSE Terms of Use, https://www.nseindia.com/static/nse-terms-of-use
```

**Implications for Planning:**
- Put a **`MarketDataProvider` abstraction** in the architecture: `NselibProvider`
  (dev), `XtsProvider` or a licensed provider (prod), and `MftoolProvider` (MF NAVs).
  Tools call the interface, never a library directly.
- Cache all market data (Postgres or Redis with a TTL) so we make few upstream calls.
  This is good manners, faster, and lowers ToU risk.
- In the PRD, separate **"chores"** that need live prices from those that work with
  EOD/delayed data. EOD is much easier to license.

**Confidence:** [VERIFIED]: NSE ToU, nselib licence and coverage, jugaad-data licence,
OpenBB status. mftool licence: [UNVERIFIED].

---

### Finding 2: vLLM is the V1 inference server (decided 2026-10-06); Foundry Local is dropped

> **Update v1.2 (2026-10-06):** v1.0 proposed Foundry Local for V1, with vLLM later. The
> project owner chose **vLLM in V1**. The findings on Foundry Local stay below for the
> record.

**Summary:** vLLM is a production serving engine. It batches requests from many users
continuously, provides an OpenAI-compatible server, and supports tool calling through
model-specific parsers. That fits a central, multi-user deployment better than Foundry
Local, which is designed for on-device use (GA 2026-04-09; no multi-user guidance).
The cost is hardware: **vLLM needs Linux and a supported GPU**, either NVIDIA (compute
capability ≥ 7.5, CUDA 12.8+), AMD (ROCm 6.3+) or Intel. It does not run natively on
Windows or macOS.

**Details:**
- **Concurrency:** a 2026 A100 benchmark showed vLLM ~2.3× faster than llama.cpp at 8
  concurrent users and far faster at 64. Single-user speed is similar (directional).
- **Tool calling:** enable it with `--enable-auto-tool-choice --tool-call-parser <p>`.
  Parsers exist for gpt-oss (`openai`), Qwen2.5/Qwen3 (`hermes`), Llama 3.x/4, Mistral,
  Granite and others. Caveat: parallel tool calls vary by model, and small models often
  produce malformed tool calls.
- **Models:** any Hugging Face model vLLM supports, not just a curated catalog. That
  widens V1 choices: Qwen3 family, gpt-oss-20b, Granite, Llama.
- **Guard model:** Qwen3Guard or Granite Guardian can run on the same vLLM host, as a
  second model or second instance. This resolves v1.0's open question about Foundry
  Local support.
- **Apple Silicon:** `vllm-metal` (community-maintained, Apache-2.0, MLX backend)
  needs **macOS 15+**. The builder's dev machine is an M2 Pro, 16 GB, macOS 14.4.1, so
  it cannot run it without an OS upgrade. Even after upgrading, 16 GB only fits small
  quantised models.

**Implications for Planning:**
- **ADR (kept):** the app talks to the LLM only through an OpenAI-compatible client
  configured by `LLM_BASE_URL` and `LLM_MODEL` (PydanticAI `VLLMProvider`/`OpenAIProvider`).
  vLLM is the V1 runtime.
- **Infrastructure (confirmed 2026-10-06): one NVIDIA A10 24 GB** (Ampere, compute
  capability 8.6, so vLLM supports it). Prompts stay inside our network.
  What the A10 means for V1:
  - **No native FP8 compute** on Ampere. FP8 checkpoints run as weight-only W8A16 via
    Marlin kernels, which still roughly halves weight memory. **AWQ/GPTQ INT4 via
    Marlin** is fully supported and is the best fit for 24 GB.
  - **gpt-oss-20b is risky on Ampere.** vLLM's recipe lists Hopper/Blackwell/AMD as
    primary targets. On Ampere it falls back to Triton attention + Marlin MXFP4 MoE,
    MXFP4 is officially Hopper+, and a Marlin MoE crash with gpt-oss-20b's
    non-128-aligned dimensions was closed "not planned" (vLLM #38022, reported on
    Blackwell). Keep it in the spike only if it starts cleanly.
  - **Primary candidate: Qwen3-14B (AWQ/GPTQ INT4)**, about 9–10 GB of weights.
    Fallback: **Qwen3-8B (FP8 W8A16 or INT4)**. 27B-class 4-bit models fit only at
    one request at a time with no room for the guard model, so they are out for a
    multi-user V1.
  - **Qwen3 tool calling:** `--enable-auto-tool-choice --tool-call-parser hermes`.
    Known vLLM issues mix reasoning ("thinking") mode with tool-call parsing, so V1
    runs with **thinking disabled** for tool turns unless the spike shows otherwise.
  - **Proposed GPU memory budget** (estimate, validate in the spike):

    | Process | `--gpu-memory-utilization` | ≈ VRAM | Notes |
    |---|---|---|---|
    | vLLM: chat model (Qwen3-14B INT4) | 0.78 | ~18.7 GB | ~9–10 GB weights + ~8 GB KV cache; `--kv-cache-dtype fp8`, `--max-model-len` 16k–32k, `--enable-prefix-caching` |
    | vLLM: guard model (Qwen3Guard-Gen-0.6B) | 0.12 | ~2.9 GB | Separate port; short context (4k) |
    | CUDA context / headroom | — | ~2.4 GB | Two processes each carry overhead |
    | Presidio (spaCy), embeddings, app, DB | CPU | — | Keep off the GPU in V1 |

  - **Host prerequisites:** Linux, NVIDIA driver new enough for CUDA 12.8+ (R570+),
    Docker + NVIDIA Container Toolkit (run the `vllm/vllm-openai` image), and enough
    disk for model weights (~50 GB to hold several candidates).
  - **Capacity: V1 target is ~10 users (confirmed 2026-10-06).** KV-cache check for
    Qwen3-14B (40 layers × 8 KV heads × 128 dim, FP8 KV): about **80 KiB per token**,
    so ~8 GiB of KV holds **~105k tokens**. Ten users all active at once with **8k
    tokens of context each (~82k)** fits. At 16k each (~164k) they would not, and vLLM
    would queue or pre-empt requests. Design implications:
    - Cap working context at **~8k tokens per conversation** (trim or summarise older
      turns, keep tool results compact) and set `--max-model-len` to about 16k.
    - Set `--max-num-seqs` to about 16. In practice 10 users are rarely all generating
      at the same moment.
    - Load-test 10 concurrent streaming sessions with the guard model co-resident in
      the spike, and set a time-to-first-token target in the PRD NFRs.
    If usage outgrows one A10, the path is a second A10 or a bigger card, or a smaller
    model.
- **Developer loop on the Mac:** either (a) point `LLM_BASE_URL` at a shared vLLM dev
  server on the GPU host (recommended, same behaviour as prod), or (b) run a dev-only
  OpenAI-compatible stand-in locally (Ollama or llama.cpp with a small model). With (b),
  tool-calling behaviour differs, so all evals and acceptance tests must run against vLLM.
- **Model-selection spike on the A10** (one of the first stories): compare Qwen3-14B
  INT4, Qwen3-8B FP8/INT4, Qwen2.5-14B INT4 and (if it starts) gpt-oss-20b on ~30
  representative finance prompts. Measure tool-call accuracy, time to first token,
  tokens/s, and concurrent sessions sustained with the guard model co-resident.
- **Deployment topology resolved:** one central server, so guardrails and audit logs
  cannot be bypassed.

**Confidence:** [VERIFIED] (vLLM install and tool-calling docs, vllm-metal README,
Foundry Local GA blog; benchmark directional; GPU sizing [UNVERIFIED], to be confirmed
by the spike)

---

### Finding 3: Guardrails need layers; LLM Guard is archived

**Summary:** No single tool covers everything. The common production pattern combines a
fast deterministic scanner, a safety/injection classifier, and output validation.

| Option | Role | Status | Verdict |
|---|---|---|---|
| **Microsoft Presidio** | PII detect/redact; built-in `IN_PAN`, `IN_AADHAAR`, `IN_GSTIN`, `IN_PASSPORT`, `IN_VOTER`; custom regex recognisers | Active | **Recommend: layer 1 (input + output)** |
| **Qwen3Guard-Gen 0.6B / 4B** | Safety + jailbreak classifier, 119 languages | Active; 4B ranked #1 in an ICLR 2026 guard benchmark | **Recommend: layer 2** |
| **IBM Granite Guardian 4.1** (5B/8B) | Strong on prompt injection, RAG grounding | Released 2026-04 | Consider (alternative to Qwen3Guard) |
| **Llama Guard 4 (12B)** | Multimodal safety classifier | Ranked lower in the same benchmark; heavy | Avoid for V1 |
| **NeMo Guardrails** | Dialogue rails (Colang) | Active; +100–800 ms latency; steep learning curve | Later, if complex flows are needed |
| **Guardrails AI** | Output/structure validation | Active; ~5–20 ms overhead | Optional (PydanticAI covers typed outputs) |
| **LLM Guard** | Input/output scanners | **Archived 2026-07-09** | **Avoid** |

**Recommended V1 guardrail pipeline:**
1. **Pre-LLM PII gate (deterministic, blocking):** Presidio with India recognisers plus
   custom ones for bank/demat account numbers, IFSC, UPI IDs, Indian mobile numbers,
   email and card numbers. Policy: **block and explain** (do not redact silently). The
   rejected text is never logged in raw form.
2. **Safety/injection classifier** on user input and on **tool outputs** (fetched data
   can carry injected text).
3. **Constrained agent:** a fixed system prompt, a narrow scope (IIFL / IIFL Finance plus
   market data), **read-only allow-listed tools** with typed arguments, and no
   free-form web access.
4. **Output checks:** a second Presidio pass, a "not investment advice" policy, and
   citations of data source and timestamp.
5. **Audit log** of every request, guard decision and tool call (see Finding 6).

**Confidence:** [VERIFIED]: Presidio India recognisers, LLM Guard archive (GitHub
banner), guard-model rankings (single benchmark paper, directional).

---

### Finding 4: Backend: FastAPI (decided 2026-10-06), with auth and admin built deliberately

> **Update v1.1 (2026-10-06):** v1.0 recommended Django 5 + Django Ninja. The project
> owner chose **FastAPI + React** for V1. This finding now records that decision and the
> extra work it brings: auth, user admin and migrations are assembled by us rather than
> coming built in.

**Summary:** FastAPI is the default for AI backends in 2026. It is async-native (ASGI on
Starlette), validates with Pydantic, generates OpenAPI docs and streams tokens over SSE
without extra libraries. It also matches the rest of the stack: PydanticAI was designed
with FastAPI ergonomics. The trade-off is that FastAPI includes no auth, admin or ORM,
so V1 must build these, and since security is the top constraint they need careful
design and tests.

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **FastAPI + SQLAlchemy 2 (async) + Alembic** | AI-ecosystem default, best async/SSE story, lightweight, one style (Pydantic) from API to agent | Auth, user admin and security defaults are ours to build and test | **Chosen** |
| Django 5 + Django Ninja | Built-in admin and mature auth | Heavier; not the owner's preference | Not chosen |
| Django + DRF + Knox | Very mature token auth | Verbose, sync-first | Not chosen |

**Auth design for FastAPI (Knox was only an example):**
- **Don't build on `fastapi-users`:** it is in **maintenance mode** (security fixes only,
  no new features; a successor toolkit is in progress). Its last release was 2026-03-27.
- **Use small, well-known building blocks instead:**
  - `pwdlib[argon2]` for Argon2id password hashing.
  - **Opaque, random session tokens** (`secrets.token_urlsafe`), stored **hashed**
    (SHA-256) in Postgres with expiry and last-seen time. This is the Knox model:
    revocable instantly and listable per user.
  - Tokens are sent to the React SPA in an **HttpOnly + Secure + SameSite=Strict cookie**
    and never stored in JavaScript. State-changing requests get CSRF protection
    (double-submit token or a required custom header).
  - Login rate limiting and lockout (e.g. `slowapi` + a failed-attempt counter).
  - Role field on the user: `admin` | `user`; FastAPI dependencies enforce it per route.
- **Avoid JWTs in V1:** instant revocation matters more than statelessness here.
- **Admin flow:** the admin creates a user (username + email) on an **admin page in the
  React app** (backed by `/api/admin/users` endpoints) → the system emails a one-time,
  expiring **set-password link** → the admin can deactivate users and revoke sessions.
  `SQLAdmin` is optional as an internal ops console, not the user-facing admin.
- MFA (TOTP via `pyotp`) and SSO (OIDC) are later add-ons.

**Implications for Planning:**
- Auth and user admin become **their own epic**, with security acceptance tests
  (session fixation, CSRF, brute force, revocation, privilege escalation).
- Keep LLM orchestration in a **plain Python package** (`assistant/`) with no FastAPI
  imports, so it can become a separate inference/agent service later.

**Confidence:** [VERIFIED]: FastAPI's position for AI backends (multiple 2026
comparisons); fastapi-users maintenance mode (GitHub org page, libraries.io).

**Confidence:** [VERIFIED] (multiple 2026 comparisons; framework docs)

---

### Finding 5: LLM orchestration: PydanticAI first, LangGraph when flows get complex

**Summary:** PydanticAI works like FastAPI (typed tools via `@agent.tool`, Pydantic
outputs). It works with any OpenAI-compatible endpoint through `OpenAIProvider(base_url=…)`
and has dedicated vLLM and Ollama providers, so it matches the swappable-runtime ADR
exactly. LangGraph adds explicit graph state and step-level audit trails, which suits
multi-step agent workflows, but it is harder to learn.

**Implications for Planning:** V1 uses PydanticAI with a small set of typed, read-only
tools: `get_quote`, `get_eod_history`, `get_corporate_results`, `get_mf_nav`,
`search_iifl_docs` (later). Revisit LangGraph if the PRD introduces multi-step "chores"
that need human approval steps.

**Confidence:** [VERIFIED] for compatibility. The token-cost benchmark (PydanticAI
cheaper than LangGraph) is [UNVERIFIED], single source.

---

### Finding 6: Regulatory context: log everything, keep PII out

**Summary:** SEBI's June 2025 consultation paper on responsible AI/ML proposes six
pillars (ethics, accountability, transparency, auditability, data privacy, fairness),
human oversight, documentation, and **retention of AI input/output data for 5 years**.
A later intermediaries amendment (Reg. 16C) makes regulated entities solely responsible
for AI outputs and data privacy. The DPDP Act 2023 and Rules 2025 govern personal data.

**Implications for Planning:** Add NFRs for an append-only audit trail (prompt, guard
verdicts, tool calls, response, model version), retention configurable to ≥5 years, a
model/version register, and a documented human-oversight owner. The PII gate also
reduces DPDP exposure, because personal data never enters prompts or logs.

**Confidence:** [VERIFIED] that guidance exists (multiple legal sources). Final binding
text: confirm with compliance.

---

## v2.0 Findings: Document Processing (2026-10-07)

### Finding 7: Docling for local parsing of PDF, DOCX, PPTX and XLSX, incl. tables and OCR
**Summary:** Docling (IBM Research, now under the Linux Foundation; **MIT licence**)
converts PDF, DOCX, PPTX, XLSX, HTML and CSV into a structured document model and
Markdown/JSON. It handles layout analysis, reading order and **TableFormer** table
structure, with pluggable OCR engines (RapidOCR, Tesseract, EasyOCR). It runs fully
locally with no telemetry. Granite-Docling-258M (Apache-2.0) is an optional compact VLM
for hard layouts.
**Implications:** one parsing library for all four formats. Layout, table and OCR models
run on **CPU**, so the host's CPU and RAM matter (open question). Model weights must be
bundled offline. **Confidence:** [VERIFIED] (GitHub + docs).

### Finding 8: vLLM `cache_salt` isolates the prefix cache between users
**Summary:** vLLM accepts an optional per-request `cache_salt`, which is injected into
the first block hash. Only requests with the same salt can reuse cached KV blocks. This
prevents timing side-channel inference of other users' cached prompts. Trade-off: a
shared system-prompt prefix is recomputed per salt (cache reuse only within a salt
group). Token-range salting is emerging for finer control.
**Implications:** salt per **user** (or per deal workspace for shared-document
prompts). Expect somewhat lower prefix-cache hit rates. Confirm the pinned vLLM version
supports it in the spike. **Confidence:** [VERIFIED] (vLLM docs, PR #17045).

### Finding 9: Qwen3-14B context is 32k native; summarise long documents in chunks
**Summary:** Qwen3-14B supports 32,768 tokens natively. YaRN can extend it to 131k, but
Qwen advises against YaRN if typical inputs are ≤ 32k because quality may degrade. On the
A10, ~105k tokens of KV cache are shared by all users (v1.4 sizing), so one 32k request
uses about a third of the cache.
**Implications:** chat Q&A stays at an ~8k working context with retrieval. Long-document
summaries use **map-reduce** (section summaries of ~6k-token chunks, then a combine
step) as **background jobs**, with at most 1–2 concurrent jobs, so interactive chat is
not starved. A 100-page document (~50–70k tokens) is roughly 10–12 map calls plus 1–2
reduce calls: a few minutes on the A10 (to be measured). **Confidence:** [VERIFIED]
(model card); throughput [UNVERIFIED] until the spike.

### Finding 10: Excel processing: typed operations over a DataFrame engine
**Summary (design research):** allowing a 14B model to write and run Python (a code
interpreter) brings sandbox-escape, resource-exhaustion and silent-error risk.
**Typed operations** (the model picks an operation and its parameters from a schema)
executed by a vetted engine are deterministic and testable. Polars (MIT) gives fast,
memory-efficient DataFrame operations. openpyxl (MIT) reads and writes .xlsx while
preserving sheets.
**Implications:** V1 uses a fixed operation catalogue compiled to Polars, with results
written to a **new** workbook. A sandboxed code runner is a later option.
**Confidence:** design recommendation (owner-approved 2026-10-07).

**Sources (v2.0):**
- vLLM. "Automatic Prefix Caching." https://docs.vllm.ai/en/stable/design/prefix_caching/. Accessed 2026-10-07.
- vLLM. PR #17045 "Prevent side-channel attacks via cache salting." https://github.com/vllm-project/vllm/pull/17045. Accessed 2026-10-07.
- Docling. https://github.com/docling-project/docling and https://docling.org/. Accessed 2026-10-07.
- Qwen. "Qwen3-14B model card." https://huggingface.co/Qwen/Qwen3-14B. Accessed 2026-10-07.

---

## Market Size & Opportunity

N/A: technical/domain research.

## Competitive Analysis

N/A: technical/domain research.

---

## Technical Assessment

### Recommended V1 Stack

| Layer | V1 choice | Why it's easy to learn now | Later optimisation path |
|---|---|---|---|
| Language / tooling | Python 3.13, **uv**, ruff, pytest | Already in the repo | — |
| Backend / API | **FastAPI** + SQLAlchemy 2 (async, asyncpg) + Alembic + pydantic-settings, on uvicorn | Async-native, Pydantic everywhere, SSE streaming built in | Split the agent into its own service; gunicorn/uvicorn workers; horizontal scaling |
| Auth / users | `pwdlib[argon2]` + **opaque hashed session tokens in Postgres**, HttpOnly cookie, CSRF, `slowapi` lockout, admin-provisioned users + set-password email (no `fastapi-users`: maintenance mode) | No custom crypto; small, auditable code | TOTP MFA (`pyotp`), SSO (OIDC), API tokens for integrations |
| LLM orchestration | **PydanticAI** (OpenAI-compatible provider) | Typed tools, FastAPI-like | LangGraph for multi-step workflows |
| Inference | **vLLM** (OpenAI-compatible server, `--enable-auto-tool-choice`) on a Linux GPU host; guard model on the same host | Standard OpenAI API; one server for chat + guard | Quantisation (FP8/AWQ), prefix caching, speculative decoding, multi-GPU tensor parallelism, autoscaling |
| Model | Spike on A10: **Qwen3-14B INT4** (primary) vs Qwen3-8B vs Qwen2.5-14B; gpt-oss-20b only if stable on Ampere | Any vLLM-supported Hugging Face model; one-line switch | Bigger or fine-tuned models (LoRA adapters served by vLLM) |
| Guardrails | **Presidio** (+ India recognisers) → **Qwen3Guard** → allow-listed tools → output scan | Deterministic first layer | Granite Guardian, NeMo rails |
| Market data | `MarketDataProvider` interface: **nselib + mftool** (dev), **IIFL XTS / NSE licensed** (prod) | Pandas-friendly | Streaming quotes via XTS websockets |
| Database | **PostgreSQL 16+** | One DB for everything | **pgvector** for RAG over IIFL / IIFL Finance filings and reports; Qdrant only if measurements show pgvector is the bottleneck |
| Cache / jobs | **Redis** (cache, rate limit) | Simple | `arq` or Celery for scheduled data refreshes |
| Frontend | **React + TypeScript (Vite)**, React Router, TanStack Query; chat SPA served same-origin (reverse proxy), streaming via SSE; includes the admin user-management page | Most widely documented UI stack | Charts (financial data), richer workflows |
| Observability | Structured logs + **OpenTelemetry**; **Langfuse** self-hosted (MIT) for LLM traces | Docker Compose | Evals, prompt versioning, dashboards |
| Packaging | Docker Compose (app, Postgres, Redis, Langfuse) | One command up | Kubernetes when there are several servers |

### Alternatives considered for the UI

| Option | Verdict | Reason |
|---|---|---|
| Streamlit (current prototype) | Avoid for V1 product | Fine for demos; weak auth and session model for a secured multi-user app |
| Chainlit | Consider | Fastest chat UI with auth hooks, but **community-maintained since May 2025**, and a second auth system next to ours | 
| Open WebUI | Avoid | Separate product with its own users/auth; hard to enforce our guardrail pipeline |
| Server templates (Jinja2) + HTMX + SSE | Not chosen | Lowest learning curve; less room for rich financial UI |
| **React + TS** | **Recommend** | Long-term extensibility (charts, tables), huge ecosystem |

**Technical Recommendations:**
1. **Abstract the two volatile dependencies (LLM runtime and market-data source)
   behind interfaces from day one.** Both are expected to change between V1 and
   production (Findings 1 and 2).
2. **Make the PII gate deterministic and run it before the model,** with
   classifier-based checks on top (Finding 3).

**Risks / Trade-offs:**
- FastAPI leaves auth and admin to us, so it is the biggest security surface we write
  ourselves. Keep it small, use proven libraries for crypto, and give it dedicated
  security tests and a review.
- Running a guard model next to the chat model needs extra GPU memory. Qwen3Guard-0.6B
  keeps the overhead small, and it can be served by vLLM on the same host.
- **GPU hardware is a new V1 dependency.** No GPU host means no V1. Procurement or
  provisioning lead time can become the critical path.
- Small local models do worse at tool calling. The model spike must measure tool-call
  accuracy, not just chat quality.

---

## Gaps & Opportunities

1. **No licence-clean open-source NSE data source exists.** Every free library scrapes.
   Opportunity: IIFL's own XTS API turns this from a blocker into an integration.
2. **India-specific PII coverage is partial.** Presidio covers PAN and Aadhaar, but bank
   and demat account numbers, IFSC and UPI need custom recognisers. Opportunity: a small,
   well-tested in-house recogniser pack (maskflow / pii-redact-indian are references).
3. **There is no ready "IIFL knowledge" source.** Answers about IIFL / IIFL Finance need
   grounding (annual reports, investor presentations, NSE/BSE corporate filings via
   `nselib`). Without RAG, a 7–20B model will make things up. pgvector RAG is likely
   needed sooner than "later".

---

## Risks & Challenges

1. **Market-data licensing (NSE ToU)**
   - Probability: High | Impact: High
   - Mitigation: Dev-only use of scrapers; secure an XTS or NSE licence before
     production; cache aggressively; record the decision in an ADR and get compliance
     sign-off.
2. **Hallucinated company facts or financial figures**
   - Probability: High | Impact: High
   - Mitigation: Tool-first answers with source and timestamp, RAG over official IIFL
     documents, refuse when no source is found, and an eval set in Langfuse.
3. **PII leakage via paraphrase or bypass**
   - Probability: Medium | Impact: High
   - Mitigation: Layered gate, red-team test suite in CI, log redaction, server-side
     enforcement only.
4. **GPU host availability and sizing (vLLM)**
   - Probability: Medium | Impact: High
   - Status: host confirmed (NVIDIA A10 24 GB). Remaining risk is capacity.
   - Mitigation: size with the model-selection spike;
     load-test the pilot; keep the OpenAI-compatible abstraction so a smaller model or
     another server is a configuration change.

---

## Recommendations

### Primary Recommendations

1. **Adopt the recommended V1 stack as the architecture baseline**
   - Action: Bring the stack table (FastAPI + React, decided) into `/bmad-architecture`. Write ADRs for the backend,
     auth, LLM runtime abstraction, guardrail pipeline, data-provider abstraction and
     frontend.
   - Rationale: Findings 2, 3, 4, 5
   - Priority: High
   - Handoff: bmad-architecture

2. **Market-data source: resolved (2026-10-06)**
   - Action: V1 uses nselib + mftool behind `MarketDataProvider`, with caching and
     ToU mitigations. Post-V1 moves to Accord Fintech ACE APIs. In the PRD, decide
     which chores need live vs EOD data and confirm ACE product scope (MF vs
     Equity/Datafeed).
   - Rationale: Finding 1
   - Priority: High
   - Handoff: bmad-prd (NFRs, constraints), decision-log

3. **Write security as testable NFRs**
   - Action: In the PRD, specify the PII entity list, the block policy, the guard
     pipeline order, audit-log fields and retention (≥5 years), the read-only tool
     policy, and red-team acceptance tests.
   - Rationale: Findings 3, 6
   - Priority: High
   - Handoff: bmad-prd

### Secondary Recommendations

- Schedule a **model-selection spike** (gpt-oss-20b / Qwen2.5-14B / Phi-4) as one of the
  first stories, measuring tool-call accuracy and latency on target hardware.
- Treat **RAG over IIFL / IIFL Finance documents** as a likely V1 epic, not V2.
- Keep the Streamlit app only as a throwaway prototype. Do not grow it into the product.

---

## Next Steps

### Immediate Planning Actions

- [x] Deployment topology: one central server
- [x] Market-data path decided: open-source in V1, ACE (Accord Fintech) APIs post-V1
- [x] Hardware and capacity: one A10 24 GB, ~10 users
- [ ] Confirm with compliance which SEBI / DPDP obligations apply (log retention, oversight)
- [ ] Run `/bmad-prd` with this report as input, then `/bmad-architecture`

### Follow-Up Research Needed

- Can the A10 hold 10 concurrent streaming sessions with chat + guard co-resident at
  ~8k context? — suggested method: load test in the model-selection spike
- Which ACE products and licence terms cover our needs (MF vs Equity/Datafeed;
  internal display use)? — suggested method: Accord Fintech sales/tech call
- Is mftool's licence confirmed? Is the AMFI data terms acceptable? — PyPI/GitHub + AMFI site
- What are the IIFL XTS API terms for internal (non-trading) data use? — IIFL developer docs / account team

### Recommended BMAD Handoff

**Next skill:** `bmad-prd` (to turn scope + security into FR/NFRs), then
`bmad-architecture` (system-architect), which consumes the Technical Assessment directly.

**Key inputs for next skill from this report:**
- Recommended stack table + the two abstraction ADRs (LLM runtime, data provider)
- Guardrail pipeline + regulatory NFRs (audit, retention, PII)

---

## Appendices

### Appendix A: Key Data Tables

See the tables in Findings 1, 3 and 4 and the Technical Assessment.

### Appendix B: Full Source Bibliography

1. NSE India. "Terms of Use." https://www.nseindia.com/static/nse-terms-of-use. Accessed 2026-10-05.
2. NSE India. "NSE Data Sharing & Usage Policy." https://www.nseindia.com/static/market-data/nse-data-policy. Accessed 2026-10-05 (fetch timed out; summary via search).
3. NSE. "Data Usage and Data Sharing Policy" (PDF). https://nsearchives.nseindia.com/web/sites/default/files/inline-files/NSE_DataUsageandSharingPolicy.pdf. Accessed 2026-10-05.
4. RuchiTanmay. "nselib." https://github.com/RuchiTanmay/nselib. Accessed 2026-10-05.
5. Libraries.io. "nselib 2.5.1." https://libraries.io/pypi/nselib. Accessed 2026-10-05.
6. Unofficed. "NSEPython." https://unofficed.com/nse-python/. Accessed 2026-10-05.
7. FOSSRadar. "NSEPython." https://www.fossradar.dev/projects/nsepython. Accessed 2026-10-05.
8. jugaad-py. "jugaad-data." https://github.com/jugaad-py/jugaad-data and https://pypi.org/project/jugaad-data/. Accessed 2026-10-05.
9. mftool. https://pypi.org/project/mftool/. Accessed 2026-10-05.
10. yfinance. https://pypi.org/project/yfinance/ and https://ranaroussi.github.io/yfinance/. Accessed 2026-10-05.
11. yfinance GitHub. "New rate-limiting · Issue #2128." https://github.com/ranaroussi/yfinance/issues/2128. Accessed 2026-10-05.
12. OpenBB. "OpenBB belongs to everyone." https://openbb.co/blog/openbb-belongs-to-everyone/. Accessed 2026-10-05.
13. OpenBB. "License FAQ." https://docs.openbb.co/odp/python/faqs/license. Accessed 2026-10-05.
14. OpenAlgo. "IIFL (XTS)." https://docs.openalgo.in/connect-brokers/brokers/iifl-xts. Accessed 2026-10-05.
15. Symphony Fintech. "xts-pythonclient-api-sdk." https://github.com/symphonyfintech/xts-pythonclient-api-sdk. Accessed 2026-10-05.
16. Microsoft Learn. "Use tool calling with Foundry Local." https://learn.microsoft.com/en-us/azure/foundry-local/how-to/how-to-use-tool-calling-with-foundry-local. Accessed 2026-10-05.
17. Microsoft. "Foundry Local is now Generally Available." https://devblogs.microsoft.com/foundry/foundry-local-ga/. Accessed 2026-10-05.
18. Microsoft Learn. "Model catalog and sourcing in Foundry Local." https://learn.microsoft.com/en-us/azure/azure-sovereign-clouds/private/foundry-local/concept-model-catalog. Accessed 2026-10-05.
19. Microsoft Presidio releases. https://github.com/microsoft/presidio/releases. Accessed 2026-10-05.
20. maskflow. "Indian PII detection & reversible masking." https://github.com/maskflow/maskflow. Accessed 2026-10-05.
21. Protect AI. "llm-guard" (archived). https://github.com/protectai/llm-guard. Accessed 2026-10-05.
22. Particula. "NeMo Guardrails vs Llama Guard vs Guardrails AI in 2026." https://particula.tech/blog/ai-guardrails-compared-nemo-guardrails-ai-llama-guard. Accessed 2026-10-05.
23. Kanopy Labs. "Guardrails AI vs NeMo Guardrails vs LLM Guard." https://kanopylabs.com/blog/guardrails-ai-vs-nemo-guardrails-vs-llm-guard. Accessed 2026-10-05.
24. arXiv. "Benchmarking Open-Source Safety Guard Models." https://arxiv.org/html/2605.28830v1. Accessed 2026-10-05.
25. VDF AI. "Open-Weight Guard Models Compared for On-Prem AI (2026)." https://vdf.ai/blog/open-weight-guard-models-on-premises-ai/. Accessed 2026-10-05.
26. MarkTechPost. "Meet Qwen3Guard." https://www.marktechpost.com/2025/09/26/meet-qwen3guard-the-qwen3-based-multilingual-safety-guardrail-models-built-for-global-real-time-ai-safety/. Accessed 2026-10-05.
27. DEV Community. "FastAPI vs Flask vs Django: which one for an AI backend in 2026." https://dev.to/ayinedjimi-consultants/fastapi-vs-flask-vs-django-which-one-for-an-ai-backend-in-2026-2heo. Accessed 2026-10-05.
28. Capital Numbers. "Django vs FastAPI in 2026." https://www.capitalnumbers.com/blog/django-vs-fastapi. Accessed 2026-10-05.
29. Softaims. "Django REST Framework vs Ninja 2026." https://softaims.com/blog/django-rest-framework-vs-ninja-api-guide-2026. Accessed 2026-10-05.
30. vladzsh. "Django Ninja vs DRF." https://vladzsh.org/blog/django-ninja-vs-drf. Accessed 2026-10-05.
31. Pydantic. "Other compatible APIs." https://pydantic.dev/docs/ai/models/compatible-apis/. Accessed 2026-10-05.
32. Open Techstack. "LangGraph vs OpenAI Agents SDK vs PydanticAI (2026)." https://open-techstack.com/blog/langgraph-vs-openai-agents-sdk-vs-pydanticai-2026/. Accessed 2026-10-05.
33. Ertas AI. "Pydantic AI vs LangGraph." https://www.ertas.ai/blog/pydantic-ai-vs-langgraph-fine-tuned-agents. Accessed 2026-10-05.
34. Chainlit. https://github.com/chainlit/chainlit and https://pypi.org/project/chainlit/. Accessed 2026-10-05.
35. Prem AI. "11 Best Open WebUI Alternatives (2026)." https://blog.premai.io/11-best-open-webui-alternatives-for-enterprise-llm-chat-2026/. Accessed 2026-10-05.
36. DEV Community. "vLLM vs Ollama: Production Serving 2026." https://dev.to/apeder/vllm-vs-ollama-production-serving-2026-37kf. Accessed 2026-10-05.
37. Spheron. "Ollama vs vLLM (2026)." https://www.spheron.network/blog/ollama-vs-vllm/. Accessed 2026-10-05.
38. Open Techstack. "pgvector vs Qdrant (2026)." https://open-techstack.com/blog/pgvector-vs-qdrant-2026/. Accessed 2026-10-05.
39. Effloow. "Langfuse: Self-Host LLM Observability (2026)." https://effloow.com/articles/langfuse-llm-observability-self-host-guide-2026. Accessed 2026-10-05.
40. Lexology. "SEBI's Consultation Paper on AI/ML Guidelines." https://www.lexology.com/library/detail.aspx?g=1ad14350-2973-4596-8e27-b4458dc6c039. Accessed 2026-10-05.
41. IndiaCorpLaw. "Analysing SEBI's AI/ML Governance Framework." https://indiacorplaw.in/2025/07/16/from-algorithms-to-accountability-analysing-sebis-ai-ml-governance-framework/. Accessed 2026-10-05.
42. AI Risk Aware. "India AI Policy: DPDP, SEBI, RBI (2026)." https://airiskaware.com/india-ai-policy. Accessed 2026-10-05.
43. FastAPI Users. GitHub organisation (maintenance-mode notice). https://github.com/fastapi-users. Accessed 2026-10-06.
44. Libraries.io. "fastapi-users 15.0.5." https://libraries.io/pypi/fastapi-users. Accessed 2026-10-06.
45. SQLAdmin. "SQLAlchemy Admin for FastAPI and Starlette." https://aminalaee.github.io/sqladmin/. Accessed 2026-10-06.
46. vLLM. "GPU installation." https://docs.vllm.ai/en/latest/getting_started/installation/gpu.html. Accessed 2026-10-06.
47. vLLM. "Tool calling." https://docs.vllm.ai/en/latest/features/tool_calling.html. Accessed 2026-10-06.
48. vLLM project. "vllm-metal." https://github.com/vllm-project/vllm-metal. Accessed 2026-10-06.
49. vLLM blog. "Announcing vllm-metal: Concurrent Serving on Apple Silicon." https://vllm.ai/blog/2026-09-22-vllm-metal-v0-28-0. Accessed 2026-10-06.
50. vLLM Recipes. "GPT OSS." https://docs.vllm.ai/projects/recipes/en/stable/OpenAI/GPT-OSS.html. Accessed 2026-10-06.
51. vLLM. Issue #38022 "Marlin MoE kernel fails with MXFP4-quantized GPT-OSS 20B." https://github.com/vllm-project/vllm/issues/38022. Accessed 2026-10-06.
52. vLLM. "FP8 W8A8" (Ampere runs FP8 as W8A16 via Marlin). https://docs.vllm.ai/en/stable/features/quantization/llm_compressor/fp8/. Accessed 2026-10-06.
53. vLLM. "Quantization." https://docs.vllm.ai/en/stable/features/quantization/. Accessed 2026-10-06.
54. Qwen. "Function Calling." https://qwen.readthedocs.io/en/latest/framework/function_call.html. Accessed 2026-10-06.
55. vLLM. Issue #19513 "Qwen3 Enable Reasoning breaks Tool Call Parsing." https://github.com/vllm-project/vllm/issues/19513. Accessed 2026-10-06.
56. DEV Community. "Qwen3.6-27B + vLLM + Hermes on 24GB VRAM." https://dev.to/xreyrobertibm/qwen36-27b-vllm-hermes-on-24gb-vram-may-2026-recipe-5452. Accessed 2026-10-06.
57. Accord Fintech. "ACE Datafeed: Authorized Vendor of BSE/NSE/MCX/NCDEX." https://www.accordfintech.com/market-data-feed. Accessed 2026-10-06.
58. Accord Fintech. "Products intended for Data feed Solutions." https://www.accordfintech.com/data-feed-solutions. Accessed 2026-10-06.

### Appendix C: Methodology Notes

Queries covered: NSE Python libraries; OpenBB India coverage and licence; NSE terms and
data policy; yfinance limits; Foundry Local catalog, tool calling and GA; Presidio India
PII; guardrail framework comparisons; guard-model benchmarks; FastAPI vs Django; Django
Ninja vs DRF; PydanticAI vs LangGraph; Chainlit / Streamlit / Open WebUI; vLLM vs
llama.cpp vs Ollama; pgvector vs Qdrant; Langfuse licence; SEBI AI/ML and DPDP; IIFL XTS
API. Direct fetches were made where licence or status claims were decision-critical
(llm-guard archive, jugaad-data licence, OpenBB wind-down, Foundry Local docs).

### Appendix D: Update History

| Version | Date | Sections Updated | Summary of Changes |
|---------|------|-----------------|-------------------|
| 1.0 | 2026-10-05 | All | Initial research report |
| 1.1 | 2026-10-06 | Executive Summary, Finding 4, Technical Assessment, Risks | Owner decision: FastAPI + React for V1 (replaces Django + Ninja). Added FastAPI auth design; fastapi-users rejected (maintenance mode). |
| 1.2 | 2026-10-06 | Executive Summary, Finding 2, Technical Assessment, Risks, Follow-up | Owner decision: vLLM in V1 (replaces Foundry Local). Added GPU/Linux prerequisite, dev-loop options, vllm-metal note. |
| 1.3 | 2026-10-06 | Finding 2, Technical Assessment, Risks, Follow-up | GPU host confirmed: NVIDIA A10 24 GB. Added Ampere quantisation notes, model shortlist, GPU memory budget, host prerequisites. |
| 1.4 | 2026-10-06 | Finding 1, Finding 2, Recommendations, Next Steps | V1 capacity ~10 users (KV-cache sizing added). Data strategy: open-source in V1 (accepted ToU risk + mitigations), Accord Fintech ACE APIs post-V1. |
| 2.0 | 2026-10-07 | Banner, new Findings 7–10 | Pivot to confidential document/Excel assistant: market-data findings superseded; added Docling, vLLM cache_salt, Qwen3 context and map-reduce, typed Excel operations |

---

**Report Generated By:** BMAD Research Skill (bmad-planning-orchestrator)
**Output Path:** bmad-output/research-report.md
**Related Documents:** bmad-output/project-context.md | bmad-output/decision-log.md
**Last Updated:** 2026-10-07
