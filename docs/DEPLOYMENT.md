# Deployment and operations

## Current free Render configuration

The current `render.yaml` defines one Free Docker web service and no paid cron jobs. It overrides the container command with `python deployment/start_free.py`: release migrations/master seeding/runtime grants run before Gunicorn starts, because Free services do not provide a pre-deploy phase. Startup fails if release preparation fails. The service uses one worker with four threads. Set all database and provider secrets in the shared environment group before deploying.

Free services sleep after inactivity and use temporary local storage. Keep PostgreSQL in Neon and private files in external private storage. The Blueprint does not schedule housekeeping or backups; use a separately configured scheduler on the owner's computer or a supported external service. No automatic backup schedule is active from this file.

Free Render services do not provide the service shell used in the paid instructions below. Run initial administrator provisioning from a trusted local terminal against Neon using the runtime connection, production Django secret and temporary bootstrap variables. Run `python manage.py bootstrap_admin` from `backend`, then remove the temporary variables. No administrator password belongs in Git.

Earlier paid-service and cron descriptions below describe the previous deployment option and do not apply to the current Free Blueprint. If paid cron services were already created, removing them from the Blueprint does not establish that they were deleted: remove them explicitly in the Render dashboard to stop charges.

This is the remaining deployment runbook, not evidence that production has been deployed.

## Environment

- Keep Django/PostgreSQL private behind a trusted HTTPS proxy. Expose Next.js and the registration hostname through that proxy. Strip client-supplied forwarded headers and set them in the proxy.
- Set `DJANGO_DEBUG=false`, a strong stable `DJANGO_SECRET_KEY`, exact allowed hosts, HTTPS CSRF origins, production public-form origin/allowed origins and only actual proxy ranges in `TRUSTED_PROXY_IPS`. HTTPS redirect, HSTS, secure cookies and frame/MIME protections activate outside development.
- Configure Turnstile site/secret keys and hostname. Development bypass is disabled with DEBUG false. Follow `GATEWAY_SETUP.md`; choose an SMS service and confirm the adapter contract before enabling it. Keep secrets out of Git.
- Use PostgreSQL with separate migration/runtime roles. The migration role must enable `pg_trgm`, or a DBA must provision it. Migrate as that role, then apply `backend/deployment/app_role.sql` using your database/role names. The runtime role must not own tables, inherit migration privileges, have BYPASSRLS or be a superuser. Verify UPDATE/DELETE/TRUNCATE of `crm_auditevent` are denied. Reapply grants after migrations.
- Configure a private S3 bucket with `S3_BUCKET`, `AWS_REGION` and restricted credentials, optionally `S3_ENDPOINT_URL`. Block public access and retain encryption. Local private files are a development alternative, not an encrypted production storage setup.
- Build Next.js with `npm ci`, `npm run typecheck`, `npm run build` and run the production server. Use a production WSGI service for Django, not `runserver`; provide the platform-appropriate server in deployment.

`compose.yaml` supplies development PostgreSQL 17. Set `POSTGRES_PASSWORD` before starting it; this is not a production stack.

## Provisioning and recovery

Run `seed_defaults` for masters/calendars/rules. Provision actual staff through a controlled initial administrator account and Settings; do not run `seed_demo` in production. ADMIN/FINANCE enroll MFA. Preserve the stable Django secret securely: it encrypts authenticator secrets, and rotation without migration prevents their decryption.

ADMIN can set a staff password in Settings. Django invalidates sessions using the prior password; MFA stays required. Self-service password reset is excluded from scope; administrators manage resets.

A trusted server operator can use `reset_staff_mfa` with `ALLOW_MFA_RESET=true` and a documented reason for a lost authenticator. Consult `manage.py reset_staff_mfa --help`. This revokes sessions and writes audit; never expose it publicly. Privileged users enroll again at the next login.

## Scheduled work

Run `backend/.venv/Scripts/python.exe backend/manage.py run_housekeeping` every minute through your service scheduler. It materializes notifications/escalations, purges expired unverified payloads/challenges, clears resolved intake data according to policy, cleans rolling rate records and flags old closed leads for review. Monitor failures. The browser refreshes every minute; scheduling is required for unattended processing.

## Backup and restoration

Locally, set a securely retained Fernet `BACKUP_ENCRYPTION_KEY`, then run `manage.py backup_restore_check --output <private-path>`. It encrypts a SQLite snapshot and verifies an isolated restore, checksum, integrity, counts and audit guards. Keep the key separately; never use an ephemeral scratch key for retained backups.

For production, use encrypted PostgreSQL backups/PITR or `pg_dump` with a protected backup role and approved encryption. Include private uploaded/import/QR files and storage versioning; database-only backup omits files. Restore to a separate environment with the matching application release. Verify counts, references, contacts, audit triggers/runtime privileges, MFA decryption, private-file retrieval and QR downloads. Keep providers disconnected during drills. Record recovery time/outcome, retain keys in managed custody and repeat drills periodically.

