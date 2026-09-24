# Compliance

What the code enforces, and what still needs a human decision.

## Enforced in code (`src/http_client.py`, `src/rate_limiter.py`)

| Rule (PDF) | How |
|---|---|
| Descriptive User-Agent with a contact email | `FundDistributionDetector/1.0 (+repo URL; <DETECTOR_CONTACT_EMAIL>)`. If no email is configured, **no request is sent**. The client never pretends to be a browser. |
| Respect robots.txt | robots.txt is fetched once per host and cached. Disallowed URL -> request refused, logged in `crawl_log`. 4xx robots.txt = no rules; 5xx or unreachable = host treated as disallowed (RFC 9309). |
| One request every 2-3 s per domain | 2.5 s minimum spacing per domain, shared by every client in the process (`DETECTOR_MIN_INTERVAL_SECONDS`). |
| Retries with exponential backoff and a hard timeout | 3 attempts, `1.5^n` s backoff plus jitter, 15 s timeout. 429/5xx retried; 4xx not. |
| If a source blocks you, log it and move on | Blocks and failures become `UNKNOWN` results and `crawl_log` rows. There is no retry-around, proxy rotation or header spoofing. |
| No accounts, no registration walls, no redistribution clauses | Only public pages and EDGAR are requested. |
| Secrets only in environment variables | `DETECTOR_CONTACT_EMAIL`, `DETECTOR_USER_AGENT`, `DATABASE_URL`. Nothing sensitive is committed. |

Tier 3 public market data pages (e.g. Yahoo Finance) are **disabled** by default
(`SourceOrchestrator(enable_tier3=False)`) because their terms of use have not been reviewed.

## Terms-of-use decisions still to record

The PDF asks for the decision, the date and the clause relied on, per site. These have not
been verified from inside the code and must be filled in by a person before a live run.

| Source | Used for | robots.txt | Terms of use decision | Date | Clause relied on |
|---|---|---|---|---|---|
| SEC EDGAR (`www.sec.gov`, `data.sec.gov`) | Tier 1: submissions, filings, daily index | checked per request | PENDING_REVIEW (SEC "Accessing EDGAR Data" fair-access policy: max 10 req/s, declared User-Agent) | | |
| TMX (`www.tmx.com`) | Tier 1 notices (Canada) | checked per request | PENDING_REVIEW | | |
| Vanguard US / Canada | Tier 2 distribution pages | checked per request | PENDING_REVIEW | | |
| BlackRock iShares US / Canada | Tier 2 | checked per request | PENDING_REVIEW | | |
| State Street SPDR, Schwab, Fidelity, Invesco, PIMCO | Tier 2 | checked per request | PENDING_REVIEW | | |
| BMO GAM, RBC GAM, TD AM, CI GAM, Global X, Mackenzie | Tier 2 | checked per request | PENDING_REVIEW | | |
| Yahoo Finance / other market data pages | Tier 3 corroboration | n/a | NOT USED (disabled) | | |

`source_registry.tos_status` is set to `PENDING_REVIEW` for every source until this table is
completed.

## Provenance

Every stored figure links (`event_evidence`) to a `raw_document` row holding the exact bytes the
source returned, their sha256 and the retrieval time. An event cannot be saved without one.
