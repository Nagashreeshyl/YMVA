# YMVA — Yellow Metal Voice Agent: Architecture & Build Plan

Status: Phase 0 (foundation) built 2026-10-25 · next: Phase 1 (voice core)
Decisions locked: fresh project on **Pipecat** (not a Dograh fork; Dograh is reference only).

---

## 1. Goals

- Create and manage many voice agents (first: gold-loan interest/repayment reminder; later support, other reminders).
- Inbound + outbound, running **simultaneously**, on **any telephony provider** (Plivo first, Vobiz alongside).
- **Multilingual**: the agent replies in whatever language the caller speaks (Kannada, Telugu, Tamil, Hindi, English, …) and switches mid-call.
- Two model modes per agent: **Pipeline** (STT → LLM → TTS) and **Gemini Live** (speech-to-speech).
- Three ways to build agents: **Manual** (Vapi-style form), **Assistant** (chat that edits an agent), **Build with AI** (describe every rule/condition, AI builds the whole agent).
- Tools: Google Sheets, databases, HTTP/webhooks, **MCP servers**, plus a **Tool Builder agent** that creates tools from plain English.
- Full history of everything: calls, recordings, transcripts, tool calls, agent versions, builder chats, settings changes.
- Compliance: per-agent toggle + text area for a call-start announcement (e.g. recording disclosure), in the caller's language.

## 2. Users & dashboard

**One dashboard, two roles** (recommended over a separate CEO dashboard — one codebase, same data, no drift):

| Role | Who | Can |
|---|---|---|
| Admin | You | Everything: agents, tools, numbers, providers, campaigns, keys, settings |
| Executive (view) | CEO | Overview page, analytics, call history, recordings, transcripts; no edits |

The **Overview** home page is CEO-friendly: calls today/this week, connect rate, promise-to-pay rate, amount promised, cost, top outcomes, language mix, live calls right now.

## 2a. UI / UX design system

Follows **yellowmetal.co** design principles (extracted from the live site) and is built and audited with the **Impeccable** design skill (pbakaus/impeccable) — its rules and `/audit` + `/polish` passes run on every screen before it ships.

**Brand tokens (from yellowmetal.co)**

| Token | Value | Use |
|---|---|---|
| Ink | `#000000` | Primary text, primary buttons |
| Paper | `#FFFFFF` | Page background |
| Surface | `#F0F0F0` / `#F4F4F5` | Cards, table headers, input fills |
| Muted text | `rgba(0,0,0,.5)` / `.32` | Secondary text, placeholders |
| Gold accent | `#F5C542` | Sparingly: active nav, key highlights, focus ring, charts' primary series |
| Dark surface | `#111` / `#1A1A1A` / `#2A2A2A` | Dark mode |
| Body font | Inter (400/500), 16 px / 24 px | All UI text |
| Display font | Bricolage Grotesque (800), tight tracking | Page titles, KPI numbers |
| Radius | pill (9999 px) for buttons/chips/segmented controls; 12–20 px for cards | |

**Principles**
- Monochrome first, gold as the only accent; status colours (success/warning/error) are muted and used only for state.
- Generous whitespace, one 4/8 px spacing grid, consistent column alignment; no card-inside-card, no gradients, no decorative clutter.
- Clear hierarchy: one primary action per screen (black pill button), secondary actions as ghost buttons.
- Simple navigation: left sidebar — Overview, Agents, Calls, Campaigns, Tools, Numbers, Integrations, Settings.
- Every screen has designed empty, loading (skeleton) and error states; destructive actions confirm.
- Tables: sticky headers, filters, search, pagination, row click → detail drawer.
- Accessible: WCAG 2.1 AA contrast, full keyboard navigation, visible focus ring, screen-reader labels.
- Responsive: desktop-first, usable on tablet and phone (CEO checks the Overview on mobile).
- Light and dark mode from the same tokens.

## 3. Tech stack

| Layer | Technology |
|---|---|
| Voice engine | Pipecat (Python 3.12, asyncio) |
| API | FastAPI + Pydantic v2 + SQLAlchemy 2 (async) + Alembic |
| Background jobs | Redis + ARQ (campaign dialer, retries, post-call analysis, webhooks) |
| Database | PostgreSQL 16 + pgvector (knowledge base) |
| Object storage | S3-compatible (AWS S3 / Cloudflare R2) for recordings, with a local-disk option for single-server installs (MinIO no longer ships free Docker images) |
| Web dashboard | Next.js (App Router) + TypeScript + Tailwind + shadcn/ui + TanStack Query |
| Browser test calls | Pipecat SmallWebRTC transport |
| Generic SIP (any carrier) | Asterisk (ARI + external media) |
| Auth | Email/password + sessions, roles, API keys; secrets encrypted at rest (Fernet / AWS KMS) |
| Reverse proxy / TLS | Caddy (auto HTTPS) |
| Deploy | Docker Compose on one VPS/EC2 first; same images move to ECS/Kubernetes later |
| Observability | Structured logs, Prometheus metrics, per-call latency + cost tracking, Sentry |
| Tests | pytest (unit, integration, simulated calls), Playwright (dashboard) |

