# Final memo - Fund Distribution Detection and Extraction Engine (Assignment 2)

**Date:** 24 September 2026
**Status:** code complete and tested offline; live measurements pending (see section 6).

> PDF closing note: "a working pipeline that covers 60 percent of cases and documents the other
> 40 percent precisely is a better outcome than one claiming full coverage that nobody can verify."
> This memo only states numbers that the code can reproduce. Where a number needs a live run or the
> manually verified gold set, it says so.

## 1. What was built

| Deliverable (PDF) | Where | State |
|---|---|---|
| Domain primer | `docs/DOMAIN_PRIMER.md` | done |
| Layer A atomic detector | `src/detector.py`, `src/strategies.py` | done; accuracy to be measured against the gold set |
| Backfill and gap logic | `src/sweep_scheduler.py`, `config/sweep_config.yaml`, table `fund_detection_state` | done |
| Routing and extraction (Layer B) | `src/extractor.py`, `src/parsers/` | done (API, HTML table, PDF/Excel, filing, manual) |
| Validation gate + DQ report | `src/validators/`, `src/pipeline.py` | done |
| Database | `src/database/`, `docs/schema.sql`, `docs/schema_diagram.md` | schema done; 24-month history needs the live backfill |
| Gold set | `config/gold_set.csv`, `docs/GOLD_SET_GUIDE.md`, `src/validators/gold_set_evaluator.py` | tooling done; the 300 events must be verified by hand |
| Reports | `src/reports/detection_report.py` | done; filled by the live run |

One command runs the whole pipeline for the universe:

```bash
python -m src.database.populator        # reference data + 24-month backfill (Layer A -> B -> validation -> DB)
python -m src.sweep_scheduler           # daily run: gap logic decides what to re-check
python -m src.reports.detection_report  # hit rate, UNKNOWN reasons, routes, coverage, cost per check
```

## 2. Universe

100 funds in `config/universe_100.json`: 60 US and 40 Canadian, 81 ETFs and 19 mutual funds,
54 monthly payers, 15 fund families. All US funds have a CIK; Canadian funds have a ticker or a
FundServ code.

## 3. Layer A - how a window is decided

Strategies run cheapest first (PDF "Detection strategies"):

1. **Calendar expectation** - on/off cadence from the fund's frequency. A trigger only.
2. **Change detection** - sha256 of the fund's distribution page against the last stored hash.
3. **Filing index polling** - EDGAR daily form index for 497 / 497K / N-CSR / N-CEN / 8-K... filed by universe CIKs.
4. **Sources** - SEC EDGAR submissions and filing documents (Tier 1, US), TMX notices (Tier 1, CA),
   the fund's official distribution page or release (Tier 2). **Targeted lookup** runs only when these are inconclusive.

Synthesis rules:

- **DECLARED** needs Tier 1 or Tier 2 evidence whose declaration date, ex-date or publication date
  is in the window. Dates are only read next to their own label; a record or payable date alone
  never places a distribution in a window; a filing date is stored as a publication date, never as
  a declaration date. Tier 3 alone never decides anything (and Tier 3 is disabled until its terms of
  use are reviewed).
- **NOT_DECLARED** needs an applicable Tier 1/2 source that demonstrably covers the whole window
  (a distribution table that spans it, or a published full-year schedule) and no outage on any
  applicable Tier 1/2 source.
- **UNKNOWN** otherwise, with a reason (SOURCE_UNAVAILABLE, RETRIEVAL_FAILED, INCOMPLETE_SOURCE,
  CONFLICTING_EVIDENCE, INSUFFICIENT_EVIDENCE). A source that does not apply to a fund (SEC for a
  Canadian fund) is ignored instead of blocking an answer.
- **Confidence** is always a float in 0.0-1.0: Tier 1 = 1.00, Tier 2 with a labelled declaration or
  ex-date = 0.90, Tier 2 publication date only = 0.80, NOT_DECLARED Tier 2 coverage = 0.85, minus 0.05
  when another applicable source failed; UNKNOWN = 0.0.

## 4. Backfill and gap logic

Per fund: `expected_frequency`, `last_confirmed_event_date`, `last_checked_at`, `consecutive_unknowns`.
A lookback sweep over the last **N months** runs when the time since the last confirmed event
exceeds **1.5x** the expected interval (monthly 30 d, quarterly 91 d, semi-annual 182 d, annual 365 d),
when UNKNOWN came back **more than twice** in a row, at **month-end** (last 3 days) and throughout the
**year-end period** (1 Dec - 15 Jan). A new fund gets a **24-month backfill**. Sweeps are cut into
calendar months so every monthly payment is detected separately. On quiet days the fund only gets
a routine check, and that is skipped when it is off cadence, its page hash is unchanged and EDGAR
shows no new filing.

**Default N = 3 months**, configurable in `config/sweep_config.yaml`. Reasons: it covers one full
cycle of the quarterly payers that make up most of the non-monthly universe; sources publish late
and amend (Canadian year-end reallocations, estimated -> final capital gains), and a 3-month window
re-reads a restated month twice more after its first appearance; and it costs only 3 checks per
triggered fund, with page and index responses reused within a run.

## 5. Layer B, validation and database

Route decision tree per cited document: structured JSON -> HTML table -> PDF / Excel ->
filing / notice text -> manual review. The route taken is stored for every check
(`detection_run.route_taken`) and every event (`distribution_event.extraction_route`).

No LLM is used. Every extracted figure passes the deterministic checks **before** it is stored:
component sum (tolerance $0.0005, i.e. rounding of 4-6 decimal per-share figures), date order
(declaration <= ex <= record <= pay), currency = share class currency, amount > 0; NAV decline and
the 20%-of-NAV magnitude check when a NAV is supplied; cross-source variance when two sources report
the same event. CRITICAL failures go to `review_queue`, not to the database; WARNINGs are stored and
flagged. Checks without input data are reported as **skipped**, not as passes.

