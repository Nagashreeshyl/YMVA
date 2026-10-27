# Deploying YMVA

This guide puts YMVA on one Linux server with its own domain and HTTPS. It covers a plain VPS (DigitalOcean, Hetzner, E2E Networks…) and AWS EC2. After the server is set up, the steps are the same on both.

Everything runs in Docker: Postgres, Redis, the API, the dashboard, Caddy (HTTPS) and a backup job. One command (`./ymva`) starts, updates, backs up and restores it.

---

## 1. Size the server

These numbers come from `ymva simulate`: spoken test calls through a simulated phone network, with real AI services, in Gemini Live mode.

| Measured | Per call |
|---|---|
| CPU | 0.08–0.16 of a core |
| Memory | about 60 MB |
| The rest of the stack, idle | about 650 MB in total |

One API process handles about **10 simultaneous calls**, because Python uses a single core per process. Set `API_WORKERS` in `.env` to run more processes. Use about one process per core, and leave one core free for Postgres and the rest.

| Simultaneous calls | Server | `API_WORKERS` | AWS example |
|---|---|---|---|
| up to 10 | 2 vCPU, 4 GB | 1 | c7i.large |
| up to 30 | 4 vCPU, 8 GB | 3 | c7i.xlarge |
| up to 60 | 8 vCPU, 16 GB | 6 | c7i.2xlarge |

The tests ran up to 10 calls in one process. The larger rows are scaled up from that, so confirm them on the new server with `./ymva simulate --calls N` (section 6).

**Your AI provider limits usually come first.** On the current Gemini key, about 8 simultaneous calls already hit `You exceeded your current quota`. Those calls go quiet and log an error. Before you increase capacity:

- **Gemini:** raise the Gemini API tier in Google AI Studio (Billing). The limit on simultaneous Live sessions grows with the tier.
- **Sarvam:** check your plan's rate limit if agents use Standard mode.

Disk: 40 GB or more. Recordings take about 1 MB per call-minute and are copied into the backups.

Avoid burstable instances (AWS t3/t4g). When their CPU credits run out, the audio stutters.

---

## 2a. Set up a VPS (Ubuntu 24.04)

1. Create the server in a region near your callers, for example Mumbai or Bangalore for India. Add your SSH key.
2. Point your domain at it. Create a DNS **A record**, for example `voice.yellowmetal.co`, with the server's IP.
3. Log in and secure it:

   ```bash
   ssh root@SERVER_IP
   apt update && apt upgrade -y
   adduser ymva && usermod -aG sudo ymva
   rsync -a ~/.ssh /home/ymva/ && chown -R ymva:ymva /home/ymva/.ssh
   ufw allow OpenSSH && ufw allow 80/tcp && ufw allow 443/tcp && ufw --force enable
   ```

   Ports 80 and 443 must be open to everyone, because telephony providers call in from many addresses. Nothing else needs to be open.
4. Install Docker:

   ```bash
   curl -fsSL https://get.docker.com | sh
   usermod -aG docker ymva
   ```

Then continue at **section 3**.

## 2b. Set up AWS EC2

1. **Region:** `ap-south-1` (Mumbai), or the one nearest your callers.
2. **Launch an instance:**
   - AMI: Ubuntu 24.04.
   - Type: from the table in section 1.
   - Storage: gp3, 40 GB or more, encrypted.
3. **Security group:**
   - 22 from **your IP only**.
   - 80 and 443 from anywhere.
4. **Elastic IP:** allocate one and attach it, so the address survives restarts. Some Indian providers (Exotel, Plivo) can whitelist it.
5. **DNS:** create an A record for your domain pointing at the Elastic IP. Route 53 or your registrar both work.
6. **Off-site backups (recommended):**
   - Create an S3 bucket, for example `yellowmetal-ymva-backups`.
   - Block public access and turn on versioning.
   - Add a lifecycle rule that deletes objects after 90 days.
   - Create an IAM role that EC2 can assume. Allow `s3:PutObject` and `s3:ListBucket` on that bucket only, and attach the role to the instance.
