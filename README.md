# PursuitAI Social Engine

Generates branded content, live-site screenshots, and short vertical videos for
[pursuitai.net](https://pursuitai.net), then publishes to X ([@pursuit_ai](https://x.com/pursuit_ai)),
Instagram, and the Pursuit AI LinkedIn Page through the official APIs. Runs on GitHub Actions.

**Every post is reviewed by a human before it goes out.** The audience is
federal contracting BD and capture professionals at small businesses — a small,
reputation-dense market where one wrong factual claim is not recoverable.
Nothing publishes without an explicit approval.

---

## How a post actually happens

```
  09:30 ET, Mon–Sat
        │
        ▼
  ┌───────────┐   picks the next topic + format, renders the media,
  │  prepare  │   writes captions, commits everything to the repo
  └─────┬─────┘
        │  Slack: the card, both captions, and a link to approve
        ▼
  ┌───────────┐   GitHub pauses here. A required reviewer must click
  │  APPROVE  │   approve in the Actions UI. Enforced by GitHub.
  └─────┬─────┘
        │
        ▼
  ┌───────────┐   verifies the content still matches what was approved,
  │  publish  │   posts to X, Instagram, and the LinkedIn Page; logs each channel
  └───────────┘
```

If nobody approves, nothing publishes and **the topic is not consumed** — it
comes back tomorrow. A failed run is loud: non-zero exit, a Slack alert, and a
`failed` row in the log.

---

## Repository layout

### `engine/` — the code

| File | What it does |
|---|---|
| `run.py` | **The orchestrator.** Owns the CLI, topic/format rotation, the state cursor, the approval gate, and per-channel publish outcomes. Everything else is called from here. |
| `awards.py` | Contract-award LinkedIn posts. Reads the public award feed, picks up to three new awards, and queues them for the same approval gate on a separate pending file. |
| `approval.py` | The content-integrity half of the gate. Hashes `pending.json`, writes and verifies `approved.json`, enforces the 24-hour expiry. |
| `captions.py` | Turns a topic into platform copy. Optionally asks Claude for a fresh variant, always falls back to the hand-written hook. Enforces X's character limit. |
| `links.py` | Builds UTM-tagged URLs so the admin dashboard can attribute a visit to a post. Also generates the Instagram bio link. |
| `cards.py` | Renders branded feature cards (1080×1350 for Instagram, 1600×900 for X) with PIL. No network. |
| `adspot.py` | Animated feature spots — real Lucide icons and type flowing through four scenes. No screenshots. |
| `narration.py` | Claude-drafted voiceover scripts, gated by the same compliance rules; deterministic fallback. |
| `voice.py` | Kokoro `af_heart` TTS, matching the product's instructional-video catalogue. Optional — ads ship silent without it. |
| `screenshots.py` | Captures pursuitai.net live via Playwright and crops platform-sized frames, so content stays current as the product changes. |
| `post_x.py` | Posts to X. Media rides the v1.1 upload endpoint, the post itself is API v2. |
| `post_ig.py` | Posts to Instagram. The Graph API fetches media from a public URL itself, which is why assets are committed. |
| `notify.py` | Slack notifications: review requests, failure alerts, the weekly heartbeat, and the LinkedIn pilot winner report. Silent no-op when unconfigured, and never raises. |
| `pilot.py` | 21-day message test on LinkedIn, X, and Instagram. Off unless `PILOT_MODE=1` or `content/pilot.json` has `"enabled": true`. |
| `media.py` | Builds the public media URL, and rejects a base Instagram's fetcher couldn't reach. One place the media host is resolved. |
| `analytics.py` | Reads post performance back from each platform into `logs/metrics.jsonl`. Runs weekly, never on the publish path. |
| `rotation.py` | Weights the topic order by measured engagement. Degrades to plain round-robin when metrics are thin. |

### `content/` — the data

| File | What it does |
|---|---|
| `calendar.json` | **The single source of truth.** Brand config plus 24 feature topics, each with a headline, body, per-platform hooks, and a stat. Add a topic here and nothing else changes. |
| `pilot.json` | The five pilot angles. Edit the copy here. Ids are `utm_content`. `"enabled"` ships `false`. `"channels"` turns X, Instagram, or LinkedIn off without a code change. |
| `state.json` | The rotation cursor: which topic is next, how many runs have published. Only advances on a confirmed post. |
| `pending.json` | The prepared-but-unpublished post, awaiting review. Committed, because the publish job is a separate run. |
| `approved.json` | Proof of approval: a hash of `pending.json` plus a timestamp. Deleted once spent. |
| `posts_preview_14days.md` | A hand-written preview of the first two weeks. Documentation only — no code reads it. |

### Everything else

| Path | What it does |
|---|---|
| `.github/workflows/daily.yml` | The two-job pipeline: `prepare` (cron) → approval → `publish`. |
| `.github/workflows/awards.yml` | Weekday award posts. Same `social-publish` gate, its own queue, LinkedIn only. |
| `scripts/preview.py` | Renders every publishable post — all ratios, every channel's copy, with copy buttons — into one self-contained HTML sheet. Nothing ships that hasn't been looked at. |
| `docs/LINKEDIN_ACCESS.md` | LinkedIn Page API status, the 60-day token re-auth, the manual fallback, and the historical access record. |
| `.github/workflows/heartbeat.yml` | Weekly "N posts in the last 7 days", so silence is itself detectable. |
| `.github/workflows/test.yml` | Runs the test suite; separately, an opt-in credential pre-flight. |
| `scripts/validate_x.py` | Proves the X credentials authenticate and can upload media. Posts nothing. |
| `scripts/validate_ig.py` | Proves the Instagram token, scopes, quota, and — critically — that Instagram can *fetch* your media URL. Creates a real container but never publishes it. |
| `assets/` | Generated cards, screenshots, and video. **Not committed** — uploaded to object storage, which is what Instagram fetches from. |
| `assets/icons/` | **Committed.** Lucide icons (ISC), rasterised once by `scripts/build_icons.py`, so the engine needs no SVG rasteriser at runtime. |
| `logs/posted.jsonl` | Append-only audit trail. One row per run: date, topic, format, captions, and each channel's outcome. |
| `logs/award_posted.jsonl` | Award posts already sent, keyed by the feed page `id` (with a PIID + parent + agency fallback). Older lines keyed by `generated_unique_award_id` still suppress a repeat. |
| `content/awards_pending.json` | The award batch waiting on review. Not the daily `pending.json`. |
| `logs/metrics.jsonl` | Append-only performance samples, keyed to the post ids in `posted.jsonl`. |
| `.github/workflows/analytics.yml` | Weekly metrics collection. Separate from publishing because X reads are billed. |
| `tests/` | 73 tests. See below. |

---

## The two rotations

**Topics** advance one per published post, round-robin through all 24.

**Formats** cycle `card → screenshot → card → video → ad`. The offset shifts each
time the topic list wraps, so a given topic appears in every format over four
cycles rather than being locked to one forever.

Neither advances unless a channel confirms a post, so a failed or unapproved
run replays the same content rather than burning it.

Once enough posts have metrics, the topic cycle is **weighted**: strong
topics appear twice per cycle, weak ones drop out, the middle is untouched,
and the calendar's order is preserved so the feed never reshuffles
wholesale. Below six scored topics it stays plain round-robin — a weighting
scheme that misbehaves on thin data is worse than none.

---

## Commands

```bash
.venv/bin/python engine/run.py --prepare          # pick, render, write pending.json
.venv/bin/python engine/run.py --notify-pending   # send it to Slack for review
.venv/bin/python engine/run.py --approve          # record approval (never automatic)
.venv/bin/python engine/run.py --publish          # verify approval, then post
.venv/bin/python engine/run.py --dry-run          # generate and print, post nothing
.venv/bin/python engine/awards.py --dry-run       # print today's award posts, queue nothing
.venv/bin/python engine/run.py                    # prepare only, then tell you what's next

.venv/bin/python engine/run.py --prepare --format ad   # force a format instead of the rotation

.venv/bin/python scripts/preview.py               # visual review sheet for every post
.venv/bin/python engine/links.py                  # print the Instagram bio link
.venv/bin/python engine/notify.py --heartbeat     # weekly liveness report
.venv/bin/python engine/pilot.py                  # print whether the pilot is on, and the angles
.venv/bin/python engine/pilot.py --report         # weekly per-angle winner, via Slack if configured
pytest tests/ -q                                  # the full suite
```

`--force` skips the approval gate. It prints a loud warning and **hard-refuses
to run in CI**, so it cannot become a production bypass.

`--format` overrides the rotation's choice for one run. The topic still
advances, so use it to preview a format, not to pin one.

`--topic <id>` posts a specific topic instead of the rotation's next — a
correction, or a one-off. It is **out-of-band**: the rotation does not
advance, so it cannot skip whatever was actually due. It bypasses the
cursor, never compliance.

---

## Award posts

Weekdays, `awards.yml` reads the platform feed
`GET /api/v1/public-awards/feed` and queues up to three LinkedIn posts
about brand-new contract awards. It posts to the Page through the same
LinkedIn client as the daily run, after the same `social-publish`
approval, and it writes `content/awards_pending.json` so it cannot collide
with the daily queue.

Only rows with `is_new_award: true` are eligible, including a task
order and a row whose `generated_unique_award_id` is still empty.
The feed is read oldest-first (`order=recent`, which is the order that
returns `next_since`). The three posts are the largest on that page —
the greater of obligation and ceiling — that are not already in
`logs/award_posted.jsonl` (page id, or the older generated id). Each
caption is written from that row (`recipient_name`, `awarding_agency`,
the lead value, and the rest of the feed fields) and links to the
feed's `url`. A task order is not called a new contract, and a vehicle
is named only when `contract_vehicle` is set. A dry run prints them
and stops:

```bash
.venv/bin/python engine/awards.py --dry-run
```

The feed 404s until the platform turns `PUBLIC_AWARD_PAGES_ENABLED` on.
That queues nothing. The token and the optional base URL are in
[SECRETS.md](SECRETS.md). The operating rules are in
[docs/OPERATIONS.md](docs/OPERATIONS.md).

## Pilot mode

Off by default. A 21-day message test: five angles, posted to LinkedIn, X, and Instagram after the same `social-publish` approval as every other post, then a weekly winner so the best one can be boosted.

**Turn it on with either of these. Both have to be the digit `1`, not the word `true`.**

1. Repository variable `PILOT_MODE` = `1`, or a repository secret of the same name. `daily.yml` and `analytics.yml` read `vars.PILOT_MODE` and fall back to `secrets.PILOT_MODE`. An unset variable is empty, which leaves the file in charge.
2. `"enabled": true` in [`content/pilot.json`](content/pilot.json).

`PILOT_MODE=0` forces the pilot off even if the file says enabled. Do not commit access tokens. Turning this on does not change any credential.

**Hold one channel** without a code change. Either spelling works, and the env wins:

| Channel | `content/pilot.json` | Actions variable (or secret of the same name) |
|---|---|---|
| X | `"channels": {"x": false}` | `PILOT_X=0` |
| Instagram | `"channels": {"instagram": false}` | `PILOT_INSTAGRAM=0` |
| LinkedIn | `"channels": {"linkedin": false}` | `PILOT_LINKEDIN=0` |

`1` forces the channel on. `0` forces it off. Unset, or any other word, leaves the file in charge. The file ships all three `true`. `daily.yml` passes the three variables into prepare and publish.

While it is on:

- Each run posts the next angle to LinkedIn, X, and Instagram, unless that channel's toggle is off. The publish job still waits on the `social-publish` environment. Nothing goes out before a human approves.
- X gets `hook_x` plus the tagged link in the post itself, sized to X's 280-character count (a URL weighs 23). There is no threaded reply on a pilot post.
- Instagram gets the angle's portrait card as a JPEG. The caption carries a link-in-bio line and the tagged URL, because an Instagram caption does not make a link tappable.
- LinkedIn rotates the same angles. The product topic cursor does not move, so the normal feed resumes on the topic that was next when the test started.
- Every link is `https://pursuitai.net/` with `utm_medium=organic`, `utm_campaign=pilot21`, and `utm_content` set to the angle id. `utm_source` is `linkedin`, `x`, or `instagram`, matching the channel. The LinkedIn quartet is stored on the `posted.jsonl` row as `utm`. `utm_channels` stores the same tags for x, ig, and linkedin.
- Starting ids, in rotation order: `fifty-percent`, `sam-search`, `teaming`, `proposal-load`, `recompete`. Edit the copy in the JSON. Keep an id once a post has shipped — it is the log key and the `utm_content` value.
- If `LINKEDIN_ORG_ID` is set, the LinkedIn post goes to the Page. If it is not, and `LINKEDIN_ACCESS_TOKEN` is set, the post goes out as that member's profile (the same opt-in `LINKEDIN_ALLOW_MEMBER_POST=1` already is; pilot mode sets it for the run). If there is no token, or the API call fails, the caption is sent through the Slack webhook and logged as `handoff`. LinkedIn is not skipped.
- X posts only when `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, and `X_ACCESS_SECRET` are all set. Instagram posts only when `IG_USER_ID`, `IG_ACCESS_TOKEN`, and `MEDIA_BASE_URL` are set and the JPEG graphic rendered. A missing name is a skip: the log names the secrets, and that channel is not recorded as posted. An expired or rejected token (HTTP 401, or an expired-session error) is the same skip, and the log names the secrets to refresh. Any other API error stays `failed`.
- Monday's analytics run sends the winner report on the same webhook as the heartbeat (`NOTIFY_WEBHOOK_URL`). It sums impressions, engagement, and clicks per angle where the platform returned them. X's own CTA reply is removed from engagement before anyone is ranked. No angled posts yet means no message.

```bash
.venv/bin/python engine/pilot.py            # ON or OFF, and the five ids
.venv/bin/python engine/pilot.py --report   # send the winner report now
```

## Posting by hand

The daily run posts to the Pursuit AI LinkedIn Page through the API, after
the same `social-publish` approval as X and Instagram. A human still
re-authorizes the LinkedIn token about every 60 days; the expiry warning
still runs. See [docs/LINKEDIN_ACCESS.md](docs/LINKEDIN_ACCESS.md).

`scripts/preview.py` is the fallback. Use it for LinkedIn when the API path
cannot post, or when you want to paste a one-off, and for Instagram when
its credentials are absent. Each channel keeps **its own cursor**, so the
two queues advance independently and neither repeats a topic. Compliance
runs first — a topic that cannot publish never reaches you.

```bash
# Still card — fast, no voice model needed
.venv/bin/python scripts/preview.py --linkedin
.venv/bin/python scripts/preview.py --instagram

# Animated spot with voiceover — same renderer the automated runs use
.venv/bin/python scripts/preview.py --linkedin  --format ad
.venv/bin/python scripts/preview.py --instagram --format ad

# A specific topic instead of whatever is next in the queue
.venv/bin/python scripts/preview.py --linkedin --format ad --topic mpjv

# Record it, so that topic does not come round again on that channel
.venv/bin/python scripts/preview.py --posted <topic-id> --channel linkedin
.venv/bin/python scripts/preview.py --posted <topic-id> --channel instagram
```

Each run prints the caption (with its character count against the channel
limit) and the files to attach, then the exact `--posted` command to close
it out. **Nothing is marked posted until you say so** — re-running before
that gives you the same topic again, so a half-finished post is never lost.

### What gets written

Into `assets/linkedin/` or `assets/instagram/`:

| Channel | `--format card` | `--format ad` |
|---|---|---|
| LinkedIn | `square` 1080×1080, `portrait` 1080×1350 | `square` 1080×1080, `video` 1080×1920 |
| Instagram | `ig` 1080×1350 | `video` 1080×1920, `square` 1080×1080 |

Attach **one**, listed most-recommended first. Video ships with a `_poster.jpg`
beside it — set that as the cover rather than accepting the platform default,
which is frame 0 and on these spots is a bare gradient.

### Notes on the animated format

* It needs the **voice model** (`requirements-voice.txt`, ~1 GB, torch from
  the CPU index) and **ffmpeg**. Without the voice model it still renders,
  silent, and says so.
* Budget roughly **2–3 minutes per ratio** on a laptop. The voiceover is
  synthesised once and shared across ratios — rendering it per ratio would
  give each spot a different edit, since scene lengths stretch to the
  narration.
* The copy is the deterministic variant (no Claude call), so the same topic
  produces the same caption every time.

---

---

## Pronunciation

Narration runs through a lexicon before synthesis — `content/pronunciations.json`,
applied by `engine/pronounce.py`. Kokoro spells NAICS out letter by letter,
reads USAspending as "you-ESS ASS-pending", and turns GovCon into "GAHV KAHN";
each rule respells a term phonetically **for the synthesizer only**, so
on-screen text and captions still show the real spelling.

Before adding a term, look at what the synthesizer actually does with it —
do not guess a respelling:

```bash
.venv/bin/python scripts/check_pronunciation.py GovCon      # one term
.venv/bin/python scripts/check_pronunciation.py             # the whole sheet
.venv/bin/python scripts/check_pronunciation.py --scripts   # every ad narration
.venv/bin/python scripts/check_pronunciation.py --drift     # vs the main app
```

The list is seeded from the main app's `video/narrate.py`, which was validated
by ear across the instructional-video catalogue, and vendored rather than
imported — the import pointed at a sibling checkout that does not exist on a
CI runner, so it silently did nothing there. `--drift` reports terms the main
app has picked up since.

Two checks, because there are two scripts. The deterministic fallback is
covered by a test: any acronym reaching the synthesizer without a rule or a
`reads_correctly` entry fails the build. The **Claude-drafted** script does
not exist until render time, so `narration.build()` checks it there and falls
back to the deterministic script if it carries jargon the lexicon has never
seen — naming the terms, so the fix is one line in the sheet.

Whatever is finally spoken is recorded on the post — `narration` as written
and `narration_spoken` as the synthesizer received it — so an ad that sounds
wrong can be traced afterwards instead of guessed at.

---

## Tests

| File | Covers |
|---|---|
| `test_engine.py` | Calendar schema, caption limits, card rendering, video duration, rotation |
| `test_publish_outcomes.py` | Per-channel outcomes, exit codes, topic consumption, notifications |
| `test_links.py` | UTM construction, the landing-page rule, X character weighting |
| `test_approval.py` | The gate: expiry, tampering, CI bypass refusal, no auto-approve |
| `test_awards.py` | Award selection, feed errors, caption fact-grounding, the separate queue |

Failure is injected by overwriting the poster modules inside a throwaway copy of
the project, so tests exercise the real code path and never touch a network.

---

## Running it locally

The engine needs Pillow, requests and tweepy. A system `python3` will
usually be missing at least one of them, and on macOS there is often no
bare `python` at all — so every command below uses the project venv:

```bash
python3 -m venv .venv                    # first time only
./.venv/bin/pip install -r requirements.txt
```

Prefer plain `python`? `source .venv/bin/activate` first — but activate
the venv rather than aliasing `python` to `python3`, which points at an
interpreter without the dependencies and fails confusingly.

## Setup and operations

See **[SECRETS.md](SECRETS.md)** for every credential the engine uses and how to obtain it.
See **[SETUP.md](SETUP.md)** for credentials, the Slack webhook, the approval
environment, the Instagram bio link, and the daily review routine.

## Channels

| Channel | How it publishes |
|---|---|
| X | API, automated — body plus a threaded CTA reply. **Pilot mode** puts the tagged link in the post itself and still waits on `social-publish`. Hold it with `PILOT_X=0`. |
| Instagram | API when credentials exist; **manual paste** meanwhile — see [Posting by hand](#posting-by-hand). **Pilot mode** posts the portrait JPEG and a caption whose link is a copy-paste CTA, after the same approval. Hold it with `PILOT_INSTAGRAM=0`. |
| LinkedIn | Page API, to the Pursuit AI Page, after the same `social-publish` approval. Community Management access is in place. The 60-day token re-auth and the expiry warning still apply. Manual preview is the fallback — see [Posting by hand](#posting-by-hand) and [docs/LINKEDIN_ACCESS.md](docs/LINKEDIN_ACCESS.md). **Pilot mode** posts the message test as the Page when `LINKEDIN_ORG_ID` is set, as the token's member when it is not, or hands you the copy. It does not skip the channel. Hold it with `PILOT_LINKEDIN=0`. |
| Facebook | Not wired. Needs `pages_manage_posts`; permission path documented, not yet verified. |

**Kill switch:** disable the workflow in the Actions tab.
