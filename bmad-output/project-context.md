# Project Context — invest-ai-llm (Confidential Document Assistant)

> The project **constitution**. This document is loaded by every BMAD planning skill
> so they all share the same ground truth. Keep it tight, current, and authoritative.
> When a major decision changes scope, update this file and append the change to
> `decision-log.md`.

- **Track:** bmad-method  _(quick-flow | bmad-method | enterprise)_
- **Created:** 2026-10-05 · **Re-scoped:** 2026-10-07 (pivot from market data to confidential documents; see decision log) · **Renamed:** 2026-10-07 from "Foundry Local AI" to **invest-ai-llm** (new repo)

---

## Project Goal

**V1:** A secure, fully self-hosted AI assistant for **investment bankers**. It lets
them **summarise, question and compare their own confidential documents** (PDF, Word,
PowerPoint, Excel/CSV) and **process Excel workbooks from plain-English prompts**. An
open-source LLM runs on our own GPU server, and no data ever leaves our infrastructure.

**Done and successful looks like:** bankers get accurate, cited summaries and
spreadsheet results in minutes instead of hours. No document, answer or file is ever
visible to anyone outside its owner or deal team. Nothing leaves the server, and
everything is deleted on schedule.

## Primary Users

- **Investment bankers (~10):** work on live, confidential deals. They need fast
  summaries of long documents (CIMs, information memoranda, term sheets, SPAs, due
  diligence reports, pitch decks), answers with page references, comparisons of document
  versions, and Excel work (cleaning, pivots, extracting tables, computing growth and
  multiples). They are not developers.
- **Deal-team members:** bankers who share a deal workspace and its documents.
- **Admin (IT):** creates and manages accounts. **Cannot read document or chat content.**
- **Compliance reviewer:** reviews audit metadata. Can access specific content only
  through a recorded, time-limited "break-glass" request.

## Scope

V1 capabilities (epics):
1. **Platform & model serving:** vLLM on one A10 (24 GB). Model selection includes
   long-document summarisation throughput.
2. **Authentication & roles:** admin-provisioned accounts, manually shared one-time
   links, roles `user`, `admin`, `compliance`.
3. **Chat experience:** streaming chat that works with attached or selected documents
   and workbooks.
4. **Guardrails:** block high-risk personal IDs typed in chat, mask them in documents,
   resist instructions hidden in documents, keep answers in scope, ground every figure
   in its source, fail closed.
5. **Workspaces & isolation:** private spaces and deal workspaces with named members;
   strict separation; per-user model-cache isolation; encryption at rest; IT admins
   can't see content.
6. **Document processing:** upload, parse (incl. tables, OCR for scans), question
   answering with citations, background summaries, comparison, table extraction to Excel.
7. **Excel processing:** describe workbooks, answer questions with exact figures, and
   run typed operations (filter, sort, group, pivot, join, calculated columns,
   finance calculations) that produce new workbooks.
8. **Data lifecycle & audit:** 30-day auto-deletion, immediate permanent deletion,
   watermarked expiring exports, metadata-only tamper-evident audit, compliance review.

## Core Constraints

- **Confidentiality is the top concern.** Documents are client-confidential and often
  contain unpublished price-sensitive information (UPSI).
  - Access is limited to the owner or deal-workspace members.
  - Nothing may cross between users or deals: not search results, caches, summaries,
    exports or logs.
- **Personal identifiers:**
  - High-risk IDs (PAN, Aadhaar, bank/demat account, card, UPI, passport, voter ID) are
    **masked in documents** before any model sees them, and **blocked when typed into
    chat**.
  - Business content (client names, financials, contacts) is allowed.
- **Self-hosted, no egress.** The model runs on our own A10 via vLLM. **No outbound
  internet access** from the running system. No third-party AI APIs. Open-source
  models only.
- **Retention:**
  - Uploaded files and all derived data are auto-deleted after **30 days**; users can
    delete permanently at any time.
  - The audit trail keeps **metadata only** (no content), 5-year default.
- **Excel processing** uses **typed operations only** in V1. The model never writes or
  runs arbitrary code.
- **Tech stack (decided):** FastAPI backend (SQLAlchemy 2 async + Alembic,
  PostgreSQL 18 + pgvector), React + TypeScript frontend, vLLM (OpenAI-compatible).
