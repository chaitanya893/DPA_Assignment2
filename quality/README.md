# quality/

Reports produced by the pipeline:

- `detection_report.{json,md}` - `python -m src.reports.detection_report` (hit rate, UNKNOWN reasons,
  route mix, extraction rate, coverage by fund family and country, cost per check, gold-set metrics).
- `gold_set_evaluation.json` - `python -m src.validators.gold_set_evaluator`.

**Legacy files.** `phase1_*`, `*_live_test_results.json`, `layer_b_extracted_events.json`,
`multi_tier_6fund_live_validation.*`, `source_*_audit.json` and `universe_100_distribution_schedule.*`
were produced before the audit fixes of September 2026. Several of them were built from windows
chosen around already-known events and from the synthetic gold set, so their accuracy figures are
not valid measurements. They are kept for history only; do not quote them.
