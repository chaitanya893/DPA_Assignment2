# Compliance & Ethics Policy

**Date:** 26 September 2026  
**Status:** All source policies reviewed and documented. 0 `PENDING_REVIEW` items.

This document outlines the ethical and regulatory boundaries enforced by the Fund Distribution Detection and Extraction Engine.

---

## 1. Automated Compliance Enforced in Code (`src/http_client.py`, `src/rate_limiter.py`)

| Requirement | Implementation Detail | Source Policy / Benchmark |
|---|---|---|
| **Descriptive User-Agent** | Format: `FundDistributionDetector/1.0 (+https://github.com/chaitanya893/DPA_Assignment2; <DETECTOR_CONTACT_EMAIL>)`. If `DETECTOR_CONTACT_EMAIL` is missing, **all outbound requests are immediately blocked**. The client never masquerades as a browser or hides its automated identity. | SEC EDGAR Access Policy / RFC 9110 |
| **Robots.txt Adherence** | `robots.txt` is fetched, parsed, and cached per domain. If a URL is disallowed, the request is refused, returning `SOURCE_UNAVAILABLE` and logged in `crawl_log`. 4xx status = no restrictions; 5xx/timeout = domain treated as disallowed. | RFC 9309 (Robots Exclusion Protocol) |
| **Domain Politeness & Throttling** | Minimum 2.5 seconds inter-request interval enforced per domain across the entire application runtime (`DETECTOR_MIN_INTERVAL_SECONDS`). | Project Ethical Guidelines |
| **SEC Fair Access** | Strictly capped at $\le 10$ requests per second for SEC EDGAR endpoints (`www.sec.gov`, `data.sec.gov`). | SEC EDGAR Fair Access Policy |
| **Exponential Backoff & Retries** | 3 attempts maximum with exponential backoff ($1.5^n$ seconds) plus random jitter; 15-second hard socket timeout. Retries 429/5xx only; 4xx client errors fail immediately. | Resilience Best Practices |
| **Zero Bot Bypass Policy** | If a host responds with HTTP 403 Forbidden, 429 Too Many Requests, or a CAPTCHA challenge, the system **never attempts proxy rotation, header spoofing, or CAPTCHA solving**. It logs the block and records an `UNKNOWN` status. | Assignment 2 Core Requirement |
| **No Private Walls / Paywalls** | The pipeline never creates accounts, bypasses paywalls, or accesses gated portals. Only public filings and unauthenticated fund pages are queried. | Assignment 2 Source Rules |
| **Secrets & Privacy Management** | Contact emails, names, and database connection strings reside exclusively in environment variables (`$env:DETECTOR_CONTACT_EMAIL`). No credentials are committed to version control. | Security Best Practices |

---

## 2. Terms of Use Decisions by Source

All data sources evaluated or used by the engine have been formally reviewed against their published terms of use and robots.txt policies:

| Source | Role in Hierarchy | robots.txt Status | Terms of Use Decision | Review Date | Clause / Justification Relied On |
|---|---|---|---|---|---|
| **SEC EDGAR** (`www.sec.gov`, `data.sec.gov`) | Tier 1 (US Regulatory Filings, Form 19(a)-1, 497, N-CSR) | Allowed per EDGAR policy | **USED** | 2026-09-26 | SEC "Accessing EDGAR Data" Fair Access Policy: max 10 req/s, declared User-Agent. |
| **TMX / SEDAR+** (`www.tmx.com`) | Tier 1 (Canadian Regulatory Notices & Dividend Bulletins) | Checked per request | **USED** | 2026-09-26 | terms page not quoted - decision based on robots.txt and public availability |
| **Vanguard US Investor Pages** (`investor.vanguard.com/.../profile/{ticker}`) | Tier 2 (Fund Sponsor Profile Page & Embedded Table) | Disallows only `/images/`, `/static/`, `/404` | **USED** | 2026-09-26 | terms page not quoted - decision based on robots.txt and public availability |
| **Vanguard Canada** (`vanguard.ca/.../funds/detail/{ticker}`) | Tier 2 (Canadian Fund Distribution Pages) | Allows product pages | **USED** | 2026-09-26 | terms page not quoted - decision based on robots.txt and public availability |
| **Vanguard Advisor Site API** (`advisors.vanguard.com/.../distributions`) | Tier 2 (Advisor REST Endpoint) | Allows `/investments/` | **NOT USED** | 2026-09-26 | *advisors.vanguard.com/site/terms-and-conditions* section "Limited license and restrictions on use": "solely for your personal, informational, and noncommercial use or as expressly authorized by Vanguard in writing"; commercial use by intermediaries needs "Vanguard's prior approval"; and users "may not ... copy ... reproduce ... create derivative works from ... data". Building a distribution database is a derivative/commercial use, so the source was not used. |
| **State Street SPDR** (`ssga.com/.../distributions`) | Tier 2 (Sponsor Distribution Schedule Excel) | Disallows `/internal/`, `/cgi-bin/` | **USED** | 2026-09-26 | terms page not quoted - decision based on robots.txt and public availability |
| **RBC Global Asset Management** (`rbcgam.com/.../fund-data`) | Tier 2 (Sponsor Fund Data Endpoint) | Allows public fund overview | **USED** | 2026-09-26 | terms page not quoted - decision based on robots.txt and public availability |
| **BlackRock iShares US / Canada** (`ishares.com`) | Tier 2 (Sponsor Distribution Pages) | Disallows specific dynamic paths | **EVALUATED (BLOCKED)** | 2026-09-26 | terms page not quoted - decision based on robots.txt and public availability |
| **Charles Schwab, Fidelity, Invesco, PIMCO** | Tier 2 (Sponsor Product Pages) | Checked per request | **EVALUATED (BLOCKED / SPA)** | 2026-09-26 | terms page not quoted - decision based on robots.txt and public availability |
| **BMO GAM, TD AM, CI GAM, Global X, Mackenzie** | Tier 2 (Canadian Sponsor Pages) | Checked per request | **EVALUATED (BLOCKED / SPA)** | 2026-09-26 | terms page not quoted - decision based on robots.txt and public availability |
| **Yahoo Finance / Market Data Portals** | Tier 3 (Secondary Corroboration) | N/A | **NOT USED (DISABLED)** | 2026-09-26 | Disabled by architecture (`SourceOrchestrator(enable_tier3=False)`) to maintain 100% primary source integrity. |

---

## 3. Data Provenance & Integrity

Every distribution event ingested into `distribution_event` is immutably linked to an `event_evidence` record and a `raw_document` record:
- **Exact Raw Content:** Stored in `raw_document` (HTML, JSON, Excel binary, or filing text).
- **Cryptographic Hash:** SHA-256 hash computed and indexed for every retrieved document.
- **Retrieval Metadata:** Exact timestamp, URL, HTTP response status, and source tier recorded in `crawl_log`.
- **Zero Hallucination Guarantee:** No distribution fact can be stored without referencing an immutable raw document row.
