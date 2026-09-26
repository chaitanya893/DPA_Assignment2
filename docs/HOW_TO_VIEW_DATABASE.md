# How to view the database

The full working database (data/fund_distributions.db, about 233 MB) is too large for GitHub.
The repository therefore contains two reviewer copies:

- data/fund_distributions_review.db (about 6 MB): every table and every row. Only the raw page text column (raw_document.raw_text) is emptied; each source document keeps its URL, sha256 and retrieval time. The raw bytes are kept locally in data/raw/.
- data/exports/: the same tables as CSV files, plus one Excel workbook with one sheet per table.

## Option 1 - Excel (easiest, nothing to install)

Open data/exports/fund_distributions_complete_export.xlsx. Each table is a separate sheet
(fund_master, share_class, distribution_event, distribution_component, event_evidence,
raw_document, detection_run, crawl_log, dq_flag, review_queue, fund_detection_state, source_registry).

## Option 2 - DB Browser for SQLite (free app, Windows and macOS)

1. Install it from https://sqlitebrowser.org
2. Open data/fund_distributions_review.db
3. "Browse Data" tab: choose a table (for example distribution_event) to see all rows.
4. "Execute SQL" tab: run the example queries below.

## Option 3 - Terminal (sqlite3 is pre-installed on macOS)

```bash
sqlite3 data/fund_distributions_review.db
```

## Example queries

```sql
-- Number of stored distributions per fund
SELECT fund_id, COUNT(*) AS events FROM distribution_event GROUP BY fund_id ORDER BY events DESC;

-- All distributions of one fund
SELECT ex_date, gross_amount, record_date, payable_date, extraction_route
FROM distribution_event WHERE fund_id = 'US_SPDR_XLF' ORDER BY ex_date;

-- Detection result for every month of one fund
SELECT window_start, window_end, status, unknown_reason
FROM detection_run WHERE fund_id = 'CA_VANGUARD_VAB' ORDER BY window_start;

-- Provenance: the source document behind each event
SELECT e.fund_id, e.ex_date, e.gross_amount, r.source_url, r.sha256, r.retrieved_at
FROM distribution_event e
JOIN event_evidence v ON v.event_id = e.event_id
JOIN raw_document r ON r.doc_id = v.doc_id
LIMIT 20;

-- Status counts over all funds and months
SELECT status, COUNT(*) FROM detection_run GROUP BY status;
```

## Main tables

| Table | What it holds |
|---|---|
| fund_master / share_class | The 100 funds and their identifiers (ticker, CIK, FundServ code, currency) |
| distribution_event | One row per share class per ex-date: dates, amount, currency, route, version |
| distribution_component | Tax components when the source publishes them |
| event_evidence / raw_document | Link from every event to the stored source document (URL, sha256) |
| detection_run | Every Layer A check: fund, month, status, reason, cost |
| crawl_log | Every HTTP attempt and its outcome (for example 403 or robots.txt refusal) |
| dq_flag / review_queue | Validation flags and items sent to manual review |
