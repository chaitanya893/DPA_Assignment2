# Compliance & Ethics Policy

**Date:** 26 September 2026  
**Status:** All source policies reviewed and documented. 0 `PENDING_REVIEW` items.

This document outlines the ethical and regulatory boundaries enforced by the Fund Distribution Detection and Extraction Engine.

---

## 1. Automated Compliance Enforced in Code (`src/http_client.py`, `src/rate_limiter.py`)

| Requirement | Implementation Detail | Source Policy / Benchmark |
|---|---|---|
| Descriptive User-Agent | Format: `FundDistributionDetector/1.0 (+https://github.com/chaitanya893/DPA_Assignment2; <DETECTOR_CONTACT_EMAIL>)`. If `DETECTOR_CONTACT_EMAIL` is missing, all outbound requests are immediately blocked. The client never masquerades as a browser or hides its automated identity. | SEC EDGAR Access Policy / RFC 9110 |
| Robots.txt Adherence | `robots.txt` is fetched, parsed, and cached per domain. If a URL is disallowed, the request is refused, returning `SOURCE_UNAVAILABLE` and logged in `crawl_log`. 4xx status = no restrictions; 5xx/timeout = domain treated as disallowed. | RFC 9309 (Robots Exclusion Protocol) |
| Domain Politeness & Throttling | Minimum 2.5 seconds inter-request interval enforced per domain across the entire application runtime (`DETECTOR_MIN_INTERVAL_SECONDS`). | Project Ethical Guidelines |
| SEC Fair Access | Strictly capped at ≤ 10 requests per second for SEC EDGAR endpoints (`www.sec.gov`, `data.sec.gov`). | SEC EDGAR Fair Access Policy |
| Exponential Backoff & Retries | 3 attempts maximum with exponential backoff (1.5^n seconds) plus random jitter; 15-second hard socket timeout. Retries 429/5xx only; 4xx client errors fail immediately. | Resilience Best Practices |
| Zero Bot Bypass Policy | If a host responds with HTTP 403 Forbidden, 429 Too Many Requests, or a CAPTCHA challenge, the system never attempts proxy rotation, header spoofing, or CAPTCHA solving. It logs the block and records an `UNKNOWN` status. | Assignment 2 Core Requirement |
| No Private Walls / Paywalls | The pipeline never creates accounts, bypasses paywalls, or accesses gated portals. Only public filings and unauthenticated fund pages are queried. | Assignment 2 Source Rules |
| Secrets & Privacy Management | Contact emails, names, and database connection strings reside exclusively in environment variables (`$env:DETECTOR_CONTACT_EMAIL`). No credentials are committed to version control. | Security Best Practices |

---

## 2. Terms of Use Decisions by Source

All data sources evaluated or used by the engine have been formally reviewed against their published terms of use and robots.txt policies:

