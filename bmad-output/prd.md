# Product Requirements Document (PRD)

**Project Name:** invest-ai-llm (Confidential Document Assistant)
**Version:** 2.3
**Date:** 2026-10-07
**Author:** John (PM), facilitated by bmad-prd for Chintapalli Dharmendra
**Status:** Baselined. Owner accepted recommendations 2026-10-07; two assumptions flagged (OCR, document size)
**Track:** BMad Method

> Source of truth for *what* and *why*; *how* belongs to architecture.
> **v2.0 is a re-scope.** The market-data assistant (v1.1) is replaced by a confidential
> document and Excel assistant for investment bankers. v1.1 is archived at
> `archive/prd-v1.1-market-data.md`. Requirement IDs are stable: retired requirements
> are marked **WON'T (retired 2026-10-07)**, and new requirements start at FR-039 /
> NFR-020. Overflow lives in `addendum.md`; decisions in `decision-log.md`.

---

## Executive Summary

**Problem Statement:** Investment bankers spend hours on document and spreadsheet work:
reading 100-page CIMs and diligence reports, comparing term-sheet versions, pulling
tables out of PDFs, and reshaping Excel models. Public AI tools could speed this up,
but sending client-confidential deal material, which is usually unpublished
price-sensitive information, to third-party AI services is unacceptable. It breaches
client confidentiality, information barriers and insider-trading controls.

**Proposed Solution:** A fully self-hosted assistant. Bankers upload their own documents
(PDF, Word, PowerPoint, Excel/CSV) into a private space or a deal workspace. They can
then ask questions with page-level citations, get structured summaries, compare
versions, extract tables, and transform workbooks with plain-English instructions.
An open-source LLM runs on our own GPU, and the system has no internet access. Every
document is isolated to its owner or deal team, encrypted at rest, scanned for hidden
instructions, stripped of high-risk personal identifiers before the model sees it, and
deleted automatically after 30 days.

**Business Value:** Hours saved per deal on reading and spreadsheet work, with **zero**
exposure of confidential material outside the firm or across deal teams. The audit
trail is acceptable to Compliance.

**Target Outcome:** Within one month of launch:
- at least 8 of ~10 pilot bankers use the assistant weekly;
- a 100-page document is summarised in ≤ 10 minutes;
- at least 85% of evaluation tasks pass;
- **zero** cross-user/cross-deal access or data-egress incidents.

---

## Project Overview

### Background
The project started as a market-data chat assistant (PRD v1.1, archived). On 2026-10-07
the owner re-scoped it to confidential document processing for investment bankers,
accepting recommended defaults for visibility, identifier handling, retention, Excel
processing and formats (see `decision-log.md`).

Platform decisions still stand: FastAPI + React, vLLM on one NVIDIA A10 (24 GB) for ~10
users, session login with admin-shared links, fail-closed guardrails, figure grounding
and a tamper-evident audit trail.

### Current State → Desired State
- **Current:** Bankers read and summarise documents by hand and do spreadsheet work
  manually. There is no sanctioned AI tool for confidential material.
- **Desired:** One internal assistant behind a login. It works only on the user's own
  or deal-team documents, cites every statement and figure, produces new workbooks and
  summaries, never lets content leave its owners or the server, and deletes everything
  on schedule.

### Stakeholders
| Stakeholder | Role | Interest | Influence |
|-------------|------|----------|-----------|
| Chintapalli Dharmendra | Builder / product owner | Delivers V1 | High |
| Investment bankers (~10) | End users, in deal teams | Fast, accurate document/Excel work; confidentiality | High |
| Admin (IT) | Account management | Simple user control; **no access to content** | Medium |
| Compliance | Reviewer, break-glass access | Information barriers, auditability, no leakage | High |

---

## Goals and Objectives

### Business Goals
1. **BG-1:** Cut the time bankers spend reading, summarising and comparing documents and doing routine Excel work.
2. **BG-2:** Guarantee confidentiality: no content leaves the server, and nothing crosses between users or deal teams.
3. **BG-3:** Give accurate, verifiable outputs. Every statement and figure traces to a page, slide, sheet or calculation.
4. **BG-4:** Satisfy Compliance: information barriers, retention and deletion, a tamper-evident metadata audit, and controlled review access.
5. **BG-5:** Build a foundation that can grow (more users, better models, more formats, sandboxed code) without rework.

### User Goals
1. **UG-1:** Drop in a document or workbook and get a useful answer, summary or new file in minutes.
2. **UG-2:** Trust the output: see exactly where each statement and number came from.
3. **UG-3:** Be certain that my deal material is visible only to me and my deal team, and is deleted when I'm done.
4. **UG-4 (admin/compliance):** Manage access and review activity without seeing deal content (unless break-glass is justified).

---

## Response Scope & Style Policy

> Enforced by FR-013, FR-015, FR-030, FR-036, FR-041. Changing it is a PRD change.

### Level 1: Answer fully
| Area | Examples |
|---|---|
| Work on the user's own / deal-workspace documents | "Summarise this CIM", "What are the key conditions precedent?", "List the reps & warranties", "Compare v3 and v4 of the term sheet" |
| Work on the user's own / deal-workspace workbooks | "Total revenue by segment for FY23–FY26", "Pivot by region", "Add EBITDA margin", "Remove duplicate rows", "Merge Sheet1 and Sheet2 on Company ID" |
| Drafting **from** those materials | "Draft a one-page summary memo of the key terms", "Bullet the risks for the IC note" |
| Financial and deal concepts | "What is a locked-box mechanism?", "Explain EV/EBITDA" |
| Using the assistant | "What file types can I upload?", "How do I share with my deal team?" |

### Level 2: Answer with care
| Area | Rule |
|---|---|
| Projections, valuations, forecasts **in the document** | Report them as the document's figures ("the CIM projects…"). Never create new forecasts. |
| Legal or tax provisions | Explain what the document says; add "not legal/tax advice; confirm with counsel". |
| Anything needing outside data (market prices, news, public filings) | Say the assistant has no internet or market-data access; offer to work from uploaded files. |
| Calculations | Done by the calculation engine only (FR-023), with the formula basis stated. |

### Level 3: Decline politely and offer 2–3 alternatives
| Area | Reason |
|---|---|
| Any document, workspace or conversation the user is not a member of ("what's in Rahul's deal?") | Information barrier (FR-041) |
| Using deal information to trade, tip or share outside the deal team | Insider-trading controls |
| Off-topic: coding help, trivia, personal writing unrelated to work, politics, opinions on people | Approved purpose (FR-013) |
| The assistant's instructions, configuration or other users' activity | Manipulation and confidentiality (FR-014) |

### Style rules
- Plain, professional English. Summaries follow the chosen template (FR-046).
  Spreadsheet results are shown as tables with a downloadable file.
- **Every statement drawn from a document carries a citation** (document, page, slide
  or sheet!range). **Every figure** comes from a document, a sheet or the calculation
  engine (FR-036).
- Indian and international number formats are both supported. The user's document
  convention is kept (₹ crore or USD million, as written).
- When a high-risk identifier was masked, it appears as a placeholder such as `[PAN]`,
  and the answer says so if it matters.

---

## Functional Requirements

> Format: `FR-###: <PRIORITY> — <capability>`. IDs are immutable. "Space" means a
> user's private space or a deal workspace. "Accessible" means owned by the user or in a
> workspace where they are a member.

### EPIC-002: Authentication, Roles & Administration

