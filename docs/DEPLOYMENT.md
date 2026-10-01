# Deployment and operations

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
