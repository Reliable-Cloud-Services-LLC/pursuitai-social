# Operations

**What this is:** enough context to pick this up cold — on a different
laptop, after a break, or as a different person. The README explains how
the engine works; this explains where it currently stands, what has bitten
us, and what is still open.

Keep it current. A stale operations doc is worse than none, because it is
believed.

_Last reviewed: 2026-08-01._

---

## Where things stand

| | |
|---|---|
| **X** | Automated. Cron live, every post human-approved. |
| **Instagram** | Automated. System User token (never expires). Cards, screenshots and Reels all proven live. |
| **LinkedIn** | Manual by necessity — no API access for a first-party publishing bot. See [LINKEDIN_ACCESS.md](LINKEDIN_ACCESS.md). |
| **Facebook** | Not wired. |
| **Rotation** | 23 of 24 topics publishable. `card → screenshot → card → ad`. |
| **Cron** | Mon–Sat 13:37 UTC, `daily.yml`. |

The one blocked topic is `capability-statement` — **UNVERIFIABLE**, not
pending work. Its central claim cannot be traced to a source, so it stays
out.

### The daily rhythm

1. Cron prepares a post and pushes it for review (Slack).
2. **It parks at the `social-publish` environment until a human approves.**
   GitHub enforces that pause, not our code — no flag or env var here can
   bypass it.
3. A run nobody approves simply expires. The topic is **not** consumed and
   comes round again.

### Alarms

- **Missed-run alarm** — `missed-run.yml`, 17:07 UTC Mon–Sat. Alerts if no
  post landed on a day one was due.
- **Heartbeat** — `heartbeat.yml`, Mondays 14:23 UTC. Weekly liveness.
  (UTC like the rest of this section — it used to be written in ET, which
  made it the one time here you had to convert before comparing it to a
  cron.)

---

## Things that will surprise you

### The cron runs ~2 hours late, and that is normal

GitHub documents it: *"The `schedule` event can be delayed during periods
of high loads… If the load is sufficiently high enough, some queued jobs
may be dropped."*

Observed here, twice, almost identically:

| date | cron | fired | late |
|---|---|---|---|
| 2026-07-30 | 13:30 | 15:28 | 118 min |
| 2026-07-31 | 13:30 | 15:33 | 123 min |

So ~2h is **typical**, not the tail. The missed-run alarm sits at 17:07 —
210 minutes after the cron — because an alarm that cries wolf is worse
than no alarm: the next real one gets dismissed.

**Do not "fix" a late run.** Only a *missing* one is a problem.

### Local rendering lies about fonts

`cards.py` prefers DejaVu and falls back to Arial. CI has DejaVu; macOS
does not. **DejaVu is wider**, so copy that fits locally can overflow on
the runner.

The card-overflow test therefore **skips** when DejaVu is absent rather
than passing against the wrong font — a visible skip beats a false green.
It shipped a broken card twice before that skip existed.

**Card layout is confirmed by CI, not by your laptop.** Install DejaVu
locally if you want the check to mean something here.

### Two channels, two viewports

The `screenshot` format captures the site **twice** — desktop for X,
portrait for Instagram. One capture cropped two ways put half the headline
outside the 4:5 frame ("Win More Set-Asides." became "Set-Asides."), and it
looked *correct on X the whole time*, because a 16:9 crop trims height
instead of width.

### Instagram accepts JPEG only

Meta's reference: *"JPEG is the only image format supported."* Everything
renders as PNG, so the Instagram variant is converted at **prepare** time —
prepare is what syncs to the bucket, so a file created during publish is
never uploaded for Instagram to fetch.

### A conflicting PR is not a failing PR

GitHub cannot build a merge commit for a conflicting PR, so **it runs no
checks at all**. No red X — just silence that looks identical to "still
queuing."

---

## Runbook

### Post to LinkedIn (manual, ~weekly)

