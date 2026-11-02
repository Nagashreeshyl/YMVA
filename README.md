# YMVA — Yellow Metal Voice Agent

Yellow Metal's platform for AI phone agents: build agents in plain English, call customers in their own language over any telephony provider, and keep a complete history of every call. See [docs/PLAN.md](docs/PLAN.md) for the full architecture and roadmap.

## Quick start: copy, paste, run

Each step is one line. Run them in order in a terminal, on any Mac, Windows (WSL) or Linux
computer or server.

**Step 1: install Docker** (skip if you have it). On Mac or Windows install
[Docker Desktop](https://www.docker.com/products/docker-desktop/) and open it. On a Linux
server:

```bash
curl -fsSL https://get.docker.com | sudo sh
```

**Step 2: download and run YMVA, A to Z.** This one line downloads YMVA, creates its secrets,
builds everything (dashboard, voice server, database, and the n8n node), starts it, and prints
the admin sign-in. The repository is private, so GitHub asks you to sign in (use a
[personal access token](https://github.com/settings/tokens) as the password), or use
`gh repo clone Nagashreeshyl/YMVA` instead of `git clone` if you have the GitHub CLI.

```bash
git clone https://github.com/Nagashreeshyl/YMVA.git && cd YMVA && ./ymva start
```

Open <http://localhost> and sign in. To take real phone calls, YMVA needs a public address:
add `--tunnel` for a temporary one, or `--domain voice.yellowmetal.co` on a server with that
domain pointed at it (HTTPS is set up automatically):

```bash
./ymva start --domain voice.yellowmetal.co
```

**Step 3: add the YMVA node to n8n.** On the n8n server, one line installs (or updates) the
node straight from YMVA. Use your YMVA address and your n8n container's name (`docker ps`
shows it):

```bash
docker exec -u node n8n sh -c 'mkdir -p ~/.n8n/nodes && cd ~/.n8n/nodes && npm install --legacy-peer-deps https://YOUR-YMVA-ADDRESS/downloads/n8n-nodes-ymva.tgz' && docker restart n8n
```

Then in YMVA create an API key (**Settings → API keys**) and add it in n8n as a **YMVA API**
credential. Other applications use the same key: see
[docs/INTEGRATION-GUIDE.md](docs/INTEGRATION-GUIDE.md).

**Later:** `./ymva update` gets the newest version (backing up first), `./ymva stop` stops it.

## Run it

You need **Docker** (Docker Desktop on Mac/Windows, Docker Engine on Linux). Then:

```bash
./ymva start
```

That's it. The first run generates secure secrets in `.env`, builds everything, and prints the dashboard address and the admin sign-in. Open <http://localhost> and sign in.

On a server with a domain pointed at it, use the domain instead and HTTPS is set up automatically:

```bash
./ymva start --domain voice.yellowmetal.co
```

### Everyday commands

| Command | What it does |
|---|---|
| `./ymva start` | Build and start everything (safe to run again) |
| `./ymva start --tunnel` | Same, plus a temporary public HTTPS address so phone providers can reach this computer |
| `./ymva stop` | Stop everything (data is kept) |
| `./ymva restart` | Apply changes made to `.env` and restart |
| `./ymva status` | Show what's running |
| `./ymva logs api` | Follow logs (`api`, `web`, `postgres`, `redis`, `caddy`) |
| `./ymva update` | Back up the database, pull the latest code, redeploy |
| `./ymva backup` | Save a database backup now (daily automatic backups also go to `./backups`, kept 14 days) |
| `./ymva reset-admin` | Reset the admin account to `ADMIN_EMAIL` / `ADMIN_NAME` / `ADMIN_PASSWORD` in `.env` |
| `./ymva dev` | Developer mode with hot reload, no Docker (needs local PostgreSQL + Redis) |
| `./ymva test` | Run all checks and tests |

## Your first calls

1. **Integrations** → paste your **Sarvam** and **Google Gemini** API keys (each is tested when saved).
2. **Agents** → *New agent* → start from the interest-reminder template. Adjust it, then **Publish**.
3. **Test in browser** (on the agent page) → talk to it with your microphone. Use Chrome, Edge or Safari, and headphones.
4. For real phone calls, the server needs a public HTTPS address so Plivo / Vobiz can reach it:
   - on a server: `./ymva start --domain voice.yellowmetal.co`
   - from this computer, for testing: `./ymva start --tunnel` (temporary address, changes each restart)
5. **Integrations** → *Connect account* (Plivo or Vobiz Auth ID + Token) → **Numbers** → add your number and pick
   the agent that answers it → on the agent page, **Call a phone**.
6. Every call, transcript and language switch appears in **Calls**.

For incoming calls, open *Inbound setup* on the telephony account and paste the Answer and Hangup URLs into your
provider's application.

## Admin account

The admin is defined in `.env` (`ADMIN_EMAIL`, `ADMIN_NAME`, `ADMIN_PASSWORD`). On every start:

- with no users yet, that admin is created;
- if you changed `ADMIN_EMAIL`, the first admin account moves to the new email, name and password;
- otherwise accounts are left alone, so passwords changed in the dashboard are kept.

Forgot the password? Set `ADMIN_PASSWORD` in `.env` and run `./ymva reset-admin`.

## What's inside

| Part | Tech | Folder |
|---|---|---|
| API and voice runtime | Python 3.12, FastAPI, Pipecat 1.12, SQLAlchemy, Alembic | `backend/` |
| Speech and AI | Sarvam (speech in Indian languages), Google Gemini / Gemini Live | `backend/src/ymva/voice/` |
| Telephony | Plivo, Vobiz (more in Phase 5) | `backend/src/ymva/telephony/` |
| Dashboard | Next.js 16, React 19, Tailwind CSS 4, TanStack Query | `web/` |
| Database / queue | PostgreSQL 17 (pgvector), Redis | `docker-compose.yml` |
| HTTPS + routing | Caddy | `deploy/Caddyfile` |

Design rules for the dashboard are in [DESIGN.md](DESIGN.md) (Yellow Metal brand) and [PRODUCT.md](PRODUCT.md); UI work uses the Impeccable skill in `.claude/skills/impeccable`.

## Running it on a server

See [docs/DEPLOY.md](docs/DEPLOY.md): server sizing, VPS and AWS setup, updates without dropped calls,
backups, monitoring and alerts, and `./ymva simulate` for regression and load tests.

## Security notes

- `.env` holds all secrets and is never committed. Back it up somewhere safe: without `ENCRYPTION_KEY`, saved provider credentials can't be decrypted.
- Sessions are HTTP-only cookies; passwords are hashed with Argon2; provider keys are encrypted at rest; sign-in is rate-limited; every change is in the audit log.
