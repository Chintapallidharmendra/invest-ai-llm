# PRD Addendum — invest-ai-llm (Confidential Document Assistant)

**Companion to:** `prd.md` v2.0
**Version:** 2.0
**Date:** 2026-10-07

> Overflow and working notes. `prd.md` is the source of truth; decisions are in
> `decision-log.md`. Pre-pivot addendum archived at `archive/addendum-v1.0-market-data.md`.

---

## Open Questions

| # | Question | Owner | Needed By | Status |
|---|----------|-------|-----------|--------|
| Q1–Q6, Q10, Q12 | Market-data era questions | — | — | closed by pivot (see archive) |
| Q7 | Will Compliance accept: metadata-only audit, break-glass content access (FR-054), 30-day retention, and the AI usage under SEBI AI/ML guidance? | Product owner + Compliance | Before pilot launch | open |
| Q8 | Session timeouts (60 min idle / 12 h absolute): OK for bankers' long days? | Product owner | Before EPIC-002 | open |
| Q9 | Pilot users, deal teams, admin(s) and compliance reviewer(s)? | Product owner | Before pilot launch | open |
| Q11 | Approved internal channel for sharing set-password links? | Admin | Before pilot launch | open |
| Q13 | **[ASSUMED]** Is OCR needed for scanned or signed PDFs? (FR-044 Should) | Product owner | Before EPIC-009 | assumed yes |
| Q14 | **[ASSUMED]** Typical document sizes ≤ 300 pages, and summaries in minutes are acceptable? | Product owner | Before EPIC-009 | assumed yes |
| Q15 | A10 host CPU cores, RAM and disk? | Builder | Before STORY-001 | **resolved 2026-10-07:** 36 vCPU, 432 GiB RAM, A10-24Q (24 GB), driver 570/CUDA 12.8; storage actions in decision log |
| Q16 | Off-host encrypted backup target? Needed for NFR-014 and the NFR-022 backup-deletion window. | IT | Before launch | open |
| Q17 | Should Compliance break-glass need a second approver (two-person rule)? | Compliance | Before EPIC-007 | open (setting, default off) |
| Q18 | Do bankers need to move or copy documents between workspaces? (Default: move private → workspace only, with confirmation; never workspace → workspace.) | Product owner | Before EPIC-008 | open |
| Q21 | Is the host a cloud VM, UAT or production, and is Key Vault available? | Product owner + IT | Before STORY-004 | **resolved 2026-10-07:** Azure VM, UAT; Key Vault not readily available (avoid); KEK stays a root-only file |
| Q22 | At-rest disk encryption: LUKS or cloud? | IT | Before real data | **resolved 2026-10-07:** default Azure SSE on managed disks; `/mnt` temp disk never holds content |
| Q23 | Real deal documents on the UAT VM during the pilot? | Product owner + IT + Compliance | Before any real document upload | **resolved 2026-10-07:** synthetic corpus only on UAT until NSG egress deny, private-storage backups, KEK escrow and Compliance sign-off are in place; then promote or build production from the same runbook |
| Q24 | Outbound blocking, inbound ports, backup storage | IT | Before STORY-004 | **resolved 2026-10-07:** outbound blocked; inbound 8000–8050 (app on 8043); backups local on `/data/backups` for 2 days until IT provides storage. **Off-host backup storage is required before any real deal data** (tracked as Q25) |
| Q25 | IT to provide the private backup storage (private endpoint, 30-day lifecycle) | IT | **Before any real deal document** (not before development) | open |
| Q19 | KEK custody: who holds the offline escrow copy (proposed: product owner + one Compliance officer, split knowledge), and the rotation schedule (proposed: annual)? (architecture AQ-6) | Product owner + Compliance | Before launch | open |
| Q20 | Does Compliance accept the documented plaintext search-index exception (keyword index and embeddings of masked text, on a LUKS-encrypted DB, 30-day lifetime)? Alternative: vector-only search with lower recall for names and numbers. (architecture AQ-7) | Compliance | Before EPIC-009 | open |

---

## Deferred Requirements (parked, not cut)

- **D-01 (MFA):** TOTP two-factor sign-in, mandatory for admin and compliance. *Deferred:* first V2 security addition.
- **D-02 (SSO):** OIDC with the corporate identity provider.
- **D-10 (Email):** invitations, resets and notifications by email. *Deferred:* no relay.
- **D-11 (Sandboxed code execution):** the model writes Python (pandas/Polars) executed in an isolated, network-less, resource-limited sandbox for workbook tasks beyond the catalogue. *Deferred:* risk; typed operations first (owner decision 2026-10-07).
- **D-12 (Generated decks and charts):** PPTX or chart outputs from summaries and workbooks.
- **D-13 (Password-protected files):** prompt for the password at upload and decrypt in memory only.
- **D-14 (More languages):** Hindi and others for documents and UI.
- **D-15 (VLM document parsing):** Granite-Docling-258M or similar for hard layouts. Needs GPU memory or a second GPU.
- **D-16 (Write-back edits):** produce a modified copy of a DOCX (e.g. redline) rather than separate outputs.
- *Retired with the pivot:* D-03 (charts of prices), D-04 (ACE), D-05 (real-time quotes), D-06 (confidential public library → now core), D-07 (scheduled market reports), D-08 (languages → D-14), D-09 (internal market DB).

