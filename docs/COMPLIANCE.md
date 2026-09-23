# 🛡️ REGULATORY COMPLIANCE, ETHICAL SCRAPING & AUDITABILITY POLICY

**Document Version:** 1.0.0  
**Effective Date:** September 2026  
**Audience:** Internal Data Engineering, Compliance Officers, Legal Counsel  

---

## 1. SEC EDGAR Ingestion Compliance (US Regulated Feeds)

All automated network interactions with the **U.S. Securities and Exchange Commission (SEC) EDGAR** systems adhere strictly to the **SEC EDGAR Access Policy**:

1. **User-Agent Declarations:**
   - Every request to `data.sec.gov` or `www.sec.gov` declares a structured, compliant `User-Agent` header in the mandatory format:
     `User-Agent: Sample Company Name AdminContact@samplecompany.com`
   - Anonymous or generic user-agents are rejected at the client level.
2. **Rate Limiting & Backoff:**
   - Strictly throttled to a maximum of **10 requests per second** across all worker threads.
   - Exponential backoff with jitter is applied automatically on any HTTP 429 (Too Many Requests) or HTTP 503 response.

---

## 2. Robots.txt & Terms of Service (ToS) Compliance

1. **Robots.txt Adherence:**
   - The engine queries and caches `/robots.txt` from all Tier 2 fund sponsor domains (`vanguard.com`, `blackrock.com`, `bmogam.com`, `fidelity.com`, `schwab.com`, `invesco.com`, `pimco.com`).
   - Disallowed sub-paths (e.g. transactional account login areas) are strictly isolated from the crawler whitelist.
2. **Public Data Access Only:**
   - The engine accesses exclusively public, unauthenticated distribution schedules, regulatory press releases, and SEC/SEDAR filings.
   - Zero access or harvesting of user-authenticated, non-public, or personal data.

---

## 3. Cryptographic Provenance & Immutable Audit Trail

To satisfy internal audit and financial compliance mandates:
1. **SHA-256 Document Hashing:**
   - Every raw HTML page, regulatory PDF, or JSON payload ingested by the pipeline is immediately hashed using cryptographic `SHA-256`.
   - The hash digest acts as the unique primary key in the `raw_document` table.
2. **Evidence Linking (`event_evidence` Table):**
   - Every distribution record in `distribution_event` is bound to the exact `doc_id` and byte snippet from which it was extracted.
   - No distribution value can be inserted or updated without a verified evidence link.

---

## 4. Rate Limiting, Timeouts & Circuit Breaking

1. **Hard Timeouts:** Every network request has a strict 15-second hard connect and read timeout.
2. **Retries with Exponential Backoff:** Network failures are retried up to 3 times with exponential backoff ($2^n \times \text{base\_delay}$).
3. **Graceful Degradation (`UNKNOWN` Status):**
   - If an endpoint returns HTTP 403 (Forbidden), WAF challenge, or persistent 500 errors, the engine classifies the detection outcome as `UNKNOWN` rather than crashing, fabricating data, or retrying indefinitely.

---
*Maintained by the Compliance & Data Governance Operations Team.*
