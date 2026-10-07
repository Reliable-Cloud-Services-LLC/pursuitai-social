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

## Video (the `ad` format) — built 2026-10-02, UNPROVEN AGAINST LIVE API

`ad` is 1 post in 4. Until now every LinkedIn post went through
`post_image`, whose `check_image` accepts only jpg/jpeg/png/gif — so a
quarter of the calendar could never reach LinkedIn. `post_media` now
dispatches on the file suffix and `ad` goes through the
[Videos API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/videos-api),
which is a different API from Images rather than a variant of it:

1. `POST /rest/videos?action=initializeUpload` with `fileSizeBytes` →
   returns a video URN, an `uploadToken`, and per-part byte ranges.
2. `PUT` each range. **The response ETag is that part's id.**
3. `POST /rest/videos?action=finalizeUpload` with the ids IN ORDER.
4. Poll `GET /rest/videos/{url-encoded urn}` until `AVAILABLE`.

Three places the documentation contradicts itself, each handled:

* **Part size.** The prose says `split -b 4194303` while the instructions
  describe parts as `0 - 4194303` INCLUSIVE, which is 4194304 bytes. The
  code slices from the ranges LinkedIn sends, never a constant. A part of
  the wrong length uploads cleanly, finalizes cleanly, and yields a corrupt
  video with no error to read.
* **ETag form.** The schema shows a quoted hex digest; the live sample
  shows an unquoted `/ambry-video/signedId/…bin` path. Surrounding quotes
  are stripped when present and anything else passes through.
* **Size cap.** The specifications say 75kb–500MB; the schema table says
  5GB. The tighter bound is enforced — our ads are a few MB, so the choice
  costs nothing.

**Duration is NOT checked locally.** LinkedIn requires 3s–30min, measuring
needs ffprobe, and the publish job has no ffmpeg (only prepare installs
it). `adspot.SCENES` totals ~14.2s before narration scaling, so our ads sit
an order of magnitude clear of the floor. If that changes, LinkedIn reports
`PROCESSING_FAILED` and the error carries its `processingFailureReason`
verbatim — which the image path has no equivalent of.

### PROVEN LIVE 2026-10-02

Run against a real `w_member_social` token, twice, uploading for real and
publishing nothing:

```
# the shipping artifact — 14.17s, 1080x1080 h264, 893KB
[linkedin] uploading 893,252 bytes in 1 part(s)
✓ uploaded and AVAILABLE: urn:li:video:D4E10AQFti5koQoyt0w

# a 42.5s loop, deliberately over the 4MiB part boundary
[linkedin] uploading 5,972,766 bytes in 2 part(s)
✓ uploaded and AVAILABLE: urn:li:video:D4E10AQHwegsO2w2PuA
```

The second run is the one that mattered. At 893KB the real ad is a SINGLE
part, so it never touches byte-range slicing, multiple ETags, or finalize
ordering — the three things most likely to be silently wrong. The looped
file forces two parts, and reaching AVAILABLE proves the reassembly was
byte-correct: a mis-sliced part uploads and finalizes cleanly, then fails
the transcode. It did not.

The part count is now printed by `upload_video` — in the publish log as
well as the validator — because "it probably used one part" is not a fact.

Still unproven: **narration surviving LinkedIn's transcode.** The local
render is silent (no torch/kokoro/misaki/spaCy on this machine), and the
Videos API handles bytes, so audio is irrelevant to upload, finalize and
transcode. Only a CI-rendered ad would close that, and the first scheduled
`ad` does it for free.

To re-prove after any change:

```bash
export LINKEDIN_ACCESS_TOKEN=...          # LINKEDIN_ORG_ID optional
python scripts/validate_linkedin.py --member --upload --file <ad>.mp4
```

---

## The channel was wired everywhere EXCEPT the publish step

Found 2026-10-02 while setting the repository secrets. The publish step was
named `Publish to X + Instagram` and passed no LinkedIn credentials at all.
`run.py`'s `POSTERS` gates the channel on `LINKEDIN_ACCESS_TOKEN`, so with
that variable absent from the step's env it skipped on EVERY run:

```
2026-09-24  card        linkedin=skipped  LINKEDIN_ACCESS_TOKEN not set
2026-09-30  screenshot  linkedin=skipped  LINKEDIN_ACCESS_TOKEN not set
2026-10-01  card        linkedin=skipped  LINKEDIN_ACCESS_TOKEN not set
```

