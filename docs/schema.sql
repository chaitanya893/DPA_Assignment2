-- ============================================================================
-- DPA Assignment 2 - PostgreSQL DDL (generated from src/database/models.py)
--
-- Regenerate after any model change:
--   python -c "from sqlalchemy.schema import CreateTable; from sqlalchemy.dialects import postgresql; \
--     from src.database.models import Base; [print(CreateTable(t).compile(dialect=postgresql.dialect()),';') \
--     for t in Base.metadata.sorted_tables]"
--
-- Core tables (PDF Phase 3): fund_master, share_class, source_registry, crawl_log,
-- raw_document, distribution_event, distribution_component, event_evidence, dq_flag.
-- Added tables (justified in docs/schema_diagram.md): detection_run,
-- fund_detection_state, review_queue.
--
-- Non-negotiables:
--   Idempotency : UNIQUE (class_id, ex_date, distribution_category, estimated_or_final, version)
--   Amendments  : new version row + is_superseded / superseded_by on the old row, never UPDATE of figures
--   Provenance  : every distribution_event has >= 1 event_evidence row -> raw_document (exact bytes, sha256)
--   Estimated vs final : estimated_or_final is part of the key, so both rows coexist
-- ============================================================================

CREATE TABLE fund_master (
	fund_id VARCHAR(64) NOT NULL, 
	fund_name VARCHAR(255) NOT NULL, 
	fund_family VARCHAR(128) NOT NULL, 
	country VARCHAR(8) NOT NULL, 
	fund_type VARCHAR(32) NOT NULL, 
	cik VARCHAR(16), 
	sedar_id VARCHAR(32), 
	inception_date DATE, 
	status VARCHAR(32) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (fund_id)
);

CREATE INDEX ix_fund_master_fund_family ON fund_master (fund_family);
CREATE INDEX ix_fund_master_country ON fund_master (country);

CREATE TABLE source_registry (
	source_id VARCHAR(64) NOT NULL, 
	source_name VARCHAR(255) NOT NULL, 
	source_tier INTEGER NOT NULL, 
	url_pattern VARCHAR(512) NOT NULL, 
	parser_version VARCHAR(32) NOT NULL, 
	robots_status VARCHAR(32) NOT NULL, 
	tos_status VARCHAR(32) NOT NULL, 
	last_success TIMESTAMP WITH TIME ZONE, 
	is_active BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (source_id)
);

CREATE INDEX ix_source_registry_source_tier ON source_registry (source_tier);

CREATE TABLE crawl_log (
	log_id VARCHAR(64) NOT NULL, 
	source_id VARCHAR(64) NOT NULL, 
	fund_id VARCHAR(64), 
	target_url TEXT NOT NULL, 
	retrieved_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	http_status INTEGER, 
	content_sha256 VARCHAR(64), 
	outcome VARCHAR(32) NOT NULL, 
	duration_seconds FLOAT NOT NULL, 
	error_message TEXT, 
	PRIMARY KEY (log_id), 
	FOREIGN KEY(source_id) REFERENCES source_registry (source_id), 
	FOREIGN KEY(fund_id) REFERENCES fund_master (fund_id)
);

CREATE INDEX ix_crawl_log_fund_id ON crawl_log (fund_id);
CREATE INDEX ix_crawl_log_retrieved_at ON crawl_log (retrieved_at);
CREATE INDEX ix_crawl_log_source_id ON crawl_log (source_id);

CREATE TABLE detection_run (
	check_id VARCHAR(64) NOT NULL, 
	run_id VARCHAR(64) NOT NULL, 
	fund_id VARCHAR(64) NOT NULL, 
	window_start DATE NOT NULL, 
	window_end DATE NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	confidence NUMERIC(4, 3), 
	unknown_reason VARCHAR(32), 
	suggested_route VARCHAR(16), 
	route_taken VARCHAR(16), 
	events_extracted INTEGER NOT NULL, 
	sent_to_review INTEGER NOT NULL, 
	evidence_json TEXT NOT NULL, 
	http_requests INTEGER NOT NULL, 
	bytes_downloaded INTEGER NOT NULL, 
	duration_seconds FLOAT NOT NULL, 
	sweep_reason VARCHAR(255), 
	checked_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (check_id), 
	FOREIGN KEY(fund_id) REFERENCES fund_master (fund_id)
);

