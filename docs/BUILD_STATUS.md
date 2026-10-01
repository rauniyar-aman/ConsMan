# ConsMan — Phase 1 status

Updated 1 October 2026. Baseline: PRD v1.5 and the supplied brand/design specifications.

**The Phase 1 application is implemented and locally verified. Production acceptance is pending external services, PostgreSQL validation, deployment, and staff UAT.** Later admissions, visa processing, finance and student-portal phases are outside this build.

## Implemented

- Responsive staff workspace, Person 360, public registration and official logo from `docs/logo_ConsMan.jpg`.
- Session authentication, CSRF, password policy, login throttling, encrypted authenticator MFA required for ADMIN/FINANCE, and audited operator MFA recovery.
- Configuration-driven six-role permissions, branch/owner/assigned-team scopes, masked discovery, access requests, grants, reassignment and direct API enforcement.
- UUID/reference, normalized and verified multiple/shared contacts, source/campaign/detail/tags, education/test records, editable profiles, archival, lifecycle reasons, same-ID conversion and bulk actions.
- Duplicate confidence tiers using phone/email, shared numbers, normalized/transliterated/reordered names, similarity, DOB and country. LOW suggestions appear at entry; higher signals enter human review. No automatic merge. Reviewed merges move related records; protected ADMIN reversal is available for seven days.
- Append-only activity corrections, unified timeline, communication outcomes, click-to-chat logging prompt, follow-ups, prioritized tasks, earliest next action, attention queues, notifications, business calendars, configurable SLA rules and capacity-aware assignment.
- Store-first UNVERIFIED intake, consent, Turnstile, honeypot/payload guards, hashed OTP challenges, expiry/attempt/resend/correction limits, rolling phone/IP/QR/global limits, spend caps, refresh/resume, neutral duplicate confirmation, staff call verification and retention cleanup.
- Gateway adapter matched to the local WA-AKG checkout, signed delivery webhooks and configurable HTTP SMS adapter. Provider-disabled submissions remain saved and reviewable.
- Permanent QR codes for registration/WhatsApp/URL/contact cards; module/eye styles, colors/gradients/frames, official or safe uploaded logo, final PNG/SVG/PDF decode validation, versioning, pause/expiry/clone/archive and funnel metrics.
- CSV/XLSX staging, mapping, validation/review counts, consent, approval, idempotent commit, duplicate recheck after approval, provenance and whole-batch rollback when unedited. Authorized filtered exports and scoped reports.
- SQLite/PostgreSQL audit immutability triggers, structured logs without bodies, private local/S3 storage support, production HTTPS/cookie settings, generated OpenAPI TypeScript contract, pinned dependencies and SQLite/PostgreSQL CI configuration.

## Local verification

- **48 Django tests passed**, including all 108 direct endpoint role/action combinations, permission matrix checks, lifecycle/contact/merge behavior, import rollback and approval races, MFA/CSRF, OTP isolation and limits, Gateway contract/webhooks, QR downloads and calendar/SLA behavior.
- A production-settings `check --deploy` run had only the optional HSTS-preload warning; preload enrollment remains a deployment decision.
- Django checks, migration drift check and OpenAPI validation passed without schema warnings.
- TypeScript checking and the Next.js production build passed.
- At 20,000 synthetic records, 30 sequential local search/filter requests measured **p95 99.1 ms**. This local SQLite/API benchmark does not establish production PostgreSQL or concurrent-user performance.
- Browser checks exercised login, profile editing, tasks, provider-disabled public save, refresh/resume, staff call verification, QR creation, import mapping/commit/rollback, all workspace sections, administrator MFA and settings. No JavaScript errors were observed. The 390 px mobile workspace had no page-level horizontal overflow.
- Final PNG/SVG/A4/A5/tent PDF downloads passed automatic decoding. An A4 poster was exported, rendered with Poppler and visually inspected.
- Encrypted local SQLite backup restored into an isolated clean database: checksum, integrity, Person/audit counts and audit guards passed. The scratch drill used an ephemeral test key; it is not a retained production backup.

## Acceptance tracking