The repository secret existed. A credential the publish step cannot see is
the same as no credential.

Four PRs built this channel — Images API, Videos API, member authorship, a
live-proven upload — and none of them could ever have fired. Nothing failed;
the suite was green throughout, because every test asserted on code and the
gap was in the wiring between the code and its credentials.

**The guard that was missing** is now
`test_every_channel_gate_reaches_the_publish_step`, which derives the check
from `POSTERS` rather than a hand-maintained list, so a new channel cannot be
added without its credential being wired. The old test split the workflow on
the step's TITLE and asserted against a hardcoded list of secrets — adding a
third channel updated neither.

`LINKEDIN_ORG_ID` is passed to the step while deliberately UNSET: absent
means author as the member, and setting the secret later moves posting to
the Page with no workflow edit.

---

## MEMBER POSTING IS PROVEN — live, 2026-10-02

Run against a real `w_member_social` token on app `78r1pxbwy1a5mg`:

```
[1] token valid — Aqeel Butt → urn:li:person:S8tpf7RIhR
[2] scope w_member_social granted · token has 59 days left
[3] uploaded and AVAILABLE: urn:li:image:D4E10AQEzimgJkzAJsw — no post created
```

**The versioned `/rest` surface accepts a person-owned upload and lets a
`w_member_social` token read the status back.** So the member path is a
one-field change: `author` and the asset `owner` become the Person URN. The
Images API, the AVAILABLE poll and the `x-restli-id` read all carry over,
and the legacy `/v2/ugcPosts` implementation is NOT needed.

**Documentation discrepancy, recorded not designed around.** The Images doc
states: *"`w_member_social` permission are write-only and tokens with only
`w_member_social` permissions would be unable to perform a GET call for
rest/images."* That token performed exactly that GET and got `AVAILABLE`.
Observed behaviour wins; if LinkedIn ever enforces the documented
restriction, `_await_available` is where it will surface, as a 403.

### The bug this uncovered — latent since August

The first live run failed with `Syntax exception in path variables` (400)
on the status poll. Cause: `_await_available` built
`GET /rest/images/urn:li:image:...` **unencoded**, while we send
`X-Restli-Protocol-Version: 2.0.0`, which requires path keys be encoded.

Two things made it survive two months unnoticed:

* The Images doc's GET sample shows the URN raw — but that sample also
  omits the protocol header. The Videos doc, which keeps the header, shows
  the encoded form. Following one sample literally while sending the other
  one's headers is what produced the bug.
* This file recorded the LinkedIn app as "validated" on 2026-08-27. What
  actually ran that day was `--check-app`, which never touches the upload
  path. A confident-sounding note was mistaken for evidence, and the
  encoding bug would have failed the FIRST real post on either route.

### How authorship is configured

`LINKEDIN_ORG_ID` is the switch, and its absence is the current state:

| `LINKEDIN_ORG_ID` | author | route |
|---|---|---|
| unset | `urn:li:person:…`, derived from the token | B — member, live now |
| set | `urn:li:organization:…` | A — the Page, when the appeal lands |

The person URN is derived from `/v2/userinfo` rather than stored, so there
is no second secret to drift out of sync with the token — getting it wrong
would mean posting as somebody else. The asset `owner` and the post
`author` always come from the same call, because *"the caller needs to
match the image owner"* and a mismatch is a 403 AFTER the upload is spent.

Publishing as a member prints the author URN, so posting to a profile when
someone meant the Page is announced rather than discovered from the feed.

---

## The self-serve route: member posting (verified 2026-10-02)

After the rejection, a second route was checked against the docs. It is real,
it needs no review at all, and it has one hard limitation.

### Posting to the PAGE self-serve is impossible