### FR-001: Admin creates a user account — MUST
**Description:** An admin creates an account (username, email, role). The system
generates a one-time set-password link, which the admin shares manually through an
approved internal channel (no email in V1). The admin can re-issue a link at any time,
which is also how forgotten passwords are handled.
**Acceptance Criteria:**
- The link is shown to the admin **once**, expires in 24 hours, is single-use, and requires the username.
- Re-issuing invalidates earlier links. Using a link ends all of the user's sessions.
- Duplicate username or email is rejected. Non-admins cannot reach user management (refused and audited).
- Link issue, re-issue and use are audited.
**Related Epic:** EPIC-002

### FR-002: User signs in and signs out — MUST
**Acceptance Criteria:**
- Valid credentials → chat screen. Invalid credentials → a generic message.
- 5 consecutive failures → 15-minute lock (audited).
- Sign-out ends the session immediately.
**Related Epic:** EPIC-002

### FR-003: Admin deactivates users and revokes sessions — MUST
**Acceptance Criteria:**
- Deactivation ends sessions within 5 s. A deactivated user cannot sign in.
- Status and last sign-in are visible to the admin.
- All admin actions are audited.
- Deactivating a user does **not** delete their spaces automatically. The admin is told to transfer or confirm deletion (FR-053).
**Related Epic:** EPIC-002

### FR-004: User resets a forgotten password by email — WON'T (V1)
**Description:** Superseded 2026-10-06. There is no email in V1; the admin re-issues links (FR-001).
**Acceptance Criteria:**
- The sign-in page shows "Forgot password? Contact your administrator."
**Related Epic:** EPIC-002

### FR-005: Three roles with distinct permissions — MUST
**Description:**
- **user** (banker): own spaces; workspaces where they are a member.
- **admin** (IT): user management and system settings. **No access to any document,
  workbook, conversation or output content.**
- **compliance:** audit metadata, plus break-glass access (FR-054).

A person may hold `user` together with one other role only if that is explicitly granted.
**Acceptance Criteria:**
- Every admin-only and compliance-only function refuses other roles.
- No admin endpoint returns content, titles or file names of user documents.
- An admin cannot remove the last admin or their own admin role.
- Role changes take effect on the next request and are audited.
**Related Epic:** EPIC-002

### FR-038: User changes their own password while signed in — SHOULD
**Acceptance Criteria:**
- Requires the current password. Policy: ≥ 12 characters and not a common breached password.
- A change ends the user's other sessions and is audited.
**Related Epic:** EPIC-002

---

### EPIC-003: Chat Experience

### FR-006: Streamed answers — MUST
**Acceptance Criteria:**
- Text starts within the NFR-001 budget and streams.
- A status indicator shows progress (e.g. "Reading pages 10–24…", "Running pivot…").
- The user can stop a response.
- Failures show a plain message and allow retry.
**Related Epic:** EPIC-003

### FR-007: Follow-up questions use conversation context — SHOULD
**Acceptance Criteria:**
- ≥ 85% of eval multi-turn follow-ups resolve the right document, section or sheet.
- Long conversations are condensed without errors.
- A new conversation clears context.
**Related Epic:** EPIC-003

### FR-008: User views and manages past conversations — SHOULD
**Acceptance Criteria:**
- The list shows title and date, and conversations can be reopened.
- A user sees only their own conversations.
- Deleting a conversation is a permanent delete (FR-053). Conversations also expire with the 30-day rule (FR-052).
**Related Epic:** EPIC-003

### FR-009: Every document-based statement is cited — MUST
**Description:** Answers drawn from documents or workbooks cite their location.
**Acceptance Criteria:**
- Citations are: document name + page (PDF/DOCX), slide (PPTX), or sheet + cell range (XLSX/CSV).
- Clicking a citation opens the cited page, slide or range in a viewer available only to users with access.
- 100% of document-based answers on the eval set include at least one correct citation.
**Related Epic:** EPIC-003

### FR-010: Starter prompts — SHOULD
**Acceptance Criteria:**
- 4–6 examples covering summary, Q&A, comparison and Excel operation, configurable by the admin.
- Clicking an example fills the input box rather than sending, so the user can attach a file first.
**Related Epic:** EPIC-003

### FR-011: User rates an answer — COULD
**Acceptance Criteria:**
- Thumbs up/down + optional comment.
- The comment is encrypted like other content (NFR-021).
- Only aggregate counts are visible to the admin.
**Related Epic:** EPIC-003

### FR-055: Choose which documents a conversation uses — MUST
**Description:** In a conversation, the user attaches new files or selects existing
accessible documents/workbooks. The assistant works only on the selected set.
**Acceptance Criteria:**
- The conversation shows the selected documents, and the user can add or remove them at any time.
- Answers never draw on documents outside the selected set (eval check: 0 occurrences).
- When no document is selected, the assistant answers only Level 1 concept and how-to questions, and suggests attaching a file.
- Documents from a deal workspace can only be selected by its members.
**Related Epic:** EPIC-003

---

### EPIC-004: Guardrails & Data Protection

### FR-012: High-risk personal identifiers are blocked in chat and masked in documents — MUST
**Description:** High-risk identifiers are:
- PAN, Aadhaar (including partial)
- bank account number with context, demat/DP ID
- card number (Luhn)
- UPI ID
- passport, voter ID

When **typed into chat**, the message is blocked with an explanation. When found **in
uploaded documents and workbooks**, they are **masked** with a typed placeholder
(e.g. `[PAN]`) before any model sees the text. Business content (client and company
names, financials, contact details) is **not** blocked or masked.
**Acceptance Criteria:**
- A chat message with a high-risk identifier is never sent to a model or stored. The user sees the detected type.
- Document and workbook text is masked at ingestion. The original file is kept encrypted for the owner's own viewing, but its unmasked text never enters model prompts, search indexes, logs or traces.
- After ingestion the uploader sees a masking report: types, counts, page/slide/sheet locations.
- A masked placeholder in an answer is never "unmasked" by the model (eval check).
**Related Epic:** EPIC-004

### FR-013: Off-topic requests are declined — SHOULD
**Acceptance Criteria:**
- ≥ 95% of Level 3 off-topic prompts are declined.
- ≥ 95% of Level 1/2 prompts are *not* wrongly declined.
- Each decline offers 2–3 alternatives.
**Related Epic:** EPIC-004

### FR-014: Hidden or injected instructions in documents are never followed — MUST
**Description:** Documents come from counterparties and may contain hidden or embedded
instructions (white text, comments, speaker notes, cell notes, metadata). The assistant
treats all document content as data.
**Acceptance Criteria:**
- On a red-team suite with ≥ 50 document-embedded injections across all formats, ≥ 95% have no effect on behaviour. The rest are detected and flagged.
- Detected injection content is flagged in the ingestion report and excluded from model input.
- Chat-based jailbreak attempts are refused (≥ 95% of ≥ 50 prompts). The system prompt is never disclosed.
**Related Epic:** EPIC-004

### FR-015: Responses are screened before display — MUST
**Acceptance Criteria:**
- Responses never contain unmasked high-risk identifiers (output scan).
- Responses never contain external URLs, images or links. Only internal citation links are rendered. This blocks exfiltration through rendered content.
- A response that fails screening is withheld or replaced, and audited (metadata only).
**Related Epic:** EPIC-004

