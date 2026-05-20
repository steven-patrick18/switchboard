# Deploying Switchboard to Railway

A step-by-step guide for someone who doesn't write code. Goal:
**Switchboard running at your own domain, in production**, in about
30 minutes.

> What you'll have at the end:
> - **API** at `https://api.your-domain.com` (or `*.up.railway.app`)
> - **Web** at `https://your-domain.com` (the operator dashboard)
> - **Postgres** managed by Railway (your data lives here)
> - **Auto-deploy** from GitHub — push to `main`, Railway rebuilds
>   and ships it.

---

## Be honest about what you DON'T need yet

When this project was scaffolded, the `.env.example` listed
placeholders for Telegram, Gmail OAuth, DocuSign, Sentry, and a
few other services. **None of those are wired into the platform
right now.** Don't sign up for them; they were brainstorming
artifacts. The cleaned-up `.env.example` (post-2026-05) lists only
what's actually used.

**What you actually need accounts for:**

| Service | Why | Required? |
|---|---|---|
| GitHub | Stores the code; Railway pulls from it | ✅ yes |
| Railway | Hosts the API + Web + Postgres | ✅ yes |
| Anthropic Console | API key for the AI agents | ✅ yes (or agents return 503) |
| Namecheap (or any registrar) | Your custom domain | optional |
| Any SMTP provider (Gmail App Password, SendGrid, Mailgun, Postmark, AWS SES) | Email notifications | optional |

That's it. Five accounts, four of them optional.

---

## Prerequisites (one-time, ~10 min)

1. **GitHub account** with this repo pushed (you already have one:
   `https://github.com/steven-patrick18/switchboard`).
2. **Railway account**: sign up at `railway.app` with your GitHub
   account. Free tier covers a small workload; the $5/month
   "Hobby" plan covers a real client easily.
3. **Anthropic API key**: go to `console.anthropic.com` → API Keys
   → Create Key. **Set a monthly spend cap** in Settings → Limits
   before you do anything else (so a runaway agent can't burn
   $thousands).
4. *(Optional)* **Your domain** purchased at Namecheap (or any
   registrar). Skip this step and you get `*.up.railway.app`
   subdomains — fine for testing.

---

## Step 1 — Create the Railway project

1. Go to `railway.app` → **New Project** → **Deploy from GitHub repo**.
2. Pick `steven-patrick18/switchboard`.
3. Railway will try to auto-detect a service. **Cancel** the auto
   deploy — you'll add services manually so the API and Web get
   their own Dockerfiles.

---

## Step 2 — Add Postgres

1. In your Railway project → **+ New** → **Database** → **Add
   PostgreSQL**. Done.
2. Railway will set a `DATABASE_URL` environment variable that
   other services in this project can reference as
   `${{Postgres.DATABASE_URL}}`.

---

## Step 3 — Deploy the API service

1. **+ New** → **GitHub Repo** → pick `switchboard` → name the
   service **`api`**.
2. Open the service → **Settings** tab:
   - **Root Directory**: `apps/api`
   - **Build**: Dockerfile (auto-detected from `apps/api/Dockerfile`)
   - **Watch Paths**: `apps/api/**` (so the API only rebuilds when
     backend code changes)
3. Still in **Settings** → **Networking** → **Generate Domain**
   (gives you something like `api-production-abc.up.railway.app`).
