# Phase 1 Layer A — SEC False Positive Elimination & Identity Validation Report

**Date:** September 21, 2026  
**Scope:** Assignment 2 — Phase 1 Layer A Detector Hardening  
**Target Module:** [`src/strategies.py`](file:///c:/Users/chait/Desktop/DPA_Project2/src/strategies.py) — `SECEdgarSubmissionsStrategy`  
**Test Suite Created:** [`tests/test_sec_false_positives.py`](file:///c:/Users/chait/Desktop/DPA_Project2/tests/test_sec_false_positives.py)  
**Live 100-Fund Scan Executed:** **NO** (*Strictly prohibited; proved exclusively via deterministic unit/regression test suite*)  
**Status of Existing 100-Fund Result Files:** **Intentionally preserved unchanged** pending authorized rerun.

---

## 1. Root Cause Analysis

During the Phase 1 audit of `quality/phase1_final_results.json`, three false positive `DECLARED` results were identified (`US_ISHARES_IVV`, `US_ISHARES_IWM`, `US_ISHARES_IEFA`), all stemming from a single SEC filing:  
`https://www.sec.gov/Archives/edgar/data/1100663/000119312526114192/d45638d497.htm`

### The Specific Bugs:
1. **Cross-Fund Identity Contamination:**
   * In SEC Statements of Additional Information (SAIs), funds are required to disclose trustee personal equity holdings across the entire fund complex.
   * In the SAI for `IVV` (iShares Core S&P 500 ETF), a *Trustee Equity Ownership Table* listed holdings in other funds (e.g., *"Madhav V. Rajan ... iShares Russell 2000 ETF Over $100,000"*, *"Jane D. Carlin ... iShares Core MSCI EAFE ETF $50,001-$100,000"*).
   * The previous identity check used an unconstrained substring match `fund.fund_name.lower() in clean_text.lower()`, erroneously binding IVV's revised SAI to `IWM` and `IEFA`.
2. **Tax Disclosure Boilerplate Matched as Operative Declaration:**
   * The SAI contained standard narrative explaining the pass-through taxation of qualified dividend income under the Internal Revenue Code (*"capital gain rates to the extent the Fund receives qualified dividend income..."*).
   * Overly permissive regex matching treated the phrase `"capital gain rates"` as an operative distribution announcement and assigned the revised SAI filing date (`2026-03-18`) as a dividend declaration date.

---

## 2. Exact Code Changes Implemented

All modifications were applied to [`src/strategies.py`](file:///c:/Users/chait/Desktop/DPA_Project2/src/strategies.py) inside `SECEdgarSubmissionsStrategy._inspect_filing_content`:

### A. Strict Fund Identity Validation Rule
To prevent cross-fund contamination from trustee tables, the identity validator now:
1. Strips non-operative trustee, officer, and compensation disclosure blocks (`r"(?:Trustee|Director|Officer)\s+(?:Equity\s+)?(?:Ownership|Holdings|Compensation)..."`) prior to checking fund identity.
2. Restricts fund name and ticker matching to the **authoritative Title / Cover / Header block** (first 12,000 characters of the filing) or verified SEC Series ID (`fund.sec_series_id`).
3. Rejects any filing where a fund name appears solely buried within trustee personal portfolio disclosures or general contract comparison tables.

### B. Strict Distribution Semantics Rule
To prevent narrative tax explanations and fee disclosures from triggering declarations:
1. Strips statutory tax narrative:
   * Qualified dividend income explanations (`"capital gain rates to the extent the fund receives qualified dividend income"`, `"corporate dividends received deduction"`).
   * IRC Section 852(b)(7) spillback tax description boilerplate.
   * 12b-1 distribution and service fee lines (`"12b-1 distribution fee"`).
   * Descriptive policy language without operative action (`"the fund intends to distribute"`, `"distributions, if any, will be declared"`).
2. Requires an **operative declaration action**:
   * **Rule 19a-1 Notice:** Form 19A-1 with `"source of distribution"` or `"section 19(a)"`.
   * **Board / Fund Declaration:** Explicit operative language (e.g. `"The Board of Trustees declared a distribution of $X per share payable on ... to shareholders of record on ..."`).

---

## 3. False Positives Eliminated

| Target Case | Original Flawed Status | Corrected Status | Reason for Rejection |
| :--- | :---: | :---: | :--- |
| `US_ISHARES_IVV` (Revised SAI) | `DECLARED` | **`UNKNOWN`** | Qualified dividend income tax explanation is non-operative tax narrative. |
| `US_ISHARES_IWM` (Trustee table) | `DECLARED` | **`UNKNOWN`** | Document belongs to IVV; IWM appeared only in trustee personal equity table. |
| `US_ISHARES_IEFA` (Trustee table) | `DECLARED` | **`UNKNOWN`** | Document belongs to IVV; IEFA appeared only in trustee personal equity table. |
| `US_VANGUARD_VTI` (12b-1 fee table) | False Positive Risk | **`UNKNOWN`** | 12b-1 distribution fee in fee table rejected as non-declaration. |

---

## 4. Test Suite & Verification Results

A dedicated deterministic regression test suite [`tests/test_sec_false_positives.py`](file:///c:/Users/chait/Desktop/DPA_Project2/tests/test_sec_false_positives.py) was constructed and executed with zero external network dependencies:

```
===========================================================================
                       DETERMINISTIC REGRESSION SUITE
===========================================================================
[PASS] test_ivv_revised_sai_tax_narrative_rejected
       -> IVV revised SAI tax narrative evaluated to UNKNOWN (0 false positives)
[PASS] test_iwm_cross_fund_contamination_via_trustee_holdings_rejected
       -> IVV SAI with IWM in trustee table evaluated to UNKNOWN (cross-fund rejected)
[PASS] test_iefa_cross_fund_contamination_via_trustee_holdings_rejected
       -> IVV SAI with IEFA in trustee table evaluated to UNKNOWN (cross-fund rejected)
[PASS] test_12b1_distribution_fee_rejected
       -> 12b-1 Distribution Fee in fee tables evaluated to UNKNOWN
[PASS] test_pimco_rule_19a1_remains_declared
       -> Real PIMCO Rule 19a-1 source notice remains recognized as DECLARED
[PASS] test_bmo_official_announcement_remains_declared
       -> Real BMO official press release remains recognized as DECLARED
[PASS] test_exhaustive_negative_schedule_remains_not_declared
       -> Verified complete negative schedule remains recognized as NOT_DECLARED
---------------------------------------------------------------------------
Total Targeted Regression Tests:  7 / 7 PASSED (100%)
===========================================================================
```

### Full Project Quality Gate Status:
* **Pytest Suite:** **95 / 95 PASSED** (0 failures, 0 errors in 2.51s).
* **Ruff Linter:** **0 errors / All checks passed**.
* **Black Formatter:** **28 files checked / 100% compliant**.

---

## 5. Summary & Next Steps

1. The underlying SEC matching bug and cross-fund contamination vulnerabilities have been eliminated with strict fund identity and operative distribution semantic rules.
2. All 7 regression cases and all 95 unit tests pass deterministically.
3. No live 100-fund scan was executed during this step, and the existing 100-fund report files remain untouched pending user authorization for the next live run.
