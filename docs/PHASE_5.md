# Phase 5: Communication and automation

Started 2 October 2026 after Phase 4 implementation.

## First implemented slice

Person 360 now includes Communications for permitted staff. Administrators can create versioned EMAIL, WHATSAPP and SMS templates using {{student_name}}, {{person_reference}} and {{branch_name}} placeholders. Staff record explicit per-channel consent with evidence, choose a verified contact, queue a rendered template immediately or for a future date, inspect stored history and cancel queued messages. Opt-out cancels queued messages for the channel. Existing general intake consent does not automatically authorize messaging.

Queued messages store the recipient, rendered content and template/consent references. The dispatch eligibility helper rechecks current consent, verification, recipient changes and archived/merged people. Person access follows existing CRM scopes. ADMIN, MANAGER and COUNSELOR can queue within their person access; MANAGEMENT can inspect history; template authoring is ADMIN-only.

No delivery worker is enabled in this slice. Queue creation is persistence, not a claim of provider delivery. No live student messages are sent by deployment.

## Next work

Email/Gateway/SMS delivery adapters, Redis/Celery dispatch and retries, signed inbound/delivery webhook processing, immutable communication events, two-way history and merge handling, staff/student reminders, automation configuration, Facebook/Instagram lead-form sync and operational provider acceptance.

## Verification

Three focused tests cover consent blocking and revocation, rendered snapshots, verified recipient enforcement, template versions, unsupported placeholders and person/role permissions. SQLite and PostgreSQL checks, frontend TypeScript checking, schema validation and the Cloudflare production build passed. Browser checks created a template and recorded an opt-out through Person 360 without script errors.