4. **Variables** tab — add:

   ```
   DATABASE_URL=${{Postgres.DATABASE_URL}}
   APP_SECRET_KEY=<long-random-string>
   ENVIRONMENT=production
   CORS_ORIGINS=https://<web-service-url-from-step-4>
   ANTHROPIC_API_KEY=<paste-from-console.anthropic.com>
   APP_BASE_URL=https://<web-service-url-from-step-4>
   ```

   For `APP_SECRET_KEY`, run this anywhere (or use any password
   manager's generator):

   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

   **Save it somewhere** — if you ever change this value, all stored
   credentials become un-decryptable.

   For `CORS_ORIGINS` and `APP_BASE_URL`, you don't know the web
   URL yet; put a placeholder for now and update after Step 4.

5. **Deploy**. The first build takes 2-3 minutes. When it's green,
   visit `https://<your-api-url>/health` — you should see
   `{"status":"ok","environment":"production"}`.

---

## Step 4 — Deploy the Web service

1. **+ New** → **GitHub Repo** → same `switchboard` repo → name
   it **`web`**.
2. **Settings**:
   - **Root Directory**: `apps/web`
   - **Build**: Dockerfile (auto-detected from `apps/web/Dockerfile`)
   - **Watch Paths**: `apps/web/**`
3. **Networking** → **Generate Domain**.
4. **Variables** — set the API URL as a **build-time** arg (Next.js
   inlines `NEXT_PUBLIC_*` at build, not runtime):

   ```
   NEXT_PUBLIC_API_URL=https://<api-url-from-step-3>
   ```

5. **Settings** → **Build** → in "Build Args" add the same:
   - `NEXT_PUBLIC_API_URL` = `https://<api-url-from-step-3>`

   (Railway needs it as both an env var AND a build arg.)

6. **Deploy**. After it's green, you have a working operator UI.

---

## Step 5 — Fix the CORS / public URLs you left blank

Go back to the **api** service → **Variables**:

- `CORS_ORIGINS` = `https://<web-url-from-step-4>`
- `APP_BASE_URL` = `https://<web-url-from-step-4>`

Save. The API restarts automatically.

---

## Step 6 — First login + create your operator account

1. Visit your web URL.
2. **Sign up** with your real email and a strong password. The
   first account you create has no special privileges — but for
   now it's *your* account.
3. Go to **Settings** → **Platform readiness**. You should see:
   - **Anthropic API key**: `ready` (set from the env var in Step 3)
   - **Email notifications**: `not set`
   - **Public URL**: `ready`

---

## Step 7 (optional) — Email notifications

To get an email when an agent queues an approval:

1. **Pick an SMTP provider**:
   - **Gmail (easiest, free)**: Google Account → Security → 2-Step
     Verification → App Passwords → make one for "Switchboard".
     SMTP host `smtp.gmail.com`, port `587`, TLS on.
   - **Postmark / SendGrid / Mailgun / AWS SES**: paid but
     deliverability is better than personal Gmail. Get SMTP
     credentials from their dashboard.
2. In Switchboard → **Settings** → **Email notifications**: paste
   host, port, username, password, from-address. **Save SMTP
   settings**.
3. Click **Send test email** → check your inbox. If it lands,
   you're done.

---

## Step 8 (optional) — Your own domain

1. In Railway, **api** service → **Networking** → **Custom Domain**
   → enter `api.your-domain.com`. Railway shows you a CNAME target.
2. **web** service → same thing for `app.your-domain.com` (or just
   `your-domain.com` if you set up the apex via Namecheap's ALIAS
   record).
3. In **Namecheap** → Domain → **Advanced DNS**:
   - Add a `CNAME` record: host `api`, value `<railway-target>`.
   - Add a `CNAME` record: host `app`, value `<railway-target>`.
   - For the apex `your-domain.com` → use Namecheap's ALIAS record
     pointing to the Railway target.
4. Wait 5-10 min for DNS to propagate. Railway auto-provisions a
   Let's Encrypt cert when the CNAME resolves.
5. Back in Switchboard → **Settings** → update `APP_BASE_URL` to
   `https://app.your-domain.com` so notification emails carry the
   right link. Also update `CORS_ORIGINS` on the **api** service
   to include the new web origin.

---

## Step 9 — Verify by creating a real client

1. **Workspaces** → **Add client** → name it.
2. Click the client → fill the **Intake** form → upload at least
   one document.
3. **AI Agents** → look around. You have six built-in agents and
   can edit/clone any of them. The "Lessons" button on each agent
   is empty for now — it fills up as you reject/edit approvals.
4. Back on the client page → **Run an agent** → pick `intake` →
   instruction: "List what you'd need to ask the client". Hit
   **Run**. You should get an answer from Claude and (depending
   on what it proposed) one or more rows in your approval queue.

If all of that worked, you're live.

---

## Day-2 operations

| Task | Where |
|---|---|
| Add / rotate Anthropic key | Settings → Anthropic (Claude) API |
| Change SMTP host or password | Settings → Email notifications |
| Add a new AI agent | AI Agents → + New agent |
| See what an agent has learned | AI Agents → Lessons (N) button |
| Export full audit trail | Dashboard → Export audit ↓ |
| Archive everything for a client | Client detail → Archive ↓ |
| Generate a magic link for the client | Client detail → Client share links |
| Restart a service | Railway → service → ⋯ → Restart |
| See logs | Railway → service → Deploys → click the build → Logs |

---

## Things you should be aware of

- **Document storage** lives on the API container's local disk
  (`.documents/`). Railway containers are ephemeral — they get
  reset on every deploy. **For production**, attach a Railway
  **Volume** to the API service mounted at `/app/.documents` so
  uploaded files survive deploys. (When you outgrow that, the
  next step is S3; the storage module is one file you swap.)
- **APP_SECRET_KEY** is used for both JWT signing and credential
  encryption. Changing it logs everyone out AND makes stored
  credentials un-decryptable. Don't change it casually.
- **Costs**: Anthropic charges per token (the platform shows
  $cost per agent run on the Dashboard). Railway runs $5-20/month
  for a small workload. Postgres and bandwidth are included on
  the Hobby plan.
- **Backups**: Railway Postgres has built-in daily backups on
  paid plans. Restoring is one click.
