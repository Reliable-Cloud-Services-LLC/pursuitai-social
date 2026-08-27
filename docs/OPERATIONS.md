# Operations

**What this is:** enough context to pick this up cold — on a different
laptop, after a break, or as a different person. The README explains how
the engine works; this explains where it currently stands, what has bitten
us, and what is still open.

Keep it current. A stale operations doc is worse than none, because it is
believed.

_Last reviewed: 2026-08-27._

---

## Where things stand

| | |
|---|---|
| **X** | Automated. Cron live, every post human-approved. |
| **Instagram** | Automated. System User token (never expires). Cards, screenshots and Reels all proven live. |
| **LinkedIn** | Manual by necessity — no API access for a first-party publishing bot. See [LINKEDIN_ACCESS.md](LINKEDIN_ACCESS.md). |
| **Facebook** | Not wired. |
| **Rotation** | 23 of 24 topics publishable. `card → screenshot → card → ad`. |
| **Cron** | Mon–Sat 13:37 UTC, `daily.yml`. Late is normal; dropped happens. |

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
  post landed on a day one was due. **It cannot cover the day GitHub drops
  every schedule, because it is dropped too** — see below.
- **Gap check** — a job on `daily.yml`, so it runs whenever the workflow
  fires rather than on a schedule of its own. Reports scheduled days
  immediately before today on which no run was created. This is the
  backstop for the alarm's blind spot; it is retroactive by nature.
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

### …and some days it does not run at all

On **2026-08-27** GitHub dropped *every* scheduled run for this repo — the
post at 13:37 and the alarm at 17:07. No run records, no failures, no
incident on GitHub's status page. Nothing on our side had changed: cron
intact, workflow `active`, default branch correct, YAML valid.

Two things follow, and the second is the one that bites.

**A drop leaves no trace.** A late run is visible; a dropped one is
indistinguishable from a day nobody scheduled. Check for the *absence* of a
run record, not for a failure.

**The alarm shares the failure mode it detects.** `missed-run.yml` is itself
a scheduled workflow, so the day GitHub drops schedules it is dropped too.
It is structurally blind to its own case and no tuning fixes that — a
scheduled workflow cannot detect its own non-execution. That is why the gap
check lives on `daily.yml` and looks *backwards*: only a workflow that
actually fired can report one that did not.

The residual hole cannot be closed from inside GitHub Actions: if every
scheduled workflow is dropped for several days running, nothing reports
anything until one fires again — and then the gap check reports the whole
outage at once.

### An `ad` takes 9–13 minutes to prepare, and that is not a hang

Observed: **8.8 min** on the first successful ad run (2026-07-29),
**~13 min** on 2026-08-07. A `card` finishes in well under a minute, so
the contrast reads as a stall when it is not.

The cost is real work: ~550 frames rendered in PIL at **3× supersampling**
(a 3240×3240 canvas per frame at 1:1) on a 2-core runner, plus a Claude
narration call and Kokoro TTS before any of it starts.

Before concluding a run is stuck, check **which step** it is on:

```bash
gh run view <run-id> --json jobs \
  --jq '.jobs[] | select(.name=="prepare") | .steps[]
        | select(.status!="completed") | .name'
```

`Prepare assets + captions` on an ad is patience. Anything else for that
long is worth looking at.

### apt can stall for tens of minutes

On 2026-08-07 the `Install ffmpeg` step hung past **17 minutes**. Nothing
was wrong with the render — a Debian mirror was simply not responding, and
the step had no bound, so it was on course to burn the whole 30-minute job
budget and lose the post window.

`daily.yml` now caps that step at **6 minutes** (a healthy install is under
two), matching the guard `test.yml` had carried since PR #9 — that lesson
existed for a year and was never back-ported to the job that actually
publishes. A test asserts every `apt-get install` across all workflows sits
under a step timeout.

**It resolved on its own.** If it happens again: cancel, re-dispatch, and
expect a fast failure rather than a long one. Nothing is lost — no post
published, no topic consumed.

**Every run installs ffmpeg and the ~1 GB voice model, including the three
formats that need neither.** Gating them would mean surfacing the
rotation's format choice before `prepare` runs, which is a real refactor,
not a flag. Until then, a `card` post can fail on a video dependency.

### Local rendering lies about fonts

`cards.py` prefers DejaVu and falls back to Arial. CI has DejaVu; macOS
does not. **DejaVu is wider**, so copy that fits locally can overflow on
the runner.

The card-overflow test therefore **skips** when DejaVu is absent rather
than passing against the wrong font — a visible skip beats a false green.
It shipped a broken card twice before that skip existed.

**Card layout is confirmed by CI, not by your laptop.** Install DejaVu
locally if you want the check to mean something here.

### A screenshot post shows the topic's own feature card

