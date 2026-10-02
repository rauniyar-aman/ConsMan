# Phase 5: Communication and automation

Implemented 2 October 2026. Administrator configuration is available under **Communications**. Person 360 contains the student conversation, consent controls, template authoring, previews, queueing and delivery history.

## Implemented

- Versioned email/WhatsApp/SMS templates, explicit channel consent with evidence, verified recipient enforcement, previews and scheduled outbound messages. Supported fields: student_name, person_reference, branch_name, due_at, event_title, application_reference and application_state. Reminder fields are populated from workflow records; manual previews leave unused reminder fields blank.
- SMTP email, the existing WA-AKG Gateway contract and an HTTP SMS adapter. Provider credentials remain in environment variables. Disabled providers retain queued messages without delivery attempts.
- Persistent database queues, Redis/Celery task dispatch, minute scheduler, safe retry backoff, stale-worker recovery, delivery events and administrator reconciliation. Unknown send outcomes never automatically resend. Confirmed READ/DELIVERED callbacks cannot be overwritten by late responses or downgraded status events.
- Hourly/daily limits and per-channel monthly estimated cost budgets. Cost reservations include OTP estimates, are recorded at claim time and conservatively reserve SMS Unicode segments. Paid-message budgets default to zero. Estimates are operational limits, not invoice/accounting totals.
- Signed, replay-safe Gateway/email/SMS callbacks, asynchronous processing and incoming-message matching. Shared/unverified numbers remain in administrator review. Incoming STOP/UNSUBSCRIBE cancels queued messages for the matching channel. Email unsubscribe links require an explicit confirmation POST; mail scanners merely opening the link do not revoke consent.
- Immutable template, consent and delivery event history, frozen message content, restricted runtime database privileges and a chronological two-way conversation with older-page loading. Merged records retain original history and are included in the survivor conversation; consent is not automatically transferred.
- Configurable branch reminder rules for follow-ups, tasks, application deadlines and admission/offer/visa/enrollment transitions. Staff receive assigned-workspace notifications; students receive template messages only with channel consent and verified contacts. Rule/event deduplication prevents repeated reminders. Rules start disabled and show their outcomes.
- Facebook/Instagram lead-form webhook verification, form/page mapping, asynchronous Graph retrieval, provider ID deduplication and administrator lead review. Leads are staged before creation; potential duplicates require linking to an existing person. Imported contacts remain unverified, and social leads do not imply messaging consent.
- Administrator queue counts, unknown-delivery review, unmatched incoming messages, webhook retries, lead retrieval retries and worker heartbeat monitoring. No paid Render worker declaration is introduced.

## Validation

114 PostgreSQL functional tests passed during final integration, excluding the separately tracked performance benchmark. Final focused checks passed 21 communication tests on PostgreSQL and 19 on SQLite (two worker/concurrency tests require PostgreSQL). The final checks include an actual Celery worker using isolated in-memory transport and mocked delivery, concurrent dispatch, budgets, immutable history, unsubscribe confirmation, old history pages, provider contracts, signed callbacks, consent and social lead review. Redis network transport and real provider/account approvals are not established by those tests.

Browser checks covered reminder creation/enabling/disabling, templates, consent, message preview, queueing and cancellation. No script errors or horizontal overflow were observed at 320, 390, 768 and 1280 pixels. The Cloudflare production build, migration drift check and schema validation passed.

## Activation still required

See [communication operations](COMMUNICATION_OPERATIONS.md). SMTP/SMS accounts, Meta app/page permissions and access tokens, a reachable Gateway callback configuration, and an always-running Redis worker/scheduler must be supplied/activated before live delivery and social synchronization can be accepted. Docker Desktop's Linux engine was unavailable on the current PC during verification. Existing staff UAT and other deployment acceptance items remain open. Implementation completion does not constitute live provider acceptance.
