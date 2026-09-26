# Detection report (generated from the database)

- **checks**: 2501
- **total_check_executions**: 2517
- **funds_checked**: 100
## status_counts
- **NOT_DECLARED**: 294
- **DECLARED**: 404
- **UNKNOWN**: 1803

- **hit_rate_pct**: 16.15
- **not_declared_rate_pct**: 11.76
- **unknown_rate_pct**: 72.09
## unknown_reasons
- **INSUFFICIENT_EVIDENCE**: 603
- **RETRIEVAL_FAILED**: 1200

## unknown_reasons_by_family
- **BMO Global Asset Management**: {'RETRIEVAL_FAILED': 200}
- **BlackRock iShares**: {'RETRIEVAL_FAILED': 350}
- **BlackRock iShares Canada**: {'RETRIEVAL_FAILED': 200}
- **CI Global Asset Management**: {'RETRIEVAL_FAILED': 50, 'INSUFFICIENT_EVIDENCE': 25}
- **Charles Schwab**: {'RETRIEVAL_FAILED': 125}
- **Fidelity**: {'INSUFFICIENT_EVIDENCE': 175, 'RETRIEVAL_FAILED': 50}
- **Global X Investments Canada**: {'RETRIEVAL_FAILED': 75}
- **Invesco**: {'RETRIEVAL_FAILED': 75}
- **Mackenzie Investments**: {'RETRIEVAL_FAILED': 25}
- **PIMCO**: {'RETRIEVAL_FAILED': 50}
- **RBC Global Asset Management**: {'INSUFFICIENT_EVIDENCE': 75}
- **TD Asset Management**: {'INSUFFICIENT_EVIDENCE': 125}
- **Vanguard**: {'INSUFFICIENT_EVIDENCE': 148}
- **Vanguard Canada**: {'INSUFFICIENT_EVIDENCE': 55}

## route_taken_on_declared
- **PDF**: 98
- **API**: 247
- **HTML_TABLE**: 59

- **route_logged_pct**: 100.0
- **extracted_without_manual_pct**: 100.0
- **events_stored**: 425
## events_by_route
- **PDF**: 104
- **API**: 256
- **HTML_TABLE**: 65

## events_by_source_tier
- **2**: 425

- **events_with_published_components_pct**: 24.47
- **review_queue_open**: 0
## review_reasons

## coverage_by_family
- **BMO Global Asset Management**: {'UNKNOWN': 200}
- **BlackRock iShares**: {'UNKNOWN': 350}
- **BlackRock iShares Canada**: {'UNKNOWN': 200}
- **CI Global Asset Management**: {'UNKNOWN': 75}
- **Charles Schwab**: {'UNKNOWN': 125}
- **Fidelity**: {'UNKNOWN': 225}
- **Global X Investments Canada**: {'UNKNOWN': 75}
- **Invesco**: {'UNKNOWN': 75}
- **Mackenzie Investments**: {'UNKNOWN': 25}
- **PIMCO**: {'UNKNOWN': 50}
- **RBC Global Asset Management**: {'NOT_DECLARED': 2, 'DECLARED': 48, 'UNKNOWN': 75}
- **State Street SPDR**: {'NOT_DECLARED': 77, 'DECLARED': 98}
- **TD Asset Management**: {'UNKNOWN': 125}
- **Vanguard**: {'UNKNOWN': 148, 'DECLARED': 199, 'NOT_DECLARED': 154}
- **Vanguard Canada**: {'UNKNOWN': 55, 'DECLARED': 59, 'NOT_DECLARED': 61}

## coverage_by_country
- **CA**: {'UNKNOWN': 830, 'DECLARED': 107, 'NOT_DECLARED': 63}
- **US**: {'NOT_DECLARED': 231, 'DECLARED': 297, 'UNKNOWN': 973}

## crawl_outcomes_by_source
- **sec_edgar_submissions_api**: {'SUCCESS': 94}
- **official_fund_sponsor_page**: {'SUCCESS': 52, 'RETRIEVAL_FAILED': 60, 'INSUFFICIENT_EVIDENCE': 1}
- **sec_edgar_filings**: {'SUCCESS': 3382, 'SOURCE_UNAVAILABLE': 48, 'RETRIEVAL_FAILED': 9}
- **sedar_tmx_notices**: {'INSUFFICIENT_EVIDENCE': 35}

- **avg_http_requests_per_check**: 1.46
- **avg_kb_per_check**: 568.5
- **avg_seconds_per_check**: 6.22
- **usd_per_compute_hour_assumed**: 0.1
- **avg_compute_cost_usd_per_check**: 0.000173
## gold_set
- **precision_pct**: 100.0
- **recall_pct**: 74.52
- **false_negative_rate_pct**: 25.48
- **extraction_accuracy_pct**: 98.88

