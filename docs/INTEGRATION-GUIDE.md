# Starting YMVA calls from another application

Any application (a CRM, a loan system, a hospital system, n8n, Zapier…) can ask YMVA to
phone a customer with one of its AI agents, and get the result back when the call ends.

You need three things: **YMVA's address**, an **API key**, and the **agent's ID**.

- YMVA's address is where the dashboard is opened, e.g. `https://voice.yellowmetal.co`
  (today: `https://nonnasally-uncavalier-deadra.ngrok-free.dev`). Below it's written
  `https://YMVA`.
- Every request carries the key in a header: `Authorization: Bearer ymva_…`
- Requests and answers are JSON.

## 1. Get an API key

In YMVA: **Settings → API keys → New API key**. Give it a name (e.g. "Loan system") and
pick what it may do:

| Access | Can |
|---|---|
| **Caller** (recommended) | start calls, read the calls it started, list agents and numbers |
| **Campaign manager** | also read every call and run campaigns |

The key is shown **once**. Store it on your server (environment variable or secret
store), never in a web page or mobile app, where anyone could read it. If it leaks,
revoke it in the same screen and make a new one: the old key stops working at once.

## 2. Find the agent and the details it needs

```bash
curl https://YMVA/api/agents -H "Authorization: Bearer $YMVA_API_KEY"
```

```json
[
  {
    "id": "c198f8a2-…",
    "name": "Hospital Appointment",
    "status": "active",
    "languages": ["en", "kn", "hi", "te", "ta"],
    "details": [
      {"name": "patient_name", "description": "Full name of the patient", "example": "Rahul Sharma"}
    ]
  }
]
```

`details` lists what that agent needs to know about the customer: the
`{{variables}}` in its instructions. **Every one is required**; YMVA refuses a call that
misses one, so the agent never calls an "unknown customer". New agents and changed
agents show up here automatically. Use the agent's `id` when starting calls.

## 3. Start a call

`POST https://YMVA/api/calls/outbound`

| Field | Required | What |
|---|---|---|
| `agent_id` | yes | from step 2 |
| `to` | yes | customer's number with country code, e.g. `+919845011111` (10-digit Indian numbers also work) |
| `variables` | yes, if the agent has details | every detail from step 2, plus optionally `state` / `preferred_language` (see 4) |
| `callback_url` | no | YMVA POSTs the result here when the call ends (see 5) |
| `from_number_id` | no | which YMVA number to call from (`GET /api/numbers`); leave out to use the agent's number |

**curl**
```bash
curl -X POST https://YMVA/api/calls/outbound \
  -H "Authorization: Bearer $YMVA_API_KEY" -H "Content-Type: application/json" \
  -d '{
    "agent_id": "c198f8a2-…",
    "to": "+919845011111",
    "variables": {"patient_name": "Rahul Sharma", "state": "Karnataka"},
    "callback_url": "https://your-app.example.com/ymva/call-ended"
  }'
```

**Python**
```python
import os, requests

resp = requests.post(
    "https://YMVA/api/calls/outbound",
    headers={"Authorization": f"Bearer {os.environ['YMVA_API_KEY']}"},
    json={
        "agent_id": "c198f8a2-…",
        "to": "+919845011111",
        "variables": {"patient_name": "Rahul Sharma", "state": "Karnataka"},
        "callback_url": "https://your-app.example.com/ymva/call-ended",
    },
    timeout=30,
)
if resp.status_code != 201:
    raise RuntimeError(resp.json()["detail"])  # YMVA explains what's wrong
call_id = resp.json()["id"]
```

**JavaScript (Node.js 18+)**
```js
const resp = await fetch("https://YMVA/api/calls/outbound", {
  method: "POST",
  headers: {
    Authorization: `Bearer ${process.env.YMVA_API_KEY}`,
    "Content-Type": "application/json",
  },
  body: JSON.stringify({
    agent_id: "c198f8a2-…",
    to: "+919845011111",
    variables: { patient_name: "Rahul Sharma", state: "Karnataka" },
    callback_url: "https://your-app.example.com/ymva/call-ended",
  }),
});
const call = await resp.json();
if (resp.status !== 201) throw new Error(call.detail);
console.log(call.id, call.status); // e.g. "ringing"
```

