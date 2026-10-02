# Finance and institution commission development

Started 2 October 2026 after completion of the Phase 3 implementation.

The first backend foundation includes student invoices, allocation of verified Phase 3 payments to invoices, consultancy expenses, versioned institution commission rules and commission records with stored calculation snapshots. Allocation locks invoice/payment records and rejects excess balances, refunds, person/application mismatches and currency mismatches. Commission calculations support fixed amounts and percentages with explicit currency and decimal rounding.

This is the initial foundation, not a completed Phase 4 release. Remaining work: finance permissions and endpoints, immutable financial histories/rules, receipts, expense verification, commission receipt tracking, dashboards/exports, responsive workspace screens and end-to-end acceptance checks. Institution commissions concern payments from universities to the consultancy; agency/sub-agent commissions are excluded.

Validation: two finance foundation tests passed on SQLite and PostgreSQL; Django system and migration drift checks passed. Financial APIs and screens are not exposed yet.