### FR-016: Tools are read-only and typed; no code execution — MUST
**Description:** The assistant acts only through allow-listed, typed tools. They can
read accessible documents, run catalogue spreadsheet operations, run calculations and
create **new** output files.
**Acceptance Criteria:**
- Tools never modify or delete an original upload.
- Tools never execute code, macros or workbook formulas from user files. They never access the network.
- Tool arguments are validated, and tools are scoped to the conversation's selected documents.
- Unknown tools or invalid arguments are rejected and audited.
**Related Epic:** EPIC-004

### FR-017: Admin sees blocked and flagged counts — COULD
**Acceptance Criteria:**
- Counts by category (identifier blocks, injection flags, declines) per week.
- No content, document names or detected values are shown.
**Related Epic:** EPIC-004

### FR-036: Every figure in an answer is grounded — MUST
**Description:** Every number in an answer must appear in, or be computed by the
calculation engine from, the selected documents and sheets used in that turn.
**Acceptance Criteria:**
- Ungrounded figures are never shown as stated: the answer is regenerated, or the figure is replaced with "couldn't verify".
- 0 ungrounded figures reach users on the eval set.
- Unit and format variants match (₹ crore vs million; 1.2bn vs 1,200m).
- Grounding failures are audited (metadata).
**Related Epic:** EPIC-004

### FR-037: Per-user rate limits and upload quotas — SHOULD
**Acceptance Criteria:**
- Defaults: 10 messages/min, 300/day; 50 uploads/day; 2 GB of active storage per user. All admin-configurable.
- Clear messages when a limit is hit; events audited.
**Related Epic:** EPIC-004

### FR-030: Says when the documents don't contain the answer — MUST
**Acceptance Criteria:**
- ≥ 90% of eval "not in document" cases get an explicit "I couldn't find that in the selected documents".
- No statement presented as from a document lacks a citation.
- The answer suggests attaching other documents.
**Related Epic:** EPIC-004

---

### EPIC-008: Workspaces, Isolation & Encryption

### FR-039: Every user has a private space — MUST
**Acceptance Criteria:**
- Uploads go to the user's private space by default.
- Private-space content (documents, workbooks, conversations, outputs) is accessible only to its owner, never to admins (FR-005).
- Compliance break-glass (FR-054) is the only exception.
**Related Epic:** EPIC-008

### FR-040: Deal workspaces with named members — SHOULD
**Description:** A user creates a deal workspace (code name), adds or removes members,
and moves or uploads documents into it. Members can use its documents; each member's
conversations stay private to them.
**Acceptance Criteria:**
- Only the workspace owner (and co-owners) can add or remove members. Changes are audited.
- A removed member loses access immediately (next request), including to outputs they generated from workspace documents.
- Workspace names are visible only to members.
- Moving a document from a private space to a workspace requires an explicit confirmation.
- A workspace can be closed by its owner, which triggers permanent deletion (FR-053).
**Related Epic:** EPIC-008

### FR-041: Strict isolation and no content access for admins — MUST
**Description:** Information barrier guarantee.
**Acceptance Criteria:**
- Search, Q&A, summaries, exports, citations, previews and downloads only ever return content from spaces the requesting user can access.
- Guessing an ID returns "not found".
- Admin and system screens never show document names, conversation titles or content.
- Model-server caching cannot be shared between users (no cross-user cache reuse).
- An automated isolation suite passes with 0 violations (NFR-020).
**Related Epic:** EPIC-008

---

### EPIC-009: Document Processing

### FR-042: Upload documents — MUST
**Description:** Upload PDF, DOCX, PPTX, XLSX and CSV into a space.
**Acceptance Criteria:**
- Limits:
  - PDF/DOCX ≤ 300 pages and ≤ 50 MB
  - PPTX ≤ 150 slides
  - XLSX/CSV ≤ 20 MB, ≤ 50 sheets, ≤ 500k rows per sheet
- Other types are rejected with a clear message.
- Macro-enabled files (`.xlsm`, `.docm`, `.pptm`) and encrypted or password-protected files are rejected with an explanation.
- Duplicate uploads to the same space are detected and offered as "use existing".
- Each upload shows progress and a final status: ready, ready with warnings, or failed (with reason).
**Related Epic:** EPIC-009

### FR-043: Ingestion report — SHOULD
**Description:** After processing, the uploader sees what happened to the document.
**Acceptance Criteria:**
- The report shows: page/slide/sheet count; pages with no extractable text; OCR used; identifiers masked (types, counts, locations); injection-flagged passages (locations); tables detected.
- The report is visible only to users with access to the document.
**Related Epic:** EPIC-009

### FR-044: OCR for scanned pages — SHOULD [ASSUMED]
**Acceptance Criteria:**
- Pages with no text layer are OCR'd automatically (English).
- OCR'd text is marked so citations note "OCR".
- At most 300 OCR pages per document. Larger scans are rejected with an explanation.
**Related Epic:** EPIC-009

### FR-045: Ask questions about selected documents — MUST
**Acceptance Criteria:**
- Answers use only the selected documents (FR-055), with citations (FR-009).
- Questions spanning several documents cite each source.
- Tables in documents can be queried ("What is FY25 revenue in the table on page 32?") with exact figures (FR-036).
- ≥ 85% of eval Q&A cases pass.
**Related Epic:** EPIC-009

