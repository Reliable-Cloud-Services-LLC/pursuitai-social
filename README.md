# PursuitAI Social Engine

Generates branded content, live-site screenshots, and short vertical videos for
[pursuitai.net](https://pursuitai.net), then publishes to X ([@pursuit_ai](https://x.com/pursuit_ai))
and Instagram through the official APIs. Runs on GitHub Actions.

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
  │  publish  │   posts to X + Instagram, logs the outcome per channel
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
| `approval.py` | The content-integrity half of the gate. Hashes `pending.json`, writes and verifies `approved.json`, enforces the 24-hour expiry. |
| `captions.py` | Turns a topic into platform copy. Optionally asks Claude for a fresh variant, always falls back to the hand-written hook. Enforces X's character limit. |
| `links.py` | Builds UTM-tagged URLs so the admin dashboard can attribute a visit to a post. Also generates the Instagram bio link. |
| `cards.py` | Renders branded feature cards (1080×1350 for Instagram, 1600×900 for X) with PIL. No network. |
| `video.py` | Builds a ~14s vertical 1080×1920 clip from PIL slides via ffmpeg. No external footage. |
| `adspot.py` | Animated feature spots — real Lucide icons and type flowing through four scenes. No screenshots. |
| `narration.py` | Claude-drafted voiceover scripts, gated by the same compliance rules; deterministic fallback. |
| `voice.py` | Kokoro `af_heart` TTS, matching the product's instructional-video catalogue. Optional — ads ship silent without it. |
| `screenshots.py` | Captures pursuitai.net live via Playwright and crops platform-sized frames, so content stays current as the product changes. |
| `post_x.py` | Posts to X. Media rides the v1.1 upload endpoint, the post itself is API v2. |
| `post_ig.py` | Posts to Instagram. The Graph API fetches media from a public URL itself, which is why assets are committed. |
| `notify.py` | Slack notifications: review requests, failure alerts, and the weekly heartbeat. Silent no-op when unconfigured, and never raises. |
| `media.py` | Builds the public media URL, and rejects a base Instagram's fetcher couldn't reach. One place the media host is resolved. |
| `analytics.py` | Reads post performance back from each platform into `logs/metrics.jsonl`. Runs weekly, never on the publish path. |
| `rotation.py` | Weights the topic order by measured engagement. Degrades to plain round-robin when metrics are thin. |

### `content/` — the data

| File | What it does |
|---|---|
| `calendar.json` | **The single source of truth.** Brand config plus 24 feature topics, each with a headline, body, per-platform hooks, and a stat. Add a topic here and nothing else changes. |
| `state.json` | The rotation cursor: which topic is next, how many runs have published. Only advances on a confirmed post. |
| `pending.json` | The prepared-but-unpublished post, awaiting review. Committed, because the publish job is a separate run. |
| `approved.json` | Proof of approval: a hash of `pending.json` plus a timestamp. Deleted once spent. |
| `posts_preview_14days.md` | A hand-written preview of the first two weeks. Documentation only — no code reads it. |

### Everything else

| Path | What it does |
|---|---|
| `.github/workflows/daily.yml` | The two-job pipeline: `prepare` (cron) → approval → `publish`. |
| `scripts/preview.py` | Renders every publishable post — all ratios, every channel's copy, with copy buttons — into one self-contained HTML sheet. Nothing ships that hasn't been looked at. |
| `docs/LINKEDIN_ACCESS.md` | Why LinkedIn is pasted by hand, and the Development-tier application to submit. |
| `.github/workflows/heartbeat.yml` | Weekly "N posts in the last 7 days", so silence is itself detectable. |
| `.github/workflows/test.yml` | Runs the test suite; separately, an opt-in credential pre-flight. |
| `scripts/validate_x.py` | Proves the X credentials authenticate and can upload media. Posts nothing. |
| `scripts/validate_ig.py` | Proves the Instagram token, scopes, quota, and — critically — that Instagram can *fetch* your media URL. Creates a real container but never publishes it. |
| `assets/` | Generated cards, screenshots, and video. **Not committed** — uploaded to object storage, which is what Instagram fetches from. |
| `assets/icons/` | **Committed.** Lucide icons (ISC), rasterised once by `scripts/build_icons.py`, so the engine needs no SVG rasteriser at runtime. |
| `logs/posted.jsonl` | Append-only audit trail. One row per run: date, topic, format, captions, and each channel's outcome. |
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
.venv/bin/python engine/run.py                    # prepare only, then tell you what's next

.venv/bin/python engine/run.py --prepare --format ad   # force a format instead of the rotation

.venv/bin/python scripts/preview.py               # visual review sheet for every post
.venv/bin/python engine/links.py                  # print the Instagram bio link
.venv/bin/python engine/notify.py --heartbeat     # weekly liveness report
pytest tests/ -q                                  # the full suite
```

`--force` skips the approval gate. It prints a loud warning and **hard-refuses
to run in CI**, so it cannot become a production bypass.

`--format` overrides the rotation's choice for one run. The topic still
advances, so use it to preview a format, not to pin one.

---

## Posting by hand

LinkedIn has no API access, and Instagram has none yet. Both are posted by
hand from artifacts this script generates. Each channel keeps **its own
cursor**, so the two queues advance independently and neither repeats a
topic. Compliance runs first — a topic that cannot publish never reaches you.

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

## Tests

| File | Covers |
|---|---|
| `test_engine.py` | Calendar schema, caption limits, card rendering, video duration, rotation |
| `test_publish_outcomes.py` | Per-channel outcomes, exit codes, topic consumption, notifications |
| `test_links.py` | UTM construction, the landing-page rule, X character weighting |
| `test_approval.py` | The gate: expiry, tampering, CI bypass refusal, no auto-approve |

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
| X | API, automated — body plus a threaded CTA reply |
| Instagram | API when credentials exist; **manual paste** meanwhile — see [Posting by hand](#posting-by-hand) |
| **LinkedIn** | **Manual paste — see [Posting by hand](#posting-by-hand).** Community Management Standard tier review requires demonstrating application users, a third-party OAuth flow, and member profile data in a UI — none of which a first-party publishing bot has. See [docs/LINKEDIN_ACCESS.md](docs/LINKEDIN_ACCESS.md). |
| Facebook | Not wired. Needs `pages_manage_posts`; permission path documented, not yet verified. |

**Kill switch:** disable the workflow in the Actions tab.