7. Log in as `ubuntu` and install Docker, as in 2a step 4 (`usermod -aG docker ubuntu`).
8. **Optional:** in Data Lifecycle Manager, add a daily EBS snapshot policy as a second safety net.

---

## 3. Install and start

On the server, as the non-root user:

```bash
git clone <your YMVA repository> ymva && cd ymva
./ymva start --domain voice.yellowmetal.co
```

The first start:

- creates `.env` with strong random secrets and an admin password, and prints the admin password;
- builds and starts everything;
- has Caddy get an HTTPS certificate for the domain. This needs the DNS record from step 2 to be live already.

Open `https://voice.yellowmetal.co`, sign in, and change the admin password under **Settings → Your account**.

**Back up `.env` somewhere safe**, for example a password manager. It holds `ENCRYPTION_KEY`. Without that key, a database backup cannot decrypt the saved API keys and provider credentials.

To serve more than 10 simultaneous calls, set `API_WORKERS` in `.env` (section 1), then run `./ymva restart`.

---

## 4. Connect AI and telephony

1. **Integrations → AI keys:** paste the Sarvam and Google Gemini keys. Each is tested when saved.
2. **Integrations → Connect account:** pick the provider (Plivo, Vobiz, Twilio, Telnyx, Exotel or Vonage) and paste its credentials.
3. **Inbound calls:**
   - Open **Setup** on the account.
   - Copy the Answer and Status/Hangup URLs into the provider's console, exactly as shown. Each URL contains a secret.
4. **Numbers:** add each number and choose the agent that answers it.
5. Test with **Call a phone** on an agent page, then call the number from a mobile.

---

## 5. Day-to-day operation

| Task | Command |
|---|---|
| See what's running | `./ymva status` |
| Read logs | `./ymva logs api` (or `web`, `caddy`, `postgres`) |
| Update to the latest code | `./ymva update` |
| Restart | `./ymva restart` |
| Back up now | `./ymva backup` |
| Check a backup restores | `./ymva verify-backup [file]` |
| Restore a backup | `./ymva restore <file>` |
| Regression / load test | `./ymva simulate --calls N` |

### Updates without dropped calls

`./ymva update` works in this order:

1. Pulls the code.
2. Builds the new version while the old one keeps serving.
3. Takes a database backup.
4. **Drains**: no new calls start, campaigns pause, and live calls finish. This waits up to 15 minutes; set `DRAIN_WAIT=…` in seconds to change it.
5. Switches over.

During the drain, a "Call a phone" request shows "restarting for an update". Incoming calls to a busy line are rejected politely, and campaigns pick up again afterwards.

### Rolling back

```bash
git log --oneline              # find the previous version
git checkout <commit>
./ymva update
```

If the new version changed the database, also restore the backup that `update` took just before switching. It is the newest `backups/ymva-*.sql.gz` from before the update:

```bash
./ymva restore backups/ymva-YYYYMMDD-HHMMSS.sql.gz
```

`restore` asks for confirmation, takes a safety backup of the current data, drains live calls, and then restores.

---

## 6. Test the server: `ymva simulate`

```bash
./ymva simulate                 # 4 calls: a quick regression check after any change
./ymva simulate --calls 20      # load test
./ymva simulate --agent "Interest reminder"
```

**What it does:**

- Makes spoken test calls to the oldest published agent, or the one named with `--agent`.
- Half are incoming calls. They are spread across the Twilio, Telnyx, Exotel and Vonage formats.
- The other half are outgoing calls from a temporary campaign.
- A simulated customer answers in Kannada, then promises to pay.

**Each call passes when:**

- the agent greets within 8 seconds;
- it replies to the customer;
- the after-call analysis records an outcome;
- no service error is logged.

At the end it prints the greeting and reply times, the outcomes, and the API container's CPU use. It exits non-zero if any call failed, so it can gate a deploy.

