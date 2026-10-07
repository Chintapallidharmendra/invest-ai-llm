# PRD Addendum — Foundry Local AI

**Companion to:** `prd.md`
**Version:** 1.0
**Date:** 2026-10-06

> Overflow and working notes that would bloat the PRD. Nothing here is the source of truth for *what* to build — that stays in `prd.md`. Decisions belong in `decision-log.md`.

---

## Open Questions

| # | Question | Owner | Needed By | Status |
|---|----------|-------|-----------|--------|
| Q1 | Are the V1 chores right? Default: stock and index lookups, MF NAV and returns, company results/actions/announcements, export + calculations (FR-018–024, FR-027). Anything to add or drop? | Product owner | Before story compilation | **confirmed 2026-10-06** (default accepted) |
| Q2 | IIFL knowledge source. Default: filings via data tools (Must) + admin-uploaded public document library with citations (Should, FR-028/029). Alternatives: tools only, which drops FR-028/029. | Product owner | Before architecture | **confirmed 2026-10-06** (default accepted) |
| Q3 | PII policy. Default: **block and explain** (FR-012). Alternative: mask and continue, which is riskier because missed values reach the model. | Product owner + Compliance | Before architecture | **confirmed 2026-10-06** (default accepted) |
| Q4 | Retention. Default: user chat history (deletable from view) + audit trail, 5-year configurable default (FR-008, FR-031, NFR-017). | Product owner + Compliance | Before architecture | **confirmed 2026-10-06** (default accepted) |
| Q5 | Is an internal SMTP relay available for invitation and reset emails? | Product owner | Before EPIC-002 | **resolved 2026-10-06:** no email in V1; admin shares one-time links manually (FR-001, FR-004 → Won't, FR-038) |
| Q6 | Which ACE products and licence terms cover post-V1 needs? | Product owner | Post-V1 planning | **resolved 2026-10-06:** ACE **full subscription**, or data fed from **internal database tables** (D-04, D-09) |
| Q7 | Will compliance formally acknowledge the V1 NSE data approach and the AI usage (SEBI AI/ML guidance)? | Product owner | Before pilot launch | open |
| Q8 | Session timeouts (60 min idle / 12 h absolute, NFR-007): do these suit a full working day? | Product owner | Before EPIC-002 | open |
| Q9 | Which named users and which admin(s) are in the pilot group? | Product owner | Before pilot launch | open |
| Q10 | If internal database tables are used: which database(s) and tables/views, who owns them, and are they classified as published/non-sensitive (no UPSI, no customer PII)? Read-only service account available? | Product owner + data owner + Compliance | Before post-V1 data work | open |
| Q11 | Which internal channel is approved for sharing set-password links (in person, corporate chat)? | Admin | Before pilot launch | open |
| Q12 | (From architecture AQ-1) Document PII policy for FR-012/FR-028: reject documents with high-risk identifiers (PAN, Aadhaar, bank/demat account, card, UPI, passport, voter ID) but **mask** emails and phone numbers, and allow director/officer names (public figures)? Strict "reject any PII" would reject every annual report. | Product owner + Compliance | Before EPIC-006 | **confirmed 2026-10-07:** proposed policy accepted; FR-012 and FR-028 updated (PRD v1.1) |

---

## Deferred Requirements (parked, not cut)

- **D-01 (MFA):** Users can enable TOTP-based two-factor sign-in; admins must use it. *Reason deferred:* small pilot on internal network; first V2 security addition.
- **D-02 (SSO):** Sign in with the corporate identity provider (OIDC). *Reason deferred:* not needed for ~10 users.
- **D-03 (Charts):** Price/NAV history shown as a line chart alongside the table. *Reason deferred:* tables + export cover V1.
- **D-04 (ACE provider):** Market, fund and company data served from an Accord Fintech ACE **full subscription** (equity, MF, derivatives, corporate announcements, real-time/EOD) through the same provider layer. *Reason deferred:* subscription planned after V1.
- **D-09 (Internal database provider):** Market/fund/company data read from approved internal database tables or views via a read-only provider adapter, as an alternative or complement to ACE. *Preconditions:* Q10 answered; only published/non-sensitive data; data-classification sign-off; read-only service account; tables declared as sources for FR-009 attribution. *Reason deferred:* post-V1.
- **D-10 (Email):** Invitations, self-service reset (FR-004) and admin alerts by email. *Reason deferred:* no email relay in V1.
- **D-05 (Real-time quotes):** Live/streaming prices. *Reason deferred:* licensing; requires ACE or another licensed feed.
- **D-06 (Confidential documents):** Library supports internal/confidential documents with per-document access control. *Reason deferred:* needs access-control design; V1 is public documents only.
- **D-07 (Scheduled reports):** Users schedule a recurring summary (e.g. daily NIFTY + IIFL close) by email. *Reason deferred:* adds outbound messaging; revisit after pilot feedback.
- **D-08 (Hindi / regional languages):** *Reason deferred:* English-only pilot.

---

## Detailed Acceptance Criteria Overflow

### FR-012 (PII types, detail for test-suite authors)
- PAN: 5 letters + 4 digits + 1 letter, any case, with or without spaces.
- Aadhaar: 12 digits, grouped 4-4-4 with space or hyphen, or contiguous. Include masked forms like "XXXX XXXX 1234" (block: partial Aadhaar is still sensitive).
- Bank account: 9–18 digits when near context words (account, a/c, acct, IFSC, bank).
- Card number: 13–19 digits passing a Luhn check.
- Indian mobile: 10 digits starting 6–9, optional +91 / 0 prefix.
- UPI ID: `handle@psp` pattern.
- Demat / DP ID: NSDL (IN + 14 digits) and CDSL (16 digits) formats.
- Benign look-alikes that must **not** block: NSE symbols, ISINs (INE…), scheme codes, amounts, dates, phone numbers of public IIFL offices quoted from documents (documents only).

### Guardrail test-suite composition (FR-013, FR-014, FR-034, FR-035, NFR-006)
| Suite | Min. size | Categories |
|---|---|---|
| Scope (Level 1/2 must answer) | 60 | Each Level 1 and Level 2 row, including care notes present |
| Scope (Level 3 must decline) | 60 | Advice, personal finance/tax/legal, customer data, off-topic, self-disclosure |
| UPSI | 30 | Unannounced results, rumoured deals, internal figures, insider-style phrasing, indirect ("what are people at IIFL saying…") |
| Forecasts | 30 | Direct targets, "good time to enter", trend extrapolation, disguised ("hypothetically, if…") |
| Jailbreak / injection | 100 | Role-play, instruction override, encoding tricks, planted text in documents and tool results, multi-turn escalation |
| Figure grounding | 30 | Planted mismatches, unit/format variants (cr vs million), rounding, derived figures (CAGR) |

### FR-031 (audit record fields)
- Correlation ID, timestamp, user ID, event type, input guard verdicts (type/category only), model name + version, tools called with validated arguments, data sources + as-of times, output guard verdicts, final response text, latency per stage.

---

## Prioritization Working Notes

RICE was not run: no contested ordering. MoSCoW reasoning:
- Musts are limited to safety (auth, guardrails, audit), trust (sources, no guessing) and the core chores (quote, history, NAV, company results). That gives 19 of 33 FRs (58%), under the 60% inflation threshold.
- Index performance (FR-020) is Should because stock and NAV lookups cover the main chores.
- The data-source kill switch (FR-026) is Should because the manual fallback is a config change and restart.
- The document library (FR-028/029) is Should because filings (FR-027) cover the core company facts. The library adds breadth (strategy, segments, commentary).

---

## Supporting Research / References

- `research-report.md` v1.4: library choices (nselib, mftool), NSE ToU, vLLM on A10 sizing and KV-cache estimate, guardrail stack, SEBI AI/ML guidance.
- `decision-log.md`: stack, runtime, GPU, capacity and data-strategy decisions (2026-10-05/06).

---

## Glossary

| Term | Definition |
|------|------------|
| Chore | A routine information task a finance user does today by hand (e.g. checking a NAV) |
| In-scope topics | IIFL / IIFL Finance, Indian equity and index markets, Indian mutual funds, general financial concepts |
| PII gate | The check that blocks messages containing personal data before any model sees them |
| Eval set | Curated questions with expected behaviour used to measure answer quality (NFR-010) |
| Red-team suite | Curated jailbreak and prompt-injection prompts used to measure manipulation resistance (NFR-006) |
| As-of time | The date/time the shown data represents |
| Fail closed | If a safety check is unavailable, the request is refused rather than passed through |
| † threshold | Provisional NFR value to be confirmed by the model-selection spike |
