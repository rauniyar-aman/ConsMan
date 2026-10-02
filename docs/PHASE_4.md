# Phase 4: Finance and institution commissions

Implemented 2 October 2026. Open **Finance** in the staff workspace.

## Workflows

- Create and edit draft student invoices, issue a permanent billing snapshot, download branded PDFs, cancel unpaid invoices and allocate verified payments. Allocation locks both records and rejects excess balances, mismatched people/applications and currencies.
- Record fee/deposit payments, upload protected PDF/image proof, mark received, verify, cancel or refund with an audited reason. Verification issues an immutable numbered receipt with a PDF download. Refunds retain the original receipt and reopen the invoice balance.
- Record branch operating expenses, attach proof, verify or void with a reason.
- Administrators create versioned institution commission rules using fixed amounts or percentages. Record application commissions with saved rule/calculation snapshots, track partial/full receipts and cancel unreceived commissions. Agency/sub-agent commissions are excluded.
- View balances and income/expense/commission totals separately by currency, search and filter records, inspect financial history and export authorized filtered CSVs. No implicit currency conversion is performed.

## Access and history

ADMIN has full finance access and manages commission rules. MANAGER and FINANCE can operate within their branch. MANAGEMENT has read-only access. COUNSELOR and DOCS cannot inspect financial amounts. Finance staff have a dedicated workspace without requiring general CRM access.

Financial receipts, allocations, rule versions, proof files and events are append-only, enforced by database triggers and runtime role privileges. Issued invoice billing details are frozen. Merge reversal rejects later financial activity that would invalidate ledger relationships. Uploaded proofs are served only through authorized private endpoints.

## Verification

- 92 PostgreSQL functional tests passed, excluding the separately tracked performance benchmark.
- Final focused PostgreSQL checks passed for finance and admissions merge/reversal, including concurrent allocation, immutable histories, issued billing snapshots, branch/role permissions, proof validation, refunds, commission receipts and export protection.
- SQLite finance checks passed; the PostgreSQL-specific concurrent allocation test is skipped on SQLite.
- The Cloudflare production build passed. Browser checks covered invoice/payment/receipt/allocation, expense verification, partial/full commission receipts, downloads and Finance/Management/Counselor access. No script errors or horizontal overflow were observed at 320, 390, 768 and 1280 pixels. A downloaded receipt PDF was rendered and visually reviewed.

## Operational acceptance

Implementation and automated workflow verification are complete. Staff must review actual financial workflows and configure real institution commission agreements. Human approval, physical printed QR scans, real spreadsheet migration and the previously unmet concurrent-reader performance target remain separate acceptance items. This release does not claim those checks have been completed.
