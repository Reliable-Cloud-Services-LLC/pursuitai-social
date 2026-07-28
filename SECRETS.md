# Secrets & Credentials Reference

Every credential the engine uses, where to get it, and what breaks without it.

All of these are set as **GitHub repository secrets**:
Repo → **Settings → Secrets and variables → Actions → New repository secret**.

Nothing is ever read from a file or hardcoded. Locally, export them in your
shell or put them in a `.env` you never commit (`.env` is gitignored).

---

## The complete list

| Secret | Required? | Platform | Without it |
|---|---|---|---|
| `X_API_KEY` | yes, for X | X developer portal | X is **skipped** — the run exits non-zero and alerts |
| `X_API_SECRET` | yes, for X | X developer portal | X posts fail at auth |
| `X_ACCESS_TOKEN` | yes, for X | X developer portal | X posts fail at auth |
| `X_ACCESS_SECRET` | yes, for X | X developer portal | X posts fail at auth |
| `IG_USER_ID` | yes, for Instagram | Meta Graph API | Instagram is **skipped** |
| `IG_ACCESS_TOKEN` | yes, for Instagram | Meta Graph API | Instagram posts fail at auth |
| `NOTIFY_WEBHOOK_URL` | strongly recommended | Slack | **Every alert and review request is silently dropped.** The approval gate becomes a silent stall — posts are prepared daily and nobody is ever told. |
| `ANTHROPIC_API_KEY` | optional | Claude Console | Captions fall back to the hand-written hooks in `calendar.json`. Degrades cleanly; nothing breaks. |

Only `X_API_KEY` and `IG_USER_ID` gate whether a channel is *attempted* — the
engine checks those two as the "are credentials present" signal. The rest fail
at authentication time if missing, which surfaces as a `failed` channel.

### Not secrets — supplied automatically

| Variable | Source | Purpose |
|---|---|---|
| `MEDIA_BASE_URL` | computed in the workflow | Public base URL Instagram fetches media from. Built from `github.repository` + `github.ref_name`. |
| `REVIEW_URL` | computed in the workflow | Link to the Actions run, included in the Slack review request. |
| `GITHUB_ACTOR` | GitHub Actions | Recorded in `approved.json`. |
| `GITHUB_ACTIONS` | GitHub Actions | Set to `true` in CI, which is how `--force` refuses to run there. |

---

## X (Twitter) — 4 secrets

You need a developer account on the **@pursuit_ai** account itself.

1. Sign in at <https://developer.x.com> as @pursuit_ai and open the developer portal.
2. Create a **Project**, then an **App** inside it.
3. **App settings → User authentication settings**:
   - Enable **OAuth 1.0a**
   - App permissions: **Read and write** (the default is read-only, and posting will 403 without this)
   - Callback URL / Website URL can both be `https://pursuitai.net`
4. **Keys and tokens** tab:
   - **API Key and Secret** → `X_API_KEY`, `X_API_SECRET`
   - **Access Token and Secret** → `X_ACCESS_TOKEN`, `X_ACCESS_SECRET`

> **Order matters.** The access token embeds the permission level at the moment
> it was generated. If you set Read-and-write *after* generating it, you must
> **regenerate** the access token — otherwise it silently stays read-only and
> every post fails with a 403.

**Expiry:** OAuth 1.0a user tokens do not expire. They stay valid until you
revoke them or change app permissions.

**Verify without posting:**
```bash
export X_API_KEY=... X_API_SECRET=... X_ACCESS_TOKEN=... X_ACCESS_SECRET=...
python scripts/validate_x.py
```

---

## Instagram — 2 secrets

Instagram publishing requires a **Professional (Business)** account linked to a
**Facebook Page**. The Page is a mandatory bridge; there is no way around it.

1. **Instagram app** → Settings → Account type → switch to **Professional → Business**.
2. **Create a Facebook Page** for PursuitAI, then link it:
   Instagram → Settings → Business tools → Connect a Facebook Page.
3. **Create a Meta app** at <https://developers.facebook.com> → Create App → type **Business**.
   Add the **Instagram Graph API** and **Facebook Login for Business** products.