- **GPU host (verified 2026-10-07):** `nbfcuatyolomlapp01` with **36 vCPU, 432 GiB RAM** and
  **NVIDIA A10-24Q** (24 GB vGPU, driver 570, CUDA 12.8). Disks: root 62 GB,
  `/data` 251 GB (persistent), `/mnt` 1.4 TB (likely an ephemeral temp disk, so scratch
  only). **Azure VM, UAT environment (confirmed 2026-10-07).** Disk encryption: default
  Azure server-side encryption on managed disks. **No Key Vault**: the master key (KEK)
  is a root-only file excluded from backups. `/mnt` is never used for content.
  Outbound internet is already blocked; only an inbound port range from 8000 is exposed
  (8000–8050; the app serves HTTPS on **8043**). **UAT holds synthetic test documents only** until
  production controls and Compliance sign-off are in place.
  - Roughly a 14B INT4 chat model + a 0.6B guard model.
  - About 8k working context per chat. Long documents are processed in chunks;
    summaries run as background jobs (minutes).
  - Qwen3-14B has a 32k native context.
- **Capacity:** ~10 users.
- **No email in V1:** admins share one-time set-password links manually.
- **Auth:** opaque hashed session tokens in HttpOnly cookies, Argon2 passwords, no
  self-signup.
- **Abstractions required:** the LLM runtime sits behind an OpenAI-compatible gateway
  so models can be swapped.
- **Repository:** `invest-ai-llm` (new, clean repo created 2026-10-07; planning in `bmad-output/`).
  The old Streamlit/Foundry Local prototype stays in the separate `foundry_local` folder
  for reference only and is not part of this repo. Foundry Local is not used.
- No market data feeds or market-data APIs (nselib, ACE and internal market DB dropped 2026-10-07).
- No arbitrary code execution on user data in V1 (sandboxed Python deferred).
- No sending documents or prompts to any external service; no cloud LLM fallback.
- No trading or transactions; no client-facing use. This is an internal banker tool.
- No SSO / MFA / self-signup in V1.
- No model fine-tuning in V1.
- No mobile app.
- No email.

## Key Stakeholders / Roles

- **Builder / decision-maker:** a single builder (Chintapalli Dharmendra), working with AI agents.
- **End users:** ~10 investment bankers, organised in deal teams.
- **Admin (IT):** account management only.
- **Compliance:** audit review; break-glass content access.

## Glossary

- **Deal workspace:** a named space with explicit members; its documents and outputs are visible only to members.
- **Private space:** each user's default space, visible only to them.
- **UPSI:** unpublished price-sensitive information (SEBI insider-trading rules).
- **CIM / IM:** confidential information memorandum: a long sale document describing a target business.
- **Masking:** replacing a detected identifier with a placeholder (e.g. `[PAN]`) before text reaches the model.
- **Crypto-shredding:** making data unrecoverable by destroying its encryption key.
- **Break-glass:** an exceptional, justified, logged and time-limited access to content by Compliance.
- **Typed operation:** a predefined spreadsheet action (e.g. "group by X, sum Y") that the model selects and parameterises, instead of writing code.
- **vLLM:** open-source LLM serving engine with an OpenAI-compatible API.

## Open Questions

- Is OCR needed for scanned or signed PDFs? (Assumed yes, Should.)
- What are typical document sizes? (Assumed ≤ 300 pages; long summaries take minutes in the background.)
- Does Compliance accept the break-glass model and metadata-only audit?
- Which deal teams and pilot users? Who are the admin(s) and Compliance reviewer(s)?
- IT-provided off-host backup storage (needed before real deal data; interim: local 2-day backups on `/data/backups`).
- Off-host backup target; TLS certificate source.
- KEK escrow custody and rotation (architecture AQ-6).
- Compliance acceptance of the plaintext search-index exception (architecture AQ-7).

---

## Decision Thread

Running decisions live in [`decision-log.md`](./decision-log.md). Consult it before
making decisions that might contradict earlier ones. Pre-pivot artifacts are archived in
`archive/`.

## Planning Status (count-based)

- **Track:** bmad-method
- **Stories defined:** 71 in `epics.md`; 15 compiled `ready-for-dev` (waves 0–3)
- **Stories remaining:** 71 (0 done). Count-based delivery; no points, no velocity.

_This document plans the work. Implementation is handed to external dev tools via
ready-for-dev story files; the planning plugin never writes or tests application code._
