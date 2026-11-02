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

## 2. Install the node in your n8n (Hostinger VPS)

You need the package file `n8n-nodes-ymva-1.0.0.tgz` (build it with
`npm install && npm pack` in this folder).

1. **Upload it to the VPS.** From your Mac:
   ```bash
   scp n8n-nodes-ymva-1.0.0.tgz root@<VPS IP>:/root/
   ```
   (Or use the Hostinger panel's file manager / browser terminal.)
2. **Log in to the VPS** (`ssh root@<VPS IP>` or hPanel → VPS → Browser terminal) and find
   the n8n container's name:
   ```bash
   docker ps --format '{{.Names}}  {{.Image}}' | grep -i n8n
   ```
3. **Install it into n8n's data folder** (replace `n8n` with the name from step 2):
   ```bash
   docker cp /root/n8n-nodes-ymva-1.0.0.tgz n8n:/tmp/
   docker exec -u node n8n sh -c 'mkdir -p ~/.n8n/nodes && cd ~/.n8n/nodes && npm install --legacy-peer-deps /tmp/n8n-nodes-ymva-1.0.0.tgz'
   docker restart n8n
   ```
4. Open n8n, add a node and search **YMVA**.

`~/.n8n/nodes` is on n8n's data volume, so the node survives restarts and n8n updates.
To update the node later, repeat steps 1–3 with the new file. Tested with n8n 2.43.

## 3. Add the credential

In n8n: Credentials → New → **YMVA API**.

- **YMVA Address:** YMVA's public address, e.g. `https://voice.yellowmetal.co` or the ngrok
  address (no `/api`). Your Hostinger server must be able to reach it, so `localhost` won't work.
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

### Customer details

When you choose the agent, **Customer Details** lists every detail that agent needs (its
`{{variables}}`, e.g. Customer name, Due amount), and each one is required: n8n won't run
the node until they're all filled, so a call never starts as "unknown customer". Type a
value or drag one in from an earlier node (e.g. `{{ $json.name }}`). If you change an
agent's variables in YMVA, reopen the node to see the new list. Date, time, the
customer's phone, State and Language are filled in by YMVA and aren't listed.

### Getting the result back

### Language: State or Language

In **Additional Fields**, set **State** (e.g. `{{ $json.state }}`) and the call opens in
that state's language: Karnataka/KA → Kannada, Tamil Nadu/TN → Tamil, Andhra
Pradesh/Telangana → Telugu, Kerala → Malayalam, Maharashtra → Marathi, UP, Bihar, MP,
Rajasthan, Delhi… → Hindi, and so on. Full names, short codes and loose spellings all work.
**Language** picks it directly and wins over State. The agent must have that language
turned on (agent → Languages); otherwise it opens in its default language. If the
customer answers in another language the agent follows them, unless the agent is set
to keep one language.

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
| Start a call | `POST /api/calls/outbound` with `agent_id`, `to`, optional `variables` (incl. `state`, `preferred_language`), `from_number_id`, `callback_url` |
| Call status and result | `GET /api/calls/{call_id}` |

```bash
curl -X POST https://voice.yellowmetal.co/api/calls/outbound \
  -H "Authorization: Bearer ymva_…" -H "Content-Type: application/json" \
  -d '{"agent_id":"<agent id>","to":"+919845011111","variables":{"customer_name":"Ramesh","state":"Karnataka"},"callback_url":"https://your-app/webhook"}'
```

`variables.state` sets the call language as above; `variables.preferred_language`
(`kn`, `ta`, `hi`… or `Kannada`) overrides it. Without `from_number_id`, YMVA uses the number set to answer for that agent, or the only
active number. Errors come back as `{"detail": "…"}` with a plain explanation.