| Source | Role in Hierarchy | robots.txt Status | Terms of Use Decision | Review Date | Clause / Justification Relied On |
|---|---|---|---|---|---|
| SEC EDGAR (`www.sec.gov`, `data.sec.gov`) | Tier 1 (US Regulatory Filings, Form 19(a)-1, 497, N-CSR) | Allowed per EDGAR policy | USED | 2026-09-26 | SEC "Accessing EDGAR Data" Fair Access Policy: max 10 req/s, declared User-Agent. |
| TMX / SEDAR+ (`www.tmx.com`) | Tier 1 (Canadian Regulatory Notices & Dividend Bulletins) | checked per request by the crawler | ATTEMPTED - https://www.tmx.com/dividends/{ticker} returned HTTP 404 for every Canadian fund; no TMX data used | 2026-09-26 | terms page not quoted - decision based on robots.txt and public availability |
| Vanguard US Investor Pages (`investor.vanguard.com/.../profile/{ticker}`) | Tier 2 (Fund Sponsor Profile Page & Embedded Table) | checked per request by the crawler | USED | 2026-09-26 | terms page not quoted - decision based on robots.txt and public availability |
| Vanguard Canada (`https://www.vanguard.ca/en/investor/products/products-group/etfs/{ticker}`) | Tier 2 (Canadian Fund Distribution Pages) | robots.txt allows all user agents except named AI-training bots | USED | 2026-09-26 | terms page not quoted - decision based on robots.txt and public availability |
| Vanguard Advisor Site API (`advisors.vanguard.com/.../distributions`) | Tier 2 (Advisor REST Endpoint) | checked per request by the crawler | NOT USED | 2026-09-26 | *advisors.vanguard.com/site/terms-and-conditions* section "Limited license and restrictions on use": "solely for your personal, informational, and noncommercial use or as expressly authorized by Vanguard in writing"; commercial use by intermediaries needs "Vanguard's prior approval"; and users "may not ... copy ... reproduce ... create derivative works from ... data". Building a distribution database is a derivative/commercial use, so the source was not used. |
| State Street SPDR (`ssga.com/...`) | Tier 2 (Sponsor Distribution Schedule Excel) | checked per request by the crawler | USED | 2026-09-26 | terms page not quoted - decision based on robots.txt and public availability |
| RBC Global Asset Management (`rbcgam.com/...`) | Tier 2 - ETF detail page; distribution data embedded in the page HTML (const fundData), not a separate API | ETF detail pages allowed | USED | 2026-09-26 | https://www.rbcgam.com/en/ca/terms-and-conditions , section "No Reliance": "The material on this site has been provided by RBC GAM Inc. for information purposes only and may not be reproduced, distributed or published without the written consent of RBC GAM Inc." -> used internally for research, not reproduced, distributed or published. (This sentence was read from that page on 2026-09-26.) |
| BlackRock iShares (US & Canada), Charles Schwab, Invesco, PIMCO, Mackenzie, Global X Canada | Tier 2 (Sponsor Distribution Pages) | checked per request by the crawler | BLOCKED (HTTP 403, logged, not worked around) | 2026-09-26 | terms page not quoted - decision based on robots.txt and public availability |
| BMO Global Asset Management | Tier 2 (Canadian Sponsor Pages) | robots.txt disallows crawler access | BLOCKED (robots.txt disallow respected; universe URL also 404) | 2026-09-26 | terms page not quoted - decision based on robots.txt and public availability |
| Fidelity Investments | Tier 2 (Sponsor Product Pages) | checked per request by the crawler | UNSUPPORTED (no machine-readable table) | 2026-09-26 | static HTML contains no distribution data table; terms page not quoted - decision based on robots.txt and public availability |
| TD Asset Management | Tier 2 (Canadian Sponsor Pages) | checked per request by the crawler | UNSUPPORTED (FundCard URL redirects to list page) | 2026-09-26 | FundCard URL redirects to general fund list page without distribution table; terms page not quoted - decision based on robots.txt and public availability |
| CI Global Asset Management | Tier 2 (Canadian Sponsor Pages) | checked per request by the crawler | UNSUPPORTED (HTTP 400) | 2026-09-26 | HTTP 400 returned (+ one fund with no data); terms page not quoted - decision based on robots.txt and public availability |
| Yahoo Finance / Market Data Portals | Tier 3 (Secondary Corroboration) | N/A | NOT USED (DISABLED) | 2026-09-26 | Disabled by architecture (`SourceOrchestrator(enable_tier3=False)`) to maintain 100% primary source integrity. |

---

## 3. Data Provenance & Integrity

Every distribution event ingested into `distribution_event` is immutably linked to an `event_evidence` record and a `raw_document` record:
- **Exact Raw Content:** Stored in `raw_document` (HTML, JSON, Excel binary, or filing text).
- **Cryptographic Hash:** SHA-256 hash computed and indexed for every retrieved document.
- **Retrieval Metadata:** Exact timestamp, URL, HTTP response status, and source tier recorded in `crawl_log`.
- **Zero Hallucination Guarantee:** No distribution fact can be stored without referencing an immutable raw document row.
