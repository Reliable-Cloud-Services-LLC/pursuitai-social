# LinkedIn access — findings, decision, and the application to submit

**Researched 2026-07-28** against LinkedIn's official documentation. Every
figure below is quoted from a linked source; nothing here is from memory.

---

## The decision: manual-assisted now, API in parallel

LinkedIn posts are **pasted by hand** from the preview sheet. The engine
generates the card and the copy; a person pastes it.

That is not a stopgap born of laziness — it follows from a structural
finding about LinkedIn's review process.

## The daily routine

```bash
.venv/bin/python scripts/preview.py --linkedin
```

Prints the post text and writes real PNGs to `assets/linkedin/` — both the
1:1 and 4:5 cards. Attach one, paste the copy, post. Then:

```bash
.venv/bin/python scripts/preview.py --posted <topic-id>
```

Instagram can be posted the same way while its API credentials are pending:

```bash
.venv/bin/python scripts/preview.py --instagram
.venv/bin/python scripts/preview.py --posted <topic-id> --channel instagram
```

The queue is `logs/linkedin_posted.jsonl`, append-only and **independent of
`content/state.json`**. Each channel keeps its OWN cursor within that file,
so LinkedIn posting a topic does not make Instagram skip it — they are
separate audiences reached on separate days. LinkedIn should not stall because X is out of API
credits, and should not skip ahead because X published. It walks the
calendar in order, then the least recently posted once everything has been
round once.

## Why we are not calling the API yet

### The mechanics are easy

[Posts API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api):

```
POST https://api.linkedin.com/rest/posts
Authorization: Bearer {token}
Linkedin-Version: {YYYYMM}
X-Restli-Protocol-Version: 2.0.0

{ "author": "urn:li:organization:{id}", "commentary": "...",
  "visibility": "PUBLIC",
  "distribution": {"feedDistribution": "MAIN_FEED", ...},
  "lifecycleState": "PUBLISHED" }
```

Permission `w_organization_social`, requiring an ADMINISTRATOR /
DIRECT_SPONSORED_CONTENT_POSTER / CONTENT_ADMIN role on the Page. Images
and video upload separately to get a URN. Roughly 150 lines of work.

### The access tiers are the problem

