# YMVA node for n8n

Start AI phone calls with a YMVA agent from any n8n workflow, and get the result
(outcome, summary, collected details) back when the call ends.

Operations: **Start Call**, **Get Call**, **List Agents**. The agent and "call from"
number are dropdowns loaded from YMVA.

## 1. Make an API key in YMVA

Settings → **API keys** → New API key. Pick the access:

- **Caller**: can only start calls and read the calls it started (recommended for n8n).
- **Campaign manager**: also reads every call and runs campaigns.

The key is shown once. Paste it into n8n's credential, nowhere else.

## 2. Install the node in your self-hosted n8n

Build it once (Node.js 20+):

```bash
cd integrations/n8n-nodes-ymva && npm install && npm run build
```

Then copy the whole `n8n-nodes-ymva` folder (with `dist/`) into n8n's custom folder and
restart n8n:

- **n8n in Docker:** mount it, e.g. in your n8n `docker-compose.yml`:
  ```yaml
  volumes:
    - ./n8n-nodes-ymva:/home/node/.n8n/custom/n8n-nodes-ymva:ro
  ```
- **n8n installed with npm:** copy it to `~/.n8n/custom/n8n-nodes-ymva`.

"YMVA" now appears in the node list. Tested with n8n 2.43.

## 3. Add the credential

In n8n: Credentials → New → **YMVA API**.

- **YMVA Address:** YMVA's public address, e.g. `https://voice.yellowmetal.co`
  (no `/api`). n8n must be able to reach it.
- **API Key:** the key from step 1.

"Test" checks the key.

## 4. Use it

Import `examples/start-call-and-wait-for-result.json` (Workflows → Import from file),
choose the agent in **Start YMVA call**, select the credential, and activate it. Then any
app can ask for a call:

```bash
curl -X POST https://<your-n8n>/webhook/ymva-call -H "Content-Type: application/json" -d '{"phone":"+919845011111","name":"Ramesh"}'
```

The workflow starts the call, waits (up to 30 minutes) and continues with the result.

### Getting the result back

Set **Additional Fields → Callback URL** to `{{ $execution.resumeUrl }}` and follow the
node with a **Wait** node set to *Resume: On Webhook Call* (POST). When the call ends
YMVA POSTs:

```json
{
  "event": "call.ended",
  "call_id": "…",
  "status": "completed",
  "outcome": "Appointment booked",
  "summary": "Gagandeep booked an appointment for 2 Nov at 18:00.",
  "details": {"Patient full name": "Gagandeep", "Preferred appointment date": "2026-11-02"},
  "agent_id": "…", "agent_name": "Hospital Appointment",
  "to": "+917019913128", "from": "+918064269915",
  "duration_seconds": 95, "end_reason": "agent_ended",
  "variables": {"customer_name": "Gagandeep"},
  "started_at": "…", "ended_at": "…"
}
```

Unanswered calls are reported too (`status` is `no_answer`, `busy` or `failed`, with no
outcome). If the callback can't be delivered YMVA retries three times.

**n8n on the company network:** YMVA refuses to send to private addresses (192.168.x,
10.x…) unless `EGRESS_ALLOW_PRIVATE=true` is set in YMVA's `.env`. n8n's resume URL must
also be its real address: set `WEBHOOK_URL` in n8n's environment.

## Without n8n: the plain API

Any app can do the same with the key in an `Authorization: Bearer` header:

| What | Request |
|---|---|
| List agents | `GET /api/agents` |
| List numbers | `GET /api/numbers` |
| Start a call | `POST /api/calls/outbound` with `agent_id`, `to`, optional `variables`, `from_number_id`, `callback_url` |
| Call status and result | `GET /api/calls/{call_id}` |

```bash
curl -X POST https://voice.yellowmetal.co/api/calls/outbound \
  -H "Authorization: Bearer ymva_…" -H "Content-Type: application/json" \
  -d '{"agent_id":"<agent id>","to":"+919845011111","variables":{"customer_name":"Ramesh"},"callback_url":"https://your-app/webhook"}'
```

Without `from_number_id`, YMVA uses the number set to answer for that agent, or the only
active number. Errors come back as `{"detail": "…"}` with a plain explanation.
