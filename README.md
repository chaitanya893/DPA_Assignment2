# Fund Distribution Detection and Extraction Engine (Assignment 2)

An end-to-end, multi-tier data pipeline that monitors US and Canadian mutual funds and ETFs, detects dividend and capital gain distribution declarations (Layer A), extracts structured financial facts and tax breakdowns (Layer B), validates them against deterministic accounting rules, and persists them with cryptographic SHA-256 byte provenance.

```
+------------------+     +-------------------+     +------------------+     +--------------------+
| Sweep Scheduler  | --> |  Layer A Detector | --> | Layer B Extractor| --> | Validation Gate &  |
| (Gap/Cadence/Back|     |  (Multi-tier logic|     | (JSON/Table/PDF/ |     | SQLite Ingestion   |
|  fill logic)     |     |   DECLARED/NOT/UNK|     |  Filing route)   |     | (Review Queue)     |
+------------------+     +-------------------+     +------------------+     +--------------------+
```

---

## 1. Quick Review for a Reviewer (About 5 Minutes)

To quickly inspect and verify the repository's code, test suite, database, and evaluation artifacts:

```bash
# 1. Verify offline test suite (564 unit & integration tests pass in ~15s)
python -m pytest -q

# 2. Verify code quality and formatting
python -m ruff check src tests
python -m black --check src tests

# 3. Verify the gold set integrity (361 DECLARED, 78 NOT_DECLARED, 50 funds, 24 months)
python -m src.validators.gold_set_evaluator --validate-only

# 4. Inspect the validated database using the review SQLite database (5.95 MB)
python -m src.database.view_db --db-url sqlite:///data/fund_distributions_review.db --all

# 5. Run the data quality audit on the stored distributions
python -m src.validators.run_dq_audit --db-url sqlite:///data/fund_distributions_review.db
```

Key deliverable documents:
- **Comprehensive Final Memo:** [docs/FINAL_MEMO.md](docs/FINAL_MEMO.md)
- **Requirement Traceability Index:** [docs/REPORT_INDEX.md](docs/REPORT_INDEX.md)
- **Compliance & Terms of Use:** [docs/COMPLIANCE.md](docs/COMPLIANCE.md)
- **Gold Set Documentation & Manual Evidence:** [docs/GOLD_SET_GUIDE.md](docs/GOLD_SET_GUIDE.md) and [docs/gold_set_evidence/](docs/gold_set_evidence/)

---

## 2. Key Results & Verified Metrics

All figures below are directly reproducible from `quality/detection_report.json`, `quality/gold_set_evaluation.json`, and `data/exports/dq_audit_report.json`:

| Metric Category | Metric | Value | Target / Benchmark |
|---|---|---|---|
| **Universe & Volume** | Funds Covered | **100 funds** (60 US, 40 CA) | 100 funds |
| | Deduplicated Window Checks | **2,501 checks** (2,517 total runs) | 24-month backfill |
| | Market Events Stored | **425 events** | Verified primary events |
| **Layer A Detection** | Precision (0 False Positives) | **100.0%** (269 / 269) | $\ge 99.0\%$ |
| | Recall (Overall, 50 funds) | **74.52%** (269 / 361) | 92 FN due to HTTP 403 blocks |
| | Recall (Automated Sponsors) | **100.0%** (269 / 269) | **100.0%** on SPDR, Vanguard, RBC |
| **Layer B Extraction**| Extraction Accuracy | **98.88%** (266 / 269 exact) | High fidelity |
| | Zero-Intervention Extraction | **100.0%** (0 review queue items) | $\ge 90.0\%$ |
| **Data Quality Gate** | Pass Rate (0 Critical flags) | **98.97%** (10 warnings, 0 critical)| Clean audit |
| **Cost & Performance**| Average Compute Cost per Check| **$0.000173** (6.22s, 1.46 HTTP reqs)| Economical & throttled |

---

## 3. Setup & Environment

```bash
# Setup virtual environment
python -m venv .venv
.venv\Scripts\activate          # Windows PowerShell (or source .venv/bin/activate on Linux/macOS)
pip install -r requirements-dev.txt

# Set compliance environment variables (required for live crawling)
$env:DETECTOR_CONTACT_EMAIL = "you@yourcompany.com"
$env:DETECTOR_CONTACT_NAME = "Your Name"
```

---

## 4. Operational Commands

| Task | Command |
|---|---|
| **Full Run** (Backfill 100 funds for 24 months) | `python -m src.database.populator` |
| **Daily Sweep** (Gap logic re-checks) | `python -m src.sweep_scheduler` |
| **Scheduler Dry-Run** (Inspect sweep plan offline)| `python -m src.sweep_scheduler --dry-run` |
| **Targeted Fund Run** (Single fund check) | `python run.py --fund-id US_VANGUARD_VTI` |
| **Detection Report** (Hit rate, routes, cost) | `python -m src.reports.detection_report` |
| **Gold Set Evaluator** (Live benchmark against 439 gold rows) | `python -m src.validators.gold_set_evaluator` |
| **Data Quality Audit** (Validate all stored rows) | `python -m src.validators.run_dq_audit` |
| **Export All Tables** (CSV and Excel formats) | `python -m src.database.export_db` |

---

## 5. Repository Structure

```
config/       universe_100.json, source_registry.yaml, sweep_config.yaml, gold_set.csv
src/          detector.py (Layer A), strategies.py, extractor.py (Layer B), parsers/,
              sweep_scheduler.py, pipeline.py, http_client.py, database/, validators/, reports/
tests/        564 offline unit and integration tests (zero network dependency)
docs/         FINAL_MEMO.md, REPORT_INDEX.md, COMPLIANCE.md, GOLD_SET_GUIDE.md,
              DOMAIN_PRIMER.md, schema.sql, schema_diagram.md, gold_set_evidence/
data/         fund_distributions.db, fund_distributions_review.db, exports/ (CSV + Excel)
quality/      detection_report.json, detection_report.md, gold_set_evaluation.json, legacy/
```

---

## 6. Known Limitations & Edge Cases

1. **Anti-Bot Defenses (HTTP 403):** Certain sponsors (BlackRock iShares, Charles Schwab, Invesco, PIMCO, Mackenzie, Global X Canada) respond with HTTP 403. Under our zero-circumvention compliance policy, these are recorded as `UNKNOWN` rather than bypassed.
2. **Dynamic JavaScript SPAs:** Fund families without server-rendered tables (Fidelity, TD AM) return empty HTML shells to HTTP parsers.
3. **Historical Publication Depth:** Vanguard US investor profile pages maintain active history for ~18 months (March 2025 onward). Earlier windows return `UNKNOWN`.
4. **Scanned PDF Documents:** Bitmap PDFs without a text layer route to manual review rather than relying on unverified OCR.
