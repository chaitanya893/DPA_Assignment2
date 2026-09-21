# Controlled 6-Fund Multi-Tier Live Validation Report

**Date/Time:** 2026-09-20 19:39:27 UTC  
**Evaluation Window:** `2026-02-01` to `2026-02-28`  
**Scope:** Exactly 6 real funds evaluated with live multi-tier fallback architecture.  

---

## Executive Summary

- **Total Funds Tested:** 6
- **DECLARED:** 0
- **NOT_DECLARED:** 0
- **UNKNOWN:** 6
- **Tier 1 → Tier 2 Fallback:** PASS
- **Targeted Lookup Fallback:** PASS
- **Tier 3 Non-Authority Protection:** PASS

---

## Detailed Fund Validation Results

### `US_PIMCO_BOND` (BOND) — PIMCO Active Bond Exchange-Traded Fund
- **Country:** US
- **Final Status:** `UNKNOWN` (Confidence: `None`)
- **Source Attempt Trail:**

| Tier | Source ID | URL | Retrieval | Coverage State | Evidence Found | Notes / Failure Reason |
|---|---|---|---|---|---|---|
| `TIER_1_AUTHORITATIVE` | `sec_edgar_submissions` | [https://data.sec.gov/submissions/CIK0001...](https://data.sec.gov/submissions/CIK0001450011.json) | `SUCCESS` | `UNAVAILABLE` | `False` | SEC submissions API error: RETRIEVAL_FAILED |
| `TIER_2_PRIMARY_UNSTRUCTURED` | `official_fund_sponsor_portal` | [https://www.pimco.com/en-us/investments/...](https://www.pimco.com/en-us/investments/etf/active-bond-etf/bond) | `SUCCESS` | `UNAVAILABLE` | `False` | Sponsor portal retrieval failure: RETRIEVAL_FAILED |
| `TIER_2_PRIMARY_UNSTRUCTURED` | `targeted_secondary_lookup` | [targeted://official_secondary_candidates...](targeted://official_secondary_candidates) | `FAILED` | `PARTIAL` | `False` | No targeted secondary candidate URLs configured. |
| `TIER_3_CORROBORATION` | `public_market_data_corroboration` | [https://finance.yahoo.com/quote/BOND...](https://finance.yahoo.com/quote/BOND) | `SUCCESS` | `PARTIAL` | `False` | Corroboration only. Never sole evidence for DECLARED or NOT_ |

### `CA_BMO_ZCN` (ZCN) — BMO S&P/TSX Capped Composite Index ETF
- **Country:** CA
- **Final Status:** `UNKNOWN` (Confidence: `None`)
- **Source Attempt Trail:**

| Tier | Source ID | URL | Retrieval | Coverage State | Evidence Found | Notes / Failure Reason |
|---|---|---|---|---|---|---|
| `TIER_2_PRIMARY_UNSTRUCTURED` | `official_fund_sponsor_portal` | [https://www.bmogam.com/ca-en/investors/i...](https://www.bmogam.com/ca-en/investors/investment-solutions/etfs/bmo-sptsx-capped-composite-index-etf-zcn/) | `SUCCESS` | `UNAVAILABLE` | `False` | Sponsor portal retrieval failure: RETRIEVAL_FAILED |
| `TIER_2_PRIMARY_UNSTRUCTURED` | `targeted_secondary_lookup` | [targeted://official_secondary_candidates...](targeted://official_secondary_candidates) | `FAILED` | `PARTIAL` | `False` | No targeted secondary candidate URLs configured. |
| `TIER_3_CORROBORATION` | `public_market_data_corroboration` | [https://finance.yahoo.com/quote/ZCN...](https://finance.yahoo.com/quote/ZCN) | `SUCCESS` | `PARTIAL` | `False` | Corroboration only. Never sole evidence for DECLARED or NOT_ |

### `CA_VANGUARD_VCN` (VCN) — Vanguard FTSE Canada All Cap Index ETF
- **Country:** CA
- **Final Status:** `UNKNOWN` (Confidence: `None`)
- **Source Attempt Trail:**

| Tier | Source ID | URL | Retrieval | Coverage State | Evidence Found | Notes / Failure Reason |
|---|---|---|---|---|---|---|
| `TIER_2_PRIMARY_UNSTRUCTURED` | `official_fund_sponsor_portal` | [https://www.vanguard.ca/en/investor/prod...](https://www.vanguard.ca/en/investor/products/products-group/etfs/VCN) | `SUCCESS` | `UNAVAILABLE` | `False` | Sponsor portal retrieval failure: RETRIEVAL_FAILED |
| `TIER_2_PRIMARY_UNSTRUCTURED` | `targeted_secondary_lookup` | [targeted://official_secondary_candidates...](targeted://official_secondary_candidates) | `FAILED` | `PARTIAL` | `False` | No targeted secondary candidate URLs configured. |
| `TIER_3_CORROBORATION` | `public_market_data_corroboration` | [https://finance.yahoo.com/quote/VCN...](https://finance.yahoo.com/quote/VCN) | `SUCCESS` | `PARTIAL` | `False` | Corroboration only. Never sole evidence for DECLARED or NOT_ |

### `US_ISHARES_AGG` (AGG) — iShares Core U.S. Aggregate Bond ETF
- **Country:** US
- **Final Status:** `UNKNOWN` (Confidence: `None`)
- **Source Attempt Trail:**

| Tier | Source ID | URL | Retrieval | Coverage State | Evidence Found | Notes / Failure Reason |
|---|---|---|---|---|---|---|
| `TIER_1_AUTHORITATIVE` | `sec_edgar_submissions` | [https://data.sec.gov/submissions/CIK0001...](https://data.sec.gov/submissions/CIK0001100663.json) | `SUCCESS` | `UNAVAILABLE` | `False` | SEC submissions API error: RETRIEVAL_FAILED |
| `TIER_2_PRIMARY_UNSTRUCTURED` | `official_fund_sponsor_portal` | [https://www.ishares.com/us/products/2394...](https://www.ishares.com/us/products/239458/) | `SUCCESS` | `UNAVAILABLE` | `False` | Sponsor portal retrieval failure: RETRIEVAL_FAILED |
| `TIER_2_PRIMARY_UNSTRUCTURED` | `targeted_secondary_lookup` | [targeted://official_secondary_candidates...](targeted://official_secondary_candidates) | `FAILED` | `PARTIAL` | `False` | No targeted secondary candidate URLs configured. |
| `TIER_3_CORROBORATION` | `public_market_data_corroboration` | [https://finance.yahoo.com/quote/AGG...](https://finance.yahoo.com/quote/AGG) | `SUCCESS` | `PARTIAL` | `False` | Corroboration only. Never sole evidence for DECLARED or NOT_ |

### `US_VANGUARD_VTI` (VTI) — Vanguard Total Stock Market Index Fund ETF
- **Country:** US
- **Final Status:** `UNKNOWN` (Confidence: `None`)
- **Source Attempt Trail:**

| Tier | Source ID | URL | Retrieval | Coverage State | Evidence Found | Notes / Failure Reason |
|---|---|---|---|---|---|---|
| `TIER_1_AUTHORITATIVE` | `sec_edgar_submissions` | [https://data.sec.gov/submissions/CIK0000...](https://data.sec.gov/submissions/CIK0000036405.json) | `SUCCESS` | `UNAVAILABLE` | `False` | SEC submissions API error: RETRIEVAL_FAILED |
| `TIER_2_PRIMARY_UNSTRUCTURED` | `official_fund_sponsor_portal` | [https://investor.vanguard.com/investment...](https://investor.vanguard.com/investment-products/etfs/profile/vti) | `SUCCESS` | `PARTIAL` | `False` | Sponsor HTML retrieved but lacks verifiable distribution sch |
| `TIER_2_PRIMARY_UNSTRUCTURED` | `targeted_secondary_lookup` | [targeted://official_secondary_candidates...](targeted://official_secondary_candidates) | `FAILED` | `PARTIAL` | `False` | No targeted secondary candidate URLs configured. |
| `TIER_3_CORROBORATION` | `public_market_data_corroboration` | [https://finance.yahoo.com/quote/VTI...](https://finance.yahoo.com/quote/VTI) | `SUCCESS` | `PARTIAL` | `False` | Corroboration only. Never sole evidence for DECLARED or NOT_ |

### `US_SPDR_SPYD` (SPYD) — SPDR S&P Dividend ETF
- **Country:** US
- **Final Status:** `UNKNOWN` (Confidence: `None`)
- **Source Attempt Trail:**

| Tier | Source ID | URL | Retrieval | Coverage State | Evidence Found | Notes / Failure Reason |
|---|---|---|---|---|---|---|
| `TIER_1_AUTHORITATIVE` | `sec_edgar_submissions` | [https://data.sec.gov/submissions/CIK0001...](https://data.sec.gov/submissions/CIK0001064642.json) | `SUCCESS` | `UNAVAILABLE` | `False` | SEC submissions API error: RETRIEVAL_FAILED |
| `TIER_2_PRIMARY_UNSTRUCTURED` | `official_fund_sponsor_portal` | [https://www.ssga.com/us/en/intermediary/...](https://www.ssga.com/us/en/intermediary/etfs/funds/spdr-sp-dividend-etf-spyd) | `SUCCESS` | `UNAVAILABLE` | `False` | Sponsor portal retrieval failure: RETRIEVAL_FAILED |
| `TIER_2_PRIMARY_UNSTRUCTURED` | `targeted_secondary_lookup` | [targeted://official_secondary_candidates...](targeted://official_secondary_candidates) | `FAILED` | `PARTIAL` | `False` | No targeted secondary candidate URLs configured. |
| `TIER_3_CORROBORATION` | `public_market_data_corroboration` | [https://finance.yahoo.com/quote/SPYD...](https://finance.yahoo.com/quote/SPYD) | `SUCCESS` | `PARTIAL` | `False` | Corroboration only. Never sole evidence for DECLARED or NOT_ |
