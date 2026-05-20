# Self-Hosting Switchboard on Your Own VPS

A step-by-step guide for someone who doesn't write code. Goal:
**Switchboard running at your own domain on your own VPS**, with
auto-HTTPS, in ~20 minutes (plus DNS propagation).

> What you'll have at the end:
> - **API** at `https://api.your-domain.com`
> - **Web** at `https://app.your-domain.com` (operator dashboard)
> - **Postgres** + **document storage** persisting on the VPS
> - **Caddy** reverse proxy with auto-renewed Let's Encrypt certs
> - **One-line update**: `bash deploy/update.sh` after each `git push`

If you'd rather use Railway (managed, no Linux knowledge needed),
see [DEPLOY.md](DEPLOY.md). VPS gives you full control and avoids
platform lock-in; Railway is faster to set up.

---

## What you need

1. **A VPS** — Ubuntu 22.04 LTS or newer. Minimum **1 vCPU / 2 GB RAM**
   (Hetzner CX22 or DigitalOcean Basic 2GB is plenty for a single
   client; bump to 4 GB if you'll run 3+ clients with heavy agent
   loads). 25 GB disk is plenty.
2. **SSH access** to the VPS as a sudo user (or root).
3. **A domain** you control (Namecheap, Cloudflare, GoDaddy, etc.)
   and the ability to add DNS A records.
4. **An Anthropic API key** — `console.anthropic.com`. Set a monthly
   spend cap there before you do anything else.

---

## Step 1 — Point DNS at the VPS (do this FIRST, ~5 min)

Caddy auto-fetches Let's Encrypt certs the moment containers start,
but only if DNS already resolves. So set DNS up first, then SSH in.

In your registrar's DNS panel (Namecheap → Advanced DNS, Cloudflare
→ DNS, etc.) add two **A records** pointing at your VPS's public IP:

| Type | Host | Value |
|---|---|---|
| `A` | `api` | `<your-VPS-public-IP>` |
| `A` | `app` | `<your-VPS-public-IP>` |

If using Cloudflare, set both records to **DNS only** (gray cloud,
not orange) for the first deploy — Cloudflare's proxy interferes
with Let's Encrypt's HTTP-01 challenge. You can re-enable the proxy
after certs are issued.

While DNS propagates (usually 1-5 min, occasionally 30+), continue
to Step 2. You can confirm propagation with `dig api.your-domain.com`
on your laptop — it should return your VPS IP.

---

## Step 2 — SSH in + clone the repo (~3 min)

```bash
ssh root@your-vps-ip

# Update + install git (Docker gets installed automatically in Step 3)
apt update && apt install -y git

# Clone the repo
cd /opt
git clone https://github.com/steven-patrick18/switchboard.git
cd switchboard
```

> The repo is public (no credentials needed). If you've forked it,
> use your fork's URL instead.

---

## Step 3 — Run the installer (~5 min)

```bash
bash deploy/install.sh
```

The installer is idempotent. Run it twice:

**First run** — installs Docker (if missing), creates `deploy/.env`
from the template, generates secure random values for
`POSTGRES_PASSWORD` and `APP_SECRET_KEY`, then **stops** and tells you
to fill in your domains.

Edit `deploy/.env` with `nano` or `vim`:

```bash
nano deploy/.env
```

Set:

```env
API_DOMAIN=api.your-domain.com
WEB_DOMAIN=app.your-domain.com
ACME_EMAIL=your-real-email@your-domain.com
```

Save and exit (Ctrl-O, Enter, Ctrl-X in nano).

> **Important about secrets**: leave `POSTGRES_PASSWORD` and
> `APP_SECRET_KEY` as the installer-generated values. **Never change
> `APP_SECRET_KEY` after you've stored any credentials** — it's the
> key the credential vault uses, so changing it makes existing stored
> secrets un-decryptable. Back the `.env` up somewhere safe (a password
> manager).

**Second run**:

```bash
bash deploy/install.sh
```

This time it builds the Docker images, starts all four containers
(postgres, api, web, caddy), and Caddy fetches Let's Encrypt certs
in the background.

Watch the certs being issued:

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml logs -f caddy
```

You'll see `obtaining certificates ... successfully obtained certificate`
within 30-90 seconds. Ctrl-C to stop tailing.

---

## Step 4 — Verify (~2 min)

In a browser, visit:

- **`https://api.your-domain.com/health`** — should return
  `{"status":"ok","environment":"production"}`. If you see a
  Let's Encrypt error, DNS hasn't propagated yet; wait 5 min
  and try again.
- **`https://app.your-domain.com`** — the Switchboard login page.
  **Sign up** with your real email and a strong password. The
  first account you create is your operator account.

Once signed in, go to **Settings → Platform readiness**:

- **Anthropic API key**: paste your Anthropic key, click
  **Save Anthropic settings**. The card should flip to `ready`.
- (Optional) **Email notifications**: paste SMTP settings if you
  want emails when an agent queues an approval. Test with the
  **Send test email** button.
- The bottom row shows **Version: v1.0.0** and the commit hash
  Caddy is serving, plus a **What's new ↗** link to the CHANGELOG.