CREATE INDEX ix_detection_run_fund_id ON detection_run (fund_id);
CREATE INDEX ix_detection_run_checked_at ON detection_run (checked_at);
CREATE INDEX ix_detection_run_run_id ON detection_run (run_id);
CREATE INDEX ix_detection_run_status ON detection_run (status);

CREATE TABLE fund_detection_state (
	fund_id VARCHAR(64) NOT NULL, 
	expected_frequency VARCHAR(32) NOT NULL, 
	last_confirmed_event_date DATE, 
	last_checked_at TIMESTAMP WITH TIME ZONE, 
	consecutive_unknowns INTEGER NOT NULL, 
	last_status VARCHAR(16), 
	backfill_completed_at TIMESTAMP WITH TIME ZONE, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (fund_id), 
	FOREIGN KEY(fund_id) REFERENCES fund_master (fund_id)
);


CREATE TABLE raw_document (
	doc_id VARCHAR(64) NOT NULL, 
	sha256 VARCHAR(64) NOT NULL, 
	source_id VARCHAR(64) NOT NULL, 
	source_url TEXT NOT NULL, 
	retrieved_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	content_type VARCHAR(64) NOT NULL, 
	byte_size INTEGER NOT NULL, 
	raw_text TEXT, 
	storage_path VARCHAR(512), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (doc_id), 
	FOREIGN KEY(source_id) REFERENCES source_registry (source_id)
);

CREATE INDEX ix_raw_document_source_id ON raw_document (source_id);
CREATE UNIQUE INDEX ix_raw_document_sha256 ON raw_document (sha256);

CREATE TABLE share_class (
	class_id VARCHAR(64) NOT NULL, 
	fund_id VARCHAR(64) NOT NULL, 
	ticker VARCHAR(16), 
	cusip VARCHAR(16), 
	isin VARCHAR(20), 
	sec_series_id VARCHAR(32), 
	sec_class_id VARCHAR(32), 
	fundserv_code VARCHAR(32), 
	currency VARCHAR(8) NOT NULL, 
	expected_frequency VARCHAR(32) NOT NULL, 
	is_monthly_payer BOOLEAN NOT NULL, 
	is_etf BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (class_id), 
	FOREIGN KEY(fund_id) REFERENCES fund_master (fund_id) ON DELETE CASCADE
);

CREATE INDEX ix_share_class_fund_id ON share_class (fund_id);
CREATE INDEX ix_share_class_fundserv_code ON share_class (fundserv_code);
CREATE INDEX ix_share_class_ticker ON share_class (ticker);

CREATE TABLE distribution_event (
	event_id VARCHAR(160) NOT NULL, 
	class_id VARCHAR(64) NOT NULL, 
	fund_id VARCHAR(64) NOT NULL, 
	ex_date DATE NOT NULL, 
	record_date DATE, 
	payable_date DATE, 
	declaration_date DATE, 
	currency VARCHAR(8) NOT NULL, 
	gross_amount NUMERIC(12, 6) NOT NULL, 
	distribution_type VARCHAR(128) NOT NULL, 
	distribution_category VARCHAR(32) NOT NULL, 
	components_reported BOOLEAN NOT NULL, 
	source_tier INTEGER NOT NULL, 
	estimated_or_final VARCHAR(16) NOT NULL, 
	version INTEGER NOT NULL, 
	is_superseded BOOLEAN NOT NULL, 
	superseded_by VARCHAR(160), 
	extraction_route VARCHAR(32) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (event_id), 
	CONSTRAINT uq_event_natural_key UNIQUE (class_id, ex_date, distribution_category, estimated_or_final, version), 
	FOREIGN KEY(class_id) REFERENCES share_class (class_id), 
	FOREIGN KEY(fund_id) REFERENCES fund_master (fund_id), 
	FOREIGN KEY(superseded_by) REFERENCES distribution_event (event_id)
);