No Rust: latency is dominated by the AI models, and the Pipecat ecosystem is Python.

## 4. Architecture

```
                 ┌──────────── Next.js dashboard ────────────┐
                 │ agents · builder · tools · numbers ·      │
                 │ campaigns · history · analytics · settings│
                 └───────────────────┬───────────────────────┘
                                     │ REST + WebSocket (live calls)
┌───────────────┐  webhooks   ┌──────▼──────┐   jobs   ┌────────────┐
│ Plivo/Vobiz/  │────────────►│  FastAPI    │◄────────►│ ARQ worker │
│ Exotel/Twilio/│             │  (API)      │  Redis   │ dialer,    │
│ Telnyx/Vonage/│◄──media WS─►│             │          │ analysis   │
│ SIP           │             │             │          │            │
└───────────────┘             └──────┬──────┘          └────────────┘
                                     │ spawns per call
                              ┌──────▼──────────────┐
                              │ Voice runner        │  Pipecat pipeline per call
                              │ (N processes)       │──► STT / LLM / TTS / Gemini Live
                              └──────┬──────────────┘──► Tools: Sheets, DB, HTTP, MCP
                                     │
                         PostgreSQL · Redis · S3/MinIO
```

- **Telephony adapter layer**: one interface (`start_outbound`, `handle_inbound`, `media_ws`, `hangup`, `transfer`, `send_dtmf`) with an implementation per provider. Pipecat ships serializers for Plivo, Exotel, Twilio, Telnyx, Vonage. **Vobiz** has no Pipecat serializer, so YMVA adds its own `VobizFrameSerializer` for Vobiz's JSON-over-WebSocket media stream (mu-law 8 kHz or Linear16 16 kHz), modelled on the Plivo serializer. Everything else (Ozonetel, Knowlarity, Tata, Airtel, any SIP trunk) goes through Asterisk.
- **Concurrency**: each call is an isolated asyncio pipeline; voice runners scale horizontally. A Redis limiter enforces global, per-provider, per-number and per-campaign concurrency, so inbound and outbound share capacity safely.

## 5. Model modes & multilingual

| | Pipeline mode (default for Indian languages) | Gemini Live mode |
|---|---|---|
| STT | Sarvam (saaras/saarika, auto language detect, code-mixed) · Deepgram for others | built-in |
| LLM | Gemini Flash (default) · OpenAI · Claude · Sarvam | built-in |
| TTS | Sarvam Bulbul (Indian voices) · ElevenLabs / Cartesia | built-in |
| Language switching | STT detects language per utterance → LLM told to answer in it → TTS voice switches to that language | native |

- Per agent: allowed languages, voice per language, and the **greeting language**, chosen in this order:
  1. Customer's `preferred_language` from the loan data, if set.
  2. Otherwise the branch's state: Karnataka → Kannada, Andhra Pradesh / Telangana → Telugu, Tamil Nadu → Tamil (mapping editable in settings).
  3. Otherwise the agent's default language (English).
- **Rule: reply in the language of the caller's latest turn**, switching immediately when the caller switches (English → Telugu → Hindi …).
- **Language guard** (both modes) — the LLM is never trusted to switch on its own:
  1. Detect the language of every caller turn (Sarvam STT in pipeline mode; Gemini input transcription + a fast detector in Live mode).
  2. On a change, inject a short instruction into the conversation before the reply: "The caller is now speaking Telugu. Reply only in Telugu."
  3. Pipeline mode: also switch the TTS language/voice to the detected language, so speech is always in the right language.
  4. Live mode: never set `language_code` (native audio models then lock or reject languages); state the rule in the system instruction.
  5. Log detected vs. spoken language per turn; a mismatch counts as a defect in tests and analytics.
  6. If Gemini Live still answers in the wrong language in Phase 1 tests, Indian-language agents default to pipeline mode, where the reply language is fully controlled.
