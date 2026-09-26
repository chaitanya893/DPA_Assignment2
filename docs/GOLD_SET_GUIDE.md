# Building and Evaluating the Gold Set (PDF: "Build a gold set")

The gold set is the ground-truth benchmark used to evaluate the accuracy, precision, and recall of the Layer A atomic detector and the Layer B extraction engine.

---

## 1. Scope & Target Criteria

To provide a robust evaluation benchmark meeting all Assignment 2 requirements, the gold set satisfies:
- **≥ 300 DECLARED events** across **≥ 50 funds**, spanning **≥ 24 months** (October 2024 through September 2026).
- **NOT_DECLARED rows** included for non-distribution months (essential for measuring false positives and precision).
- **Primary sources only:** Direct verification against primary sponsor tables and official distribution schedules. Zero secondary market data feeds.

---

## 2. Dataset Structure: `config/gold_set.csv`

The gold set is stored in [../config/gold_set.csv](../config/gold_set.csv) with **439 total rows** (361 `DECLARED`, 78 `NOT_DECLARED`) across 50 funds:

| Column | Description |
|---|---|
| `gold_id` | Unique identifier (e.g. `g0001` - `g0439`) |
| `fund_id` | Standardized fund identifier matching `config/universe_100.json` |
| `window_start`, `window_end` | The evaluation date window (standard calendar month) |
| `expected_status` | Ground truth label: `DECLARED` or `NOT_DECLARED` |
| `ex_date` | Official ex-dividend date (YYYY-MM-DD) for declared events |
| `gross_amount` | Exact published per-share distribution amount |
| `currency` | Share class currency (`USD` or `CAD`) |
| `evidence_url` | Direct URL to the primary regulatory filing or sponsor document |
| `verified_by` | Name of the analyst who verified the distribution fact |
| `verified_at` | Timestamp of verification (YYYY-MM-DD) |
| `notes` | Contextual notes (e.g. quarterly schedule, capital gain breakdown) |

---

## 3. How This Gold Set Was Built

The gold set in `config/gold_set.csv` was compiled from primary source distribution tables:
- **Primary Sources Used:** SSGA official SPDR distributions Excel; Vanguard US profile pages; `vanguard.ca` distribution history tables; `rbcgam.com` Distributions tab; `ishares.com` Distributions table; `schwabassetmanagement.com` Distributions table. (Not SEC 19(a)/497 filings, not SPDR PDF press releases, not TMX bulletins.)
- **Verification Method:** 52 rows across all 6 source families were verified manually by the author against the source pages (evidence: [gold_set_evidence/](gold_set_evidence/)); the remaining rows were compiled from the same primary sources with AI assistance and are labelled as such in `config/gold_set.csv`.
- **Exclusions & Isolation:** Nothing was taken from the database or the detector output; `US_VANGUARD_VWO` March 2026 was excluded (no row on the source page, not confirmable from a second source).
- **Manual Proofs & Evidence Repository:**
  - Full manual verification workbook: [gold_set_evidence/gold_set_for_verification_Done.xlsx](gold_set_evidence/gold_set_for_verification_Done.xlsx).
  - Screenshot visual proofs catalog: [gold_set_evidence/Screenshots_of_Distributions_Manual_Proofs/](gold_set_evidence/Screenshots_of_Distributions_Manual_Proofs/).

---

## 4. Evaluation Tooling & Metrics

The gold set evaluator runs the real detector and extraction engine against live primary sources without feeding any gold labels to the system:

```bash
# 1. Validate file syntax, schema, fund count, and coverage without network calls
python -m src.validators.gold_set_evaluator --validate-only

# 2. Run the live evaluator across all 439 gold windows
python -m src.validators.gold_set_evaluator
```

### Empirical Results (from `quality/gold_set_evaluation.json`)
- **Detector Precision:** 100.0% (269 TP, 0 FP) — Target ≥ 99% MET
- **Detector Recall (Overall):** 74.52% (269 / 361 TP, 92 FN due to 403 blocks)
- **Detector Recall (Automated Sponsors):** 100.0% (269 / 269 TP) — Target ≥ 98% MET
- **Layer B Extraction Accuracy:** 98.88% (266 / 269 exact matches on ex-date and amount)