```bash
.venv/bin/python scripts/preview.py --linkedin              # next card
.venv/bin/python scripts/preview.py --linkedin --format ad  # animated spot
.venv/bin/python scripts/preview.py --posted <topic-id> --channel linkedin
```

Nothing is marked posted until that last command, so re-running gives you
the same topic back. An animated spot needs the voice model + ffmpeg and
takes 2–3 min per ratio.

### Re-post one channel after a single-channel failure

The topic is consumed once *any* channel reaches an audience, so a naive
re-run duplicates on the healthy channel. Instead:

```
Actions → Daily social post → Run workflow
  format = <same>   topic = <same>   skip = x        (or skip = ig)
```

`--topic` is **out-of-band**: the rotation does not advance, so the
correction cannot consume whatever was genuinely due next.

### Verify Instagram credentials before trusting them

```bash
export IG_USER_ID=... IG_ACCESS_TOKEN=... MEDIA_BASE_URL=...
.venv/bin/python scripts/validate_ig.py --container   # image path
.venv/bin/python scripts/validate_ig.py --reel        # video path
```

Publishes nothing. Containers expire in 24h. **It fails deliberately on a
token expiring within 7 days** — a chain can be perfect and still not
survive a daily cron.

### Replace a bad post

Neither platform can swap media on a live post. Delete it by hand, then
re-post out-of-band with `skip` set to the healthy channel. **Deleting an X
post does not delete its threaded reply** — that has to go separately or it
is left orphaned.

---

## Claims discipline

Every publishable claim is traced to executing code, a primary regulatory
source, or a dated operator measurement. `verification` on each topic in
`content/calendar.json` records the evidence — and, where a previous pass
got it wrong, what it missed.

**The lesson worth carrying:** an independent audit overturned 5 of 22
claims that had been marked VERIFIED. In every case the `source_ref` proved
the *easy half* of the sentence — the 30-occupation constant but not the
feed list; the accepted file types but not the schema's fields. A ref that
proves part of a claim reads exactly like one that proves the claim.

`source_type` values: `code`, `external`, `external+code`, and
`code+operator` — the last for a figure code *cannot* confirm (a production
row count), which must record what was measured and when.

---

## What the gates do and do not check

Compliance, pronunciation, freshness, card-overflow and the claims schema
all check whether a post is **permitted**. None check whether it is
**good**.

Both of these passed every automated gate and were caught by a human
looking at the result:

- a video that shipped **silent**, with text pulsing in and out
- a screenshot with its **headline sliced in half**

That is the work the approval gate exists to make possible. Look at the
post, not just the checkmark.

---

## Open threads

- **Award posts** — LinkedIn posts about recent federal awards
  (OrangeSlices-style). Mock approved: lead with **obligated**, not
  ceiling; **skip sole-source**. Needs a PAT so the engine can read
  `/contract-awards/feed`, plus two filters: `modification_count`
  (so "wins" never describes an option-year mod) and enrichment coverage
  (so the competition line is not silently absent).
- **Customer win detection** — spec handed to the main-app session:
  detect when a *customer* wins via `contract_awards.recipient_uei →
  company_profiles.uei`, with explicit opt-in before naming anyone.
- **Cron timing** — if the ~2h delay proves systematic, moving the cron
  ~2h earlier would land posts at the intended 9:30 ET. Wants a third data
  point first.
- **Hero animation** — a looping animation on the live site occasionally
  drifts across the headline in captures. Cosmetic; a per-section
  `scroll_y` tweak would dodge it.

---

## Where the rest lives

| | |
|---|---|
| How it works | [README.md](../README.md) |
| Credentials | [SECRETS.md](../SECRETS.md) |
| LinkedIn API refusal | [LINKEDIN_ACCESS.md](LINKEDIN_ACCESS.md) |
| Why each claim is trusted | `verification` blocks in `content/calendar.json` |
| Why a change was made | the PR that made it — they record reasoning, not just diffs |
| Gotchas | test docstrings, which name the incident they came from |