Not a crop of the marketing site. `capture_topic()` grabs the element the
live site tags `data-social-shot="<topic-id>"` (added in pursuit-ai#2146)
and composes it onto the brand gradient: a 16:9 spotlight for X — headline,
stat and price beside the card — and a 4:5 fill for Instagram, which gains
a headline above the card only when the card does not already fill the
frame. ~8s per topic.

It got here the long way, and the wrong turn is worth knowing. Screenshot
posts originally took a viewport crop of whatever generic section a cursor
landed on, and **every screenshot post for two months used the identical
hero image** — the selection loop broke on the first section that existed,
and the site root always exists. Rotating the sections fixed the repetition
and made relevance *worse*: an 8(a) post then showed Opportunity Discovery,
Grants and AI Fit Scoring. A different generic image is still generic.

Two things the old approach taught, both still live:

* **The page animates.** Sections fade in on scroll and numbers count up, so
  a capture races all of them. One caught a counter mid-count reading 0
  where it should have read 97. Captures now run with
  `reduced_motion="reduce"`.
* **Anything pinned to the top is painted OVER the element**, and an element
  screenshot takes pixels as rendered — the site nav landed inside the frame
  and clipped tall cards. `_hide_pinned_chrome` matches on *computed
  position*, not tag name: the first attempt hid `header`, the nav is a
  `<nav>`, and it silently did nothing.

**The fallback still exists and still matters.** A topic whose anchor is
missing from the live site — a new topic, or a capture against a deploy
that predates the anchors — falls back to a generic section, so it gets a
weaker post rather than no post. `pricing-plans` takes it permanently: its
anchor tags a whole *section*, 6.13 tall/wide against 0.58–1.32 for every
real card, and composed it was an illegible sliver. `SECTIONS_WITHHELD`
records the three generic sections not fit to publish, each with the defect
it shows, because an undocumented exclusion rots into superstition.

### A reel container that comes back ERROR is re-created

On 2026-08-17 a reel failed with `2207085` — a code **outside** Meta's
documented `2207001–2207057` range, so no file-shaped complaint fired
(`2207026` is "video format is not supported", `2207052` is "could not be
fetched") and nothing distinguished a bad file from a transient transcode.
Those need opposite responses, so nothing was done for ten days.

It was transient. The 2026-08-24 ad published from the same renderer and
pipeline, and a fresh container for that file transcodes clean. The answer
was in `posted.jsonl` the whole time; **nothing surfaced it because the reel
path had no CI coverage** — `validate_ig.py --reel` existed for exactly this
and was never wired in. It is now a step in the credential-validation job.

`post_reel` retries an `ERROR` verdict once. Deliberately nothing else: a
rejected *creation* is a 4xx about credentials or payload that an identical
re-send cannot fix, and a container that never *finishes* burns the full
ten-minute poll first, which twice over does not fit the publish job's
thirty-minute budget. That distinction is why `ERROR` raises its own
`ContainerProcessingError` rather than being matched on message text.

**Confirmed, not inferred.** On 2026-08-27 the exact rejected file was
re-run through a fresh container with `--reel --file
assets/video/pricing-plans_ad.mp4` and reached `transcode FINISHED`. The
file was never bad, and a second attempt on the day would very likely have
published it.

Getting there needed `--file`, because `--reel` defaults to the NEWEST
posted video — which by then was a later, healthy ad. A run had already come
back green against that file and said nothing about the one in question, so
the output now names its target and marks whether `--file` chose it. A green
reel check that tested something else looks identical to one that did not.

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

You usually do not need local secrets for this: both paths now run in CI —
Actions → *Tests & credential validation* → Run workflow, with
`validate_credentials` ticked. That is also the only way to exercise them
with the same credentials production publishes with. NB `--reel` builds a
real REELS container each run, so it is no longer a free check.

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

All three of these passed every automated gate and were caught by a human
looking at the result:

- a video that shipped **silent**, with text pulsing in and out
- a screenshot with its **headline sliced in half**
- a screenshot that was perfectly rendered and **about the wrong feature** —
  8(a) copy over a picture of Opportunity Discovery, Grants and AI Fit
  Scoring

The third is the instructive one. Nothing was broken: the image was sharp,
correctly cropped, on-brand, and every check passed. It was simply not about
the thing the post was about, and no gate has an opinion on that. Relevance
is not a property any of these tests can see.

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
- **Cron timing** — the third data point arrived and changed the question:
  2026-08-27 was not a delay but a **drop**. Moving the cron earlier does
  nothing for a drop, so this is now about whether ~2h late is worth
  correcting on its own. Probably not.
- **LinkedIn automation** — being scoped. Manual today; see
  [LINKEDIN_ACCESS.md](LINKEDIN_ACCESS.md) for what was already refused and
  why.
- **Hero animation** — was: a looping animation drifting across the headline
  in captures. Largely moot now that captures run with
  `reduced_motion="reduce"` and target an element rather than a viewport.
  Still live for the generic-section fallback, which is why
  `how-it-works`, `pipeline-board` and `why` remain withheld.

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
