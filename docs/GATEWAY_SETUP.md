# WhatsApp Gateway connection

Gateway checkout: `C:\Users\ASUS\Desktop\gateway`.
ConsMan's adapter uses the checkout's WA-AKG API:

`POST /api/messages/{sessionId}/{phoneDigits}@s.whatsapp.net/send`

Header: `X-API-Key`. JSON body: `{"message":{"text":"verification message"}}`.
The session must belong to, or be shared with, the API-key account.

## Local configuration

Keep ConsMan on port 3000 and run Gateway on a different port, for example 3001.
Pair a WhatsApp session in Gateway and generate its account API key.
Set these variables in the shell that starts ConsMan's Django backend:

```powershell
$env:WHATSAPP_GATEWAY_URL='http://127.0.0.1:3001'
$env:WHATSAPP_GATEWAY_KEY='<your API key>'
$env:WHATSAPP_GATEWAY_SESSION='<paired session ID>'
$env:WHATSAPP_GATEWAY_WEBHOOK_SECRET='<shared webhook secret>'
```

Restart Django after configuration. The `.env.example` file is a template;
the backend does not automatically load `.env` files. Keep keys out of Git.

## Delivery webhooks

In Gateway, configure `message.sent` and `message.status` events with the same
secret and the callback `http://127.0.0.1:8000/api/public/messaging/gateway-webhook/`.
For separate machines or containers, use an address reachable from Gateway.
Webhook availability also depends on Gateway's account plan configuration.
ConsMan validates Gateway's `X-Webhook-Signature: sha256=...` HMAC and session ID.

The send response does not include a message ID. ConsMan associates the signed
`message.sent` event with the delivery using hashes of recipient and message,
then processes delivery statuses. It does not persist the OTP in plain text.

An intake submission is saved before attempting delivery. When Gateway is
unavailable, staff can review the saved submission and verify it by phone.
A consented live OTP send and user verification passed on 1 October 2026.
Gateway reported the message READ; its HTTP response timed out and the test
was reconciled using the stored message content hash. Live signed webhooks
and send-timeout handling still require completion.
