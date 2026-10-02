# Connect Gateway to live ConsMan

## Run while the PC is off

Gateway needs a continuously running host. A tunnel on the PC stops when the PC shuts down. Render Free sleeps after inactivity, interrupting the persistent WhatsApp connection; it is unsuitable for continuous Gateway operation.

1. Provision an Ubuntu cloud VM. For a zero-cost candidate, use only Oracle resources labelled Always Free eligible and remain within the account's current limits. Capacity may be unavailable and idle instances may be reclaimed; free hosting does not guarantee uninterrupted service. See [Oracle's current limits](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm).
2. Install Docker on that VM and deploy the cloned Gateway using its Dockerfile. Store configuration in a private environment file. The current clone uses PostgreSQL through Prisma, including its WhatsApp authentication state; use a separate Gateway database and back it up. Preserve `/app/data/media` in a persistent volume.
3. Configure automatic container restart and run the Cloudflare Tunnel connector as a server service. Point `gateway.rauniyaraman.com.np` to Gateway on the VM, with the API key enforced. Keep dashboard access private.
4. Pair the office WhatsApp session on the hosted Gateway. Stop the PC instance before running the same session on the server to avoid simultaneous connections.
5. Configure Render with the Gateway URL, API key, actual session ID and signing secret below. Configure the existing delivery/incoming webhooks on the hosted instance.
6. Move the ConsMan message worker and its Redis service to the VM too, using the private configuration described in `COMMUNICATION_OPERATIONS.md`. OTP calls run in the web service; queued outbound messages, incoming-message processing and reminders require the worker.
7. Test delivery and incoming callbacks with the PC powered off. Validate automatic restart and session restoration before relying on this setup.

No cloud VM or Gateway migration has been performed by these documentation changes. Provisioning requires access to the chosen server.

## Connect a running PC for testing

Render cannot reach the Gateway at the PC's localhost. Use a stable HTTPS Cloudflare Tunnel endpoint while keeping Gateway and its paired session running. This sends WhatsApp messages; carrier SMS requires its own provider.

1. In Cloudflare, create a named tunnel under **Networking → Tunnels**. Install the Windows connector using the command supplied for that tunnel. Keep its token private.
2. Publish `gateway.rauniyaraman.com.np`, pointing to HTTP `localhost:3030`. Restrict public routing to the API endpoints needed for messaging; keep the dashboard accessed locally. The Gateway's API-key checks must remain enabled. Do not place an interactive browser challenge in front of backend API requests.
3. In the local Gateway dashboard, copy the API key and the paired session's actual session ID. The session ID is not necessarily the WhatsApp phone number.
4. In Render's ConsMan environment, set:

| Key | Value |
|---|---|
| WHATSAPP_GATEWAY_URL | https://gateway.rauniyaraman.com.np |
| WHATSAPP_GATEWAY_KEY | Private Gateway API key |
| WHATSAPP_GATEWAY_SESSION | Paired Gateway session ID |
| WHATSAPP_GATEWAY_WEBHOOK_SECRET | Random private callback-signing secret |
| WHATSAPP_MESSAGE_COST_MINOR | 0 when the selected Gateway has no per-message charge |

5. Save/redeploy Render. Never put these credentials in the frontend or GitHub.
6. Add signed Gateway webhooks for `https://consman-rauniyaraman-api.onrender.com/api/public/messaging/gateway-webhook/` (OTP) and `https://consman-rauniyaraman-api.onrender.com/api/v1/communications/webhooks/gateway/` (communication history). Preserve both. Use the same signing secret configured on Render; subscribe to `message.sent`, `message.status` and `message.received` as appropriate.
7. OTP sending runs in the web service. General queued messages, incoming communication processing and reminders additionally need Redis plus the communications worker/scheduler. Follow [Communication operations](COMMUNICATION_OPERATIONS.md). Copy the same runtime database, application secret and Gateway settings into the ignored worker configuration. A Docker worker on this PC can reach Gateway at `http://host.docker.internal:3030`.
8. Confirm the student contact is verified and WhatsApp consent is recorded. Send an approved test through Person 360; confirm delivery/read events and an incoming reply before enabling reminder rules.

The PC must remain on, connected to the internet, and running Gateway, the tunnel connector and the worker processes. If it is off, live OTP delivery cannot reach it and queued messages await worker/provider recovery.

See the [official Cloudflare Tunnel setup](https://developers.cloudflare.com/tunnel/get-started/) for connector and hostname instructions. No tunnel or provider credentials are committed by this guide.
