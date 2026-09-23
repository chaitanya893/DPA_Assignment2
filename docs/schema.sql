-- ============================================================================
-- DPA Assignment 2: Relational Database Schema (PostgreSQL DDL)
-- Phase 3: Database Design & Storage Layer
--
-- Strict Implementation of Assignment 2 Specifications:
-- 1. fund_master (Fund level master metadata)
-- 2. share_class (Class level identifiers and cadence)
-- 3. source_registry (Source metadata, tier, parser versions)
-- 4. crawl_log (Every crawl attempt, status, hash, duration)
-- 5. raw_document (Immutable copies with SHA-256 and retrieval timestamp)
-- 6. distribution_event (Ex-date keyed events with idempotency & versioning)
-- 7. distribution_component (Granular tax component child rows)
-- 8. event_evidence (Provenance links from events to raw documents)
-- 9. dq_flag (Data quality flags, severity, resolution status)
-- ============================================================================

-- Extensions (for UUID support in PostgreSQL)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ----------------------------------------------------------------------------
-- 1. fund_master: Master fund entity record
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fund_master (
    fund_id VARCHAR(64) PRIMARY KEY,
    fund_name VARCHAR(255) NOT NULL,
    fund_family VARCHAR(128) NOT NULL,
    country VARCHAR(8) NOT NULL CHECK (country IN ('US', 'CA')),
    fund_type VARCHAR(32) NOT NULL CHECK (fund_type IN ('ETF', 'MUTUAL_FUND', 'CLOSED_END_FUND')),
    cik VARCHAR(16),
    sedar_id VARCHAR(32),
    inception_date DATE,
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_fund_master_family ON fund_master(fund_family);
CREATE INDEX IF NOT EXISTS idx_fund_master_country ON fund_master(country);

-- ----------------------------------------------------------------------------
-- 2. share_class: Share class level identifiers and distribution cadence
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS share_class (
    class_id VARCHAR(64) PRIMARY KEY,
    fund_id VARCHAR(64) NOT NULL REFERENCES fund_master(fund_id) ON DELETE CASCADE,
    ticker VARCHAR(16),
    cusip VARCHAR(16),
    isin VARCHAR(20),
    sec_series_id VARCHAR(32),
    sec_class_id VARCHAR(32),
    fundserv_code VARCHAR(32),
    currency VARCHAR(8) NOT NULL CHECK (currency IN ('USD', 'CAD')),
    expected_frequency VARCHAR(32) NOT NULL CHECK (expected_frequency IN ('DAILY', 'MONTHLY', 'QUARTERLY', 'SEMI_ANNUAL', 'ANNUAL', 'IRREGULAR')),
    is_monthly_payer BOOLEAN NOT NULL DEFAULT FALSE,
    is_etf BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_share_class_ticker ON share_class(ticker);
CREATE INDEX IF NOT EXISTS idx_share_class_fundserv ON share_class(fundserv_code);
CREATE INDEX IF NOT EXISTS idx_share_class_fund_id ON share_class(fund_id);

-- ----------------------------------------------------------------------------
-- 3. source_registry: Source endpoint definitions and metadata
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS source_registry (
    source_id VARCHAR(64) PRIMARY KEY,
    source_name VARCHAR(255) NOT NULL,
    source_tier INT NOT NULL CHECK (source_tier IN (1, 2, 3)),
    url_pattern VARCHAR(512) NOT NULL,
    parser_version VARCHAR(32) NOT NULL DEFAULT '1.0.0',
    robots_status VARCHAR(32) NOT NULL DEFAULT 'ALLOWED',
    tos_status VARCHAR(32) NOT NULL DEFAULT 'COMPLIANT',
    last_success TIMESTAMP WITH TIME ZONE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_source_registry_tier ON source_registry(source_tier);

-- ----------------------------------------------------------------------------
-- 4. crawl_log: Comprehensive audit trail of every acquisition attempt
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS crawl_log (
    log_id VARCHAR(64) PRIMARY KEY,
    source_id VARCHAR(64) NOT NULL REFERENCES source_registry(source_id),
    fund_id VARCHAR(64) REFERENCES fund_master(fund_id),
    target_url TEXT NOT NULL,
    retrieved_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    http_status INT,
    content_sha256 VARCHAR(64),
    outcome VARCHAR(32) NOT NULL CHECK (outcome IN ('SUCCESS', 'FAILURE', 'TIMEOUT', 'BLOCKED_403', 'RATE_LIMITED_429', 'REDIRECTED', 'EMPTY')),
    duration_seconds NUMERIC(8, 4) NOT NULL DEFAULT 0.0,
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_crawl_log_source ON crawl_log(source_id);
CREATE INDEX IF NOT EXISTS idx_crawl_log_fund ON crawl_log(fund_id);
CREATE INDEX IF NOT EXISTS idx_crawl_log_retrieved_at ON crawl_log(retrieved_at);

-- ----------------------------------------------------------------------------
-- 5. raw_document: Immutable storage of raw source artifacts
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw_document (
    doc_id VARCHAR(64) PRIMARY KEY,
    sha256 VARCHAR(64) UNIQUE NOT NULL,
    source_id VARCHAR(64) NOT NULL REFERENCES source_registry(source_id),
    source_url TEXT NOT NULL,
    retrieved_at TIMESTAMP WITH TIME ZONE NOT NULL,
    content_type VARCHAR(64) NOT NULL,
    byte_size INT NOT NULL,
    raw_text TEXT,
    storage_path VARCHAR(512),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_raw_doc_sha256 ON raw_document(sha256);
CREATE INDEX IF NOT EXISTS idx_raw_doc_source ON raw_document(source_id);

-- ----------------------------------------------------------------------------
-- 6. distribution_event: Master distribution event table with versioning & natural key
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS distribution_event (
    event_id VARCHAR(64) PRIMARY KEY,
    class_id VARCHAR(64) NOT NULL REFERENCES share_class(class_id),
    fund_id VARCHAR(64) NOT NULL REFERENCES fund_master(fund_id),
    ex_date DATE NOT NULL,
    record_date DATE,
    payable_date DATE,
    declaration_date DATE,
    currency VARCHAR(8) NOT NULL CHECK (currency IN ('USD', 'CAD')),
    gross_amount NUMERIC(12, 6) NOT NULL,
    distribution_type VARCHAR(64) NOT NULL DEFAULT 'Income',
    estimated_or_final VARCHAR(16) NOT NULL CHECK (estimated_or_final IN ('ESTIMATED', 'FINAL')),
    version INT NOT NULL DEFAULT 1,
    is_superseded BOOLEAN NOT NULL DEFAULT FALSE,
    superseded_by VARCHAR(64) REFERENCES distribution_event(event_id),
    extraction_route VARCHAR(32) NOT NULL CHECK (extraction_route IN ('API', 'HTML_TABLE', 'PDF', 'FILING', 'MANUAL')),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    
    -- Natural Key constraint preventing duplicate insertion on idempotent re-runs
    CONSTRAINT uq_event_natural_key UNIQUE (class_id, ex_date, estimated_or_final, version)
);

CREATE INDEX IF NOT EXISTS idx_event_class_ex ON distribution_event(class_id, ex_date);
CREATE INDEX IF NOT EXISTS idx_event_fund_id ON distribution_event(fund_id);
CREATE INDEX IF NOT EXISTS idx_event_ex_date ON distribution_event(ex_date);
CREATE INDEX IF NOT EXISTS idx_event_is_superseded ON distribution_event(is_superseded);

-- ----------------------------------------------------------------------------
-- 7. distribution_component: Granular child tax components
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS distribution_component (
    component_id VARCHAR(64) PRIMARY KEY,
    event_id VARCHAR(64) NOT NULL REFERENCES distribution_event(event_id) ON DELETE CASCADE,
    component_type VARCHAR(64) NOT NULL,
    component_name VARCHAR(128) NOT NULL,
    amount NUMERIC(12, 6) NOT NULL,
    percentage NUMERIC(6, 3),
    is_tax_reallocated BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_component_event_id ON distribution_component(event_id);
CREATE INDEX IF NOT EXISTS idx_component_type ON distribution_component(component_type);

-- ----------------------------------------------------------------------------
-- 8. event_evidence: Direct provenance links between events and raw source documents
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS event_evidence (
    evidence_id VARCHAR(64) PRIMARY KEY,
    event_id VARCHAR(64) NOT NULL REFERENCES distribution_event(event_id) ON DELETE CASCADE,
    doc_id VARCHAR(64) NOT NULL REFERENCES raw_document(doc_id),
    source_tier INT NOT NULL CHECK (source_tier IN (1, 2, 3)),
    locator_or_snippet TEXT NOT NULL,
    confidence NUMERIC(4, 3) NOT NULL DEFAULT 1.0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_evidence_event_id ON event_evidence(event_id);
CREATE INDEX IF NOT EXISTS idx_evidence_doc_id ON event_evidence(doc_id);

-- ----------------------------------------------------------------------------
-- 9. dq_flag: Data quality audit flags, severity levels, and resolution status
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dq_flag (
    flag_id VARCHAR(64) PRIMARY KEY,
    event_id VARCHAR(64) REFERENCES distribution_event(event_id) ON DELETE CASCADE,
    fund_id VARCHAR(64) NOT NULL REFERENCES fund_master(fund_id),
    rule_name VARCHAR(64) NOT NULL,
    severity VARCHAR(16) NOT NULL CHECK (severity IN ('INFO', 'WARNING', 'CRITICAL')),
    message TEXT NOT NULL,
    resolution_status VARCHAR(32) NOT NULL DEFAULT 'OPEN' CHECK (resolution_status IN ('OPEN', 'REVIEWED', 'SUPPRESSED', 'RESOLVED')),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_dq_flag_event ON dq_flag(event_id);
CREATE INDEX IF NOT EXISTS idx_dq_flag_severity ON dq_flag(severity);
CREATE INDEX IF NOT EXISTS idx_dq_flag_fund ON dq_flag(fund_id);
