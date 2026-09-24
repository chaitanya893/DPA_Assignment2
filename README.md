# Fund Distribution Detection and Extraction Engine (Assignment 2)

For each US or Canadian mutual fund or ETF in the universe, the engine decides whether a
dividend or capital gain distribution was declared in a given period (Layer A). When one was,
it extracts the full details (Layer B), validates them, and stores them with a link to the
source document they came from.

```
sweep scheduler (gap logic) -> Layer A detect_distribution -> DECLARED? -> Layer B route tree
    -> validation gate -> database (with raw source bytes)      \-> review_queue on failure
```

## Setup

```bash
python -m venv .venv && .venv\Scripts\activate          # Windows (use source .venv/bin/activate on Linux/macOS)
pip install -r requirements-dev.txt
set DETECTOR_CONTACT_EMAIL=you@yourcompany.com          # required: goes into the User-Agent (compliance)
```

Optional: `DATABASE_URL` (default SQLite at `data/fund_distributions.db`),
`DETECTOR_MIN_INTERVAL_SECONDS` (default 2.5 s per domain).

## Run

| What | Command |
|---|---|
| Full run: reference data + 24-month backfill for all 100 funds (needs internet) | `python -m src.database.populator` |
| Daily run: gap logic decides which funds and windows to check | `python -m src.sweep_scheduler` |
| See what the scheduler would do (no network) | `python -m src.sweep_scheduler --dry-run` |
| One fund, one window | `python run.py --fund-id US_VANGUARD_VTI` |
| Hit rate, UNKNOWN reasons, routes, coverage by family, cost per check | `python -m src.reports.detection_report` |
| Data quality audit over the database | `python -m src.validators.run_dq_audit` |
| Gold set check / accuracy | `python -m src.validators.gold_set_evaluator [--validate-only]` |
| Inspect the database | `python -m src.database.view_db --all` / `python -m src.view_extracted` |
| Export all tables to CSV + Excel | `python -m src.database.export_db` |
| Tests (offline) / lint | `python -m pytest` / `ruff check src tests` / `black --check src tests` |

## Repository

```
config/    universe_100.json, source_registry.yaml, sweep_config.yaml, gold_set.csv (to be filled by hand)
src/       detector.py (Layer A), strategies.py, extractor.py (Layer B), parsers/, pipeline.py,
           sweep_scheduler.py (backfill + gap logic), edgar_index.py, http_client.py (compliance),
           database/, validators/, reports/
tests/     offline pytest suite (no network; synthetic fixtures only)
docs/      DOMAIN_PRIMER.md, FINAL_MEMO.md, COMPLIANCE.md, GOLD_SET_GUIDE.md, schema.sql, schema_diagram.md
data/      fund_distributions.db, exports/, samples/  (data/raw/ = raw source bytes, git-ignored)
quality/   generated reports (see quality/README.md; older files there are legacy)
notebooks/ exploration only
scratch/   exploration scripts, not part of the deliverable
```

## Known limitations

- **Accuracy is not measured yet.** Recall and precision need the manually verified gold set
  (`docs/GOLD_SET_GUIDE.md`); live coverage needs a run with internet access. See `docs/FINAL_MEMO.md`.
- **The database holds reference data only** (100 funds, share classes, sources) until
  `python -m src.database.populator` is run with internet access. Earlier rows were loaded from
  hand-made JSON without source documents and were removed.
- **Terms-of-use decisions** per site still need to be recorded in `docs/COMPLIANCE.md`.
- **JavaScript-rendered sponsor pages** cannot prove coverage and yield UNKNOWN. There is no headless browser.
- **Scanned PDFs** go to manual review (no OCR).
- **Tax components** are stored only when the source publishes them; many events will have none.
- **Tier 3** market-data pages are disabled until their terms are reviewed.