CREATE INDEX ix_distribution_event_fund_id ON distribution_event (fund_id);
CREATE INDEX ix_distribution_event_is_superseded ON distribution_event (is_superseded);
CREATE INDEX idx_event_class_ex ON distribution_event (class_id, ex_date);
CREATE INDEX ix_distribution_event_ex_date ON distribution_event (ex_date);

CREATE TABLE review_queue (
	review_id VARCHAR(64) NOT NULL, 
	fund_id VARCHAR(64) NOT NULL, 
	window_start DATE, 
	window_end DATE, 
	reason VARCHAR(64) NOT NULL, 
	details TEXT NOT NULL, 
	payload_json TEXT, 
	source_url TEXT, 
	doc_id VARCHAR(64), 
	status VARCHAR(16) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (review_id), 
	FOREIGN KEY(fund_id) REFERENCES fund_master (fund_id), 
	FOREIGN KEY(doc_id) REFERENCES raw_document (doc_id)
);

CREATE INDEX ix_review_queue_fund_id ON review_queue (fund_id);
CREATE INDEX ix_review_queue_status ON review_queue (status);

CREATE TABLE distribution_component (
	component_id VARCHAR(200) NOT NULL, 
	event_id VARCHAR(160) NOT NULL, 
	component_type VARCHAR(64) NOT NULL, 
	component_name VARCHAR(128) NOT NULL, 
	amount NUMERIC(12, 6) NOT NULL, 
	percentage NUMERIC(6, 3), 
	is_tax_reallocated BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (component_id), 
	FOREIGN KEY(event_id) REFERENCES distribution_event (event_id) ON DELETE CASCADE
);

CREATE INDEX ix_distribution_component_component_type ON distribution_component (component_type);
CREATE INDEX ix_distribution_component_event_id ON distribution_component (event_id);

CREATE TABLE dq_flag (
	flag_id VARCHAR(64) NOT NULL, 
	event_id VARCHAR(160), 
	fund_id VARCHAR(64) NOT NULL, 
	rule_name VARCHAR(64) NOT NULL, 
	severity VARCHAR(16) NOT NULL, 
	message TEXT NOT NULL, 
	resolution_status VARCHAR(32) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (flag_id), 
	FOREIGN KEY(event_id) REFERENCES distribution_event (event_id) ON DELETE CASCADE, 
	FOREIGN KEY(fund_id) REFERENCES fund_master (fund_id)
);

CREATE INDEX ix_dq_flag_fund_id ON dq_flag (fund_id);
CREATE INDEX ix_dq_flag_event_id ON dq_flag (event_id);
CREATE INDEX ix_dq_flag_severity ON dq_flag (severity);

CREATE TABLE event_evidence (
	evidence_id VARCHAR(200) NOT NULL, 
	event_id VARCHAR(160) NOT NULL, 
	doc_id VARCHAR(64) NOT NULL, 
	source_tier INTEGER NOT NULL, 
	locator_or_snippet TEXT NOT NULL, 
	reported_amount NUMERIC(12, 6), 
	confidence NUMERIC(4, 3), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (evidence_id), 
	FOREIGN KEY(event_id) REFERENCES distribution_event (event_id) ON DELETE CASCADE, 
	FOREIGN KEY(doc_id) REFERENCES raw_document (doc_id)
);

CREATE INDEX ix_event_evidence_event_id ON event_evidence (event_id);
CREATE INDEX ix_event_evidence_doc_id ON event_evidence (doc_id);