---

## Step 5 — Updating after a git push (the whole point)

Whenever this repo updates on `main`, run **one command** on the VPS:

```bash
ssh root@your-vps-ip
cd /opt/switchboard
bash deploy/update.sh
```

The script:
1. `git pull` from origin/main.
2. Rebuilds only the containers whose source changed (compose
   detects this from build context hashes).
3. Restarts containers in dependency order — api waits for postgres
   to be healthy, web waits for api, caddy waits for both.
4. The api container runs `alembic upgrade head` on boot, so schema
   migrations apply automatically.

Total: ~30 seconds when only code changed, ~2 minutes when
dependencies changed.

Roll back: `git log` to find the previous commit, then
`git checkout <hash>` and rerun update.sh. Migrations are NOT
auto-reverted; run `docker compose exec api python -m alembic
downgrade -1` if needed.

### Optional: auto-pull every 5 minutes

If you want the VPS to track `main` automatically (the closest
self-hosted equivalent of Railway's auto-deploy), drop this in
the root user's cron:

```bash
crontab -e
```

Add:

```cron
*/5 * * * * /opt/switchboard/deploy/update.sh >> /var/log/switchboard-update.log 2>&1
```

It's a no-op when there's nothing new on `main` (the script checks
`git rev-parse` and exits early).

---

## Step 6 — Backups (~2 min, then forget about it)

Daily Postgres dumps + a tar of the documents volume:

```bash
mkdir -p /var/backups/switchboard
crontab -e
```

Add:

```cron
0 3 * * * /opt/switchboard/deploy/backup.sh /var/backups/switchboard
```

The script keeps the last 14 days, so disk usage stays bounded.

**Off-site copy** — schedule an `rsync` to S3, Backblaze B2, or
another VPS so a single-VPS failure doesn't lose data. Example
nightly off-site:

```cron
30 3 * * * rsync -az --delete /var/backups/switchboard/ \
    backup-host:/srv/switchboard-backups/
```

---

## Day-2 operations

| Task | Command |
|---|---|
| Tail all logs | `docker compose --env-file deploy/.env -f deploy/docker-compose.yml logs -f` |
| Tail just one service | `docker compose ... logs -f api` (or `web`, `caddy`, `postgres`) |
| Restart the API only | `docker compose ... restart api` |
| Apply a schema change manually | `docker compose ... exec api python -m alembic upgrade head` |
| Drop into the DB | `docker compose ... exec postgres psql -U switchboard switchboard` |
| Disk usage | `docker system df` |
| Clean dangling images | `docker image prune -f` |
| Stop everything | `docker compose --env-file deploy/.env -f deploy/docker-compose.yml down` |
| Restart everything | `docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d` |

Set an alias in your shell to skip the long compose flags:

```bash
echo "alias sb='docker compose --env-file /opt/switchboard/deploy/.env -f /opt/switchboard/deploy/docker-compose.yml'" >> ~/.bashrc
source ~/.bashrc
# now just:  sb logs -f api   /   sb ps   /   sb restart api
```

---

## Things you should know

- **Domains baked at build time**: `NEXT_PUBLIC_API_URL` is baked
  into the web bundle when the web image builds. If you change
  `API_DOMAIN` later, you must rebuild the web image — `update.sh`
  already does this when `.env` changes.
- **Storage lives on the VPS**: Postgres data + uploaded documents
  are in named Docker volumes (`postgres_data`, `documents_data`).
  Survive container rebuilds; lost with `docker compose down -v`
  (note the `-v`). Backups are your safety net.
- **HTTPS just works**: as long as DNS resolves, Caddy obtains and
  renews certs automatically. No certbot. No nginx config. Renewal
  happens 30 days before expiry without intervention.
- **Resource limits**: a 2 GB VPS comfortably runs the stack for
  1-3 active clients. Heavy concurrent agent runs benefit from
  4 GB. Postgres dominates the memory; the API and web are small.
- **Hardening reminders for production**: change the SSH port,
  disable root SSH login (use `ufw` to allow only ports 22, 80,
  443), set up fail2ban. None of this is Switchboard-specific —
  every VPS gets the same treatment.

---

## Troubleshooting

**Caddy logs say "no such host" or "ACME challenge failed"** —
DNS hasn't propagated yet OR the A records point to the wrong
IP. Verify with `dig api.your-domain.com` from your laptop; the
ANSWER section should show your VPS IP. Wait a few minutes
and `docker compose restart caddy`.

**Browser shows "not secure" / certificate error** — same root
cause as above. Caddy needs DNS to be live before it can prove
ownership to Let's Encrypt.

**API container restarts on a loop** — usually a bad
`APP_SECRET_KEY` or `DATABASE_URL`. `docker compose logs api`
shows the stack trace. If migrations failed,
`docker compose exec api python -m alembic current` shows the
last applied revision; `python -m alembic upgrade head` reapplies.

**Want to start over** —
`docker compose --env-file deploy/.env -f deploy/docker-compose.yml down -v`
**deletes all data**, then `bash deploy/install.sh` rebuilds
from scratch. Only do this if you have a backup or genuinely
want a clean slate.