It uses real Gemini and Sarvam calls, which costs a few rupees per call. Afterwards it removes everything it created; pass `--keep` to look at the calls in **Calls** first.

---

## 7. Backups

**Automatic:** the `backup` container runs once a day. It:

- writes `backups/auto-YYYYMMDD-HHMMSS.sql.gz`;
- keeps 14 days of these;
- copies new recordings into `backups/recordings/`.

`./ymva update` and `./ymva restore` also take their own backups, `ymva-*.sql.gz`.

**Check that backups work, monthly:**

```bash
./ymva verify-backup
```

This restores the newest backup into a scratch database and counts what's in it. Live data is not touched.

**Copy backups off the server.** A server that dies takes `./backups` with it.

- **AWS**, with the IAM role from 2b. Run `crontab -e` as the YMVA user and add:

  ```
  30 3 * * * aws s3 sync /home/ubuntu/ymva/backups s3://yellowmetal-ymva-backups/ --only-show-errors
  ```

  Install the CLI first with `sudo snap install aws-cli --classic`.
- **VPS:** use `rclone sync` to any storage (S3, Backblaze B2, Google Drive), or `rsync` to another machine, in the same cron slot.

**Restore on a new server:**

1. Install as in section 3, using the **same `.env`**.
2. Copy the backup file into `backups/`.
3. Run `./ymva restore backups/<file>`.
4. Copy `backups/recordings/` back into the recordings volume:

   ```bash
   docker compose cp backups/recordings/. api:/data/recordings/
   ```

---

## 8. Monitoring and alerts

**Settings → Health & alerts** shows:

- live calls and the last hour's results;
- typical reply time;
- today's cost;
- campaign and dialer status;
- the age of the newest backup;
- any failing provider account or AI key.

**Alerts:** paste a Slack, Google Chat or Discord webhook URL in **Settings → Health & alerts**, then press **Send a test alert**. YMVA posts a message when a check starts failing, and again when it recovers. The checks are:

| Check | Fires when |
|---|---|
| Failed calls | 30% or more of the last hour's calls (at least 5) failed to connect |
| Service errors | 10 or more AI/provider errors on calls in 15 minutes, usually a quota or rate limit |
| Dialer | campaigns are running but the dialer hasn't run for 2 minutes |
| Backups | no backup, or the newest is more than 30 hours old |
| Disk space | less than 10% free on the disk that holds recordings |
| Integrations | a telephony account or AI key fails its check |

**Graphs (optional):** `./ymva start --domain … --monitoring` also runs Prometheus and Grafana. Grafana listens only on the server itself, so reach it through an SSH tunnel:

```bash
ssh -L 3001:127.0.0.1:3001 ymva@SERVER_IP
```

Then open http://localhost:3001. The user is `admin` and the password is `GRAFANA_PASSWORD` in `.env`. The dashboard is called "YMVA".

**Uptime from outside:** point an external monitor, such as UptimeRobot or AWS Route 53 health checks, at `https://your-domain/api/health`.

---

## 9. Security checklist

- [ ] SSH accepts keys only (`PasswordAuthentication no` in `/etc/ssh/sshd_config`).
- [ ] Firewall or security group: only 80 and 443 are open to everyone; 22 is limited to you where possible.
- [ ] The admin password is changed and every user has their own account.
- [ ] `.env` is backed up somewhere safe and nowhere public.
- [ ] Off-site backups run, and `./ymva verify-backup` passes.
- [ ] Unattended OS security updates are on (`sudo apt install unattended-upgrades`).
- [ ] An alert webhook is set and its test message arrived.
- [ ] Tools only reach the internet. Leave `EGRESS_ALLOW_PRIVATE=false` unless a tool must call a system on your private network.
- [ ] `METRICS_TOKEN` is set in `.env` (`./ymva start` adds it). Without it, `/api/metrics` stays off.
