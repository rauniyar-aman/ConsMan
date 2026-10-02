# Admissions management

Implemented 2 October 2026 against PRD v1.5, section 16.

- University, campus, course, intake and unique course offerings; fees, availability, academic and English requirements, scholarships.
- Destination workflow versions with configurable milestones, document requirements, offer conditions, enrollment and visa requirements, and generated tasks. Existing applications retain their original version. Historical configuration and application events are protected by database triggers.
- Multiple applications per person with independent APP references, owners, state history, reviewed deferrals and updated intake deadlines.
- Person/application blockers, deadlines and scoped dashboard queues. Required documents and unresolved blockers prevent Ready and Submitted transitions.
- Document request, upload, review, verification, rejection, resubmission, expiry, waiver and not-applicable states. Immutable, checksummed versions and authorized downloads. PDF/PNG/JPEG uploads are limited to 5 MB each and stored in PostgreSQL so free-service restarts preserve files.
- Conditional/unconditional offer receipt, conditions, expiry and decline/expiry handling. Acceptance and final enrollment belong to Phase 3.
- Structured student preferences and explainable rules for matching fees, academic and English scores, destination, level, course and intake. Missing evidence requires review; institution admission is never inferred from a match.
- Staff Admissions workspace and Person 360 admissions tab, catalogue maintenance, application details, upload/review, offers, matching and queues. Existing branch, owner and team permissions apply. Documentation staff cannot change application states.
- Person merges move admissions records and preserve reversible history. Subsequent application changes prevent unsafe reversal. Retried application writes support idempotency keys.

Validation: 13 admissions tests passed on SQLite and PostgreSQL; the broader SQLite suite passed 71 tests with one PostgreSQL-only skip before the final two admissions regression tests were added. API schema validation and Cloudflare production build passed. Browser acceptance created an application, waived a checklist document with a reason, advanced to Ready and recorded an offer with no browser errors.

The broader PostgreSQL run passed functional checks but failed the existing 30-reader performance target: p95 1,298.6 ms against 1,000 ms, with zero request errors. This remains an open performance acceptance item.

Production catalogue data must be entered by the administrator; no demonstration institutions or student documents are seeded into production. PostgreSQL document storage consumes the database plan's capacity. Staff review and actual institution requirements remain operational acceptance tasks.