---

## Detailed Acceptance Criteria Overflow

### FR-012 (high-risk identifier patterns, for test-suite authors)
- PAN: 5 letters + 4 digits + 1 letter, any case, optional spaces.
- Aadhaar: 12 digits (4-4-4 with space or hyphen, or contiguous), including masked forms like "XXXX XXXX 1234".
- Bank account: 9–18 digits near context words (account, a/c, acct, IFSC, bank).
- Demat/DP ID: NSDL `IN` + 14 digits; CDSL 16 digits.
- Card: 13–19 digits passing Luhn.
- UPI: `handle@psp`. Passport: Indian passport pattern with context. Voter ID: EPIC pattern with context.
- **Not masked or blocked (business content):** company and client names, CIN/LLPIN, GSTIN of companies, ISINs, deal values, emails and phone numbers of business contacts, addresses of companies.
- Workbooks: scan every cell value (not formulas), plus cell comments and sheet names. Mask the cell value in the extracted data.

### FR-046 (summary templates)
| Template | Sections |
|---|---|
| Executive summary | Overview, business, financial snapshot, transaction (if any), key risks, open questions |
| Key terms | Parties, structure, consideration and mechanism, conditions precedent, key dates, termination, governing law, other notable terms |
| Financial highlights | Revenue/EBITDA/PAT history and projections (as stated), margins, segment mix, balance-sheet highlights, KPIs |
| Risks & red flags | Legal, financial, operational, regulatory, customer concentration, related-party, inconsistencies between sections |
| Custom focus | User instruction, constrained to the document |

Every bullet carries a citation, and figures go through grounding (FR-036).

### FR-050 (operation catalogue v1)
`select_columns`, `rename_columns`, `filter_rows` (comparison, in-list, contains, date range, null checks; AND/OR), `sort`, `deduplicate`, `fill_blanks` (value, forward-fill), `cast_type`, `split_column`, `merge_columns`, `group_aggregate` (sum, mean, min, max, count, count_distinct, median), `pivot`, `unpivot`, `join` (inner/left on keys), `append`, `add_calculated_column` (safe expression grammar: arithmetic, ROUND, IF, comparisons over column references; no functions beyond the list), `top_n`, `growth`/`cagr`/`margin`/`ratio` (via FR-023 engine).

### NFR-020 (isolation suite categories)
| Category | Min. cases |
|---|---|
| Direct object access by ID for every resource type (doc, chunk, page view, workbook, output, conversation, summary job, export link) across users | 30 |
| Search and retrieval scoping (query that matches another space's content) | 15 |
| Workspace membership: add/remove, removed member, non-member | 15 |
| Admin and compliance boundaries (admin endpoints never return content; compliance only via break-glass) | 15 |
| Job and result leakage (background summary results delivered to the right user only) | 10 |
| Model cache isolation (timing check that one user's prompts don't warm another's) | 5 |
| Export/download links used by another user or after expiry | 10 |

### Test suites (release gates)
| Suite | Min. size |
|---|---|
| Identifier (chat + files) | 300 positive + 200 benign |
| Red-team (document-embedded + chat) | 100 (≥ 50 document-embedded) |
| Isolation | 100 |
| Eval (Q&A, summaries with rubric, comparison, table extraction, Excel operations, "not found") | 80 |
| Excel operations reference outputs | 40 |
| Malicious files (macros, zip bombs, external links, huge sheets, malformed) | 30 |
| Canary (content in logs/traces/audit) | every release |
| Deletion (planted canary through DB, files, index, backup restore) | every release |

---

## Prioritization Working Notes

- RICE was not run. Must share is 25 of 42 active FRs (60%), at the limit.
- Must: confidentiality (FR-039, 041, 012, 014, 015, 016, 052, 053), trust (009, 030, 036) and core jobs (042, 045, 046, 049, 050, 023, 024, 055).
- Deal workspaces (FR-040) are Should, though most bankers work in teams. Private spaces deliver value first. Recommend building FR-040 in the same release.
- OCR (FR-044) is Should pending Q13.

---

## Glossary

| Term | Definition |
|------|------------|
| Space | A private space or a deal workspace |
| Selected set | The documents a conversation is allowed to use (FR-055) |
| Masking report | Post-ingestion summary of masked identifiers and flagged passages (FR-043) |
| Operation plan | Plain-language list of typed operations shown before applying (FR-050) |
| Break-glass | Justified, time-limited, audited compliance access to specific content (FR-054) |
| Crypto-shredding | Deleting data by destroying its encryption key |
| Canary | A unique planted string used to prove content never appears where it shouldn't |