- Phase 1 validation: phone audio is narrow-band (8 kHz), so we run real test calls in Kannada, Telugu, Tamil, Hindi and English, including mid-call switches, on both modes, and pick defaults from the results.

### 5a. Latency budget

Target: caller stops speaking → agent audio starts in **≤ 1 s p95 on phone calls** (≈ 0.6–0.8 s typical).

| Stage | Budget | How |
|---|---|---|
| End-of-turn detection | 200–300 ms | Silero VAD + Pipecat smart-turn model (no fixed long silence timeout) |
| STT final | 100–200 ms | Streaming STT over a kept-warm WebSocket |
| LLM first token | 250–400 ms | Gemini Flash with thinking off, short prompts, prompt caching |
| TTS first audio | 100–200 ms | Streaming TTS, start speaking at the first sentence |
| Network | < 50 ms | Servers in Mumbai (AWS ap-south-1 or Indian VPS), close to Plivo/Vobiz/Exotel media |

- Slow tools (Sheets, CRM, DB): agent says a short filler ("one moment, let me check") while the tool runs; tool timeouts with graceful fallback.
- Interruptions: caller can talk over the agent at any time; agent stops within ~200 ms.
- Every call records per-stage latency; the dashboard shows p50/p95 per agent, so regressions are visible.

### 5b. Human-like call ambience

Per-agent settings (toggle + volume + choice of sound), mixed into the agent's outgoing audio by Pipecat's audio mixer, so they add **no latency**:

- **Background ambience**: a quiet, looped office/call-centre bed (low murmur, faint room tone) under the agent's voice for the whole call.
- **Action sounds**: short keyboard-typing clip when the agent notes something or runs a tool (e.g. after the caller gives a payment date, while the PTP date is written to the Sheet). Triggered by tool calls and by "noting" moments marked in the agent config.
- Kept low and band-limited to phone audio, so it never hurts clarity or speech recognition; ambience ducks slightly while the agent speaks.
- Phase 1 check: confirm the ambience doesn't trigger false interruptions via echo on the caller's line; tune volume accordingly.

## 6. Agent builder (three modes, one engine)

All three edit the same **Agent Config** (versioned JSON): identity, system prompt, rules/conditions, first message, announcement, languages & voices, model mode & providers, variables, tools, outcomes to extract, end-call/transfer/voicemail behaviour, calling hours.

1. **Manual** — Vapi-style form + prompt editor + "Test call" button.
2. **Assistant** — chat panel beside the form: "if customer says already paid, ask for the UTR number". It edits the config through structured actions and shows a diff you accept or reject.
3. **Build with AI** — you keep describing every condition in plain English; the builder agent asks clarifying questions, then generates the full agent (prompt, rules, variables, outcomes, required tools, test scenarios). Re-prompt any time to refine.

Every change creates a version; publish/rollback per version; builder chats are saved.

## 7. Tools & integrations

- **Built-in tools**: end call, transfer to human, send SMS/WhatsApp (payment link), schedule callback, knowledge-base lookup.
- **Google Sheets**: read row by phone/loan ID, append/update row (e.g. promise-to-pay date). Dates are normalised: agent always knows today's date + timezone (IST), tool schema requires ISO dates, agent confirms the slot aloud before saving.
- **Databases**: Postgres/MySQL connections with **predefined, parameterised queries** (AI fills parameters, never writes raw SQL).
- **HTTP / webhooks**: any REST API; post-call webhook with summary + extracted data.
- **MCP**: attach any MCP server (streamable HTTP/SSE); its tools become available to the agent during calls. YMVA also exposes its own MCP server so Claude Code etc. can manage agents.
- **Tool Builder agent**: describe the tool in English → it picks the type (HTTP / Sheets / DB query / MCP), generates name, description, parameter schema and mapping, then you **test it with sample inputs** before attaching it to an agent.
- Later: HubSpot / Zoho / LeadSquared, Google Calendar.

## 8. History, analytics & compliance

