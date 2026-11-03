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
| `MEDIA_BASE_URL` | yes, for Instagram | your bucket | **Instagram cannot publish.** It fetches media by public URL; without this there is nowhere to fetch from. |
| `MEDIA_BUCKET` | yes | your bucket | The prepare job fails loudly — media has nowhere to go. |
| `MEDIA_ACCESS_KEY_ID` / `MEDIA_SECRET_ACCESS_KEY` | yes | R2 or IAM | Upload and fetch both fail. |
| `MEDIA_ENDPOINT` | R2 only | Cloudflare | Leave unset for AWS S3. |
| `LINKEDIN_ACCESS_TOKEN` | yes, for LinkedIn | LinkedIn Developer Portal | LinkedIn is **skipped** — the other channels are unaffected |
| `LINKEDIN_ORG_ID` | yes, for LinkedIn | `validate_linkedin.py --discover` | LinkedIn posts fail — there is no Page to post as |
| `LINKEDIN_TOKEN_EXPIRES_AT` | strongly recommended | `validate_linkedin.py --discover` | **Nothing can warn before the 60-day token dies.** The first symptom is a channel that quietly stopped posting. |
| `LINKEDIN_CLIENT_ID` | recommended | LinkedIn app → Auth tab | No token introspection: a **revoked** token reads as healthy, and granted scopes are invisible until a post 403s. |
| `LINKEDIN_CLIENT_SECRET` | recommended | LinkedIn app → Auth tab | As above — both are needed for introspection. |

Only `X_API_KEY` and `IG_USER_ID` gate whether a channel is *attempted* — the
engine checks those two as the "are credentials present" signal. The rest fail
at authentication time if missing, which surfaces as a `failed` channel.

### Not secrets — supplied automatically

| Variable | Source | Purpose |
|---|---|---|
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

**Credits, not a free tier.** X is pay-per-usage since February 2026 — you
buy credits and each call consumes them. A zero balance surfaces as
`402 Payment Required — credits depleted` at publish time, *after*
`validate_x.py` passes, because the credentials are fine and only the
billing is not. Budget for two writes per publish (post + threaded reply)
plus the weekly analytics reads.

**Verify without posting:**
```bash
export X_API_KEY=... X_API_SECRET=... X_ACCESS_TOKEN=... X_ACCESS_SECRET=...
.venv/bin/python scripts/validate_x.py
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
.venv/bin/python scripts/validate_ig.py --container
```
The `--container` flag creates a real media container to prove Instagram can
actually *fetch* your media URL, then never publishes it — unpublished
containers expire harmlessly after 24h. This is the single most useful check,
because a media URL Instagram can't reach is the most common silent failure.

---

## LinkedIn — 3 secrets, and two you should NOT store

Requires the **Community Management API** approved on the app (Development
tier is enough — 500 calls/day against our 3–5). See
[docs/LINKEDIN_ACCESS.md](docs/LINKEDIN_ACCESS.md) for the application, and
for the full runbook to obtain these.

### Store the Client ID and Client Secret too

Creating the app hands you both. They are worth storing — not because the
posting path uses them (it does not) but because they unlock **token
introspection**, which answers two questions a stored expiry timestamp
cannot:

```
POST https://www.linkedin.com/oauth/v2/introspectToken
  client_id, client_secret, token  ->  { active, status, expires_at, scope }
```

* **Revocation.** A revoked token keeps a *future* `expires_at`. The
  timestamp path reports it healthy right up until the post fails; `status`
  says `revoked`.
* **Scopes.** A token carries what the approving member consented to, which
  is not necessarily what you meant to tick. Finding out at publish time is
  finding out too late.

It also removes the hand-arithmetic: with these two set, the real
`expires_at` is read from LinkedIn instead of being computed and pasted.

Introspection is an **upgrade, not a dependency**. Without the client
credentials everything still works, falling back to
`LINKEDIN_TOKEN_EXPIRES_AT` — you just lose revocation and scope detection,
and the validator says so rather than implying it checked.

### The three that ARE stored

1. **Mint the token.** [Token Generator](https://www.linkedin.com/developers/tools/oauth/token-generator)
   → select the app → tick `w_organization_social` and
   `rw_organization_admin` → approve as a member holding an **ADMINISTRATOR**
   role on the PursuitAI Page. Note the TTL shown under Token Details.

   The token inherits that member's roles, so approving as someone without
   the Page role yields a token that passes every check and 403s on publish.

2. **Derive the other two:**
   ```bash
   export LINKEDIN_ACCESS_TOKEN=<from step 1>
   python scripts/validate_linkedin.py --discover --ttl-seconds <TTL>
   ```
   Prints `LINKEDIN_ORG_ID` and `LINKEDIN_TOKEN_EXPIRES_AT` ready to paste.
   The org id is asked of the API rather than read off a Page URL, so it
   cannot be for a Page the token cannot actually post to.

3. **Prove it before trusting it:**
   ```bash
   export LINKEDIN_ORG_ID=<from step 2>
   python scripts/validate_linkedin.py --upload
   ```
   Uploads a real image and waits for `AVAILABLE`. Publishes nothing.

### Every 60 days

Repeat all three. Refresh is a browser flow, so this cannot be automated
without programmatic refresh tokens. `LINKEDIN_TOKEN_EXPIRES_AT` is what
makes the deadline visible in advance instead of as an outage.

---

## Media hosting — 4 secrets

Rendered cards, screenshots and video are uploaded to object storage, not
committed. Instagram fetches media by public URL and the repo was only ever
standing in as that host — at the cost of growing with every run.

**Cloudflare R2** is the cheaper option (no egress fees, which matters when
Instagram and X both pull every asset). AWS S3 works identically; skip
`MEDIA_ENDPOINT`.

1. Create a bucket, e.g. `pursuitai-social-media`.
2. **Enable public read access.** Instagram's fetcher is unauthenticated —
   a private bucket fails exactly the way a private repo did. On R2 this is
   *Settings → Public access → Allow Access*, which gives you an
   `https://pub-<hash>.r2.dev` domain (or attach a custom domain).
3. Create an API token scoped to **Object Read & Write** on that bucket
   only → `MEDIA_ACCESS_KEY_ID`, `MEDIA_SECRET_ACCESS_KEY`.
4. Set the secrets:

| Secret | Example |
|---|---|
| `MEDIA_BUCKET` | `pursuitai-social-media` |
| `MEDIA_ENDPOINT` | `https://<account-id>.r2.cloudflarestorage.com` (R2 only) |
| `MEDIA_BASE_URL` | `https://pub-<hash>.r2.dev` — the **public read** domain, not the S3 endpoint |

`MEDIA_BASE_URL` and `MEDIA_ENDPOINT` are different URLs and mixing them is
the easy mistake: one is where we write, the other is where Instagram
reads. `engine/media.py` rejects a non-https or loopback base up front,
because the alternative is a Graph API error hours later that says nothing
about the cause.

**Verify before trusting it:**
```bash
export MEDIA_BASE_URL=... IG_USER_ID=... IG_ACCESS_TOKEN=...
.venv/bin/python scripts/validate_ig.py --container
```
That HEADs a real asset URL and creates a container Instagram must fetch —
it is the only check that proves the bucket is actually reachable.

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