[Getting Access](https://learn.microsoft.com/en-us/linkedin/shared/authentication/getting-access)
carries the complete list of permissions obtainable without approval:

> **Open Permissions (Consumer)** … "Open Permissions are the only
> permissions that are available to all developers without special
> approval."

| Product | Permission |
|---|---|
| Sign in with LinkedIn (OIDC) | `profile` |
| Sign in with LinkedIn (OIDC) | `email` |
| **Share on LinkedIn** | **`w_member_social`** |

`w_organization_social` is not on it.

**CORRECTION (2026-10-07): Community Management is not the only product that
grants it.** [Increasing
Access](https://learn.microsoft.com/en-us/linkedin/marketing/increasing-access)
lists `w_organization_social` under BOTH Community Management and the
**Advertising API**. That is a real second route, and it was checked
properly before being ruled out — recorded here so nobody retraces it:

- **It is vetted by the same gate.** [Ads
  overview](https://learn.microsoft.com/en-us/linkedin/marketing/integrations/ads/ads-overview):
  *"Advertising API | **Vetted Product** with development and standard
  tiers"*, and *"API calls succeed only when made with correct
  scopes/permissions assigned to the app after the vetting process."*
  Switching products does not route around an ORGANISATION-level
  verification failure.
- **It requires an ad account we do not have.** *"Requires Enterprise or
  Business Ad Account with one authenticated user as account
  administrator"*, and quick-start step 2 has you map a nine-digit Campaign
  Manager account id onto the app.
- **The use case does not match.** The product is *"manage LinkedIn's
  campaign management platform on behalf of clients"*; quick-start step 1
  warns to check restrictions so the application *"is not rejected due to a
  restricted use case"*. We post one organic card a day to our own Page.
- **It would also forfeit the CM route on that app** — CM must be the only
  product (see the dedicated-app prerequisite below), and a pending product
  request counts.

So the conclusion is unchanged, but for a better reason: the self-serve
route authors as a member because every route to `w_organization_social`
runs through the same vetting.

**Being a Page ADMINISTRATOR does not substitute.** The role is checked in
ADDITION to the scope, never instead of it — the Posts API error table says a
403 means "Ensure the required OAuth scope (`w_organization_social`, …) is
granted **and** that the authenticated member has the necessary company page
role." We hold the second condition and cannot obtain the first self-serve.

So the self-serve route authors as `urn:li:person:{id}`. That is a product
decision — posts come from a person, not the brand — not an implementation
detail to be worked around later.

### What it costs and what it buys

- **No review.** [Share on LinkedIn](https://learn.microsoft.com/en-us/linkedin/consumer/integrations/self-serve/share-on-linkedin):
  "add the Share on LinkedIn product which will grant you `w_member_social`."
- **Rate limit 150/member/day**, against our 3–5.
- **Still 60 days.** Token lifespan is a property of LinkedIn OAuth, not of
  the tier, so the human re-authorization step is unchanged.

### THE TRAP: it burns the app for Community Management

The Community Management API must be the ONLY product on its application.
Adding Share on LinkedIn to an app permanently disqualifies that app from
ever requesting CMA. **Route A and Route B need two separate apps.**

### The one thing the docs do not answer

The versioned Posts API lists `w_member_social` in its own permissions table
and documents `author` as "Person URN or Organization URN" — so on paper
`post_linkedin.py` works for a member by swapping one field. But the Share on
LinkedIn documentation describes the LEGACY endpoints (`/v2/ugcPosts`,
`/v2/assets?action=registerUpload`), and nothing states whether an app
holding only the self-serve product may call the versioned `/rest/*` surface.

One is a field swap; the other is a separate implementation. Rather than
guess, ask LinkedIn:

```bash
export LINKEDIN_ACCESS_TOKEN=...          # from the Share on LinkedIn app
python scripts/validate_linkedin.py --member --upload
```

The image upload is the probe: it exercises the versioned surface with a
person-owned asset and publishes nothing, because an uploaded image attached
to no post appears nowhere. The command prints a VERDICT either way.

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
- [x] **Legal name: `RELIABLE CLOUD SERVICES, L.L.C.`** — the registry's
      punctuation, VERBATIM (Maryland SDAT Dept ID W19522465, verified
      2026-10-02). Attempt 1 submitted "Reliable Cloud Services LLC" and
      failed identity vetting on the mismatch; see the correction below.
      Decided 2026-08-30. The
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

## Attempt 1 — REJECTED (portal 2026-08-30; email 2026-09-01)

> "At this time we are unable to grant you Development Tier access to our
> Community Management API for the following reason:
> **Access Denied: Business verification failed**"

The portal states it more precisely — **"Identity vetting failed"**, with the
reason and an appeal link. See the correction below; the portal wording is
the one that identifies the cause.

Application id `266127073`, CRM `015285037679362`, app `PursuitAI-App`
(`78b4lrsxr06b7q`). Submitted as legal name **Reliable Cloud Services LLC**
— WITHOUT the registry's comma and periods, which is what failed — no
alternate name, Page management only.

**That app is now burned.** LinkedIn: "You won't be able to re-apply for
Development tier access with your existing app." A third app is required,
and it burns the same way if the underlying problem is unchanged. Every
attempt costs an app, which is why the next step is a question, not a
resubmission.

### CORRECTION (2026-10-02): the cause is UNKNOWN; two theories are dead

The section this replaces theorised that vetting failed because
pursuitai.net names no operating legal entity. That is **unproven**, and it
is recorded rather than deleted because a legal-pages change was drafted on
its basis before the real denial text surfaced.

LinkedIn's actual denial, read from the portal:

> **Identity vetting failed**
> "We were unable to verify that your organization is a legally registered,
> active entity. If you wish to appeal, click here to submit supporting
> documentation."

That is a claim about the ENTITY, not the website. And the entity passes on
the public record — Maryland SDAT, checked 2026-10-02:

| field | value |
|---|---|
| Department ID | **W19522465** |
| Business name | **RELIABLE CLOUD SERVICES, L.L.C.** |
| Status | **ACTIVE** |
| Good standing | **THIS BUSINESS IS IN GOOD STANDING** |
| Type / formed | Domestic LLC, 03/20/2019 |
| Principal office | 23006 Blue Flag Cir, Clarksburg, MD 20871 |

**Two theories were raised and both are dead:**

1. *Forfeiture / lost good standing.* Maryland forfeits entities that miss
   the April 15 annual report, which would read exactly as "not active".
   **Disproven**: status ACTIVE, in good standing.
2. *Exact-name mismatch.* The registry prints
   `RELIABLE CLOUD SERVICES, L.L.C.`; attempt 1 submitted
   `Reliable Cloud Services LLC`. **Unsupported**: SDAT's own search for the
   submitted string returns the entity (1 result, Active). A registry that
   resolves both spellings is not evidence that a mismatch broke the lookup.

So **the cause is not known.** LinkedIn gave one line and no detail, and
vetting of this kind typically runs through a third-party KYB vendor whose
matching rules are not observable from outside — which is why guessing at
the mechanism has now been wrong twice.

**The consequence for the appeal: do not assert a cause.** An appeal that
leads with a diagnosis invites a reviewer to reject the diagnosis rather
than look at the record. Supply the verifiable facts, note that the public
registry resolves the entity under either spelling, and ask what
specifically could not be verified. The entity being demonstrably registered
and active is the whole argument; it does not need a theory attached.

Still worth doing regardless: submit the registry's punctuation verbatim.
It costs nothing and removes one variable.

### A THIRD theory, and unlike the first two it is documented (2026-10-07)

The section above says the cause is unknown. It is now narrower than that.

[Community Management App
Review](https://learn.microsoft.com/en-us/linkedin/marketing/community-management-app-review)
states the requirement outright:

> "Be prepared to share your **business email address** … Your business
> email address will have to be verified. … **Personal email addresses
> won't pass the vetting process.**"

and lists **"Verified business email address"** as its own line item in the
Development-tier review criteria, separate from "Verified organization".

**The evidence that this applies to us.** The denial for application
`266127073` (CRM `015285037679362`) was emailed to
**the owner's personal `@gmail.com` address**, and the
`@reliablecloudllc.com` business address is NOT registered with LinkedIn, so it cannot
have been verified against the app. Whatever went in the form field, no
business email was ever verified for attempt 1. That criterion fails on its
face.

Not proven: that the form's business-email FIELD held the personal address.
A denial notice may route to the account email regardless of what was typed.
The decisive artefact would be a LinkedIn verification mail at
`reliablecloudllc.com`, which was never received. Either way it is a
blocker on the next attempt.

**Consequence for the appeal filed 2026-10-02: it argues the wrong case.**
It supplies SDAT registry evidence for an entity-registration question,
and the appeal form has no business-email field — so if this is the
failing criterion, no registry document can reach it.

### The likelier shape of the problem: nothing corroborates anything

Read the four review criteria against what attempt 1 actually presented:

| criterion | what the reviewer saw |
|---|---|
| Verified business email address | a personal `@gmail.com` |
| Verified organization | RELIABLE CLOUD SERVICES, L.L.C. — real and ACTIVE |
| Verified organization website and domain | pursuitai.net — which names no RCS entity anywhere |
| App verified by Page of the same organization | `company/pursuit-ai` ("Pursuit AI") — not an RCS Page |

The entity is demonstrably registered. What is missing is any public thread
tying THAT entity to this app, this website, this Page or this email. On
that reading the denial is not "we could not find the registration" but
**"we could not verify this applicant is that organization"** — which is
also why a correct registry record did not help.

This subsumes the website gap noted below and the email finding above; they
are the same failure seen from different sides.

**Two coherent ways out, and they differ on a decision already taken once:**

- **A — apply as Reliable Cloud Services.** Entity RCS, website
  `reliablecloudllc.com` (live; its own title reads "Reliable Cloud
  Services"), business email `aqeel@reliablecloudllc.com`, app verified by
  an RCS Page. Every field corroborates every other, and **nothing on
  pursuitai.net changes** — so PursuitAI's public separation from RCS is
  untouched.
- **B — make pursuitai.net evidence the entity.** Name RCS as operating
  entity on the site. Fixes the ToS counterparty gap too, but publicly ties
  PursuitAI to RCS. The earlier reasoning that declaring RCS "does not
  weaken the separation" held *because the legal name was a private
  declaration*. B breaks that premise; A preserves it. Flagged as a
  conflict, not a recommendation — it is a business decision.

**Route A's open question is now ANSWERED (2026-10-07): the app's verifying
Page does NOT scope which Pages it can post to.** [Organization Access
Control by
Role](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/organizations/organization-access-control-by-role)
is explicit that runtime access follows the AUTHENTICATED MEMBER:

> "A role defines the privileges that a member has within the organization.
> You must be an **authenticated member with role type `ADMINISTRATOR`** for
> an organization to use many of the Organization APIs."

and `rw_organization_admin` is *"Restricted to organizations in which the
authenticated member has the role type `ADMINISTRATOR`"*. `GET
/organizationAcls?q=roleAssignee` returns a PAGINATED LIST of every
organization the member holds a role on — plural by construction. No page
in the docs limits an application to the Page that verified it; that
association is an application-time ownership check.

So: app verified by the **Reliable Cloud Services** Page
(`company/reliable-cloud-services`), token held by an admin of both, posting
to **Pursuit AI** (`company/pursuit-ai`). RCS Pages exist and both resolve.

**Residual risk, and why it does not justify delay.** The CM review page
reserves the right to impose *"all other requirements or restrictions that
LinkedIn separately communicates to you (e.g. during or after the vetting
process)"*, so a grant could in principle arrive scoped. That cannot be
pre-tested — the probe endpoint itself needs a permission we do not hold.
But note **the scope question cannot burn an app: only a REJECTION does.**
If vetting passes and the grant turns out narrower than expected, we hold an
approved app and the conversation is a support ticket, not a third attempt.
Route A maximises the chance of passing vetting, which is the only step that
costs anything.

**Also unverified on our side:** the criteria say *"Ensure a **super
admin** of the LinkedIn Page … has verified your application."* Admin is
not stated to be sufficient. Check the role before attempt 2.

### Attempt 1 appeal — SUBMITTED 2026-10-02

Filed via Developer Support, Form Type "Vetting Appeal", API Program
"Community Management", against app `78b4lrsxr06b7q`. The appeal supplies
the public registry record (SDAT W19522465, ACTIVE, in good standing) and
asks which specific element could not be verified — deliberately asserting
no cause, since both theories died and a wrong diagnosis invites a reviewer
to reject the diagnosis rather than read the record.

**The app is tied to `linkedin.com/company/pursuit-ai`** — confirmed under
My Apps → Settings, and the same Page pursuitai.net links to. So the
"Application verified by LinkedIn Page associated with same organization"
criterion was already satisfied and is NOT a candidate cause.

### Two LinkedIn Pages exist — the app uses pursuit-ai (found 2026-10-02)

Both resolve, with different names:

| slug | Page name |
|---|---|
| `linkedin.com/company/pursuit-ai` | Pursuit AI |
| `linkedin.com/company/pursuitai-rcs` | PursuitAI |

pursuitai.net links to `pursuit-ai` (twice, including the JSON-LD `sameAs`),
and `pursuit-ai` is also the Page the app is verified against — so those two
agree. The duplicate Page is a presentation problem, not an access one.

NB the `pursuitai-rcs` slug publicly ties the product to RCS, which is a
presentation decision worth making deliberately rather than inheriting.

### The website gap is still real — just not this

Terms of Service are a contract and pursuitai.net's name no counterparty;
the liability clause caps "RCS'S AGGREGATE LIABILITY" for an acronym the
document never defines. Worth fixing on its own merits. It is NOT what
blocked API access.

### Before attempt 2

1. **Ask developer support what actually failed**, quoting the CRM number
   and application id. The one-line reason does not distinguish "we could
   not find the registration", "the website does not match the entity", or
   something else — and each guess costs an app.
2. **Then close whichever gap they name.** If it is the website/entity link,
   the options are to state the operating entity on the site (normal
   practice, and it fixes the contract gap too), register PursuitAI as a
   DBA so the brand is itself evidenced, or wait for the spin-out and apply
   as PursuitAI. Which of those is right is a business decision, not a
   documentation one.
3. Only then create the third app — dedicated, no other products, Page
   verified — and resubmit.

---

## Attempt 2 — SUBMITTED 2026-10-07 (route A)

The first application where every Development-tier criterion corroborates
every other. Attempt 1 failed with four fields that pointed at four
different things; this one points at one organization throughout.

| Review criterion | Attempt 1 | Attempt 2 |
|---|---|---|
| Approved use case | (not recorded) | **Direct Advertiser** — "manage only owned and operated LinkedIn activity/data streams" |
| Verified business email address | never verified; denial went to a personal address | **`aqeel@reliablecloudllc.com` — verified 2026-10-07** |
| Verified organization | RELIABLE CLOUD SERVICES, L.L.C. (ACTIVE, W19522465) | same |
| Verified organization website + domain | pursuitai.net — named no RCS entity | **reliablecloudllc.com** + `/privacy.html`, which names the entity |
| App verified by Page of same organization | `company/pursuit-ai` — not an RCS Page | **`company/reliable-cloud-services`** — verified 2026-10-07 |

App: **Reliable Cloud Services Social Publisher**, Page association
**irreversible** and set to the RCS Page. Other use case declared: Page
management + Page analytics. Profile management and Employee advocacy
deliberately NOT declared — permissions arrive with the PRODUCT, not the
checkbox ("Once your API access is approved, the corresponding permissions
are automatically granted to your application"), so a narrower declared
intent costs no scope and keeps the review surface small.

### Correction: the business email IS collected and verified separately

An earlier revision of this file said the business-email field was not on
the access request form. That was wrong — it is not on the *use-case* page,
which is what made it look absent, but LinkedIn does collect it and sends
its own verification mail, exactly as the docs say. Confirmed by receiving
and completing that verification at the business address on 2026-10-07.

### All five criteria are satisfied — the application is complete

The app was verified from the RCS Page on 2026-10-07, closing the last one.
So for the first time every Development-tier criterion is met and they all
point at the same organization. Nothing further is actionable on this
application; it is now a review-queue wait.

### LinkedIn sends NO acknowledgement on submission

Do not read silence as a failed submission. Attempt 1's only correspondence
was the OUTCOME ("Regarding your access request CRM:015285037679362"),
emailed 2026-09-01 against prerequisites confirmed 08-27 and a portal
rejection on 08-30 — so on the order of 2-5 days, with nothing at all at
submission time. There is no published SLA; that precedent is the only
figure we have.

Revisiting the access-request survey link after completing it returns *"You
have either already completed the survey or your session has expired"* with
a 0% progress bar. That is the normal response to a finished survey, not
evidence of a lost submission. The pending/in-review status on the app's
Products tab is the place to confirm.

### What is NOT yet confirmed

- Whether the Oct 2 appeal on attempt 1 ever gets a human response. Leave
  it open: it is the only channel where a person reads the registry
  evidence, and the access form has no free-text field to argue in.

### Do not touch this app

Community Management must be the only product, and a *pending* request
counts. No Advertising API, no Events, nothing — see the Advertising API
dead end recorded above.

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
