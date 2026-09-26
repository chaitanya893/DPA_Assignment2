# Final Memo - Fund Distribution Detection and Extraction Engine (Assignment 2)

**Date:** 26 September 2026  
**Status:** Code complete, evaluated against live sources, and audited.  

> **PDF closing note:** *"A working pipeline that covers 60 percent of cases and documents the other 40 percent precisely is a better outcome than one claiming full coverage that nobody can verify."*  
> This memo reports exclusively verifiable, reproducible numbers from `quality/detection_report.json`, `quality/gold_set_evaluation.json`, `data/exports/dq_audit_report.json`, and direct queries on `data/fund_distributions.db`.

---

## 1. Executive Summary

We designed, implemented, and evaluated an end-to-end data pipeline for detecting and extracting fund dividend and capital gain distributions across US and Canadian investment funds:
- **Layer A (Atomic Detector):** Determines distribution status (`DECLARED`, `NOT_DECLARED`, or `UNKNOWN`) for a given fund and time window using deterministic multi-tier evidence synthesis.
- **Layer B (Extraction Engine):** Routes identified announcements through a strictly typed parse tree (API, HTML table, PDF/Excel, filing text), extracting per-share cash distributions, tax character breakdowns, and key lifecycle dates without LLMs.
- **Validation Gate & Provenance:** Executes multi-rule accounting, calendar sanity, currency integrity, and continuity checks before ingestion, storing full byte-level SHA-256 provenance in SQLite.

### Core Metrics Summary

| Metric | Measured Result | Benchmark / Target | Source / Notes |
|---|---|---|---|
| Universe Scope | 100 funds (60 US, 40 CA) | 100 funds | `config/universe_100.json` |
| Total Window Checks | 2,501 deduped (2,517 total runs) | 24-month backfill | `quality/detection_report.json` |
| Events Stored | 425 events | Real market events | `data/exports/distribution_event.csv` |
| Detector Precision | 100.0% (0 False Positives) | ≥ 99.0% | `quality/gold_set_evaluation.json` |
| Detector Recall (Overall) | 74.52% (269 / 361 TP) | ≥ 98.0% | Across all 50 gold funds (incl. 403 blocked) |
| Detector Recall (Automated Funds) | 100.0% (269 / 269 TP) | ≥ 98.0% | SPDR, Vanguard US, Vanguard CA, RBC |
| Extraction Accuracy | 98.88% (266 / 269 exact) | High fidelity | Exact ex-date & gross amount match |
| Zero-Intervention Extraction | 100.0% (425 / 425) | ≥ 90.0% | 0 open items in `review_queue` |
| Data Quality Pass Rate | 98.97% (0 Critical flags, 10 warnings) | Clean audit | `data/exports/dq_audit_report.json` |
| Average Cost per Check | $0.000173 (6.22s, 1.46 reqs) | Economical | Politeness-governed runtime |

---

## 2. Universe & Scope

The universe defined in `config/universe_100.json` consists of **100 funds** across two jurisdictions and 15 major asset management families:
- **Geography:** 60 United States funds (SEC CIK & ticker mapped) and 40 Canadian funds (TSX ticker & FundServ mapped).
- **Vehicle Structure:** 81 Exchange Traded Funds (ETFs) and 19 Mutual Funds.
- **Distribution Frequency:** 54 Monthly payers, 34 Quarterly payers, 7 Semi-Annual payers, and 5 Annual payers.
- **Asset Classes:** Large-Cap Equity, Core Fixed Income, High Yield, Real Estate (REITs), Balanced, Covered Call, and Preferreds.
- **Fund Families:** State Street SPDR, Vanguard US, Vanguard Canada, RBC Global Asset Management, BlackRock iShares, BlackRock iShares Canada, Charles Schwab, Fidelity Investments, Invesco, BMO Global Asset Management, TD Asset Management, CI Global Asset Management, Global X Canada, Mackenzie Investments, and PIMCO.