### FR-046: Summarise a document — MUST
**Description:** The user requests a summary using a template: **executive summary**,
**key terms** (parties, consideration, structure, conditions, dates), **financial
highlights**, **risks & red flags**, or **custom focus** (the user's own instruction).
Long documents are processed in the background.
**Acceptance Criteria:**
- A summary of a document up to 300 pages completes. Progress is visible and the user can keep chatting meanwhile.
- When it finishes, the summary appears in the conversation with an in-app notification.
- Every point in the summary carries a citation. Every figure is grounded.
- The summary can be downloaded as DOCX (FR-024).
- ≥ 85% of eval summaries score ≥ 4/5 on a rubric (coverage, accuracy, citations), and 0 have ungrounded figures.
**Related Epic:** EPIC-009

### FR-047: Compare two documents — SHOULD
**Description:** For example term-sheet v3 vs v4, or two years' reports.
**Acceptance Criteria:**
- The output lists added, removed and changed provisions or figures with citations to both documents.
- A "changed figures" table is produced from document data.
- Documents must both be accessible and selected.
**Related Epic:** EPIC-009

### FR-048: Extract tables from documents to Excel — SHOULD
**Acceptance Criteria:**
- The user picks a table (by citation or description). The system produces an XLSX with the table, source document, page and extraction timestamp.
- Numeric cells are numbers, not text. Merged headers are flattened sensibly.
- On the eval set, ≥ 95% of extracted cells match the source.
**Related Epic:** EPIC-009

---

### EPIC-010: Excel Processing

### FR-049: Describe a workbook and answer questions with exact figures — MUST
**Acceptance Criteria:**
- On upload, a profile is built: sheets, header row, columns with types, row counts, blank/duplicate counts.
- Questions ("total FY25 revenue for India") are answered with exact figures and a sheet!range citation.
- Formulas in uploaded workbooks are **never executed**. Their cached values are used, and cells without cached values are reported as such.
**Related Epic:** EPIC-010

### FR-050: Transform workbooks with typed operations — MUST
**Description:** From a plain-English instruction, the assistant chooses operations
from a fixed catalogue and their parameters. The catalogue includes: select/rename
columns, filter rows, sort, remove duplicates, fill blanks, change type, split/merge
columns, group & aggregate, pivot/unpivot, join/append sheets, add calculated column
(from a safe expression set), and top-N.
**Acceptance Criteria:**
- Before applying, the user sees the operation plan in plain language plus a preview of the first 20 rows.
- Applying it produces a **new** workbook; the original is unchanged.
- The output includes an "Operations" sheet logging each step and its parameters.
- Requests outside the catalogue are declined with the closest supported alternative.
- On the eval set, ≥ 90% of instructions map to the correct operations, and 100% of applied results match reference outputs.
**Related Epic:** EPIC-010

### FR-023: Deterministic financial calculations — MUST
**Description:** Growth %, CAGR, margins, ratios, multiples (e.g. EV/EBITDA from given
inputs), sums and averages. They are computed by the calculation engine, never
estimated by the model.
**Acceptance Criteria:**
- 100% of computed figures match a reference calculation to 2 decimal places.
- The answer states the formula and inputs (with citations).
- Invalid inputs (zero denominators, mismatched periods) produce clear messages.
**Related Epic:** EPIC-010

### FR-051: Chain operations on the previous result — SHOULD
**Acceptance Criteria:**
- Follow-up instructions ("now pivot that by quarter") apply to the latest output workbook.
- Each step creates a new version and the user can download any version.
- The Operations log accumulates all steps.
**Related Epic:** EPIC-010

---

### EPIC-007: Data Lifecycle, Audit & Compliance

### FR-024: Download outputs as watermarked, expiring files — MUST
**Description:** Users download output workbooks (XLSX/CSV), extracted tables and
summaries (DOCX).
**Acceptance Criteria:**
- Every file carries a confidentiality footer or sheet note with user, timestamp and workspace code name.
- Download links expire after 24 hours and work only for users with access.
- Downloads are audited (metadata: file type, size, hash, user, time).
**Related Epic:** EPIC-007

### FR-052: Automatic deletion after 30 days — MUST
**Acceptance Criteria:**
- The 30-day clock covers documents, workbooks, extracted text, indexes, outputs and conversations, from upload or creation. The period is configurable by admin within 7–90 days.
- Users see the expiry date and get an in-app warning 7 days before.
- A workspace owner can extend once by 30 days, which is audited.
- Expired data is permanently deleted within 24 hours of expiry (NFR-022).
**Related Epic:** EPIC-007

### FR-053: Permanent deletion on demand — MUST
**Acceptance Criteria:**
- The user deletes a document, conversation or output immediately. A workspace owner can delete the whole workspace.
- Deletion removes originals, extracted text, masked text, chunks, embeddings, previews, outputs and cached results.
- After deletion, the content is unusable within 15 minutes and unrecoverable from backups within 30 days (NFR-022).
- Deletion is audited as metadata only.
**Related Epic:** EPIC-007

### FR-031: Metadata-only, tamper-evident audit trail — MUST
**Description:** Every request, ingestion, operation, download, membership change,
deletion, sign-in and admin or compliance action is recorded **without content**:
- actor, time, action, object IDs, sizes, hashes, counts
- model and version, guard verdict categories, latency
**Acceptance Criteria:**
- 100% of the listed events are recorded. Records cannot be altered through the application.
- Records contain no document text, prompts, answers, file names or titles (canary test: 0 occurrences).
- Default retention is 5 years, configurable.
**Related Epic:** EPIC-007

### FR-032: Compliance searches and exports audit records — SHOULD
**Acceptance Criteria:**
- Compliance can filter by user, workspace ID, date range and event type, and export to CSV (≤ 10,000 rows).
- Exports of the audit trail are themselves audited.
- Admins (IT) cannot use this function.
**Related Epic:** EPIC-007

### FR-033: Usage overview — COULD
**Acceptance Criteria:**
- Counts only: active users, documents processed, summaries, operations, blocks and flags, over 7 and 30 days.
- No names or content.
**Related Epic:** EPIC-007

### FR-054: Compliance break-glass access — SHOULD
**Description:** In an investigation, Compliance can request access to a specific
user's or workspace's content.
**Acceptance Criteria:**
- The request needs a written reason and a case reference. Access is limited to the named objects, read-only, for up to 24 hours.
- Every break-glass grant and every item viewed is audited with high priority.
- Optional (setting): a second compliance approver.
- The affected user is notified in-app after access ends, unless Compliance records a documented exception.
**Related Epic:** EPIC-007

---

### Retired requirements (WON'T, retired 2026-10-07)

Full v1.1 text is in `archive/prd-v1.1-market-data.md`.

| ID | Was | Reason |
|---|---|---|
| FR-018 | Stock quote lookup | Market data dropped (pivot) |
| FR-019 | Price history | Market data dropped |
| FR-020 | Index performance | Market data dropped |
| FR-021 | Mutual fund NAV | Market data dropped |
| FR-022 | Instrument comparison | Market data dropped (document comparison is FR-047) |
| FR-025 | Market-data freshness | Market data dropped |
| FR-026 | Data-source kill switch | Market data dropped |
| FR-027 | Company results via filings | Public filings dropped |
| FR-028 | Admin public document library | Replaced by user spaces (FR-039/040, FR-042) |
| FR-029 | Library answers with citations | Replaced by FR-045 |
| FR-034 | Decline UPSI questions | Inverted: UPSI is now the content; **contained** by FR-039–041 and the Level 3 tipping/trading rule |
| FR-035 | Refuse price predictions | No market data. Inventing forecasts is prevented by FR-036; document projections are Level 2 |

---

## Non-Functional Requirements

> Measured at ~10 users and up to 10 concurrent chats on one A10 (24 GB). † marks
> values that the model-selection spike (EPIC-001) must confirm.

### NFR-001: Time to first token — MUST (Performance)
**Threshold:** p95 ≤ 3 s for concept questions; ≤ 8 s for document Q&A and sheet questions; with 10 concurrent chats and up to 2 background summary jobs running.†
**Measurement:** load test (10 virtual users + 2 summary jobs), server-side timing.

### NFR-002: Streaming speed — SHOULD (Performance)
**Threshold:** ≥ 15 tokens/s per stream at 10 concurrent streams.†
**Measurement:** same load test.

### NFR-003: Guardrail overhead — MUST (Performance)
**Threshold:** chat input checks ≤ 500 ms p95; output checks ≤ 300 ms p95.
**Measurement:** per-stage trace timing.

### NFR-004: Concurrent capacity — MUST (Scalability)
**Threshold:** 10 concurrent chats + 2 concurrent summary jobs for 30 minutes, with 0 errors and 0 out-of-memory events. Further summary jobs queue fairly (first in, first out per user).
**Measurement:** 30-minute soak test.

### NFR-005: Identifier detection — MUST (Security)
**Threshold:**
- ≥ 99% recall for high-risk identifiers, both in chat (block) and in documents/workbooks (mask), on ≥ 300 samples across formats and file types.
- 0 unmasked identifiers in model inputs, indexes, logs or traces during the test.
- Chat false-positive rate ≤ 5% on ≥ 200 benign banking messages.
**Measurement:** automated identifier suite every release; trace and index inspection with planted values.

### NFR-006: Injection and jailbreak resistance — MUST (Security)
**Threshold:**
- ≥ 95% of ≥ 100 red-team cases (≥ 50 document-embedded across PDF/DOCX/PPTX/XLSX, ≥ 50 chat) are neutralised.
- 0 external URLs, images or network requests produced.
- 0 non-catalogue tool executions.
**Measurement:** red-team suite every release and on any model, prompt or guard change.

### NFR-007: Authentication and session security — MUST (Security)
**Threshold:**
- OWASP ASVS v4 Level 2 for authentication and session management.
- Sessions expire after 60 min idle and 12 h absolute; revocation takes effect in ≤ 5 s.
- Credentials are not accessible to scripts.
**Measurement:** ASVS checklist review + targeted tests.

### NFR-008: Zero internet egress — MUST (Security)
**Threshold:** the running system makes **0 outbound connections to the internet**. Model and parser weights are loaded offline. Updates go through a controlled maintenance procedure only.
**Measurement:** egress firewall default-deny review + network capture during a full suite run.

### NFR-009: Encryption in transit and secrets — MUST (Security)
**Threshold:** TLS 1.2+ for all user traffic. No secrets in source or images. Database and model servers are reachable only on internal networks.
**Measurement:** TLS scan, secret scanning, exposure review.

### NFR-010: Output quality — MUST (Usability / Quality)
**Threshold:**
- ≥ 85% pass on the eval set (≥ 80 tasks across Q&A, summaries, comparison, table extraction and Excel operations).
- 100% of figures grounded.
- 100% of applied operations match their reference outputs.
**Measurement:** eval suite on every model or prompt change and before each release.

### NFR-011: Non-technical usability — SHOULD (Usability)
**Threshold:**
- In a hallway test with ≥ 3 bankers, each completes four tasks unaided: upload & summarise, ask with citation, pivot a workbook, share to a workspace.
- Interaction time ≤ 3 minutes per task, excluding background processing.
- WCAG 2.1 AA on core screens.
**Measurement:** moderated test + automated accessibility scan.

### NFR-012: Availability — SHOULD (Reliability)
**Threshold:** ≥ 99% during business hours (Mon–Fri 08:00–21:00 IST).
**Measurement:** health probe each minute; monthly report.

### NFR-013: Fail-safe degradation — MUST (Reliability)
**Threshold:**
- If identifier detection, the guard model or isolation checks are unavailable, no content reaches the model and no results are shown (fail closed).
- Background jobs resume or fail cleanly after a restart (no half-processed document is marked ready).
- Components auto-restart within ≤ 2 minutes.
**Measurement:** fault-injection tests.

### NFR-014: Backup and recovery — SHOULD (Reliability)
**Threshold:** daily encrypted backups; RPO ≤ 24 h; RTO ≤ 4 h; restore tested before launch. Must stay compatible with NFR-022 deletion guarantees. *Interim (UAT, synthetic data only): local encrypted backups kept 2 days on the data disk; the off-host target is required before real deal data (decision 2026-10-07).*
**Measurement:** restore drill.

### NFR-015: Swappable model — MUST (Maintainability)
**Threshold:** switching the served chat or guard model is a configuration change only. Document and Excel features need no code change.
**Measurement:** demonstration with two shortlisted models.

### NFR-016: Observability without content — SHOULD (Maintainability)
**Threshold:** 100% of requests and jobs carry a correlation ID; per-stage latency is visible. Logs and traces contain no content (see NFR-023).
**Measurement:** trace review + canary scan.

### NFR-017: Audit retention and integrity — MUST (Compliance)
**Threshold:** append-only and tamper-evident; 5-year default; metadata only.
**Measurement:** integrity verification + modification attempt + canary scan.

### NFR-018: Market-data usage discipline — WON'T (retired 2026-10-07)
**Threshold:** n/a. Market data was dropped.
**Measurement:** n/a.

### NFR-019: One-command deployment — SHOULD (Operability)
**Threshold:** the full stack starts with one documented command; the runbook covers start/stop, model switch, backup/restore, key rotation and the offline update procedure.
**Measurement:** fresh-host rehearsal.

### NFR-020: Isolation verified — MUST (Security)
**Threshold:** an automated isolation suite of ≥ 100 cases returns **0 violations**. Cases cover:
- cross-user and cross-workspace access via every API, search, citation, preview, export and job result
- ID guessing
- removed-member access
- admin and compliance boundaries
- **per-user model-cache isolation** (no cross-user prefix-cache reuse)

**Measurement:** suite runs on every release.

### NFR-021: Encryption at rest — MUST (Security)
**Threshold:**
- Original files, extracted and masked text, chunks, conversation messages, outputs and feedback comments are encrypted with AES-256 using **per-space data keys**.
- Database and file volumes are also disk-encrypted (Azure managed-disk default server-side encryption is acceptable; decision 2026-10-07).
- Keys are never stored in plaintext alongside the data.

**Measurement:** storage inspection (no plaintext content on disk or in DB dumps, except the search-index fields documented in the architecture); key-rotation test.

### NFR-022: Deletion guarantees — MUST (Compliance)
**Threshold:**
- On-demand deletion makes content unusable in ≤ 15 minutes (keys destroyed, rows and files removed).
- Expiry deletion happens ≤ 24 h after the expiry date.
- Deleted content is unrecoverable from backups within ≤ 30 days.
**Measurement:** deletion test with planted canary content: search, DB dump, file system and restored backup show 0 recoverable content after the windows.

### NFR-023: No content in logs, traces or audit — MUST (Security)
**Threshold:** 0 occurrences of canary strings from documents, prompts or answers in logs, traces, metrics, audit records or error reports.
**Measurement:** canary test every release.

### NFR-024: Processing performance — SHOULD (Performance)
**Threshold:**
- 50-page text PDF ready for Q&A in ≤ 2 min.
- OCR ≤ 15 s per page.
- 100-page summary ≤ 10 min p95.†
- Excel operation on 100k rows ≤ 10 s.
- Workbook profile ≤ 30 s for 20 MB.

**Measurement:** benchmark set on the A10 host.

### NFR-025: Spreadsheet and file safety — MUST (Security)
**Threshold:**
- No user-supplied code, macros, formulas or external links are ever executed or followed.
- Every operation is bounded: ≤ 30 s CPU, ≤ 2 GB memory, ≤ 5M output cells.
- Parsers run with resource limits and no network.
- Malformed or zip-bomb files are rejected without affecting other users.

**Measurement:** a malicious-file test set (macro files, zip bombs, external links, huge sheets) yields 0 executions and 0 service disruption.

---

## Epics and User Stories (Outline)

> Outline only; story files are compiled by `bmad-epics-and-stories`. No points. Each
> story fits one agent session (~2–8 h). Retired story numbers (STORY-025–036, 042, 043)
> are not reused.

### EPIC-001: Platform Foundation & Model Selection
**Business Value:** proves the A10 can serve chat and background summaries, and sets up the eval harness (BG-3, BG-5).
**Related Requirements:** NFR-001, NFR-002, NFR-004, NFR-010, NFR-015, NFR-019, NFR-024
- **STORY-001:** As the builder, I want the chat and guard models served on the A10, so that the app has self-hosted inference.
  - Given the host, when the stack starts, then health checks confirm both models are serving.
- **STORY-002:** As the builder, I want an eval set of ≥ 80 document and Excel tasks with reference outputs, so that quality is measured.
- **STORY-003:** As the builder, I want shortlisted models compared on Q&A, summaries, Excel-operation mapping, latency, background summary throughput and per-user cache-salt support, so that the model choice and † thresholds are evidence-based.
- **STORY-004:** As the builder, I want the app skeleton deployable with one command and no internet egress, so that later stories build on a safe base.

### EPIC-002: Authentication, Roles & Administration
**Related Requirements:** FR-001–FR-005, FR-038, NFR-007
- **STORY-005:** As an admin, I want to create a user with a role and get a one-time link to share, so that they can set a password without email. (FR-001)
- **STORY-006:** As a user, I want to sign in and out securely. (FR-002)
- **STORY-007:** As an admin, I want to deactivate users and revoke sessions, with a prompt about their spaces. (FR-003)
- **STORY-008:** As a user, I want to change my password. (FR-038)
- **STORY-009:** As the builder, I want three roles enforced, with admins unable to see content. (FR-005)

### EPIC-003: Chat Experience
**Related Requirements:** FR-006–FR-011, FR-055, NFR-001, NFR-002, NFR-011
- **STORY-010:** As a user, I want streamed answers with progress, so that I'm not left waiting. (FR-006)
- **STORY-011:** As a user, I want follow-ups to keep context. (FR-007)
- **STORY-012:** As a user, I want to see and resume my conversations. (FR-008)
- **STORY-013:** As a user, I want clickable citations that open the page, slide or range, so that I can verify answers. (FR-009)
- **STORY-014:** As a first-time user, I want example prompts. (FR-010)
- **STORY-015:** As a user, I want to rate answers. (FR-011)
- **STORY-047:** As a user, I want to attach or select the documents a conversation uses, so that answers come only from them. (FR-055)
  - Given two selected documents, when I ask a question, then the answer cites only those two.

### EPIC-004: Guardrails & Data Protection
**Related Requirements:** FR-012–FR-017, FR-030, FR-036, FR-037, NFR-003, NFR-005, NFR-006, NFR-013, NFR-025
- **STORY-016:** As a user, I want chat messages containing high-risk identifiers blocked with an explanation. (FR-012)
- **STORY-017:** As the builder, I want an identifier suite of ≥ 300 samples across chat and file formats, plus ≥ 200 benign messages. (NFR-005)
- **STORY-018:** As the builder, I want off-topic and out-of-barrier requests declined with alternatives. (FR-013)
- **STORY-019:** As the builder, I want hidden or injected document instructions detected and neutralised. (FR-014)
- **STORY-020:** As the builder, I want a red-team suite of ≥ 100 cases (≥ 50 document-embedded) run on every release. (NFR-006)
- **STORY-021:** As compliance, I want responses screened for unmasked identifiers and external links or images. (FR-015)
- **STORY-022:** As the builder, I want only catalogue tools, typed and scoped to selected documents, with no code, macros or network. (FR-016, NFR-025)
- **STORY-023:** As the builder, I want safety checks to fail closed. (NFR-013)
- **STORY-024:** As an admin, I want counts of blocks and flags, without content. (FR-017)
- **STORY-044:** As a user, I want every figure verified against documents, sheets or calculations. (FR-036)
- **STORY-045:** As the builder, I want rate limits and upload quotas. (FR-037)
- **STORY-046:** As the builder, I want the Response Scope Policy encoded as eval cases. (FR-013, FR-030)

### EPIC-008: Workspaces, Isolation & Encryption
**Business Value:** the confidentiality guarantee (BG-2, UG-3).
**Related Requirements:** FR-039–FR-041, NFR-020, NFR-021
- **STORY-048:** As a user, I want a private space that only I can see, so that my deal material is mine alone. (FR-039)
- **STORY-049:** As a deal lead, I want to create a deal workspace and manage its members, so that my team can work on shared documents. (FR-040)
  - Given I remove a member, when they next request a workspace document, then they get "not found".
- **STORY-050:** As compliance, I want isolation enforced on every data path and verified by a ≥ 100-case suite. (FR-041, NFR-020)
- **STORY-051:** As the builder, I want per-space encryption keys and encrypted storage of all content, so that data at rest is protected and deletable by key destruction. (NFR-021)
- **STORY-052:** As the builder, I want per-user model-cache isolation, so that no user can infer another's prompts. (FR-041)

### EPIC-009: Document Processing
**Related Requirements:** FR-042–FR-048, FR-012 (masking), FR-014, NFR-024, NFR-025
- **STORY-053:** As a user, I want to upload supported files with clear validation (type, size, macros, encryption). (FR-042)
- **STORY-054:** As the builder, I want a parsing pipeline that extracts text, structure and tables with page, slide and sheet references, and reports status. (FR-042, FR-043)
- **STORY-055:** As compliance, I want high-risk identifiers masked at ingestion, with a masking report. (FR-012, FR-043)
- **STORY-056:** As a user, I want scanned pages OCR'd automatically. (FR-044)
- **STORY-057:** As the builder, I want documents indexed for search, scoped to their space. (FR-045, FR-041)
- **STORY-058:** As a user, I want to ask questions about selected documents with citations. (FR-045)
- **STORY-059:** As a user, I want template-based summaries of long documents in the background, with progress and notification. (FR-046)
  - Given a 120-page CIM, when I request "key terms", then I can keep chatting and receive a cited summary when it finishes.
- **STORY-060:** As a user, I want to compare two documents. (FR-047)
- **STORY-061:** As a user, I want to extract a document table into Excel. (FR-048)

### EPIC-010: Excel Processing
**Related Requirements:** FR-049–FR-051, FR-023, NFR-024, NFR-025
- **STORY-062:** As a user, I want an automatic profile of my workbook. (FR-049)
- **STORY-063:** As a user, I want questions about my workbook answered with exact figures and range citations. (FR-049)
- **STORY-064:** As the builder, I want a typed operation catalogue and engine, with bounded execution. (FR-050, NFR-025)
- **STORY-065:** As a user, I want an operation plan + preview, then a new workbook with an Operations log. (FR-050)
- **STORY-066:** As a user, I want growth, CAGR, margins and multiples calculated exactly, with the formula shown. (FR-023)
- **STORY-067:** As a user, I want to chain operations on the previous result, with versions. (FR-051)

### EPIC-007: Data Lifecycle, Audit & Compliance
**Related Requirements:** FR-024, FR-031–FR-033, FR-052–FR-054, NFR-012, NFR-014, NFR-016, NFR-017, NFR-019, NFR-022, NFR-023
- **STORY-037:** As compliance, I want a metadata-only, tamper-evident audit trail. (FR-031, NFR-017, NFR-023)
- **STORY-038:** As compliance, I want to search and export audit records. (FR-032)
- **STORY-039:** As the builder, I want content-free traces, metrics and health checks. (NFR-016, NFR-012)
- **STORY-040:** As an admin, I want a counts-only usage overview. (FR-033)
- **STORY-041:** As the builder, I want encrypted backups, a restore drill and a runbook, compatible with deletion guarantees. (NFR-014, NFR-019)
- **STORY-068:** As a user, I want my data deleted automatically after 30 days, with a warning beforehand. (FR-052)
- **STORY-069:** As a user, I want to permanently delete a document, conversation or workspace now. (FR-053, NFR-022)
- **STORY-070:** As compliance, I want break-glass access with a reason, a time limit and a full audit. (FR-054)
- **STORY-071:** As a user, I want watermarked, expiring downloads of outputs. (FR-024)

### Retired epics
- **EPIC-005 (Market & Mutual Fund Data Tools)** and **EPIC-006 (IIFL Company Knowledge)**: retired 2026-10-07; see the archive.

---

## Prioritization Summary (MoSCoW)

| Priority | Requirements | Rationale |
|----------|--------------|-----------|
| Must | FR-001, 002, 003, 005, 006, 009, 012, 014, 015, 016, 023, 024, 030, 031, 036, 039, 041, 042, 045, 046, 049, 050, 052, 053, 055 (25 of 42 active FRs, 60%); NFR-001, 003, 004, 005, 006, 007, 008, 009, 010, 013, 015, 017, 020, 021, 022, 023, 025 | Confidentiality (isolation, encryption, deletion, identifier handling, injection), trust (citations, grounding, "not found") and the core jobs (upload, Q&A, summaries, workbook Q&A, typed operations, downloads) |
| Should | FR-007, 008, 010, 013, 032, 037, 038, 040, 043, 044, 047, 048, 051, 054; NFR-002, 011, 012, 014, 016, 019, 024 | Valuable, but a workaround exists (private spaces before deal workspaces; re-ask instead of history; manual table copy) |
| Could | FR-011, 017, 033 | Feedback and insight |
| Won't | FR-004, 018–022, 025–029, 034, 035; NFR-018 | Retired or no email (see tables above) |

Deal workspaces (FR-040) are Should: private spaces deliver value on day one, and
workspaces add team sharing. Most bankers work in teams, so plan it for the same
release if capacity allows. Must share is 25 of 42 active FRs (60%), at the limit; no
further Musts without demoting one.

---

## Success Metrics

| Metric | Baseline | Target | Measurement Method | Frequency |
|--------|----------|--------|--------------------|-----------|
| Weekly active bankers | 0 | ≥ 8 of ~10 | Distinct users per week (audit metadata) | Weekly |
| Time to summarise a 100-page document | Manual: measure in week 0 | ≤ 10 min machine time; ≥ 70% less banker time | Benchmark + timed task study (3 users) | Launch + 1 month |
| Confidentiality incidents (cross-user/deal access, egress, content in logs) | n/a | **0** | Isolation, egress and canary suites + incident reports | Every release + monthly |
| Eval pass rate | Set by spike | ≥ 85% | Eval suite | Every model/prompt change |
| Operation correctness | n/a | 100% of applied operations match reference | Excel suite | Every release |
| Deletion compliance | n/a | 100% of expiries within 24 h | Deletion job report | Weekly |

---

## Assumptions and Dependencies

### Assumptions
1. English documents. Other languages may work but are not tested.
2. Desktop browsers on the internal network or VPN.
3. ~10 users, up to 10 concurrent chats, ≤ 2 concurrent background summaries.
4. **[ASSUMED]** OCR is needed for some scanned or signed PDFs (FR-044 Should).
5. **[ASSUMED]** Typical documents are ≤ 300 pages, and minutes-long background summaries are acceptable.
6. The A10 host is dedicated, with enough CPU and RAM for document parsing and OCR (to confirm).
7. No email. Links are shared manually through an approved channel.
8. Compliance accepts metadata-only audit plus break-glass content access (to confirm).

### Dependencies
| Dependency | Type | Owner | Status | Risk | Mitigation |
|------------|------|-------|--------|------|------------|
| NVIDIA A10 host (driver R570+, Docker + NVIDIA toolkit); CPU/RAM/disk sizing | Infrastructure | Builder | GPU available; CPU/RAM unknown | Medium | Confirm in STORY-001; OCR is the main CPU load |
| Open-weight chat + guard models; parser/OCR/embedding models (offline bundles) | Open source | Builder | Shortlisted | Medium | Spike STORY-003 |
| Compliance agreement on audit, break-glass and retention | Organisational | Product owner | Not started | High | Share PRD early (addendum Q7) |
| Off-host encrypted backup target | Infrastructure | IT | Unknown | Medium | Needed for NFR-014/022 |
| Internal CA certificate | Infrastructure | IT | Unknown | Low | Caddy internal CA for the pilot |

---

## Constraints

- **Technical:**
  - FastAPI + React/TypeScript; vLLM, open-weight models only.
  - One A10 24 GB: a ~14B INT4 chat model + a small guard model; ~8k chat context; Qwen3-14B 32k native for summary map steps.
  - **No internet egress.** Typed operations only (no code execution).
  - PostgreSQL 18 + pgvector.
- **Business:** internal banker tool for confidential deal work. Information barriers apply. One builder.
- **Timeline:** no fixed date. Count-based delivery.

---

## Out of Scope

| Excluded | Reason | Revisit? |
|----------|--------|----------|
| Market data, public filings, news, any internet lookup | Pivot; zero-egress design | Only via an approved internal feed |
| Arbitrary or sandboxed code execution on user data | Risk; typed operations first | Yes, V1.x (sandboxed Python) |
| Macro-enabled or password-protected files | Execution and decryption risk | Maybe (password prompt) |
| Writing back into the original file / editing documents in place | Originals immutable; outputs are new files | Yes |
| Generating PowerPoint decks or charts | Scope | Yes, V1.x |
| Email, SSO, MFA | V1 simplicity | Yes (MFA first) |
| Languages other than English (tested) | Pilot | Yes |
| Client-facing use; trading | Internal tool | No |
| Fine-tuning on user documents | Confidentiality and scope | No for confidential data |
| Shared firm-wide document library | Information barriers | Only with ACLs and Compliance approval |
| Scale beyond ~10 concurrent users | One A10 | Yes, more GPU |

---

## Risks and Mitigations

| Risk | Impact | Probability | Mitigation | Owner |
|------|--------|-------------|------------|-------|
| Cross-user or cross-deal leakage (bug in a data path or a shared cache) | Critical | Medium | Space-scoped repositories, isolation suite with 0 tolerance (NFR-020), per-user cache salt, 404 on not-accessible | Builder |
| Hidden instructions in counterparty documents steer the model | High | Medium | Ingestion injection scan, content-as-data, no URLs or images in output, no network, typed tools only (FR-014/015/016) | Builder |
| Model mis-summarises or invents a figure in deal material | High | Medium | Citations on every point, figure grounding, eval rubric gate, "not found" behaviour | Builder |
| Host administrator could access data on a single host | High | Low | App-level encryption + disk encryption, key file outside DB, restricted host access, host login auditing. **Residual risk documented** | Product owner + IT |
| Long summaries saturate the GPU and slow chat | Medium | Medium | ≤ 2 concurrent jobs, fair queueing, measured in soak (NFR-004) | Builder |
| CPU-heavy parsing/OCR overwhelms a small host | Medium | Medium | Confirm host spec; OCR page caps; job queue | Builder |
| Backups retain deleted deal data | High | Medium | Crypto-shredding with keys outside DB backups; 30-day backup window (NFR-022) | Builder |
| Malicious files (macros, zip bombs) | Medium | Low | Reject macro and encrypted files, resource-limited parsers, malicious-file suite (NFR-025) | Builder |
| Compliance rejects metadata-only audit | Medium | Medium | Break-glass design; early review (Q7) | Product owner |

---

## Traceability Matrix

| Requirement | Business Goal | Epic | User Story | Status |
|-------------|---------------|------|------------|--------|
| FR-001 | BG-2, BG-4 | EPIC-002 | STORY-005 | draft |
| FR-002 | BG-2 | EPIC-002 | STORY-006 | draft |
| FR-003 | BG-2, BG-4 | EPIC-002 | STORY-007 | draft |
| FR-004 | — | EPIC-002 | — | won't |
| FR-005 | BG-2, BG-4 | EPIC-002 | STORY-009 | draft |
| FR-006 | BG-1 | EPIC-003 | STORY-010 | draft |
| FR-007 | BG-1 | EPIC-003 | STORY-011 | draft |
| FR-008 | BG-1 | EPIC-003 | STORY-012 | draft |
| FR-009 | BG-3 | EPIC-003 | STORY-013 | draft |
| FR-010 | BG-1 | EPIC-003 | STORY-014 | draft |
| FR-011 | BG-3 | EPIC-003 | STORY-015 | draft |
| FR-012 | BG-2 | EPIC-004 / EPIC-009 | STORY-016, STORY-017, STORY-055 | draft |
| FR-013 | BG-2, BG-4 | EPIC-004 | STORY-018, STORY-046 | draft |
| FR-014 | BG-2 | EPIC-004 | STORY-019, STORY-020 | draft |
| FR-015 | BG-2 | EPIC-004 | STORY-021 | draft |
| FR-016 | BG-2 | EPIC-004 | STORY-022 | draft |
| FR-017 | BG-4 | EPIC-004 | STORY-024 | draft |
| FR-018–FR-022 | — | EPIC-005 (retired) | — | won't |
| FR-023 | BG-3 | EPIC-010 | STORY-066 | draft |
| FR-024 | BG-1, BG-2 | EPIC-007 | STORY-071 | draft |
| FR-025–FR-029 | — | EPIC-005/006 (retired) | — | won't |
| FR-030 | BG-3 | EPIC-004 | STORY-046, STORY-058 | draft |
| FR-031 | BG-4 | EPIC-007 | STORY-037 | draft |
| FR-032 | BG-4 | EPIC-007 | STORY-038 | draft |
| FR-033 | BG-1 | EPIC-007 | STORY-040 | draft |
| FR-034, FR-035 | — | — | — | won't |
| FR-036 | BG-3 | EPIC-004 | STORY-044 | draft |
| FR-037 | BG-2 | EPIC-004 | STORY-045 | draft |
| FR-038 | BG-2 | EPIC-002 | STORY-008 | draft |
| FR-039 | BG-2 | EPIC-008 | STORY-048 | draft |
| FR-040 | BG-1, BG-2 | EPIC-008 | STORY-049 | draft |
| FR-041 | BG-2, BG-4 | EPIC-008 | STORY-050, STORY-052 | draft |
| FR-042 | BG-1 | EPIC-009 | STORY-053, STORY-054 | draft |
| FR-043 | BG-2, BG-3 | EPIC-009 | STORY-054, STORY-055 | draft |
| FR-044 | BG-1 | EPIC-009 | STORY-056 | draft |
| FR-045 | BG-1, BG-3 | EPIC-009 | STORY-057, STORY-058 | draft |
| FR-046 | BG-1, BG-3 | EPIC-009 | STORY-059 | draft |
| FR-047 | BG-1 | EPIC-009 | STORY-060 | draft |
| FR-048 | BG-1 | EPIC-009 | STORY-061 | draft |
| FR-049 | BG-1, BG-3 | EPIC-010 | STORY-062, STORY-063 | draft |
| FR-050 | BG-1, BG-3 | EPIC-010 | STORY-064, STORY-065 | draft |
| FR-051 | BG-1 | EPIC-010 | STORY-067 | draft |
| FR-052 | BG-2, BG-4 | EPIC-007 | STORY-068 | draft |
| FR-053 | BG-2, BG-4 | EPIC-007 | STORY-069 | draft |
| FR-054 | BG-4 | EPIC-007 | STORY-070 | draft |
| FR-055 | BG-2, BG-3 | EPIC-003 | STORY-047 | draft |
| NFR-001 | BG-1 | EPIC-001 / cross-cutting | STORY-003, STORY-010 | draft |
| NFR-002 | BG-1 | EPIC-001 | STORY-003 | draft |
| NFR-003 | BG-1, BG-2 | EPIC-004 | STORY-016 | draft |
| NFR-004 | BG-1 | EPIC-001 | STORY-003 | draft |
| NFR-005 | BG-2 | EPIC-004 | STORY-017 | draft |
| NFR-006 | BG-2 | EPIC-004 | STORY-020 | draft |
| NFR-007 | BG-2 | EPIC-002 | STORY-006, STORY-009 | draft |
| NFR-008 | BG-2 | cross-cutting | STORY-004 | draft |
| NFR-009 | BG-2 | cross-cutting | STORY-004 | draft |
| NFR-010 | BG-3 | EPIC-001 | STORY-002, STORY-003 | draft |
| NFR-011 | BG-1 | EPIC-003 | STORY-010, STORY-014 | draft |
| NFR-012 | BG-1 | EPIC-007 | STORY-039 | draft |
| NFR-013 | BG-2 | EPIC-004 | STORY-023 | draft |
| NFR-014 | BG-4 | EPIC-007 | STORY-041 | draft |
| NFR-015 | BG-5 | EPIC-001 | STORY-003 | draft |
| NFR-016 | BG-4, BG-5 | EPIC-007 | STORY-039 | draft |
| NFR-017 | BG-4 | EPIC-007 | STORY-037 | draft |
| NFR-018 | — | — | — | won't |
| NFR-019 | BG-5 | EPIC-001 / EPIC-007 | STORY-004, STORY-041 | draft |
| NFR-020 | BG-2 | EPIC-008 | STORY-050, STORY-052 | draft |
| NFR-021 | BG-2 | EPIC-008 | STORY-051 | draft |
| NFR-022 | BG-2, BG-4 | EPIC-007 | STORY-069, STORY-041 | draft |
| NFR-023 | BG-2 | EPIC-007 | STORY-037, STORY-039 | draft |
| NFR-024 | BG-1 | EPIC-009 / EPIC-010 | STORY-054, STORY-059, STORY-064 | draft |
| NFR-025 | BG-2 | EPIC-004 / EPIC-010 | STORY-022, STORY-064 | draft |

Goal coverage: BG-1 to BG-5 and UG-1 to UG-4 each map to at least one requirement. No orphans.

---

## Handoff

- **To Architecture (`/bmad-architecture`, Update intent):** supersede the market-data
  ADR-013 and the public-library ADR-014, and amend ADR-010/011/012/016/019/020. New
  decisions needed:
  - **space-scoped data access and isolation enforcement**
  - **envelope encryption with per-space keys and crypto-shredding**, including how backups avoid keeping keys
  - **document ingestion pipeline** (Docling, OCR, masking, injection scan, indexing)
  - **background summary jobs** (map-reduce, GPU fairness)
  - **typed spreadsheet operation engine** (Polars) and file safety
  - **per-user `cache_salt`**
  - **metadata-only audit**
  - **compliance break-glass**
  - **zero-egress deployment**
- **To Story Planning:** the epics outline above (EPIC-001 first; EPIC-008 isolation and encryption before any real deal document is uploaded).
- **Open questions:** see `addendum.md`.

---

## Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.1–1.1 | 2026-10-06/07 | John (PM) via bmad-prd | Market-data assistant (archived: `archive/prd-v1.1-market-data.md`) |
| 2.0 | 2026-10-07 | John (PM) via bmad-prd | **Re-scope** to confidential document & Excel assistant for investment bankers. New FR-039–FR-055, NFR-020–NFR-025, EPIC-008/009/010; retired FR-018–022, 025–029, 034, 035, NFR-018, EPIC-005/006; rewrote FR-005, 009, 012–016, 023, 024, 030, 031, 032 and the scope policy |
| 2.1 | 2026-10-07 | John (PM) via bmad-prd | NFR-021: Azure managed-disk default SSE accepted for the disk layer (Azure UAT host; no Key Vault) |
| 2.2 | 2026-10-07 | John (PM) via bmad-prd | NFR-014 interim note: local 2-day backups on UAT; off-host backups required before real data |
| 2.3 | 2026-10-07 | John (PM) via bmad-prd | Project renamed to invest-ai-llm (new repo); no content change |
