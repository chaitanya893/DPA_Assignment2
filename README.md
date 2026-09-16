# Fund Distribution Detection and Extraction Engine (Assignment 2)

An automated engine that determines, for any US or Canadian mutual fund or ETF, whether it declared a dividend or capital gain distribution in a given period, and captures the full distribution breakdown with deterministic data quality validation into an idempotent database.

---

## Architecture Overview

The system is designed with a two-layer architecture:
1. **Layer A — Atomic Detector (`detect_distribution`):** A single-purpose, fast, high-recall gatekeeper returning `DECLARED`, `NOT_DECLARED`, or `UNKNOWN` with mandatory structured evidence and source tiers.
2. **Layer B — Extraction Engine:** High-precision document parser (HTML tables, PDFs, filings) invoked strictly when Layer A signals `DECLARED`.

---

## Project Structure

```
├── /config/          # Source registry and 100-fund universe mappings
├── /docs/            # Domain primer, compliance, memos, and architectural docs
│   └── DOMAIN_PRIMER.md
├── /src/             # Application code (Detector, Extractor, Models, Sources)
├── /tests/           # Pytest test suites (Unit, Integration, Gold Set)
├── /data/samples/    # Sample fixtures and verified gold set references
├── .gitignore        # Standard ignore rules
└── README.md         # Repository documentation
```

---

## Completed Phases

- [x] **Phase 0: Domain Primer (`docs/DOMAIN_PRIMER.md`)** — Comprehensive financial and operational foundation covering US and Canadian fund distribution mechanics, date keying on `(share_class_id, ex_dividend_date)`, component characterization, estimated vs. final distribution handling, and Canadian T3/T5 restatement versioning.
- [ ] **Phase 1: Layer A Atomic Detector** (In Progress)
- [ ] **Phase 2: Routing & Extraction Engine**
- [ ] **Phase 3: Database Schema & Idempotent Pipeline**
- [ ] **Phase 4: Validation, Gold Set & Quality Metrics**
