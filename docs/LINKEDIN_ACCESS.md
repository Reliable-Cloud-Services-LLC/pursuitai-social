# LinkedIn access — findings, decision, and the application to submit

**Researched 2026-07-28** against LinkedIn's official documentation. Every
figure below is quoted from a linked source; nothing here is from memory.

---

## The decision: manual-assisted now, API in parallel

LinkedIn posts are **pasted by hand** from the preview sheet. The engine
generates the card and the copy; a person pastes it.

That is not a stopgap born of laziness — it follows from a structural
finding about LinkedIn's review process.

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

### We cannot pass Standard tier review as built

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

### Prerequisites

- [ ] A **dedicated** developer app. Community Management "requires that it
      be the only product associated with a developer application"
- [ ] Business email on the pursuitai.net domain — **personal addresses fail
      vetting**
- [ ] App name contains no part of "LinkedIn" or "Microsoft" (watch for
      "Linked" or "In" as substrings)
- [ ] A super admin of the PursuitAI LinkedIn Page has
      [verified the app](https://www.linkedin.com/help/linkedin/answer/a548360/associate-an-app-with-a-linkedin-page)
- [ ] Legal name, registered address, website, privacy policy to hand
- [ ] Any auto-generated survey completed **within 21 days**

### Draft answers

**Company / product overview**

> PursuitAI is a capture-management platform for small businesses pursuing
> U.S. federal contracts — firms holding 8(a), SDVOSB, WOSB and HUBZone
> designations. It aggregates federal opportunity, spend and protest data
> and layers AI scoring, compliance checking and proposal tooling on top.
> Operated by Reliable Cloud Services LLC.

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

## When to revisit

- **Development tier lands** → wire `post_linkedin.py` behind a flag,
  keeping the manual path as the fallback.
- **We ever manage Pages for other organisations** → Standard tier review
  becomes passable, because the product would then actually have the
  application users the screencast asks about.
- **Volume outgrows one company** → a unified provider (Ayrshare, $149/mo
  for 1 profile) starts to make economic sense. It does not today, and it
  would put a third party in possession of our publishing credentials.