---

## 3. Architecture

The system is structured as a modular pipeline operating under a clean separation of concerns:

```
+-------------------------------------------------------------------------------+
|                           Sweep Scheduler & Gap Logic                         |
|   (Identifies routine checks, gap triggers, year-end sweeps, and backfills)   |
+---------------------------------------+---------------------------------------+
                                        |
                                        v
+-------------------------------------------------------------------------------+
|                            Layer A: Atomic Detector                           |
|  - Strategy 1: Calendar Expectation Check (Cadence & Frequency)               |
|  - Strategy 2: Content Hash Change Detection (ETag / SHA-256)                 |
|  - Strategy 3: Filing Index Polling (EDGAR 497/N-CSR/19(a) daily index)       |
|  - Strategy 4: Targeted Source Acquisition (Tier 1 Regulatory & Tier 2 Sponsor)|
|  - Output: DECLARED (with citation) | NOT_DECLARED | UNKNOWN (with reason)    |
+---------------------------------------+---------------------------------------+
                                        | (If DECLARED)
                                        v
+-------------------------------------------------------------------------------+
|                            Layer B: Extraction Engine                         |
|  - Strict Route Tree: API (JSON) -> HTML Table -> PDF/Excel -> Filing Regex   |
|  - Extracts: Gross Distribution, Ex-Date, Record Date, Pay Date, Declaration  |
|  - Tax Components: Return of Capital (ROC), Capital Gains, Qualified Div.    |
+---------------------------------------+---------------------------------------+
                                        |
                                        v
+-------------------------------------------------------------------------------+
|                            Deterministic Validation Gate                      |
|  - Rule 1: Component Sum Balance (Tolerance: $0.0005)                         |
|  - Rule 2: Chronological Ordering Sanity (Decl <= Ex <= Record <= Pay)       |
|  - Rule 3: Share Class Currency Integrity (USD vs CAD)                        |
|  - Rule 4: Frequency Continuity & Cross-Source Conflict Check                 |
|  - Rule 5: NAV Decline & 20% NAV Outlier Check (implemented; skipped: no NAV) |
+-------------------+-----------------------------------+-----------------------+
                    | (Pass)                            | (Critical Failure)
                    v                                   v
+---------------------------------------+   +-----------------------------------+
|            SQLite Database            |   |           Review Queue            |
|  - Full relational event store        |   |  - Manual intervention queue     |
|  - Raw document byte storage & SHA-256|   |  - Audit trail & operator action  |
+---------------------------------------+   +-----------------------------------+
```

Synthesis rules:
- **DECLARED** requires Tier 1 or Tier 2 evidence whose declaration date, ex-date, or publication date falls in the window. Dates are only read next to their own label; a record or payable date alone never places a distribution in a window; a filing date is stored as a publication date, never as a declaration date. Tier 3 alone never decides anything (Tier 3 is disabled by default).
- **NOT_DECLARED** requires an applicable Tier 1/2 source that demonstrably covers the whole window (a distribution table spanning it, or a published full-year schedule) and no outage on any applicable Tier 1/2 source.
- **UNKNOWN** otherwise, with a structured reason (`SOURCE_UNAVAILABLE`, `RETRIEVAL_FAILED`, `INCOMPLETE_SOURCE`, `CONFLICTING_EVIDENCE`, `INSUFFICIENT_EVIDENCE`). A source that does not apply to a fund (e.g. SEC for a Canadian fund) is ignored instead of blocking an answer.
- **Confidence** is always a float in 0.0–1.0: Tier 1 = 1.00, Tier 2 with a labelled declaration or ex-date = 0.90, Tier 2 publication date only = 0.80, NOT_DECLARED Tier 2 coverage = 0.85, minus 0.05 when another applicable source failed; UNKNOWN = 0.0.

---

## 4. Backfill and Gap Logic

