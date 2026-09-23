# Relational Database Schema Diagram (Phase 3)

Entity-Relationship architecture for the Fund Distribution Extraction Database.

```mermaid
erDiagram
    fund_master ||--o{ share_class : "has"
    fund_master ||--o{ crawl_log : "tracked_in"
    fund_master ||--o{ distribution_event : "distributes"
    fund_master ||--o{ dq_flag : "flagged_by"

    share_class ||--o{ distribution_event : "declares"

    source_registry ||--o{ crawl_log : "logs_attempt"
    source_registry ||--o{ raw_document : "provides"

    raw_document ||--o{ event_evidence : "supports"

    distribution_event ||--o{ distribution_component : "comprises"
    distribution_event ||--o{ event_evidence : "provenanced_by"
    distribution_event ||--o{ dq_flag : "audited_by"
    distribution_event ||--o| distribution_event : "superseded_by"

    fund_master {
        string fund_id PK
        string fund_name
        string fund_family
        string country
        string fund_type
        string cik
        string sedar_id
        date inception_date
        string status
    }

    share_class {
        string class_id PK
        string fund_id FK
        string ticker
        string cusip
        string isin
        string sec_series_id
        string sec_class_id
        string fundserv_code
        string currency
        string expected_frequency
        boolean is_monthly_payer
        boolean is_etf
    }

    source_registry {
        string source_id PK
        string source_name
        int source_tier
        string url_pattern
        string parser_version
        string robots_status
        string tos_status
        timestamp last_success
        boolean is_active
    }

    crawl_log {
        string log_id PK
        string source_id FK
        string fund_id FK
        string target_url
        timestamp retrieved_at
        int http_status
        string content_sha256
        string outcome
        float duration_seconds
        string error_message
    }

    raw_document {
        string doc_id PK
        string sha256 UK
        string source_id FK
        string source_url
        timestamp retrieved_at
        string content_type
        int byte_size
        string raw_text
        string storage_path
    }

    distribution_event {
        string event_id PK
        string class_id FK
        string fund_id FK
        date ex_date
        date record_date
        date payable_date
        date declaration_date
        string currency
        decimal gross_amount
        string distribution_type
        string estimated_or_final
        int version
        boolean is_superseded
        string superseded_by FK
        string extraction_route
    }

    distribution_component {
        string component_id PK
        string event_id FK
        string component_type
        string component_name
        decimal amount
        decimal percentage
        boolean is_tax_reallocated
    }

    event_evidence {
        string evidence_id PK
        string event_id FK
        string doc_id FK
        int source_tier
        string locator_or_snippet
        decimal confidence
    }

    dq_flag {
        string flag_id PK
        string event_id FK
        string fund_id FK
        string rule_name
        string severity
        string message
        string resolution_status
    }
```

---

## 🔑 Natural Keys & Constraints Defense

1. **`distribution_event` Natural Key:**
   * Constraint: `UNIQUE(class_id, ex_date, estimated_or_final, version)`
   * **Defense:** Guarantees idempotency. Re-running the pipeline on the same date will not create duplicate rows for the same share class and ex-date.
2. **Amendments & Versioning:**
   * When a Canadian year-end T3/T5 reallocation or US estimated-to-final change occurs, the new record is inserted with `version = N + 1`, and the previous record is marked `is_superseded = TRUE` with `superseded_by = new_event_id`.
   * **Defense:** Never deletes or overwrites historic audit records.
3. **Provenance Integrity:**
   * `event_evidence` strictly maps `event_id -> doc_id`, ensuring every number in the database has a cryptographic raw document trail (`sha256`).
