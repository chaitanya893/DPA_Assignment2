# quality/

Current reports produced by the pipeline:

- `detection_report.{json,md}` - `python -m src.reports.detection_report` (hit rate, UNKNOWN reasons, route mix, extraction rate, coverage by fund family and country, cost per check, gold-set metrics).
- `gold_set_evaluation.json` - `python -m src.validators.gold_set_evaluator` (evaluates detector precision, recall, false negatives, and Layer B extraction accuracy against the verified gold set).
- `source_reachability.{md,csv}` - automated connectivity and robots.txt audit across all registered data sources.

## legacy/

The `quality/legacy/` directory contains superseded early results and exploratory reports produced before the audit and parser enhancements of September 2026. These files are kept for historical context only and must not be quoted.
