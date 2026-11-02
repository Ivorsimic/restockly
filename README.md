# WatchPage

Paste any product URL, get emailed when it restocks or the price drops. Works on any site (JSON-LD structured data first, generic text-pattern fallback otherwise) — not limited to the big retailers that funded trackers (TYPA, Honey, Karma) already cover.

## Status

Fully working locally: detection, freemium tiers (Free/Pro/Speed), background scheduler, email alerts, dev-mode Stripe-ready checkout. Tested end-to-end including an EU price-format bug fix (comma decimals) and a live restock-detection simulation.

Running in **dev mode** until you add real credentials below — emails log to `backend/dev_emails.log` instead of sending, and plan upgrades are granted for free instead of charging.

## The 4 things only you can do (accounts/payment — I can't create these for you)

### 1. Email — Resend (5 min, free, no card needed)
1. Sign up at [resend.com](https://resend.com)
2. Verify a sending domain (or use their test domain to start)
3. Copy the API key into `backend/.env` as `RESEND_API_KEY=` (copy `.env.example` to `.env` first)

### 2. Payments — Stripe (10 min, free to set up)
1. Sign up at [stripe.com](https://stripe.com)
2. Create two Products: "Pro" (€8/mo recurring) and "Speed" (€22/mo recurring)
3. Copy each Price ID into `backend/plans.py` (`stripe_price_id` fields)
4. Copy your secret key into `backend/.env` as `STRIPE_SECRET_KEY=`

### 3. Domain (~10-15€/year)
Any registrar (Namecheap, Porkbun...). Point it at wherever you deploy (step 4).

### 4. Hosting (free tier available)
Render.com or Fly.io both have free web service tiers. Steps on Render:
1. Push this folder to a GitHub repo
2. On Render: New > Web Service > connect the repo, root directory `backend`
3. Build command: `pip install -r requirements.txt`, start command: from `Procfile` (auto-detected)
4. Add your `.env` values as environment variables in Render's dashboard
5. **Important**: Render's free tier disk is not persistent across deploys — the SQLite database will reset on redeploy. Fine for testing; before real launch, either upgrade to a paid plan with a persistent disk, or migrate `db.py` to a hosted Postgres (Render/Neon/Supabase all have free Postgres tiers) — ask me to do this migration when you're ready, it's a small change.

## Running locally

```
cd backend
source venv/bin/activate
uvicorn main:app --host 127.0.0.1 --port 8001
```
Open http://127.0.0.1:8001

## What I'd do next, in order
1. You create the Resend + Stripe accounts (steps above) — 15 min total, then tell me and I'll verify the live flow
2. I migrate SQLite → Postgres so deploys don't wipe data
3. Deploy to Render together
4. Write 2-3 posts for rare-plant / indie-drop communities (you post, in your own voice — I can draft them)