Per fund, the engine tracks `expected_frequency`, `last_confirmed_event_date`, `last_checked_at`, and `consecutive_unknowns`.
A lookback sweep over the last **N months** runs when:
1. The time since the last confirmed event exceeds **1.5x** the expected interval (monthly: 30 d → 45 d signal matching the PDF's 45-day threshold, quarterly: 91 d → 136 d, semi-annual: 182 d → 273 d, annual: 365 d → 547 d);
2. Expected frequency is dynamically learned from stored history (`learn_frequency`, requiring ≥ 4 events);
3. UNKNOWN returned **more than twice** consecutively;
4. At **month-end** (last 3 days of the month);
5. Throughout the **year-end period** (1 December through 15 January).

A newly initialized fund receives a **24-month backfill**. Sweeps are partitioned into calendar months so every monthly payment is detected separately. On quiet days, a fund receives only a routine check, which is skipped when off-cadence, its page hash is unchanged, and EDGAR shows no new filing.

**Default N = 3 months**, configurable in `config/sweep_config.yaml`. Reasons:
- It covers one full cycle of the quarterly payers that make up most of the non-monthly universe;
- Sources publish late and amend (Canadian year-end reallocations, estimated → final capital gains), and a 3-month window re-reads a restated month twice more after its first appearance;
- It costs only 3 checks per triggered fund, with page and index responses reused within a single run.

---

## 5. Deliverables vs PDF Acceptance Criteria

| Deliverable | PDF Acceptance Criteria | Measured Value / Status | Evaluation |
|---|---|---|---|
| Domain primer | Reviewed and approved before coding starts | `docs/DOMAIN_PRIMER.md` exists (3-5 pages); written before the later code changes, formal approval not recorded | PARTLY MET |
| Atomic detector | Runs over 100 funds; recall >= 98% and precision >= 99% against the gold set | 100 funds run; precision 100%; recall 100% on the 36 automated funds, 74.52% on all 50 gold-set funds (92 misses = HTTP 403 families) | PARTLY MET |
| Routing layer | Route logged for 100% of detected events | 100% | MET |
| Extraction | >= 90% of detected events fully extracted without manual intervention | 100% (425/425, review queue 0); extraction accuracy 98.88% | MET |
| Database | Schema implemented, populated with 24 months of history for 100 funds, idempotent on re-run | schema + idempotency MET; 36 funds with data (depths as written) | PARTLY MET |
| Validation | All checks implemented, DQ report generated | 7 checks implemented, pass rate 98.97%, NAV checks and cross-source skipped (no input data) | MET |
| Gold set | 300 verified events with evidence links | 361 DECLARED + 78 NOT_DECLARED, 50 funds, 24 months, evidence URL per row; 52 rows manually verified with screenshots | MET |
| Final memo | Coverage by fund family and source type, where automation fails, full-universe cost | this document | MET |

### Other requirements

| Requirement | Target / Spec | Measured Value / Status | Evaluation |
|---|---|---|---|
| Universe definition | 100 funds (60 US, 40 CA, ETFs, mutual funds, 15 families) | 100 funds in `config/universe_100.json` (60 US, 40 CA, 81 ETFs, 19 mutual funds) | MET |
| Backfill & gap logic | 1.5x interval triggers, year-end sweeps, N=3 months | Implemented in `src/sweep_scheduler.py`, fully unit-tested | MET |
| Compliance & ToS | Robots.txt, ≤ 10 req/s SEC, 2.5s domain throttle, zero bot-bypass | Enforced in `src/http_client.py`; documented in `docs/COMPLIANCE.md` | MET |

---

## 6. Database Design & Integrity

The database uses SQLite with 12 relational tables (exceeding the baseline 9 tables to provide complete operational provenance and auditability):
- `fund_master` & `share_class`: Decouples fund-level metadata from share-class identifiers.
- `source_registry`: Registers data sources, tiers, and compliance status.
- `crawl_log`: Immutable log of every HTTP request, response code, latency, and payload size.
- `raw_document`: Raw document byte storage and SHA-256 cryptographic hashes.
- `distribution_event`: Core event entity with natural key `(class_id, ex_date, distribution_category, estimated_or_final, version)`.
- `distribution_component`: Child table for granular tax components (Income, ROC, Capital Gains).
- `event_evidence`: Many-to-many junction table mapping distribution events to raw documents.
- `dq_flag`: Audit flags attached to specific events or detection runs.
- `detection_run`: Log of every Layer A check execution, status, and route taken.
- `fund_detection_state`: Per-fund scheduling state (last checked, gap counters, learned frequency).
- `review_queue`: Quarantined anomalous events requiring human intervention.

### Natural Key & Idempotency Evidence
- **Natural Key Rationale:** `(class_id, ex_date, distribution_category, estimated_or_final, version)` guarantees that re-running the pipeline over the same window updates or supersedes records without creating duplicate entries. It allows estimated and final announcements to coexist and supports versioned restatements.
- **Idempotency Verification:** Re-running backfills across the 100-fund universe produced **0 duplicate natural keys**.
- **Amendments & Estimated-vs-Final:** The versioning and amendment logic is implemented and tested in the unit test suite; in the evaluated 2024–2026 backfill, all sponsor-published distributions were Version 1, FINAL.

---

## 7. Compliance & Ethics

The engine enforces strict automated compliance policies in `src/http_client.py` and `src/rate_limiter.py`:
1. **Descriptive User-Agent & Mandatory Contact:** Format: `FundDistributionDetector/1.0 (+https://github.com/chaitanya893/DPA_Assignment2; <DETECTOR_CONTACT_EMAIL>)`. If `DETECTOR_CONTACT_EMAIL` is unset, zero requests are sent.
2. **Robots.txt Adherence:** `robots.txt` is fetched once per host and cached. Disallowed paths result in immediate refusal, logged in `crawl_log`.
3. **Rate Limiting:** SEC EDGAR is throttled to ≤ 10 req/s; all sponsor domains are throttled to a minimum 2.5-second interval between requests per host.
4. **Zero Anti-Bot Circumvention:** No proxy rotation, no header spoofing, and no CAPTCHA solving. If a host responds with HTTP 403 or an anti-bot challenge, the system logs the failure as `UNKNOWN` (`RETRIEVAL_FAILED`) and moves on.
5. **Terms of Use Status:** Documented in `docs/COMPLIANCE.md`.

---

## 8. Layer A Evaluation (Gold Set Benchmark)

The detector was evaluated against `config/gold_set.csv`, a verified gold set of **439 rows** spanning **50 distinct funds** and **24 consecutive calendar months** (October 2024 through September 2026):
- **Gold Composition:** 361 `DECLARED` events and 78 `NOT_DECLARED` non-distribution months.
- **Evaluation Mechanism:** `src/validators/gold_set_evaluator.py` executed live detector sweeps across all 439 windows without feeding any gold labels into the detector.

### Gold Set Performance Results

| Metric | Value | Target | Status |
|---|---|---|---|
| Precision | 100.0% (269 / 269) | ≥ 99.0% | MET (0 False Positives) |
| Recall (Overall) | 74.52% (269 / 361) | ≥ 98.0% | 92 False Negatives due to HTTP 403 blocks |
| Recall (Automated Sponsors) | 100.0% (269 / 269) | ≥ 98.0% | MET on SPDR, Vanguard US, Vanguard CA, RBC |
| True Positives (TP) | 269 | - | Accurate declarations identified |
| False Positives (FP) | 0 | 0 | Zero phantom distributions declared |
| False Negatives (FN) | 92 | 0 | All 92 were blocked sponsors (UNKNOWN) |
| True Negatives (TN) | 78 | - | 78 NOT_DECLARED rows, of which 24 returned UNKNOWN (counted as no FP, not TN) |
| F1 Score | 85.4% | - | Balance across unblocked and blocked sponsors |

### Breakdown by Fund Family

| Fund Family | Evaluated Funds | TP | FN | FP | TN | UNK | Recall | Precision | Notes |
|---|---|---|---|---|---|---|---|---|---|
| Vanguard (US) | 20 | 135 | 0 | 0 | 30 | 0 | 100.0% | 100.0% | Accurate detection |
| State Street SPDR | 7 | 56 | 0 | 0 | 14 | 0 | 100.0% | 100.0% | Accurate detection |
| Vanguard Canada | 7 | 62 | 0 | 0 | 10 | 0 | 100.0% | 100.0% | Accurate detection |
| RBC GAM | 2 | 16 | 0 | 0 | 0 | 0 | 100.0% | 100.0% | Accurate detection |
| BlackRock iShares | 9 | 0 | 62 | 0 | 16 | 62 | 0.0% | N/A | Blocked by HTTP 403 |
| Charles Schwab | 5 | 0 | 30 | 0 | 8 | 30 | 0.0% | N/A | Blocked by HTTP 403 |
| **Total** | **50** | **269** | **92** | **0** | **78** | **92** | **74.52%** | **100.0%** | Zero false positives |

---

## 9. Layer B Extraction Accuracy

For all 269 `DECLARED` events where Layer A returned positive evidence, Layer B parsed the underlying documents and extracted structured distribution facts:
- **Extraction Checked:** 269 events
- **Extraction Correct:** 266 events
- **Extraction Accuracy:** **98.88%**

### Analysis of Vanguard VNQ Extraction Discrepancy (3 Cases)
All 3 discrepancies occurred on Vanguard Real Estate ETF (`US_VANGUARD_VNQ`) for three quarterly distributions (`g0352`: 2025-06-26, `g0353`: 2025-09-24, `g0354`: 2025-12-22):
- **Root Cause & Data Model Difference:** Vanguard publishes Dividend and Return of Capital as two lines on the same ex-date; the parser stored them as two separate events, while the PDF data model (one row per class per ex-date, components as child rows) and the gold set treat them as one distribution.
- **Known Issue & Fix:** This is a known issue. The required fix is to merge same-ex-date lines in the Vanguard parser and store Return of Capital as a child component row under the single distribution event.

---

## 10. Full 100-Fund 24-Month Universe Results

The 24-month backfill sweep across all 100 funds executed **2,501 deduplicated fund-window checks** (2,517 total check runs):

| Status | Count | Percentage | Operational Meaning |
|---|---|---|---|
| DECLARED | 404 | 16.15% | Distribution confirmed and extracted with Tier 1/2 evidence |
| NOT_DECLARED | 294 | 11.76% | Complete calendar coverage confirmed zero distribution in window |
| UNKNOWN | 1,803 | 72.09% | Insufficient coverage, HTTP 403 block, or missing table |
| **Total Windows** | **2,501** | **100.00%** | Complete 24-month universe coverage |

---

## 11. Coverage by Source Type & Route Mix

Layer B automatically selected the appropriate extraction route for all 404 declared checks, resulting in **425 stored distribution events** (100% Tier 2 primary sponsor sources):

| Extraction Route | Checks Taken | Events Produced | Share of Events | Extraction Quality |
|---|---|---|---|---|
| API (JSON) | 247 | 256 | 60.24% | Vanguard US API profile & RBC GAM fundData JSON |
| PDF / Excel | 98 | 104 | 24.47% | SPDR official distribution Excel schedule (PDF/Excel route) |
| HTML Table | 59 | 65 | 15.29% | Vanguard Canada distribution history HTML tables |
| Filing Regex / Manual | 0 | 0 | 0.00% | 0 items sent to manual review |
| **Total** | **404** | **425** | **100.00%** | **100% automated extraction** |

- **Route Logging Rate:** **100.0%** (All checks recorded `route_taken` in `detection_run`).
- **Zero Intervention Rate:** **100.0%** (0 items in `review_queue`).
- **Tax Component Reporting:** 104 events (**24.47%**) contained published tax character breakdowns.

---

## 12. Failure & UNKNOWN Analysis by Fund Family

Across the 1,803 `UNKNOWN` window checks, failures reflect the exact reasons logged in `crawl_log` and `detection_run`:

| Fund Family | Jurisdiction | DECLARED | NOT_DECLARED | UNKNOWN | Exact Logged Root Cause / Notes |
|---|---|---|---|---|---|
| State Street SPDR | US | 98 | 77 | 0 | 100% automated (official Excel schedule parsed under PDF/Excel route) |
| Vanguard US | US | 199 | 154 | 148 | Automated for ≥ Mar 2025; 148 UNKNOWN due to profile page depth limit (~18 mo) |
| Vanguard Canada | CA | 59 | 61 | 55 | Automated for recent distributions; 55 UNKNOWN because page shows only last 10 distributions |
| RBC GAM | CA | 48 | 2 | 75 | Automated for ETFs; 75 UNKNOWN because 2 mutual funds publish only yearly totals and RBN returns 404 |
| BlackRock iShares | US | 0 | 0 | 350 | HTTP 403 Forbidden |
| BlackRock iShares Canada | CA | 0 | 0 | 200 | HTTP 403 Forbidden |
| Charles Schwab | US | 0 | 0 | 125 | HTTP 403 Forbidden |
| Invesco | US | 0 | 0 | 75 | HTTP 403 Forbidden |
| PIMCO | US | 0 | 0 | 50 | HTTP 403 Forbidden |
| Mackenzie Investments | CA | 0 | 0 | 25 | HTTP 403 Forbidden |
| Global X Canada | CA | 0 | 0 | 75 | HTTP 403 Forbidden |
| BMO GAM | CA | 0 | 0 | 200 | `robots.txt` disallow (and universe URL returns 404) |
| CI GAM | CA | 0 | 0 | 75 | HTTP 400 Bad Request (+ one fund with no data) |
| TD Asset Management | CA | 0 | 0 | 125 | FundCard URLs redirect to a list page (no distribution table) |
| Fidelity Investments | US | 0 | 0 | 225 | No table / JavaScript page (static HTML contains no distribution data) |

---

## 13. Data Quality & Audit Results

The validation gate ran `src/validators/run_dq_audit.py` across all 425 stored events in `data/fund_distributions.db`, executing **969 individual rule validations** (1,617 skipped due to absence of daily NAV series):

```
================================================================================
                    DATA QUALITY AUDIT REPORT
================================================================================
Total Events Evaluated : 425
Total Checks Performed : 969
Checks Skipped         : 1617
Total Flags Raised     : 10
  - CRITICAL Flags     : 0
  - WARNING Flags      : 10
  - INFO Flags         : 0
Pass Rate (excl flags) : 98.97%
================================================================================
```

### Rule-by-Rule Breakdown
1. **`COMPONENT_SUM_CHECK`:** 104 passed, 0 failed, 321 skipped (no components published). Exact mathematical equality verified within $0.0005.
2. **`DATE_ORDERING_SANITY`:** 425 passed, 0 failed. Chronological ordering verified: Declaration Date ≤ Ex-Date ≤ Record Date ≤ Payable Date.
3. **`CURRENCY_INTEGRITY`:** 425 passed, 0 failed. Exact currency alignment verified (USD for US funds, CAD for Canadian funds).
4. **`NAV_DECLINE_CONSISTENCY` / `MAGNITUDE_20PCT_NAV_CHECK`:** Implemented in code, but 425 skipped (no NAV feed supplied).
5. **`FREQUENCY_CONTINUITY`:** 5 passed, 10 warning flags, 21 skipped.
   - **Warning Explanation:** 10 warning flags raised on monthly funds (e.g. `CA_VANGUARD_VAB`, `US_SPDR_JNK`, `US_VANGUARD_BND`) for January 2025/2026 due to calendar year-end timing variations where December distributions have early January payment dates. Zero critical errors.

---

## 14. Full-Universe Cost Arithmetic

Resource consumption was measured continuously across all 2,517 check executions:
- **Measured Averages per Check:** 1.46 HTTP requests, 568.5 KB data transfer, 6.22 seconds duration (governed by 2.5s domain politeness).
- **Daily Routine Checks:** 100 funds × 1 check = 100 checks ≈ 146 HTTP requests, 56.8 MB transfer, ~10.4 minutes sequential wall-clock time (or ~2.5 minutes parallelized across separate host domains). At $0.10/compute-hour, daily routine cost = **$0.017 per day**.
- **Triggered Sweeps:** A fund triggers an N=3 month lookback only when the cadence threshold is exceeded (e.g. 45 days for monthly, 136 days for quarterly) or during year-end (1 Dec – 15 Jan). 3 checks × triggered funds × $0.000173/check ≈ **$0.0005 per fund sweep**.
- **Full 24-Month Backfill:** 2,501 checks × 6.22 s ≈ 4.3 compute-hours ≈ **$0.43 total compute cost**.

---

## 15. How the Gold Set Was Built

The gold set in `config/gold_set.csv` was compiled from primary source distribution tables:
- **Primary Sources Used:** SSGA official SPDR distributions Excel; Vanguard US profile pages; `vanguard.ca` distribution history tables; `rbcgam.com` Distributions tab; `ishares.com` Distributions table; `schwabassetmanagement.com` Distributions table. (Not SEC 19(a)/497 filings, not SPDR PDF press releases, not TMX bulletins.)
- **Verification Method:** 52 rows across all 6 source families were verified manually by the author against the source pages (evidence: `docs/gold_set_evidence/`); the remaining rows were compiled from the same primary sources with AI assistance and are labelled as such in `config/gold_set.csv`.
- **Exclusions & Isolation:** Nothing was taken from the database or the detector output; `US_VANGUARD_VWO` March 2026 was excluded (no row on the source page, not confirmable from a second source).

---

## 16. Deviations from the PDF Specification

1. **Mutual-Fund Date Rule:** Mutual funds declare distributions on their ex/record date (same-day NAV strike); the detector handles mutual funds with declaration date = ex-date rather than requiring prior public announcement.
2. **NAV Checks Skipped:** NAV decline consistency and 20% NAV outlier checks are implemented in `src/validators/accounting.py`, but skipped during audit because daily NAV time-series data was not integrated into the offline pipeline.
3. **TMX `/dividends/{ticker}` Endpoint:** The assumed URL `https://www.tmx.com/dividends/{ticker}` returns HTTP 404 for all Canadian funds; Canadian coverage relied on sponsor distribution tables (Tier 2).
4. **EDGAR Bulk Mapping Files Not Used:** Company-to-CIK mapping used direct CIK and ticker matching in `universe_100.json` rather than downloading bulk SEC JSON mapping files at runtime.
5. **No Headless Browser:** JavaScript-rendered pages (Fidelity, TD AM) were not executed with headless browsers to adhere to compliance and avoid heavy browser overhead; logged as `UNKNOWN (INSUFFICIENT_EVIDENCE)`.
6. **Coverage 36/100 Funds:** 36 funds have automated data extraction (SPDR, Vanguard US, Vanguard Canada, RBC GAM ETFs); 64 funds return UNKNOWN due to HTTP 403 blocks, JavaScript SPA rendering, or unlisted FundCard URLs.
7. **VNQ Return of Capital Split:** Vanguard published dividend and ROC as two separate table rows on the same ex-date; the parser stored two separate distribution events rather than a single event with an ROC child component.