The answer (`201 Created`) is the call: `{"id": "…", "status": "ringing", …}`. Keep the
`id` to look the call up later.

## 4. Call language

The call opens in the customer's language when you send their state:

- `"state": "Karnataka"` → Kannada. Full names, short codes (`KA`, `TN`, `AP`, `UP`…) and
  loose spellings work. Tamil Nadu/Puducherry → Tamil, Andhra Pradesh/Telangana →
  Telugu, Kerala → Malayalam, Maharashtra → Marathi, West Bengal/Tripura → Bengali,
  Gujarat → Gujarati, Punjab → Punjabi, Odisha → Odia, UP, Bihar, MP, Rajasthan, Delhi,
  Haryana, J&K… → Hindi.
- `"preferred_language": "kn"` (or `"Kannada"`) picks the language directly and wins over
  `state`.
- The agent must have that language turned on (its `languages` in step 2); otherwise
  the call opens in the agent's default language. If the customer answers in another
  language, the agent follows them.

## 5. Get the result

**Callback (recommended).** When the call ends, YMVA POSTs to your `callback_url`:

```json
{
  "event": "call.ended",
  "call_id": "…",
  "status": "completed",
  "outcome": "Appointment booked",
  "summary": "Rahul booked a cardiology appointment for 12 Nov at 10 AM.",
  "details": {"Department": "Cardiology", "Preferred appointment date": "2026-11-12"},
  "agent_id": "…", "agent_name": "Hospital Appointment",
  "to": "+919845011111", "from": "+918064269915",
  "duration_seconds": 95, "end_reason": "agent_ended",
  "variables": {"patient_name": "Rahul Sharma", "state": "Karnataka"},
  "started_at": "2026-11-03T10:00:05Z", "ended_at": "2026-11-03T10:01:40Z"
}
```

- Answer it with any `2xx`. If your server is down, YMVA retries three times.
- Calls nobody answered are reported too: `status` is `no_answer`, `busy` or `failed`,
  without an outcome.
- The address must be reachable from the internet (YMVA won't send to private
  addresses such as `192.168.x.x` unless its admin allows it).

**Or ask.** `GET https://YMVA/api/calls/{call_id}` returns the same information
(`status`, `outcome`, `summary`, `extracted`, `duration_seconds`…). Status goes
`queued` → `ringing` → `in_progress` → `completed` (or `no_answer`, `busy`, `failed`,
`canceled`). The outcome and summary appear a few seconds after the call ends. Don't ask
more than once every few seconds.

## 6. Errors

Every error is `{"detail": "a plain explanation"}`. Show or log it as is.

| Status | Means | Example `detail` |
|---|---|---|
| 400 | something in the request is wrong | `Missing customer details: patient_name (see details in GET /api/agents)` |
| 401 | no key, wrong key, or revoked key | `Not signed in` |
| 403 | the key's access doesn't allow this | |
| 404 | agent, number or call not found | `Agent or number not found` |
| 422 | a field has the wrong form | `Enter the full number with country code, e.g. +91 80 1234 5678` |
| 502 | the phone provider refused the call | the provider's reason |

## 7. All endpoints

| What | Request |
|---|---|
| Check the key | `GET /api/auth/me` |
| List agents (with their required details) | `GET /api/agents` |
| List YMVA's phone numbers | `GET /api/numbers` |
| Start a call | `POST /api/calls/outbound` |
| A call's status and result | `GET /api/calls/{call_id}` |
| Calls this key started | `GET /api/calls?limit=50&offset=0` |

## Using n8n instead

YMVA has its own n8n node (Start Call, Get Call, List Agents) with the agent's details,
the State dropdown and the callback built in. Install it on the n8n server with one line;
see `integrations/n8n-nodes-ymva/README.md`.
