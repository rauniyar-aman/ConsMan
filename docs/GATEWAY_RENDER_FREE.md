# Gateway on Render Free with office-hours checks

The agreed check window is 09:00–18:00 Asia/Kathmandu, every ten minutes, every day. The first scheduled check is approximately 09:05 and the last 17:55. This controls checks, not service shutdown: real traffic and active connections may keep services running outside that window. Cold starts and restarts can interrupt delivery; check pairing/session recovery after a redeploy.

Render provides 750 service-hours per workspace per month, shared across all Free web services. Two continuously running services would need about 1,440 hours in a 30-day month. Nine hours a day for both is about 540 hours plus cold starts, idle tails and actual traffic. Monitor Billing → Monthly Included Usage; the schedule is not a hard spending or usage cap. Render's own Cron Job service is paid, so the scheduled checker uses a separate Cloudflare Worker.

## 1. Prepare your own Gateway fork

The local clone's origin belongs to its upstream developer. Fork `https://github.com/Vinsaeroy/gateway` under your account. Upload the two prepared files into the fork at their exact paths:

- `render.free.yaml`
- `scripts/start-render.cjs`

Copies are in this repository's `deployment/gateway` directory and already added to the local Gateway clone. Its original paid `render.yaml` is preserved. Do not upload `.env`, local configuration, databases or node_modules.

## 2. Prepare a dedicated hosted PostgreSQL database

Use a separate Gateway database. Do not give Gateway the ConsMan application database URL: both applications have unrelated schemas. Preserve the hosted URL's SSL parameters. The local PC database cannot be reached by Render. The startup script initializes Gateway's Prisma schema without a destructive override; incompatible existing schemas fail startup.

Gateway authentication state is stored in PostgreSQL and encrypted with `AUTH_SECRET`. Keep that secret stable, including across deploys. Local uploaded media is ephemeral on Free Render; this setup is for text messages and OTP. Do not depend on uploaded files surviving a restart.

## 3. Create the Render service

Render → New → Blueprint → select your Gateway fork → Blueprint Path `render.free.yaml`. Confirm the only service is Free and that no disk or paid database is requested.

Supply the required values:

| Setting | Value |
|---|---|
| DATABASE_URL | Dedicated hosted Gateway PostgreSQL URL, including SSL parameters |
| BASE_URL | Actual HTTPS Render Gateway origin |
| NEXTAUTH_URL | Same Gateway origin |
| NEXT_PUBLIC_APP_URL | Same Gateway origin |
| NEXT_PUBLIC_API_URL | Gateway origin followed by `/api` |
| GATEWAY_ADMIN_EMAIL | Office administrator's email |
| GATEWAY_ADMIN_PASSWORD | New unique password of at least 12 characters |

Render generates AUTH_SECRET once; preserve it. The Blueprint sets HOSTNAME=0.0.0.0, TZ=Asia/Kathmandu and DISABLE_KEEPALIVE=true. Render supplies PORT; leave it alone. If the final service URL differs from the intended URL, update all four URL settings and redeploy.

The first startup creates the configured administrator if absent and disables public registration. Subsequent starts do not reset the password or promote an existing lower-role user. Keep bootstrap settings private; password changes for an existing administrator happen through Gateway, not by editing this bootstrap variable.

Check `/api/health` responds successfully, then sign in at `/auth/login`. Stop the PC Gateway before pairing the hosted WhatsApp session. Record the actual session ID and create/copy its API key. The health endpoint reports process health, not WhatsApp connectivity.

## 4. Connect ConsMan and webhooks

In the existing ConsMan Render backend set WHATSAPP_GATEWAY_URL to the hosted Gateway origin; set WHATSAPP_GATEWAY_KEY and WHATSAPP_GATEWAY_SESSION from the hosted session. Set WHATSAPP_GATEWAY_WEBHOOK_SECRET to a matching private signing secret and WHATSAPP_MESSAGE_COST_MINOR=0 if delivery has no per-message fee. Save and redeploy.

Configure signed Gateway callbacks, preserving both integrations:

- `https://consman-rauniyaraman-api.onrender.com/api/public/messaging/gateway-webhook/`
- `https://consman-rauniyaraman-api.onrender.com/api/v1/communications/webhooks/gateway/`

Use the Gateway session's sent/status/received events appropriate to each integration, with the matching signing secret. Start with an explicitly approved test recipient.

## 5. Activate ten-minute Cloudflare checks

In `deployment/render-keepalive/wrangler.jsonc`, replace the empty GATEWAY_HEALTH_URL with the actual `https://YOUR-GATEWAY.onrender.com/api/health` and set ENABLED to `true`. Only HTTPS onrender.com health endpoints without credentials or query parameters are accepted. Never put API keys in the checker: these public health endpoints need none.

From `frontend` run:

```powershell
npx wrangler login
npx wrangler deploy --config ../deployment/render-keepalive/wrangler.jsonc
```

This deploys a separate `consman-render-keepalive` Worker. It does not replace the ConsMan frontend. The cron expression is every ten minutes in UTC; code applies Nepal office hours. Changes to cron triggers can take several minutes to propagate. Logs show service/status outcomes without secrets. To pause all checks, set ENABLED=false and redeploy. No check sends a WhatsApp message.

## 6. Validate with the PC off

Check the hosted WhatsApp session is connected, then power off the PC. Test OTP delivery during office hours, verify signed status callbacks, and inspect Cloudflare scheduled logs. Restart/redeploy Gateway and confirm it restores the session with the same database and AUTH_SECRET. Overnight requests may incur cold starts. Pings do not guarantee uninterrupted service or prevent free-hour suspension.

General queued messages, inbound processing and reminders still require the separate ConsMan message worker/scheduler described in `COMMUNICATION_OPERATIONS.md`. Hosting Gateway alone does not move those PC processes to Render. No paid worker is provisioned by these files; until a cloud worker is deployed, those workflows still depend on their worker host being online.

## Status

On 2026-10-02, the Free Render service `consman-whatsapp-gateway` was created in the existing project and configured with a dedicated `gateway` database in the existing hosted PostgreSQL project. The separate Cloudflare Worker `consman-render-keepalive` is deployed and enabled for 09:00–18:00 Nepal time. Gateway build/startup, WhatsApp pairing, the connection to ConsMan and live delivery still require verification. No live messages were sent. Four other Free web services were present in the Render workspace, so their activity also contributes to its 750-hour allowance. The schedule is not a guarantee of staying within that allowance.

References: [Render Free limitations](https://render.com/docs/free), [Cloudflare Cron Triggers](https://developers.cloudflare.com/workers/configuration/cron-triggers/).