Database rules: natural key `(class_id, ex_date, distribution_category, estimated_or_final, version)`;
restatements append a version and supersede the old row; a different source disagreeing is recorded
next to the event and flagged, never silently resolved; every event links to the stored raw bytes
(`raw_document`, sha256) it came from; estimated and final rows coexist.

Tax components are stored only when the source publishes them. When a page shows only a total, the
event has `components_reported = false` and no component rows. Nothing is inferred.

## 6. Results

### Measured offline (reproducible now)

- `python -m pytest`: **539 tests pass** offline. Among them:
  - a 12-fund x 24-month matrix (288 cases) where the detector must return the right status for every month and Layer B the exact amount;
  - end-to-end pipeline tests: provenance, idempotent re-runs, the validation gate and the review queue;
  - gap-logic rule tests;
  - one regression test for each bug found in the audit.
- `ruff check` and `black --check` are clean for `src/` and `tests/`.

### Needs a live run (fill from `quality/detection_report.md`)

| Metric | Value |
|---|---|
| Checks run / funds covered | PENDING LIVE RUN |
| Hit rate (DECLARED share) / NOT_DECLARED / UNKNOWN | PENDING LIVE RUN |
| UNKNOWN reasons by source (where automation fails) | PENDING LIVE RUN |
| Route taken on DECLARED checks (automatable share) | PENDING LIVE RUN |
| Extracted without manual intervention (target >= 90 %) | PENDING LIVE RUN |
| Coverage by fund family and by country | PENDING LIVE RUN |
| Average HTTP requests, KB and seconds per check | PENDING LIVE RUN |
| Events stored, 24-month coverage per fund | PENDING LIVE RUN |

### Needs the gold set (fill from `quality/gold_set_evaluation.json`)

| Metric | Target | Value |
|---|---|---|
| Recall | >= 98 % | PENDING GOLD SET |
| Precision | >= 99 % | PENDING GOLD SET |
| False negative rate | | PENDING GOLD SET |
| Extraction accuracy (ex-date and amount exact) | | PENDING GOLD SET |

The previous version of this memo reported 100 % precision and recall and a 100 % DQ pass rate. Those
figures came from a script-generated gold set that the detector also read as evidence, and from
three DQ rules that never ran. They are withdrawn.

## 7. Where automation is expected to fail (to confirm with the live run)

- **Vanguard history depth (compliance limit).** The Vanguard profile page gives about
  18 months of distributions (from March 2025). A Vanguard advisor-site endpoint with full
  history since 2016 exists and passes robots.txt, but its Terms of Use restrict data to
  personal, noncommercial use and forbid derivative works without written approval.
  It is an undocumented internal endpoint, not a "documented API" (PDF Assignment 2, Phase 2 route tree), so it was not used. Effect:
  for the 20 Vanguard US funds, windows before March 2025 return UNKNOWN (reason:
  source history limit), not NOT_DECLARED. Verified on VTI: 25 monthly checks ->
  6 DECLARED, 12 NOT_DECLARED, 7 UNKNOWN (6 before coverage, 1 current month under the
  7-day rule). Fix path: written approval from Vanguard, or a licensed data feed.
- **JavaScript-rendered sponsor pages** (several large US and Canadian families): the HTML has no
  table, so the page cannot prove coverage and the result is UNKNOWN (INSUFFICIENT_EVIDENCE). Tier 1
  sources still work for US funds. Options: a documented JSON endpoint per family (API route), or a
  headless browser, which needs its own ToS review.
- **Blocks (403 / bot management)**: logged and returned as UNKNOWN. By policy there are no proxies or
  header tricks.
- **TMX endpoint**: `https://www.tmx.com/dividends/{ticker}` was not verified from this environment.
  If it does not serve notices, Canadian funds rely on the sponsor pages (Tier 2).
- **Scanned PDFs** without a text layer go to manual review (OCR with ocrmypdf/Tesseract is not wired in).
- **Tax character** is often published only in year-end T3/T5 or 1099 files, so many events will have
  `components_reported = false` until those files are ingested (stretch goal).
- **Multi-fund trust filings** on EDGAR: identity is checked against ticker, fund name and series ID,
  but the 19(a) wording varies by sponsor, so some notices will need review.

## 8. Cost of running the full universe

Cost per check comes from `detection_run` (requests, bytes, seconds) after the live run. Model:

- Daily routine checks cost about 2-3 requests (page hash + index); only triggered funds get full checks.
- Full checks per month ~= funds x (1 routine + triggered sweeps x N windows), with responses reused within a run.
- At 2.5 s per request per domain, throughput is limited by politeness per domain, not by compute.
  Parallelise across domains (fund families), not within a domain.
- Compute: `avg_seconds_per_check x checks x $/hour` (the report uses $0.10 / hour; change with `--usd-per-compute-hour`).
- Storage: raw documents (`avg_kb_per_check x checks`) on disk or object storage.
- People: the `review_queue` volume x minutes per item. This is expected to be the dominant cost and
  should be taken from the live run's `review_queue_open` and `extracted_without_manual_pct`.

No proxy services are budgeted: working around blocks is out of policy (PDF compliance rules).

## 9. Next steps

1. Record the terms-of-use decisions in `docs/COMPLIANCE.md`.
2. Set `DETECTOR_CONTACT_EMAIL`, run the 24-month backfill, then `python -m src.reports.detection_report`.
3. Build the 300-event gold set (`docs/GOLD_SET_GUIDE.md`), run the evaluator, then fill section 6.
4. Look at the UNKNOWN reasons per family and decide whether to add a family-specific JSON parser (API route).