[Increasing Access](https://learn.microsoft.com/en-us/linkedin/marketing/increasing-access):

| | Development | Standard |
|---|---|---|
| Limits | **500 API calls/app/24h**, 100/member/24h | "No restrictions" |
| BATCH_GET | not allowed | allowed |
| Webhooks | disabled | allowed |
| Intent | "build and test integrations" | "designed for live production" |
| Clock | integrate "within twelve (12) months" | — |

500 calls/day is ~100× our need (one post/day is 3–5 calls). **Rate is not
the constraint.**

### CORRECTION (re-verified 2026-08-27): Development tier needs no screencast

The section below is about **Standard** tier, and reading it as "LinkedIn is
closed to us" was wrong. LinkedIn's app-review page heads that list
*"Requirements for Standard Tier Upgrade Only"*, and Development tier is
reviewed on administrative facts alone:

> - Approved use case
> - Verified business email address
> - Verified organization
> - Verified organization website and domain address
> - Application verified by LinkedIn Page associated with same organization

No screencast, no test credentials, no application users. And per Increasing
Access, *"All applications start with Development tier"* — it is the default
on approval, not a separate thing to win.

At **500 API calls/app/24h** against our 3–5, Development tier is not a
stepping stone to production for us. It IS production.

### The real constraint is the token, not the tier

[3-legged OAuth](https://learn.microsoft.com/en-us/linkedin/shared/authentication/authorization-code-flow):

> "Currently, all access tokens are issued with a 60-day lifespan."

Refreshing is a browser flow, not a server one. The consent screen is
skipped, but only *"provided … The member is still logged into
https://www.linkedin.com"* — so it needs a human with a browser session.

> "Programmatic refresh tokens are available for a limited set of partners."

Unless we are granted that, **a human re-authorizes roughly every 60 days.**
That is the difference between LinkedIn and the other two channels: X uses
long-lived OAuth 1.0a credentials and Instagram a System User token that
never expires, so neither has a recurring human step.

So automatic LinkedIn posting is real, and it is automatic *in 60-day
stretches*. Worth building, worth knowing.

### A rejection is recoverable

Also worth correcting: *"You won't be able to re-apply for Development tier
access with your existing app"* — but the same sentence says to *"create a
new app, and submit a new Development tier access request form."* The app is
burned; the attempt is not.

### Standard tier review — unpassable as built, and we do not need it

[App Review](https://learn.microsoft.com/en-us/linkedin/marketing/community-management-app-review)
requires a screencast demonstrating, for a Page Management use case:

> - "Demonstrate an application user approving access to their LinkedIn page data via the complete OAuth flow."
> - "Demonstrate a user posting to their LinkedIn page via your app."
> - "Demonstrate how a comment on that post by a member is displayed to users in your app."
> - "Demonstrate what personal data fields from the commenter's LinkedIn profile are displayed to users in your app."

That review is designed for **multi-tenant SaaS managing other people's
Pages** — the Hootsuite/Buffer class. This engine has no application users,
no UI, no third-party OAuth flow, and displays nobody's profile data. It
publishes to our own Page. There is no honest screencast to record.

### Two risks worth knowing

- **A rejection burns the app.** *"You won't be able to re-apply for
  Development tier access with your existing app."*
- **Versioned APIs get sunset.** Marketing 202507 is already dead. Ongoing
  migration work that X and Instagram do not impose.

---

## The Development tier application

Submit at **My Apps → your app → Products → Community Management API**.

### Prerequisites — verified 2026-08-27

The Page is **"Pursuit AI"**, at
<https://www.linkedin.com/company/pursuit-ai>. NB the slug is `pursuit-ai`,
NOT `pursuitai`: the latter 404s, and pursuitai.net links to it in four
places including the JSON-LD `sameAs`. Fixing that is a pursuit-ai change,
tracked separately — but use the real slug for anything here.

- [x] **LinkedIn Page exists** — Pursuit AI, above
- [x] **Developer app exists** — client id and secret stored and validated
      (`validate_linkedin.py --check-app`, 2026-08-27)
- [x] **Privacy policy** — <https://pursuitai.net/privacy>
- [x] **Business email** — an alias on the **pursuitai.net** domain.
      Personal addresses fail vetting; a domain matching the stated website
      is what "verified organization website and domain address" checks.
- [ ] **A DEDICATED app with NO other products.** Confirmed 2026-08-27 by
      the portal itself, which greys out Request access and explains:

      > "This API product requires that it be the only product on the
      > application for legal and security reasons. This product cannot be
      > requested because there are currently other provisioned products or
      > other pending product requests. A new developer application can be
      > created to request this product."

      This is not a soft preference and it is not negotiable after the fact:
      one other product — provisioned OR merely pending — permanently blocks
      the request on that app. The remedy LinkedIn offers is a NEW app.

      **Consequence: the client credentials change.** A new app means a new
      Client ID and Secret, so `LINKEDIN_CLIENT_ID` and
      `LINKEDIN_CLIENT_SECRET` must be REPLACED in the repository secrets.
      Credentials from the old app will introspect against the wrong
      application and report a token as inactive.

- [ ] **App name** contains no part of "LinkedIn" or "Microsoft" — watch for
      "Linked" or "In" as substrings. It must also differ from any existing
      app's name, since the dedicated app sits alongside the first one.
- [ ] **A super admin of the Pursuit AI Page has
      [verified the app](https://www.linkedin.com/help/linkedin/answer/a548360/associate-an-app-with-a-linkedin-page)**
      — this is an explicit Development-tier review criterion, so it must be
      done BEFORE submitting, not after
- [x] **Legal name: Reliable Cloud Services LLC.** Decided 2026-08-30. The
      form states it "will use the provided business name for verifying its
      active registration", so the only workable answer is the entity that
      actually survives a registry lookup — not the brand we would prefer to
      write. **Alternate legal name: No**, because PursuitAI is not
      separately registered; claiming an unregistered DBA fails the same
      verification, and a rejection burns the app.

      This does NOT weaken the PursuitAI/RCS separation. That separation is
      about public presentation — the Page, the domain, the site, the posts,
      the copy in this application — all of which stay PursuitAI. The legal
      name is a private declaration to LinkedIn's vetting team and is not
      published anywhere. A parent entity operating a product brand with its
      own Page and domain is ordinary.

      Revisit if the spin-out completes and PursuitAI becomes its own
      registered entity.
- [ ] Any auto-generated survey completed **within 21 days**

### Draft answers

**Company / product overview**

> PursuitAI is a capture-management platform for small businesses pursuing
> U.S. federal contracts — firms holding 8(a), SDVOSB, WOSB and HUBZone
> designations. It aggregates federal opportunity, spend and protest data
> and layers AI scoring, compliance checking and proposal tooling on top.

**Use case**

> First-party publishing to our own LinkedIn Page. An internal tool
> generates branded graphics and copy about our product's capabilities and
> publishes one post per weekday to the PursuitAI company Page.
>
> This is Page Management for a single Page that we own and administer. We
> are not building a product for third parties, do not connect other
> organisations' Pages, and do not read, store or display member personal
> data. No comments, reactions, or profile data are retrieved.

**What data will you access and store?**

> Only the identifiers needed to publish: our own organisation URN and the
> post URN returned on creation, which we retain in an append-only log for
> our own audit trail. No member data of any kind. No LinkedIn data is
> shown to any third party.

**Expected call volume**

> Approximately 3–5 API calls per weekday: one image upload and one post
> creation, plus retries. Well inside the Development tier's 500 calls per
> 24 hours.

**Use-case checkboxes — tick Page management ONLY**

The form asks which use cases to enable, "select all that apply". Exactly
one applies:

- [x] **Page management** — create and manage company posts. The box is
      worded more broadly than we act ("comments, and reactions, and monitor
      engagement"); that is fine, it is the right category and the written
      use-case answer above narrows it explicitly.
- [ ] **Page analytics** — deliberately NOT ticked. We do not read LinkedIn
      post metrics, and this is the option that drags member data into
      scope: reactions and comments ARE member data, carrying LinkedIn's
      storage obligations ("member social activity data can only be stored
      for 48 hours"). Our application's strongest feature is that it reads
      and stores NO member data at all; ticking this muddies that for a
      capability which does not exist.
- [ ] **Profile management** — we post as the Page, never on behalf of an
      individual.
- [ ] **Employee advocacy** — not what we do.
- [ ] **Other** — covered by Page management.

Over-selecting is not free. LinkedIn's Standard-tier review requires a
screencast demonstrating "each use case that you specified in the access
request form", so every extra tick is a demonstration owed later. It also
runs against their data-minimisation rule.

If LinkedIn engagement metrics are wanted later — they would feed the
performance-weighted rotation the way X and Instagram metrics already do —
that is a change which "may require re-review", and worth doing honestly
then rather than pre-claiming now.

### Set expectations

The application asks how you will integrate within twelve months. Answer
honestly: this is a **single-Page first-party publisher**, and we do not
intend to build the multi-tenant UI that Standard tier review assumes. If
Development tier proves inappropriate for ongoing production use, we
continue posting manually — which is what we do today, at no loss.

---

## Facebook — the permission path

Publishing to a Page needs
[`pages_manage_posts`](https://developers.facebook.com/docs/pages-api/posts)
(plus `pages_read_engagement`; `publish_video` for video). Our current
Instagram token does **not** carry it.

**Does it need App Review?** Meta's documentation does not state this
explicitly for `pages_manage_posts` either way — I checked, and I am not
going to assert it.

What we do know is the mechanism our Instagram path already relies on, per
`SETUP.md`: *"while your Meta app is in Development mode it can post to
accounts that have a role on the app (your own) — that's all this engine
needs. No App Review required."* `instagram_content_publish` works that way
today, and `pages_manage_posts` on a Page we administer should ride the
same rule.

**High confidence by parallel, not documented.** Settle it empirically
before writing any posting code — regenerate the token with
`pages_manage_posts` added to the scope list and confirm it appears in
`/debug_token`. That is a five-minute check and it costs nothing if the
answer is no.

---

## The whole sequence, in order

Nothing below can be done out of order — each step's output is the next
step's input, and the two that gate everything are LinkedIn's, not ours.

**0a — DO NOT CLICK ANY OTHER PRODUCT.** On a fresh app the Products tab
offers several, and at least one is usually requestable while Community
Management is not yet. Requesting it — even leaving it *pending* — burns the
app permanently for Community Management, with no way back. That is how the
first app was lost. The Products tab reads as a menu; treat it as a
one-way door.

**0 — Create a DEDICATED app.** Community Management must be the only
product on it, so an app that already carries another product — or a pending
request for one — cannot ever request it. Build the new app with nothing
else added: name (unique, no "Linked"/"In"/"Microsoft"), the Pursuit AI Page,
privacy policy <https://pursuitai.net/privacy>, logo.

**1 — Verify the app against the Page.** Settings → Verify → *Generate URL*,
send it to a Page admin of Pursuit AI, who opens it and clicks **Verify**.
The URL is valid 30 days and the association **cannot be undone**.

Do this before judging the Products tab: on an unverified app most products
show a greyed Request access, which looks identical to the
one-product-only block and is not the same thing. It is also a review
criterion in its own right, so doing it after submitting means resubmitting.

**2 — Submit the application.** My Apps → your app → **Products** →
Community Management API → request access, then complete the form with the
draft answers above. Development tier, which needs no screencast.

**3 — Wait.** Nothing here can be tested meanwhile: the Token Generator only
offers `w_organization_social` once the product is approved on the app, so a
token minted now would generate fine and be unable to post.

**4 — Grant the posting member an ADMINISTRATOR role** on the Page, if they
do not already hold one. The token inherits the approving member's roles.

**4b — Replace the client credentials.** The dedicated app has its own
Client ID and Secret; update `LINKEDIN_CLIENT_ID` and
`LINKEDIN_CLIENT_SECRET` to the new app's. Leaving the old ones there is a
quiet failure — introspection would validate the token against the wrong
application and report it inactive. `validate_linkedin.py --check-app`
confirms the pair before you go further.

**5 — Mint the token.**
[Token Generator](https://www.linkedin.com/developers/tools/oauth/token-generator)
→ select the app → tick `w_organization_social` and `rw_organization_admin`
→ approve as that member. Store as `LINKEDIN_ACCESS_TOKEN`.

**6 — Derive the org id.**
```bash
export LINKEDIN_ACCESS_TOKEN=...
python scripts/validate_linkedin.py --discover
```
Store the printed `LINKEDIN_ORG_ID`. Skip `LINKEDIN_TOKEN_EXPIRES_AT` — with
the client credentials stored, introspection reports the real expiry, and a
pasted copy is a second source of truth that can drift from the first.

**7 — Prove the chain.**
```bash
python scripts/validate_linkedin.py --upload
```
Uploads a real image, waits for `AVAILABLE`, publishes nothing.

**8 — Nothing else.** The next daily run picks LinkedIn up automatically:
`POSTERS` gates the channel on `LINKEDIN_ACCESS_TOKEN` being present, so
there is no flag to flip. The post still parks at the same human approval
gate as X and Instagram.

From then on the `linkedin-token` job warns at 14 days, every run, so the
60-day cycle stops being something anyone has to remember.

---

## Getting the three secrets

Verified against learn.microsoft.com on 2026-08-27.

**Order matters.** Two of the three cannot exist until the Community
Management API application is approved: the Token Generator only offers
scopes your app actually has, so before approval there is no
`w_organization_social` to tick and no token worth minting.

### 1. LINKEDIN_ACCESS_TOKEN

**No OAuth callback server is needed.** The Developer Portal mints tokens
directly — *"The LinkedIn Developer Portal Token Generator Tool allows a
quick and easy method for generating an access token"* — which matters here,
because a single-Page first-party publisher has no users to send through a
consent flow.

1. [Token Generator](https://www.linkedin.com/developers/tools/oauth/token-generator)
2. Select the app
3. Tick **`w_organization_social`** (post as the Page) and
   **`rw_organization_admin`** (read Page roles, which is how step 2 below
   finds the org id)
4. Approve as a member who holds an **ADMINISTRATOR** role on the PursuitAI
   Page — the token inherits that member's roles, so approving as someone
   without it produces a token that looks perfect and 403s on publish
5. Copy the token. Note the **TTL** shown under Token Details

### 2 & 3. LINKEDIN_ORG_ID and LINKEDIN_TOKEN_EXPIRES_AT

Ask the API rather than reading an id off a Page URL — that way the id
cannot be for a Page the token cannot actually post to:

```bash
export LINKEDIN_ACCESS_TOKEN=<from step 1>
python scripts/validate_linkedin.py --discover --ttl-seconds <TTL from step 1>
```

It prints both, ready to paste as repository secrets. Omit `--ttl-seconds`
and it assumes the documented 60-day lifespan, which is right for a
freshly-minted token and wrong for a partly-used one — in the unhelpful
direction, because the expiry alarm then fires late.

`LINKEDIN_TOKEN_EXPIRES_AT` is the one that is easy to skip. Without it
nothing can warn before the token dies, and the first symptom is a channel
that silently stops posting.

### 4. Prove it before trusting it

```bash
export LINKEDIN_ORG_ID=<from step 2>
python scripts/validate_linkedin.py --upload
```

Uploads a real image and waits for `AVAILABLE`. Publishes nothing. Re-run it
after every re-authorization — the whole point of a 60-day credential is
that last month's green result means nothing.

### Every 60 days, thereafter

Repeat steps 1–3. Refresh is a browser flow, so there is no way to automate
this without programmatic refresh tokens, which are *"available for a limited
set of partners"*.

---

## When to revisit

- **Development tier lands** → wire `post_linkedin.py` behind a flag,
  keeping the manual path as the fallback.
- **We ever manage Pages for other organisations** → Standard tier review
  becomes passable, because the product would then actually have the
  application users the screencast asks about.
- **Volume outgrows one company** → a unified provider (Ayrshare, $149/mo
  for 1 profile) starts to make economic sense. It does not today, and it
  would put a third party in possession of our publishing credentials.