## Release evidence still required

Run PostgreSQL CI, `check --deploy` with final configuration, least-privilege audit tests, realistic concurrent-user load tests and a production restore drill. Verify TLS/CSRF/CAPTCHA and real OTP/webhooks with consented recipients. Print and scan QR exports on intended devices. Dry-run actual spreadsheets in staging, review duplicate/legacy mappings and obtain branch staff UAT sign-off.

Request logs contain request ID, route, method, status and duration; never add bodies, passwords or OTPs. Configure availability/error monitoring, backup-failure alerts and provider budget/delivery alerts at deployment.

## Selected deployment target

Frontend: Cloudflare; backend: Render; database: Neon; domain: `consman.rauniyaraman.com.np`. These services are not deployed yet. Domain/account access, Turnstile, private storage, Gateway delivery webhooks, unattended operations, real-spreadsheet migration and human acceptance remain outstanding. Local PostgreSQL provisioning and restoration do not establish Neon deployment.

## Manual Render configuration and Cloudflare release

The owner will enter Neon connection URLs in Render. Import the root `render.yaml` Blueprint to prepare the backend, minute housekeeping job and daily encrypted backup job. Configure the shared `consman-production` environment group using `backend/.env.production.example`. Supply all three database variables: `DATABASE_URL` for the restricted runtime role, `MIGRATION_DATABASE_URL` for the schema owner, and `BACKUP_DATABASE_URL` for the backup reader. Use Neon URLs with SSL required. Provision the `consman_app` role in Neon before deploying, or set `APP_DATABASE_ROLE` to the actual restricted runtime role. Never use the schema owner as the runtime account. The release script migrates, seeds masters and grants access; it does not create database roles or administrator accounts.

In Render, temporarily set `BOOTSTRAP_ADMIN_USERNAME`, a strong `BOOTSTRAP_ADMIN_PASSWORD`, and `BOOTSTRAP_ADMIN_BRANCH` (default `KTM`). Run `python manage.py bootstrap_admin` in the service shell, then remove these temporary environment variables. The command refuses to run when an administrator already exists. Enroll MFA on first login. The administrator creates staff accounts and manages password resets in Settings. Managers edit branch records; counselors edit owned records or records covered by an access grant. Documentation staff edit assigned education/document fields. Management and finance do not gain general profile editing. The profile screen already exposes Edit profile according to these permissions.

For Cloudflare, run `npm ci` then `npm run build:cloudflare` in `frontend`, with `API_ORIGIN` set to the final Render HTTPS hostname. Review `wrangler.jsonc` and run `npm run deploy:cloudflare` using the owner's Cloudflare account. The Worker custom-domain route is `consman.rauniyaraman.com.np`; Cloudflare must control its DNS zone. Configure Turnstile for this hostname and supply its keys in Render. The local Worker bundle has built successfully; publishing, TLS and production Turnstile verification remain pending.

The local Gateway address cannot be reached from Render. Provide an authenticated public HTTPS Gateway endpoint, configure its API key/session in Render, and register the backend's `/api/public/messaging/gateway-webhook/` callback with the matching signing secret. Gateway rejects private callback addresses; do not bypass that protection. Verify an actual signed delivery callback after both public endpoints are available.

Configure a private S3-compatible bucket and credentials for private files and encrypted backups. Retain the Fernet backup key separately from that bucket. The Blueprint backup runs daily at 20:15 UTC (02:00 Nepal time the following day); housekeeping runs every minute. These schedules become active only after Render deployment. Add bucket versioning/retention and failure alerts, and perform a restore drill against a separate Neon database before calling cloud recovery verified. Render monitors `/api/v1/health/`, which returns 503 when the database cannot answer a readiness query. Configure an external HTTPS uptime check and alerts for backend errors, cron failures and backup failures.

## Acceptance ownership and evidence

The administrator creates and tests real staff accounts. A branch staff reviewer must confirm profile/contact editing, assigned access restrictions, tasks/follow-ups, intake review and administrator password reset. Record the reviewer, date and outcomes before marking staff acceptance approved. Automated role and workflow tests are evidence for this review, not a human signature.

Students scan the printed QR on their phones and confirm the form opens, saves and resumes correctly. Record device, print size, scan result and OTP outcome. Generated QR decoding has passed locally; a physical student scan is still required.

Real spreadsheet migration requires the actual files. Upload them to staging, review field mappings and duplicate decisions, dry-run, compare counts and obtain administrator approval before committing the import. Synthetic import/rollback tests do not establish a real migration.

The local PostgreSQL concurrency run completed 150 requests from 30 readers against 20,000 records without errors, but p95 was 1,404 ms against a 1,000 ms target. Performance acceptance remains open; repeat on the deployed service and optimize or size it using measured results. Do not silently relax the target.
