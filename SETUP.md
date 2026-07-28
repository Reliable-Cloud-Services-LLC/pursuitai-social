# PursuitAI Social Engine — Setup Guide

One-time setup (~45 min). After this, the engine posts to X and Instagram every day, Mon–Sat at 9:30 AM ET, with zero human involvement.

## How it works

GitHub Actions runs `engine/run.py` on a daily cron. Each run picks the next feature topic (round-robin through 24 topics in `content/calendar.json`), rotates the format (branded card → live site screenshot → card → short video), builds captions, and posts through the official X and Instagram APIs. State and a post log are committed back to the repo. If `ANTHROPIC_API_KEY` is set, Claude rewrites each caption fresh so posts never repeat verbatim across cycles.

## Step 1 — Create the GitHub repo

1. Create a **public** repo (public is required — Instagram fetches media from `raw.githubusercontent.com`). Name suggestion: `pursuitai-social`.
2. Push this entire folder to it:
   ```bash
   cd pursuitai-social-engine
   git init && git add -A && git commit -m "social engine"
   git branch -M main
   git remote add origin git@github.com:<you>/pursuitai-social.git
   git push -u origin main
   ```
   If you'd rather keep the repo private, host media elsewhere (S3/Cloudflare R2 public bucket) and set `MEDIA_BASE_URL` accordingly.

## Step 2 — X (Twitter) API credentials

1. Log into https://developer.x.com with the **@pursuit_ai** account → sign up for the **Free** tier (allows posting; ~500 writes/month app-level, plenty for 1/day).
2. Create a Project + App. In **App settings → User authentication settings**: enable **OAuth 1.0a**, set App permissions to **Read and write** (website/callback URL can be `https://pursuitai.net`).
3. In **Keys and tokens**, generate:
   - API Key + Secret → secrets `X_API_KEY`, `X_API_SECRET`
   - Access Token + Secret (must show "Read and Write" — regenerate after changing permissions) → `X_ACCESS_TOKEN`, `X_ACCESS_SECRET`

## Step 3 — Instagram account + Graph API

You don't have an Instagram account yet, so:

1. **Create the Instagram account** (e.g. `@pursuitai` or `@pursuit.ai`). In the app: Settings → Account type → switch to **Professional → Business**.
2. **Create a Facebook Page** for PursuitAI (required bridge), then link it: Instagram Settings → Business tools → Connect a Facebook Page.
3. **Create a Meta app** at https://developers.facebook.com → Create App → type **Business**. Add the **Instagram Graph API** and **Facebook Login for Business** products.
4. Get a **long-lived access token**:
   - Open Graph API Explorer (https://developers.facebook.com/tools/explorer), select your app, click "Get User Access Token" with scopes: `instagram_basic`, `instagram_content_publish`, `pages_show_list`, `business_management`.
   - Exchange it for a long-lived token (60 days):
     ```
     GET https://graph.facebook.com/v21.0/oauth/access_token?grant_type=fb_exchange_token&client_id=<APP_ID>&client_secret=<APP_SECRET>&fb_exchange_token=<SHORT_TOKEN>
     ```
   - For a **never-expiring** token, create a System User in Meta Business Suite → Business Settings → System Users, assign the app + page assets, and generate the token there (recommended for true autonomy).
   → secret `IG_ACCESS_TOKEN`
5. Get your **Instagram user ID**:
   ```
   GET https://graph.facebook.com/v21.0/me/accounts?access_token=<TOKEN>          → page ID
   GET https://graph.facebook.com/v21.0/<PAGE_ID>?fields=instagram_business_account&access_token=<TOKEN>
   ```
   → secret `IG_USER_ID`

Note: while your Meta app is in Development mode it can post to accounts that have a role on the app (your own) — that's all this engine needs. No App Review required.

## Step 4 — GitHub secrets

Repo → Settings → Secrets and variables → Actions → New repository secret:

| Secret | Required | Purpose |
|---|---|---|
| `X_API_KEY` / `X_API_SECRET` | yes | X app consumer keys |
| `X_ACCESS_TOKEN` / `X_ACCESS_SECRET` | yes | @pursuit_ai write access |
| `IG_USER_ID` | yes | Instagram business account ID |
| `IG_ACCESS_TOKEN` | yes | long-lived Graph API token |
| `ANTHROPIC_API_KEY` | optional | fresh Claude-written caption variants |
| `NOTIFY_WEBHOOK_URL` | strongly recommended | Slack incoming webhook. Alerts on any run that doesn't fully publish, plus a weekly heartbeat. **Without it every failure is silent** — which is exactly how nine runs published nothing while reporting success. |

To create the Slack webhook: Slack → your workspace → Apps → **Incoming Webhooks** → Add to Slack → pick the channel → copy the URL. The engine posts a plain `{"text": "..."}` payload; unset the variable and every notification becomes a silent no-op.

## Step 5 — First run

1. Repo → Actions → enable workflows.
2. Run **Daily social post** manually with `dry_run = true`. Check the committed `content/pending.json` + assets — that's exactly what would have gone out.
3. Happy? Run it again with `dry_run = false`, or just let the 9:30 AM ET cron take over.

## Ongoing autonomy

- **Content**: 24 topics × 4 formats ≈ 3+ months before any topic+format pair repeats; with `ANTHROPIC_API_KEY` set, wording is regenerated every time.
- **Adding topics**: append to `content/calendar.json` — new features, customer stories, promos. Nothing else changes.
- **Cadence**: edit the cron in `.github/workflows/daily.yml`. Two posts/day: add a second cron line (e.g. `30 21 * * 1-5` for 5:30 PM ET).
- **Kill switch**: disable the workflow in the Actions tab.
- **Token maintenance**: X tokens don't expire. IG System User tokens don't expire; user-exchanged tokens need refreshing every ~60 days (the engine will start logging IG failures in `logs/posted.jsonl` if the token lapses — the X side keeps running regardless).

## Attribution (UTM tagging)

Every clickable link the engine emits is tagged so the admin dashboard can
attribute a visit to a specific post:

```
utm_source=<platform>   utm_medium=organic
utm_campaign=<topic_id> utm_content=<format>
```

All links point at the **public landing page** (`pursuitai.net`), never
`pursuitai.net/app` — the app URL opens the sign-in screen, which shows a
first-time visitor none of the marketing they just clicked for.

Three places deliberately show the bare domain instead of a tagged link:

- **Cards, video, and screenshot footers.** The URL is drawn into the image.
  A UTM string baked into pixels is unclickable and ugly.
- **Instagram captions.** IG captions are not hyperlinked, so a tagged URL
  there is ~100 characters a human would have to retype. See below.

### One-time manual step — the Instagram bio link

Instagram's only clickable link is the one in the profile bio, so that is
where IG attribution comes from. Set it once:

```bash
python engine/links.py     # prints the exact URL to paste
```

Instagram → Edit profile → Website → paste the printed bio link. It is
generated from `content/calendar.json`, so it cannot drift from the code.

Per-post Instagram attribution is not achievable without a per-post short
link; the bio link attributes Instagram traffic in aggregate.

## Platform-rules notes (keep it boring, keep it safe)

- Posting your own product content on a schedule is fully within X automation rules and Instagram platform terms; both endpoints used here are the official, documented publish APIs.
- Instagram API caps content publishing at 50 posts/24h — we use 1.
- Don't add follow/unfollow, DM, or mass-reply automation to this engine; that's where accounts get flagged.

## Local testing

```bash
pip install -r requirements.txt
python -m playwright install chromium
python engine/run.py --dry-run          # generate + print, post nothing
python engine/run.py --skip-ig          # post to X only (after exporting env vars)
```