4. **Get a long-lived token.** In the [Graph API Explorer](https://developers.facebook.com/tools/explorer),
   select your app and request these scopes:
   `instagram_basic`, `instagram_content_publish`, `pages_show_list`,
   `pages_read_engagement`, `business_management`

   Then exchange the short token for a 60-day one:
   ```
   GET https://graph.facebook.com/v21.0/oauth/access_token
       ?grant_type=fb_exchange_token
       &client_id=<APP_ID>
       &client_secret=<APP_SECRET>
       &fb_exchange_token=<SHORT_TOKEN>
   ```
   → `IG_ACCESS_TOKEN`

   **Better: use a System User token, which never expires.** Meta Business Suite
   → Business Settings → Users → **System Users** → create one, assign the app
   and Page assets, generate a token with the same scopes. This is the right
   choice for anything scheduled — a 60-day token *will* lapse and take
   Instagram down until someone notices.

5. **Get the Instagram user ID:**
   ```
   GET https://graph.facebook.com/v21.0/me/accounts?access_token=<TOKEN>
       → the Page ID
   GET https://graph.facebook.com/v21.0/<PAGE_ID>?fields=instagram_business_account&access_token=<TOKEN>
       → the Instagram business account ID
   ```
   → `IG_USER_ID`

**Publishing cap:** Instagram allows **100 API-published posts per rolling
24-hour period**. We use 1. Carousels count as one.

**Verify without publishing:**
```bash
export IG_USER_ID=... IG_ACCESS_TOKEN=... MEDIA_BASE_URL=...
python scripts/validate_ig.py --container
```
The `--container` flag creates a real media container to prove Instagram can
actually *fetch* your media URL, then never publishes it — unpublished
containers expire harmlessly after 24h. This is the single most useful check,
because a media URL Instagram can't reach is the most common silent failure.

---

## Slack — 1 secret

Used for failure alerts, the weekly heartbeat, and the daily review request
that the approval gate depends on.

1. Go to <https://api.slack.com/apps> → **Create New App** → *From scratch*.
   Name it (e.g. "PursuitAI Social") and pick your workspace.
2. In the app settings, open **Incoming Webhooks** and toggle
   **Activate Incoming Webhooks** on.
3. Click **Add New Webhook to Workspace**, choose the destination channel, and
   authorize.
4. Copy the URL under *Webhook URLs for Your Workspace* → `NOTIFY_WEBHOOK_URL`.

   It has the shape `https://hooks.slack.com/services/` followed by three
   path segments: a workspace id, a webhook id, and a long random token.

   > A literal example is deliberately not printed here. GitHub's push
   > protection scans for that pattern and will block the push — correctly,
   > since it cannot tell a placeholder from a live webhook.

That URL **is** the credential — anyone holding it can post to your channel.
The engine never prints it, even when a send fails.

**Expiry:** none. It stays valid until revoked or the app is removed.

---

## Anthropic (Claude) — 1 secret, optional

Used only to rewrite captions so posts never repeat verbatim. Without it the
engine uses the hand-written `hook_x` / `hook_ig` copy from `calendar.json`,
which is good copy — this is a nice-to-have, not a dependency.

1. Sign in at <https://platform.claude.com>.
2. **Account Settings → API keys** (<https://platform.claude.com/settings/keys>)
   → create a key. → `ANTHROPIC_API_KEY`

**Expiry:** you choose an expiration when creating the key. Note the date — an
expired key doesn't break anything, but caption variation silently stops and
every post reverts to the template wording.

---

## GitHub — no secret needed

The approval gate uses a **deployment environment**, not a token:

Repo → **Settings → Environments → New environment** → name it exactly
`social-publish` → tick **Required reviewers** → add yourself.

Required reviewers are free on public repositories. **If this repo is ever made
private on a Free plan, that protection stops applying** — re-check it if you
change visibility.

The workflow's `git push` uses the built-in `GITHUB_TOKEN`, which Actions
provides automatically via `permissions: contents: write`.

---

## Operating notes

**Rotation.** If any credential is exposed, revoke it at the source first, then
replace the GitHub secret. Revoking at the source is what actually stops the
bleeding; changing the secret only stops *us* using it.

**Never commit any of these.** `.env` is gitignored. `posted.jsonl` records post
IDs and caption text but no credentials, by design.

**What a lapsed credential looks like.** The run exits non-zero, Slack alerts
you, `logs/posted.jsonl` records `failed` for that channel with the error, and
the topic is **not** consumed — it will be retried once the credential is fixed.
The other channel keeps working. If Slack itself is unconfigured, the weekly
heartbeat is your backstop: it reports how many posts actually went out, so a
zero is visible even when no alert fired.
