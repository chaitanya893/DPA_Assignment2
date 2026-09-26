# Assignment 2 Requirement Traceability Index

**Date:** 26 September 2026  
**Status:** Complete & Fully Verified  

This document provides a direct requirement-by-requirement traceability mapping between the Assignment 2 specification and the deliverables, source code, test suites, and verification artifacts in this repository.

---

## 1. Traceability Matrix

| # | Specification Requirement | Implementation Location | Evidence / Verification Artifact | Status |
|---|---|---|---|---|
| **1.1** | **Domain Primer**<br>Comprehensive guide to fund structures, distribution mechanics, cash flow lifecycle, tax characterization, and regulatory filings. | [`docs/DOMAIN_PRIMER.md`](file:///c:/Users/chait/Desktop/DPA_Project2/docs/DOMAIN_PRIMER.md) | Markdown guide covering US (1940 Act, RIC, 19(a)-1) and Canadian (NI 81-102, CDS, T3/T5) mechanics. | **PASSED** |
| **1.2** | **100-Fund Universe**<br>Diverse universe: 60 US, 40 CA, $\ge 10$ monthly, $\ge 10$ ETFs, mutual funds, 15 families, CIK and FundServ identifiers. | [`config/universe_100.json`](file:///c:/Users/chait/Desktop/DPA_Project2/config/universe_100.json) | 100 funds: 60 US, 40 CA, 81 ETFs, 19 mutual funds, 54 monthly, 42 quarterly, 2 semi-annual, 2 annual. | **PASSED** |
| **2.1** | **Layer A Atomic Detector**<br>Determines `DECLARED`, `NOT_DECLARED`, or `UNKNOWN` for a (fund, window) tuple with confidence scores and structured failure reasons. | [`src/detector.py`](file:///c:/Users/chait/Desktop/DPA_Project2/src/detector.py)<br>[`src/strategies.py`](file:///c:/Users/chait/Desktop/DPA_Project2/src/strategies.py) | `tests/test_detector.py`<br>`quality/gold_set_evaluation.json` (100.0% precision, 0 false positives). | **PASSED** |
| **2.2** | **Cheapest-First Strategy Hierarchy**<br>Executes strategies in cost order: Calendar Expectation -> Content Hash Change -> Filing Index -> Targeted Retrieval. | [`src/strategies.py`](file:///c:/Users/chait/Desktop/DPA_Project2/src/strategies.py)<br>[`src/edgar_index.py`](file:///c:/Users/chait/Desktop/DPA_Project2/src/edgar_index.py) | `tests/test_strategies.py`<br>Average cost per check: $0.000173, 1.46 HTTP requests/check. | **PASSED** |
| **2.3** | **Multi-Tier Source Synthesis**<br>Strict hierarchy: Tier 1 Regulatory (SEC/TMX) > Tier 2 Sponsor (Official) > Tier 3 Corroboration (disabled by default). | [`src/models.py`](file:///c:/Users/chait/Desktop/DPA_Project2/src/models.py)<br>[`src/detector.py`](file:///c:/Users/chait/Desktop/DPA_Project2/src/detector.py) | `tests/test_detector.py`<br>100% of stored events derived from Tier 2/Tier 1 primary sources. | **PASSED** |
| **3.1** | **Sweep Scheduler & Gap Logic**<br>Orchestrates routine daily checks, 1.5x interval gap triggers, consecutive unknown retries, and month/year-end sweeps. | [`src/sweep_scheduler.py`](file:///c:/Users/chait/Desktop/DPA_Project2/src/sweep_scheduler.py)<br>[`config/sweep_config.yaml`](file:///c:/Users/chait/Desktop/DPA_Project2/config/sweep_config.yaml) | `tests/test_sweep_scheduler.py`<br>Dry-run CLI: `python -m src.sweep_scheduler --dry-run`. | **PASSED** |
| **3.2** | **24-Month Backfill**<br>Executes backfill across 24 consecutive months for all 100 universe funds. | [`src/database/populator.py`](file:///c:/Users/chait/Desktop/DPA_Project2/src/database/populator.py) | `quality/detection_report.json` (2,501 deduped window checks, 425 events stored). | **PASSED** |
| **4.1** | **Layer B Extraction Engine**<br>Extracts gross amount, ex-date, record date, pay date, declaration date, and tax character breakdowns without LLMs. | [`src/extractor.py`](file:///c:/Users/chait/Desktop/DPA_Project2/src/extractor.py)<br>[`src/parsers/`](file:///c:/Users/chait/Desktop/DPA_Project2/src/parsers/) | `quality/gold_set_evaluation.json` (98.88% extraction accuracy across 269 gold events). | **PASSED** |
| **4.2** | **Strict Route Selection Tree**<br>Prefers structured JSON API -> HTML Tables -> PDF/Excel -> Regulatory Filings -> Manual Review Queue. | [`src/extractor.py`](file:///c:/Users/chait/Desktop/DPA_Project2/src/extractor.py) | `quality/detection_report.json`<br>Route mix: API (60.2%), PDF (24.5%), HTML Table (15.3%), 0 manual. | **PASSED** |
| **5.1** | **Deterministic Validation Gate**<br>Validates component sums ($0.0005 tol), date ordering, currency integrity, 20% NAV outlier check, frequency continuity. | [`src/validators/gate.py`](file:///c:/Users/chait/Desktop/DPA_Project2/src/validators/gate.py)<br>[`src/validators/accounting.py`](file:///c:/Users/chait/Desktop/DPA_Project2/src/validators/accounting.py) | `tests/test_validators.py`<br>`data/exports/dq_audit_report.json` (98.97% pass rate, 0 critical failures). | **PASSED** |
| **5.2** | **Review Queue for Anomalies**<br>Quarantines critical validation failures for human review without contaminating the database. | [`src/database/models.py`](file:///c:/Users/chait/Desktop/DPA_Project2/src/database/models.py)<br>[`src/pipeline.py`](file:///c:/Users/chait/Desktop/DPA_Project2/src/pipeline.py) | `data/exports/review_queue.csv` (0 open items; all 425 events passed validation). | **PASSED** |
| **6.1** | **Relational SQLite Database**<br>Normalized relational schema with 12 tables, tracking funds, share classes, sources, raw documents, events, runs, and flags. | [`src/database/models.py`](file:///c:/Users/chait/Desktop/DPA_Project2/src/database/models.py)<br>[`docs/schema.sql`](file:///c:/Users/chait/Desktop/DPA_Project2/docs/schema.sql) | [`docs/schema_diagram.md`](file:///c:/Users/chait/Desktop/DPA_Project2/docs/schema_diagram.md)<br>`data/fund_distributions_review.db` (5.95 MB portable database). | **PASSED** |
| **6.2** | **Cryptographic Byte Provenance**<br>Immutably links every stored distribution event to the raw source document bytes and SHA-256 hash. | [`src/database/models.py`](file:///c:/Users/chait/Desktop/DPA_Project2/src/database/models.py)<br>[`src/pipeline.py`](file:///c:/Users/chait/Desktop/DPA_Project2/src/pipeline.py) | `data/exports/raw_document.csv` (655 documents)<br>`data/exports/event_evidence.csv` (429 links). | **PASSED** |
| **6.3** | **Database Export Tools**<br>Exports complete relational database to individual CSVs and multi-tab Excel workbook. | [`src/database/export_db.py`](file:///c:/Users/chait/Desktop/DPA_Project2/src/database/export_db.py) | `data/exports/fund_distributions_complete_export.xlsx`<br>`data/exports/*.csv`. | **PASSED** |
| **7.1** | **Comprehensive Gold Set**<br>Human-verified benchmark: $\ge 300$ declared events, $\ge 50$ funds, $\ge 24$ months, with `NOT_DECLARED` rows and evidence URLs. | [`config/gold_set.csv`](file:///c:/Users/chait/Desktop/DPA_Project2/config/gold_set.csv)<br>[`docs/GOLD_SET_GUIDE.md`](file:///c:/Users/chait/Desktop/DPA_Project2/docs/GOLD_SET_GUIDE.md) | 439 rows (361 DECLARED, 78 NOT_DECLARED), 50 funds, 24 months.<br>Evidence in [`docs/gold_set_evidence/`](file:///c:/Users/chait/Desktop/DPA_Project2/docs/gold_set_evidence/). | **PASSED** |
| **7.2** | **Gold Set Evaluator Tooling**<br>Runs the live detector on gold windows to measure recall, precision, false negatives, and extraction accuracy. | [`src/validators/gold_set_evaluator.py`](file:///c:/Users/chait/Desktop/DPA_Project2/src/validators/gold_set_evaluator.py) | `quality/gold_set_evaluation.json`<br>Precision: **100.0%**, Recall: **74.52%** (100% on automated). | **PASSED** |
| **8.1** | **Automated Compliance & Ethics**<br>Mandatory contact User-Agent, robots.txt caching, SEC $\le 10$ req/s throttle, 2.5s domain delay, zero anti-bot bypass. | [`src/http_client.py`](file:///c:/Users/chait/Desktop/DPA_Project2/src/http_client.py)<br>[`src/rate_limiter.py`](file:///c:/Users/chait/Desktop/DPA_Project2/src/rate_limiter.py) | [`docs/COMPLIANCE.md`](file:///c:/Users/chait/Desktop/DPA_Project2/docs/COMPLIANCE.md) (0 `PENDING_REVIEW` items). | **PASSED** |
| **9.1** | **Detection & Audit Reports**<br>Detailed reports on hit rates, failure reasons, route mix, coverage by sponsor, and resource costs. | [`src/reports/detection_report.py`](file:///c:/Users/chait/Desktop/DPA_Project2/src/reports/detection_report.py) | [`quality/detection_report.md`](file:///c:/Users/chait/Desktop/DPA_Project2/quality/detection_report.md)<br>[`quality/detection_report.json`](file:///c:/Users/chait/Desktop/DPA_Project2/quality/detection_report.json). | **PASSED** |
| **10.1**| **Executive Memo & Deliverables**<br>Synthesized final memo containing verified performance metrics, root-cause analyses, cost models, and recommendations. | [`docs/FINAL_MEMO.md`](file:///c:/Users/chait/Desktop/DPA_Project2/docs/FINAL_MEMO.md) | Complete 15-section final memo covering all empirical results. | **PASSED** |

---

## 2. Quick Verification Command Summary

```bash
# 1. Run full offline test suite
python -m pytest -q

# 2. Check code style and formatting
python -m ruff check src tests
python -m black --check src tests

# 3. Validate gold set syntax and schema
python -m src.validators.gold_set_evaluator --validate-only

# 4. Generate detection report from stored database runs
python -m src.reports.detection_report

# 5. Run data quality audit across stored events
python -m src.validators.run_dq_audit
```