- **Per call**: recording, transcript with language tags, AI summary, outcome, extracted fields (e.g. PTP date & amount), tool call log (inputs/outputs), latency, cost breakdown, provider, number, agent version.
- **Audit log**: every create/update/delete on agents, tools, numbers, settings, with who and when.
- **Analytics**: volume, connect rate, duration, outcomes, language mix, cost/min, latency p50/p95, per agent and per campaign.
- **Compliance**:
  - Announcement toggle + text (auto-spoken in the caller's language) per agent.
  - Calling-hour windows enforced by the dialer (RBI recovery guidance: 8 AM–7 PM), DND/opt-out list, retry limits.
  - PII masking in logs, encrypted credentials, role-based access.

## 9. Campaigns (outbound)

Upload CSV / pick a Google Sheet / DB query → map columns to agent variables → schedule, calling window, concurrency, retries (no answer/busy), number pool & provider → live progress → results export back to the Sheet.

## 10. Estimated cost per minute (to be verified in Phase 1)

| | Pipeline (Sarvam + Gemini Flash) | Pipeline (Deepgram + Gemini + Cartesia) | Gemini Live |
|---|---|---|---|
| AI | ~$0.015–0.03 | ~$0.027–0.033 | ~$0.015–0.03 |
| Telephony (Plivo India) | ~$0.005–0.007 | ~$0.005–0.007 | ~$0.005–0.007 |
| Hosting | ~$0.003 | ~$0.003 | ~$0.003 |
| **Total** | **≈ ₹2–3.5/min** | **≈ ₹3–4/min** | **≈ ₹2–3.5/min** |

The dashboard records actual cost per call, so these get replaced with real numbers.

## 10a. Production readiness (definition of done for every phase)

**Reliability**
- No single call can crash another: each call is isolated; runner crashes are caught, the call is hung up cleanly and logged.
- Provider/model failover: if STT/TTS/LLM errors or times out, switch to the configured backup provider mid-call; if a telephony provider fails, the dialer retries on the next provider in the number pool.
- Idempotent webhooks and jobs (provider retries never create duplicate calls or duplicate Sheet rows).
- Graceful shutdown/deploys: runners drain active calls before restarting — zero dropped calls on deploy.
- Health checks, auto-restart, Redis-backed job queue survives restarts.

**Security**
- HTTPS everywhere; provider webhook signatures verified (Plivo, Vobiz, Exotel, Twilio, Telnyx).
- Secrets encrypted at rest; never shown again after saving; never in logs.
- Role-based access, session timeout, rate limiting, audit log; customer PII masked in logs.
- Dependency and container vulnerability scanning in CI.

**Data**
- Automated daily Postgres backups + point-in-time recovery; recordings in S3 with lifecycle/retention settings.
- Database migrations versioned (Alembic), tested before release.

**Quality gates (CI on every change)**
- Lint + type checks (ruff, mypy, ESLint, TypeScript strict).
- Unit + integration tests; simulated end-to-end calls (an AI "customer" calls the agent in each language, including mid-call language switches) with checks on outcome, language and latency.
- Playwright tests for core dashboard flows; Impeccable `/audit` on changed screens.
- Load test before release at target concurrency.

**Operations**
- Metrics + dashboards (active calls, error rate, latency p50/p95, cost), alerts to email/Slack/WhatsApp on failures.
- Structured logs with call ID correlation; Sentry for errors.
- Runbook and deploy guide for VPS and AWS; one-command deploy and rollback.

## 11. Build phases

| Phase | Deliverable | Done when |
|---|---|---|
| 0 Foundation | Monorepo, Docker Compose, Postgres/Redis/MinIO, auth & roles, DB schema, dashboard shell | `docker compose up` gives a login + empty dashboard |
| 1 Voice core | Pipecat runner, both model modes, multilingual switching, browser test call, Plivo + Vobiz inbound + outbound | Real phone call in Kannada/Telugu/English works both modes, on both providers |
| 2 Agent builder | Manual form, Assistant, Build-with-AI, versioning, announcement | Reminder agent built from plain English and called |
| 3 History & analytics | Recordings, transcripts, summaries, extraction, overview + analytics, audit log | CEO view shows real call data |
| 4 Tools | Sheets, DB, HTTP, MCP client, Tool Builder agent, post-call webhooks | Agent writes a PTP date to a Google Sheet during a call |
| 5 Campaigns & providers | Dialer, scheduling, concurrency limiter; Exotel, Twilio, Telnyx, Vonage, Asterisk SIP | Inbound + outbound concurrently across 2+ providers |
| 6 Hardening | Simulated-call regression suite, load test, monitoring, backups, deploy guide (VPS + AWS) | Load test passes at target concurrency |

## 12. Open items

- Where does customer/loan data live today (LMS, database, Sheets)? This drives the first integration.
- Target concurrent calls (e.g. 10 / 50 / 200)?
- Payment link channel: SMS, WhatsApp, or both?
- Accounts/API keys needed for Phase 1: Plivo, Vobiz, Sarvam, Google AI (Gemini). Optional: Deepgram, ElevenLabs/Cartesia.
