# Detection report (generated from the database)

- **checks**: 525
- **funds_checked**: 20
## status_counts
- **UNKNOWN**: 153
- **DECLARED**: 205
- **NOT_DECLARED**: 167

- **hit_rate_pct**: 39.05
- **not_declared_rate_pct**: 31.81
- **unknown_rate_pct**: 29.14
## unknown_reasons
- **INSUFFICIENT_EVIDENCE**: 153

## unknown_reasons_by_family
- **Vanguard**: {'INSUFFICIENT_EVIDENCE': 153}

## route_taken_on_declared
- **API**: 205

- **route_logged_pct**: 100.0
- **extracted_without_manual_pct**: 100.0
- **events_stored**: 208
## events_by_route
- **API**: 208

## events_by_source_tier
- **2**: 208

- **events_with_published_components_pct**: 0.0
- **review_queue_open**: 6
## review_reasons
- **VALIDATION_FAILED**: 6

## coverage_by_family
- **Vanguard**: {'UNKNOWN': 153, 'DECLARED': 205, 'NOT_DECLARED': 167}

## coverage_by_country
- **US**: {'UNKNOWN': 153, 'DECLARED': 205, 'NOT_DECLARED': 167}

## crawl_outcomes_by_source
- **sec_edgar_submissions_api**: {'SUCCESS': 21}
- **official_fund_sponsor_page**: {'SUCCESS': 21}
- **sec_edgar_filings**: {'SUCCESS': 510}

- **avg_http_requests_per_check**: 1.05
- **avg_kb_per_check**: 342.4
- **avg_seconds_per_check**: 2.75
- **usd_per_compute_hour_assumed**: 0.1
- **avg_compute_cost_usd_per_check**: 7.6e-05
- **gold_set**: not evaluated yet (python -m src.validators.gold_set_evaluator)
