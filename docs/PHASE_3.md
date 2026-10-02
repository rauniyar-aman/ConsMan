# Offer, visa and enrollment management

Implemented 2 October 2026 against PRD v1.5, section 17.

Open Admissions, select an application, and use **Offer, visa and enrollment** below the document checklist.

- Resolve each offer condition with verified supporting evidence or a recorded waiver reason, then accept the institution offer. Expired offers and unresolved conditions block acceptance.
- Record a deposit requirement, currency, due date or reviewed waiver. Record payments with PAY references, amount, currency, date, method, transaction reference and a document proof. Staff record receipts; administrators/managers verify or refund. Payment verification requires a verified proof file, which can be attached during verification. Refunded payments and expired/replaced proof files do not count toward required deposits.
- Track CAS, CoE, I-20, LOA and other enrollment documents with institution numbers, issue dates, expiry and verified supporting files. Workflow enrollment requirements match the document kind or supporting checklist title.
- Administrators configure country-specific visa workflow versions, stages, required documents, financial evidence requirements and pre-departure checklists. Each case pins its configuration. Existing versions and visa history are protected by database triggers and runtime-role permissions.
- VISA references, separate attempts, appointment deadlines, submission, optional biometrics/interview/administrative processing, approval, refusal, withdrawal, additional-information documents/tasks and assigned blockers. Decisions are final; a new attempt after refusal/withdrawal preserves the prior case.
- Financial-document metadata includes evidence type, holder, amount/currency, statement date, expiry, verification and review reason. Visa readiness/submission require current evidence and resolved blockers.
- Pre-departure checklist, accommodation, airport pickup, flight, departure, arrival and arrival confirmation, with Nepal-time inputs and UTC storage.
- Final enrollment checks offer acceptance, configured institution/visa requirements, blockers and verified deposit totals; records the institution reference and date and updates the same person to Student/Enrolled.
- Branch/owner/team permissions, document-staff read-only journey access, audited changes, request idempotency and payment preservation during reviewed person merges.

Validation: the 82-test PostgreSQL functional suite passed before the final additional regression test. The final 10 Phase 3 PostgreSQL tests cover acceptance guards, deposit verification, document expiry/replacement, required visa/enrollment evidence, refusal retries, immutable histories, blockers, appointments, pre-departure validation, permissions, malformed identifiers and payment retry idempotency. API schema/migration checks and Cloudflare production compilation passed. Browser acceptance completed offer acceptance, payment recording, visa approval, travel planning and final enrollment without script errors or page overflow at 320, 390, 768 and 1280 pixels.

The administrator must enter real institution and country requirements. No demonstration visa rules or student records are seeded into production. Staff/institution review remains operational acceptance; the earlier 30-reader performance target remains open (p95 1,298.6 ms against 1,000 ms). Full invoices, expenses, receipts and institution commissions belong to Phase 4.
