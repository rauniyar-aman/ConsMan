# Phase 7 — Evidence and assistance

## Implemented

- Person 360 provides a recorded-evidence summary, recent activity/application/visa timeline, document completeness and expiry checks, journey health, weighted follow-up priority, course matching and a routine communication draft.
- Each attention reason identifies its source record and weight. Matching uses destination, study level, selected courses/intakes, academic and English requirements, budget/currency and intake deadlines. Missing evidence and free-text institution requirements remain review items; the output does not establish eligibility. Up to 200 available offerings are evaluated and the total/limit are displayed.
- **Journey insights** provides scoped pipeline and application-state counts, overdue follow-ups, open deadlines, an ordered attention queue, and anomaly counts with record examples. Queue weights apply across the complete accessible active record scope, with pagination; scores are capped at 100.
- Responsible staff can accept or dismiss assistance with a reason. Reviews store an immutable evidence snapshot. Neither review nor draft generation changes admissions, financial or visa decisions or sends a message.
- Optional external text assistance generates summary/draft wording from anonymized status evidence. It excludes names, reference numbers, contacts, files, notes and free-text document titles. Returned source references must belong to the supplied evidence. Human reviewers must still verify the wording and conclusions. Provider results are immutable and can be included in a recorded review.
- External requests are disabled by default. HTTPS endpoint, private credential, model identifier and an explicit daily request limit are required. Database reservations enforce that limit; malformed results, unsupported citations and network failures return a safe error without automatic retry. Local evidence tools remain available.

## Optional configuration

Set `ASSISTANCE_SERVICE_URL`, `ASSISTANCE_SERVICE_KEY`, `ASSISTANCE_MODEL` and `ASSISTANCE_DAILY_REQUEST_LIMIT` privately on the backend. The endpoint must accept the message-completion JSON contract implemented in `backend/insights/provider.py`. A limit of zero disables calls. A request count is not a provider billing budget; select an account with an appropriate free allowance or retain the default disabled configuration.

## Verification and activation

The final 132-test PostgreSQL functional suite and runtime history privilege checks passed. Focused tests cover scope boundaries, read-only management access, expiry/completeness, immutable review snapshots, queue ordering, disabled-provider behavior, anonymized inputs, request limits and rejected unknown source references. Browser review and mobile checks, TypeScript/schema validation and the Cloudflare build passed.

External-provider tests used simulated responses; no live provider account was configured or charged. External wording therefore still needs provider activation and live acceptance. Institution criteria, staff acceptance and the existing concurrent-user performance target remain open.