| PRD criteria | Implemented local evidence | Production or human acceptance still required |
|---|---|---|
| AC-01–03 | Identity/contact/shared-number and duplicate tests | Shared-family data examples |
| AC-04–07 | Follow-ups, minute refresh, corrected timeline and click-to-chat | Counselor verifies the three-click workflow |
| AC-08 | Relationship-preserving merge/reversal tests | Real migration data review; later-phase application records outside Phase 1 |
| AC-09–12 | Mocked OTP pipeline, duplicate intake, abuse/idempotency and SLA queues | Real provider delivery and public deployment |
| AC-13 | Task tests and browser creation | Staff UAT |
| AC-14–15 | 20,000-record local p95 benchmark and filters | Production PostgreSQL/concurrent-user load test |
| AC-16–17 | Import/idempotency/rollback and scoped audited export tests | Actual spreadsheets and migration dry run |
| AC-18–24 | Lifecycle/conversion/bulk/notifications/access/assisted intake/education | Branch staff UAT |
| AC-25 | Six roles × eighteen direct API actions; matrix tests | Run the configured PostgreSQL CI job |
| AC-26 | SQLite immutable audit tests; PostgreSQL trigger and restricted-role SQL | Apply/verify actual production role privileges |
| AC-27 | Configurable stale queue; no automatic closure | Manager configuration review |
| AC-28 | Encrypted isolated SQLite restore passed | Production PostgreSQL/private-file restore drill and key custody |
| AC-29–33 | Decode-validated formats/types/styles/logo/versioning/inactive behavior | Configure registration domain/TLS; physical scan tests |
| AC-34–42 | Store-first/failure/OTP/isolation/retention/correction/rate/cost checks | Gateway/SMS, Turnstile and real-device verification |
| AC-43–47 | Calendar/SLA/assignment/next-action/timeline tests | Scheduled housekeeping and staff timing acceptance |
| AC-48–50 | Provenance, lost-reason master and human-only duplicate handling | Real-data migration and UAT sign-off |

## Additional local activation evidence

- All 48 tests also passed on PostgreSQL 18 after fixing row locking across nullable joins. The 20,000-record sequential PostgreSQL benchmark measured p95 197.1 ms.
- Separate local production/validation databases and migration/runtime/test roles were provisioned. ConsMan roles use SCRAM; incorrect credentials and runtime audit UPDATE/DELETE/TRUNCATE/schema CREATE were rejected. Gateway's database was preserved.
- Encrypted PostgreSQL/private-file restoration verified 52 tables, 16 fictional Persons, 37 audit events, 75 files and two QR assets in an isolated restore. A separate restore of the clean production database also passed; it contains no demo Persons.
- Browser staff workflows passed against PostgreSQL using the runtime role: registration save/resume, call verification, profile/task updates and import commit/rollback, with no JavaScript errors or mobile overflow.
- One live WhatsApp OTP was reported READ by Gateway and successfully verified by the user. The record was created only in the isolated validation database. The HTTP send response timed out; delivery was reconciled against Gateway's record and matching content hash. Signed live delivery callbacks and timeout handling remain to be finished.

## Remaining activation work

The selected target is Render backend, Cloudflare frontend, Neon database and `consman.rauniyaraman.com.np`. The main local backend now uses clean PostgreSQL and the paired Gateway configuration in development mode. Cloud deployment remains pending; the owner will enter Neon URLs in Render manually.

The Cloudflare Worker build passed. Render backend, minute housekeeping and daily encrypted-backup declarations are prepared. Backup storage round-trip verification passed locally; cloud scheduling and restoration still require activation. Database health readiness tests passed. The 30-reader/150-request PostgreSQL test had no errors but p95 was 1,404 ms, exceeding the 1,000 ms target. Staff editing and administrator account creation are already available within role access limits. Students will perform the physical printed QR scans.

Still required: provider-account/domain access, Neon configuration, HTTPS/Turnstile/private storage, Gateway delivery callbacks, unattended housekeeping/backups/monitoring, concurrent-user checks, printed physical QR scans, real-spreadsheet migration and human staff acceptance sign-off. Automated workflow checks are not staff sign-off.

Password recovery is complete within the agreed scope: ADMIN resets staff passwords through Settings, and privileged MFA remains required. Self-service “Forgot password” is excluded.

See `DEPLOYMENT.md` for operations. Local tests and restore drills do not constitute cloud go-live acceptance.
