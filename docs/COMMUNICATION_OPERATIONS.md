# Communication operations

The web release installs the database tables and screens. Redis, Celery worker and scheduler run separately. The supplied Docker Compose file can run them on an existing PC/server; it does not create a paid Render service. The PC must remain running with network access for unattended delivery.

## Worker activation

1. Start Docker Desktop with its Linux engine and confirm `docker info` succeeds.
2. Create the ignored file `backend/private_config/communications.env`. Copy the **runtime** database URL (including SSL parameters), application secret and provider configuration from the private deployment configuration. Do not use the migration-owner connection for workers.
3. Include `DATABASE_URL`, `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=false` and `PUBLIC_FORM_ORIGIN=https://consman.rauniyaraman.com.np`. Configure the provider keys below as needed. Redis is internal to Compose; no Redis port is published. Worker/scheduler commands refuse to start without a Redis URL, database URL and application secret.
4. After Render finishes its migrations, run from the repository root:

```bash
docker compose -f compose.communications.yaml up -d --build
```

5. Open **Communications** as ADMIN. Check the worker heartbeat becomes active. Keep reminder rules disabled until approved templates and consent are ready.
6. Create a test template and use an explicitly approved recipient for each channel. Confirm sent/delivered/incoming history and opt-out before enabling reminders. Only one scheduler should run for this deployment; database claims and rule deduplication also protect against duplicate dispatch.
7. To pause processing, disable reminder rules and stop the worker deployment:

```bash
docker compose -f compose.communications.yaml stop
```

Queued work remains in PostgreSQL. Redeploy workers after backend code changes; the frontend/backend GitHub deployments do not update a PC-hosted worker automatically. Use the same code revision on web and workers.

## Provider configuration

| Channel | Private settings | Acceptance |
|---|---|---|
| Email | EMAIL_HOST, EMAIL_PORT, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD, EMAIL_USE_TLS, DEFAULT_FROM_EMAIL | SMTP acceptance records SENT; actual delivery/bounces require the signed callback relay described below. |
| WhatsApp | WHATSAPP_GATEWAY_URL, WHATSAPP_GATEWAY_KEY, WHATSAPP_GATEWAY_SESSION, WHATSAPP_GATEWAY_WEBHOOK_SECRET | Paired Gateway session, send acceptance, sent-ID binding, delivery/read callback and incoming reply. |
| SMS | SMS_PROVIDER_URL, SMS_PROVIDER_KEY, SMS_WEBHOOK_SECRET | HTTP adapter contract, recipient coverage, provider idempotency behavior and delivery callback. |

For a Gateway on this PC, Docker workers can use `http://host.docker.internal:3030`. Render cannot reach your PC's localhost; its OTP adapter requires a reachable Gateway endpoint. Configure a second Gateway webhook for `/api/v1/communications/webhooks/gateway/`, preserving the existing OTP webhook. Use the matching HMAC secret and session. Incoming media is not downloaded; text/caption history is stored.

Hourly/daily limits default to 60/500 claimed attempts. `COMMUNICATION_EMAIL_MONTHLY_BUDGET_MINOR`, `COMMUNICATION_SMS_MONTHLY_BUDGET_MINOR` and `COMMUNICATION_WHATSAPP_MONTHLY_BUDGET_MINOR` default to **zero**. Free messages with zero configured cost can proceed; paid estimates are blocked until an explicit budget is configured. Prices use `EMAIL_MESSAGE_COST_MINOR`, `SMS_MESSAGE_COST_MINOR` and `WHATSAPP_MESSAGE_COST_MINOR`, expressed in each provider's billing currency minor units. SMS reserves a conservative UTF-16 segment estimate. Existing OTP estimates are included. These reservations can overestimate rejected sends and are not provider invoices.

Keep provider values consistent between the web service and worker configuration. The UI's provider flags mean configured, not live-tested. The worker heartbeat and message outcomes provide processing evidence.

## Signed callbacks and incoming messages

Endpoints are `/api/v1/communications/webhooks/{gateway|email|sms|meta}/`. Gateway follows the cloned repository's native `sessionId`, `event`, `data` envelope and `X-Webhook-Signature` contract. Meta uses `X-Hub-Signature-256` and its app secret.

Email/SMS relay requests use `X-Webhook-Signature: sha256=<HMAC-SHA256 of the exact raw body>`, with the respective webhook secret. The relay must authenticate the provider's native callback first and validate incoming sender provenance. Do not expose an unsigned translation endpoint.

```json
{"event":"inbound","id":"unique-provider-id","from":"sender@example.com","body":"Plain text reply"}
```

```json
{"event":"status","id":"outbound-provider-id","status":"DELIVERED"}
```

SMS delivery submits `to`, `message`, `client_reference` and an `Idempotency-Key` header; successful responses must contain `id`. Email's `X-ConsMan-Message-ID` header supplies the outbound UUID for callback correlation. Supported statuses are SENT, DELIVERED, READ and FAILED. Inbound IDs must remain stable across retries. Shared-number/ambiguous messages appear in administrator matching review. Replies consisting of STOP, UNSUBSCRIBE or CANCEL revoke channel consent. Email includes a signed unsubscribe link with a confirmation screen.

## Facebook/Instagram lead forms

1. Configure `META_APP_SECRET`, `META_VERIFY_TOKEN`, `META_PAGE_ACCESS_TOKEN` and `META_GRAPH_VERSION` using the supported version shown in the Meta app configuration. Store tokens privately.
2. Configure the HTTPS Meta webhook at `/api/v1/communications/webhooks/meta/`, subscribe the appropriate Page's leadgen events and grant the app/Page lead retrieval permissions.
3. Connect each Page/form in **Communications**, selecting the branch, platform and source, then enable that mapping.
4. Submit an approved test lead. Confirm PENDING becomes REVIEW and inspect the retrieved fields. Approve a new unique person or search/link a reviewed duplicate; reject unwanted leads.
5. Confirm replaying the lead does not create another person. Lead-form submission does not mark contacts verified or opt them into messaging.

Actual app review, Page access, permissions and native Meta delivery require the owner's account setup and live acceptance. The implementation's fixture tests do not establish those approvals.

## Monitoring and recovery

- Check heartbeat, queue counts, provider configuration, automation outcomes, failed lead retrieval and pending webhooks in **Communications**.
- Confirmed failures can be retried from Person 360 with a reason. Timeouts/interrupted sends become UNKNOWN; check the provider first, then reconcile as SENT or FAILED. UNKNOWN never automatically resends.
- Webhook processing stops after ten failures; lead retrieval stops after five failures. Fix the integration before using the administrator retry control.
- Disable a rule to stop new reminders. Cancel queued messages to stop existing queued work. A message already claimed/submitted may still arrive after consent withdrawal.
- Back up PostgreSQL with the existing private backup procedure; it contains conversation, consent, delivery, queue and lead-review history. Redis persistence is enabled, but PostgreSQL is the durable source of work.

Celery integration follows the [official Django integration instructions](https://github.com/celery/celery/blob/main/docs/django/first-steps-with-django.rst). No paid worker or broker service is declared by these files.
