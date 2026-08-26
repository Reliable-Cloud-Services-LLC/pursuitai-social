"""Daily orchestrator - the autonomous loop.

Two phases (so Instagram can fetch media from committed public URLs):

  python engine/run.py --prepare   # pick topic, build assets + captions,
                                   # write content/pending.json (no posting)
  python engine/run.py --publish   # read pending.json, post to X + IG,
                                   # advance state, append logs/posted.jsonl

  python engine/run.py             # both phases in one go (local use)
  python engine/run.py --dry-run   # prepare only, print captions

Rotation: topics round-robin through content/calendar.json; formats cycle
card -> screenshot -> card -> ad so the feed never looks templated.
"""
import argparse
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import approval
import brand as brand_tokens
import cards
import captions
import compliance
import media
import pronounce
import rotation

STATE = os.path.join(ROOT, "content", "state.json")
PENDING = os.path.join(ROOT, "content", "pending.json")
APPROVED = os.path.join(ROOT, "content", "approved.json")
LOG = os.path.join(ROOT, "logs", "posted.jsonl")
# 1:1 travels furthest — it is native on LinkedIn, and both X and Instagram
# render it without cropping. preview.py emits 9:16 and 16:9 alongside.
AD_RATIO = "square"
METRICS = os.path.join(ROOT, "logs", "metrics.jsonl")
# 5 slots against 18 publishable topics: gcd(18, 5) = 1, so every topic
# eventually appears in every format. "ad" is the animated spot (adspot.py);
# "video" is the older slide-based clip.
FORMATS = ["card", "screenshot", "card", "ad"]

def load_json(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default

def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, indent=2)

def select_format(run_count, topic_count):
    """Pick the format for a run, without locking a topic to one format.

    topic_index and run_count advance together, so a naive
    FORMATS[run_count % len(FORMATS)] locks every topic to one format
    whenever the counts share a factor — fit-scoring was always a card,
    subaward-intel always a video.

    Offset the topic's own index by the completed-cycle count. Note this
    must use the INDEX, not run_count: `(run_count + cycle)` expands to
    `i + cycle*(topic_count + 1)`, which silently re-locks whenever
    topic_count + 1 is a multiple of len(FORMATS) — 24 topics against 5
    formats did exactly that, and a test caught it when `ad` was added.
    """
    index = run_count % topic_count
    cycle = run_count // topic_count
    return FORMATS[(index + cycle) % len(FORMATS)]

# Every section screenshots.py captures, in the order they are used.
#
# The previous code walked a fixed ("hero", "features", "pricing", "why")
# and broke on the first that existed. `hero` is the site root, so it always
# captures and therefore always won: ALL NINE screenshot posts between
# 2026-07-18 and 2026-08-26 shipped the identical hero image, across nine
# different topics, while features/pricing/why were re-captured every run
# and never once used. how-it-works and pipeline-board were not even in the
# list they were selected from.
SECTION_ORDER = ("hero", "how-it-works", "pipeline-board",
                 "features", "pricing", "why")


def pick_section(shot_index, sdir):
    """(x_path, ig_path) for this screenshot post, or (None, None).

    Starts at the cursor and walks the WHOLE list, so a section whose
    capture failed is stepped over rather than costing the post — the
    resilience the old loop had, without the fixed start that made it
    degenerate to one image forever.

    Indexed by a dedicated cursor rather than something derived from
    run_count. select_format's docstring is the cautionary tale: an index
    computed by modular arithmetic over a second counter silently re-locks
    whenever the two share a factor, which is exactly how every topic got
    pinned to one format. A cursor that only ever means "how many screenshot
    posts have shipped" cannot do that.
    """
    n = len(SECTION_ORDER)
    for step in range(n):
        name = SECTION_ORDER[(shot_index + step) % n]
        xp = os.path.join(sdir, f"{name}_x.png")
        ip = os.path.join(sdir, f"{name}_ig.png")
        if os.path.exists(xp) and os.path.exists(ip):
            return xp, ip
    return None, None


def publishable_topics(cal):
    """Topics that can actually go out today.

    Two conditions, and both are needed. Status VERIFIED means the claims
    were traced to a source. A clean template caption means the copy also
    passes the content rules — a topic can be VERIFIED on its central claim
    and still carry a sentence we cannot stand behind ("No competitor has
    this"), and selecting it would deadlock: prepare picks it, publish
    refuses, forever.

    Rotation runs over this subset, and publish() indexes the same list, so
    the cursor cannot drift.
    """
    out = []
    for topic in cal["topics"]:
        if not compliance.is_publishable(topic):
            continue
        drafts = {
            "x": captions.build_x(topic, cal["brand"], fmt="card", fresh=False),
            "ig": captions.build_ig(topic, cal["brand"], fresh=False),
        }
        # The card renders headline/body/stat as pixels, which the caption
        # check never saw — a claim living in `body` shipped on every card
        # unexamined until the ad work surfaced it.
        if compliance.check_rendered(topic):
            continue
        if not any(compliance.check_claims(topic, c) for c in drafts.values()):
            out.append(topic)

    # Weight by measured performance where we have enough of it. With no
    # metrics — the state for the first several weeks — this returns `out`
    # unchanged, so rotation behaves exactly as it did before.
    import analytics
    scores = rotation.topic_scores(analytics.load_jsonl(LOG),
                                   analytics.load_jsonl(METRICS))
    return rotation.build_rotation(out, scores)


# The cursor is a bare index into the publishable list, and that list GROWS
# as topics get verified — 18 to 23 in one week of audit work. The index
# stays put while the list shifts under it, so it can land on a topic that
# already posted days ago. Caught live: slot 2 pointed at fifty-percent-rule
# ~36h after X carried exactly that card. The rotation had no memory of WHAT
# posted, only how many.
REPEAT_COOLDOWN_DAYS = 14


def _recently_posted_ids(days=REPEAT_COOLDOWN_DAYS):
    """Topic ids that reached ANY audience in the last `days` days."""
    import analytics
    import datetime
    cutoff = (datetime.date.today()
              - datetime.timedelta(days=days)).isoformat()
    recent = set()
    for entry in analytics.load_jsonl(LOG):
        channels = entry.get("channels") or {}
        posted = any((c or {}).get("status") == "posted"
                     for c in channels.values()) or entry.get("x")
        if posted and (entry.get("date") or "") >= cutoff:
            recent.add(entry.get("topic"))
    return recent


def _next_fresh_topic(topics, state):
    """The cursor's pick, advanced past anything an audience saw recently.

    Walks forward from the cursor rather than filtering the list — the
    cursor indexes into the list, so removing entries would shift every
    other topic's slot and reintroduce the exact bug this fixes. If every
    topic is recent (tiny calendar, aggressive cadence) the cursor's own
    pick stands: repeating beats publishing nothing.
    """
    recent = _recently_posted_ids()
    start = state["topic_index"] % len(topics)
    for offset in range(len(topics)):
        candidate = topics[(start + offset) % len(topics)]
        if candidate["id"] not in recent:
            if offset:
                print(f"[prepare] slot {start} ({topics[start]['id']}) "
                      f"posted within {REPEAT_COOLDOWN_DAYS}d — advanced "
                      f"{offset} to {candidate['id']}")
            return candidate
    print(f"[prepare] every topic posted within {REPEAT_COOLDOWN_DAYS}d — "
          f"repeating {topics[start]['id']}")
    return topics[start]


def prepare(force_format=None, force_topic=None):
    cal = load_json(os.path.join(ROOT, "content", "calendar.json"), None)
    brand = cal["brand"]
    topics = publishable_topics(cal)
    if not topics:
        print("[prepare] REFUSED: no VERIFIED topics. Every claim in "
              "calendar.json is untraced, mismatched, or unverifiable.")
        sys.exit(1)
    state = load_json(STATE, {"topic_index": 0, "run_count": 0})
    if force_topic:
        topic = next((t for t in topics if t["id"] == force_topic), None)
        if topic is None:
            known = any(t["id"] == force_topic for t in cal["topics"])
            print(f"[prepare] REFUSED: {force_topic!r} is "
                  + ("not publishable" if known else "not a known topic"))
            sys.exit(1)
    else:
        topic = _next_fresh_topic(topics, state)
    fmt = force_format or select_format(state["run_count"], len(topics))
    today = datetime.date.today().isoformat()
    print(f"[prepare] {today} topic={topic['id']} format={fmt}")

    narration_script = None
    shot_x = shot_ig = None
    if fmt == "screenshot":
        try:
            import screenshots
            screenshots.capture_all()
        except Exception as e:
            print(f"[prepare] screenshot refresh failed ({e})")
        shot_x, shot_ig = pick_section(
            state.get("shot_index", 0),
            os.path.join(ROOT, "assets", "screenshots"))
        if shot_x:
            print(f"[prepare] section={os.path.basename(shot_x)[:-6]}")

    card_x = os.path.join(ROOT, "assets", "cards", f"{topic['id']}_x.png")
    card_ig = os.path.join(ROOT, "assets", "cards", f"{topic['id']}_ig.png")
    cards.render_card(topic, brand, (1600, 900), card_x)
    cards.render_card(topic, brand, (1080, 1350), card_ig)

    ad_path = None
    if fmt == "ad":
        try:
            import adspot
            import narration
            import voice
            script = narration_script = narration.build(topic, brand)
            ad_dir = os.path.join(ROOT, "assets", "video")
            wav = os.path.join(ad_dir, f"{topic['id']}_vo.wav")
            secs = voice.synthesize(script, wav)
            ad_path = os.path.join(ad_dir, f"{topic['id']}_ad.mp4")
            adspot.make_ad(topic, brand, ad_path,
                           size=brand_tokens.size(AD_RATIO),
                           voice_wav=wav if secs else None,
                           narration=script)
        except Exception as e:
            # An ad is the richest format and the most ways to fail. Falling
            # back to the card keeps the day's post rather than losing it.
            print(f"[prepare] ad build failed ({e}); using card")
            # Clear the script too: a card has no audio, and recording a
            # narration against one would put a line in the post log that
            # was never spoken.
            ad_path, fmt, narration_script = None, "card", None

    if fmt == "ad" and ad_path:
        media_x, media_ig = ad_path, ad_path
    elif fmt == "screenshot" and shot_x:
        media_x, media_ig = shot_x, shot_ig
    else:
        fmt = "card"
        media_x, media_ig = card_x, card_ig

    # Instagram fetches by URL and accepts JPEG only (Meta's
    # content-publishing reference). Everything we render is PNG, so the
    # IG variant is converted here, at prepare time — the prepare job is
    # what syncs assets/ to the bucket, so a file created at publish time
    # would never be uploaded for Instagram to fetch.
    cover_ig = None
    if media_ig.lower().endswith((".mp4", ".mov")):
        # Meta defaults a Reels cover to thumb_offset=0, the first frame.
        # Our spots open on a bare gradient, so the profile grid would show
        # a blank square. The poster we already render is the cover.
        poster = media.poster_for(media_ig)
        cover_ig = poster if os.path.exists(poster) else None
    else:
        media_ig = media.as_jpeg(media_ig)

    pending = {
        "date": today, "topic": topic["id"], "format": fmt,
        "text_x": captions.build_x(topic, brand, fmt=fmt),
        "text_x_reply": captions.build_x_reply(topic, brand, fmt),
        "text_ig": captions.build_ig(topic, brand),
        "media_x": os.path.relpath(media_x, ROOT),
        "media_ig": os.path.relpath(media_ig, ROOT),
        "cover_ig": os.path.relpath(cover_ig, ROOT) if cover_ig else None,
        # An out-of-band post — a correction, or a one-off. It must NOT
        # move the rotation: advancing past a forced topic would silently
        # skip whatever was actually next.
        "out_of_band": bool(force_topic),
        # What the ad actually says. Drafted by Claude at render time and
        # previously discarded, so a mispronunciation or an off-message line
        # could not be traced after the fact — the only record was the
        # rendered audio. Both forms: as written, and as the synthesizer
        # receives it after the pronunciation lexicon.
        "narration": narration_script,
        "narration_spoken": (pronounce.spoken(narration_script)
                             if narration_script else None),
    }
    # Last check before a human is asked to review it. A Claude-rewritten
    # caption can introduce a claim the source topic never made, so the
    # rendered text is checked, not the template it came from. On a
    # violation, fall back to the hand-written copy — which the selection
    # filter already proved clean — rather than losing the day's post.
    if compliance.check_claims(topic, pending["text_x"]) or \
            compliance.check_claims(topic, pending["text_ig"]):
        print("[prepare] AI caption tripped a content rule; "
              "falling back to the vetted template copy")
        pending["text_x"] = captions.build_x(topic, brand, fmt=fmt, fresh=False)
        pending["text_x_reply"] = captions.build_x_reply(topic, brand, fmt)
        pending["text_ig"] = captions.build_ig(topic, brand, fresh=False)
    try:
        compliance.assert_publishable(topic, {"x": pending["text_x"],
                                              "ig": pending["text_ig"]})
    except compliance.ComplianceError as e:
        print(f"[prepare] BLOCKED by compliance: {e}")
        sys.exit(1)

    save_json(PENDING, pending)
    print("[prepare] wrote content/pending.json")
    return pending

def _post_x(pending):
    import post_x
    # text_x_reply is absent from a pending.json prepared before W4.
    return post_x.post(pending["text_x"],
                       os.path.join(ROOT, pending["media_x"]),
                       reply_text=pending.get("text_x_reply"))

def _post_ig(pending):
    import post_ig
    if pending["media_ig"].endswith(".mp4"):
        return post_ig.post_reel(pending["media_ig"], pending["text_ig"],
                                 cover_rel_path=pending.get("cover_ig"))
    return post_ig.post_image(pending["media_ig"], pending["text_ig"])

# channel -> (env var proving credentials are present, poster)
POSTERS = {
    "x":  ("X_API_KEY", _post_x),
    "ig": ("IG_USER_ID", _post_ig),
}

def publish(skip_x=False, skip_ig=False, force=False):
    """Post the prepared content. Four outcomes per channel:

      posted    reached the platform, carries an id
      failed    the call raised, carries the error
      skipped   enabled but credentials absent
      disabled  turned off explicitly with --skip-x / --skip-ig

    Exits non-zero if anything failed OR if nothing posted at all — a run
    that publishes nothing must never report success.
    """
    pending = load_json(PENDING, None)
    if not pending:
        print("[publish] no pending.json - run --prepare first")
        sys.exit(1)

    # The gate runs before anything else touches a network: an unapproved
    # post must reach no channel, consume no topic, and leave pending.json
    # intact for the human who still has to review it.
    if force:
        if os.environ.get("GITHUB_ACTIONS"):
            print("[publish] REFUSED: --force is local-only and cannot be "
                  "used in CI. Approve the run in the social-publish "
                  "environment instead.")
            sys.exit(1)
        print("=" * 68)
        print("WARNING: --force bypasses human review. Nothing has read this "
              "post.\n         Local development only — never in a workflow.")
        print("=" * 68)
    else:
        ok, reason = approval.verify_approval(PENDING, APPROVED)
        if not ok:
            print(f"[publish] BLOCKED: {reason}")
            import notify
            notify.blocked(pending.get("topic"), pending.get("format"), reason)
            sys.exit(1)

    cal = load_json(os.path.join(ROOT, "content", "calendar.json"), None)
    state = load_json(STATE, {"topic_index": 0, "run_count": 0})

    disabled = {"x": skip_x, "ig": skip_ig}
    results = {}
    for ch, (env_var, poster) in POSTERS.items():
        if disabled[ch]:
            results[ch] = {"status": "disabled", "id": None, "error": None}
            print(f"[publish] {ch} disabled by flag")
        elif not os.environ.get(env_var):
            results[ch] = {"status": "skipped", "id": None,
                           "error": f"{env_var} not set"}
            print(f"[publish] {ch} SKIPPED: {env_var} not set")
        else:
            try:
                # A poster returns a bare id, or a dict when it has more to
                # report — X threads a CTA reply that can fail on its own.
                outcome = poster(pending)
                extra = dict(outcome) if isinstance(outcome, dict) else {}
                results[ch] = {"status": "posted",
                               "id": extra.pop("id", None) or outcome,
                               "error": None, **extra}
            except Exception as e:
                results[ch] = {"status": "failed", "id": None,
                               "error": f"{type(e).__name__}: {e}"}
                print(f"[publish] {ch} FAILED: {type(e).__name__}: {e}")

    def by(status):
        return [c for c in POSTERS if results[c]["status"] == status]
    posted, failed, skipped = by("posted"), by("failed"), by("skipped")

    # A topic is only consumed once it has actually reached an audience —
    # and never by an out-of-band post, which is a correction or a one-off
    # rather than the rotation's turn.
    if posted and pending.get("out_of_band"):
        print("[publish] out-of-band post - rotation NOT advanced")
    elif posted:
        state["topic_index"] = ((state["topic_index"] + 1)
                                % max(1, len(publishable_topics(cal))))
        state["run_count"] = state.get("run_count", 0) + 1
        # Only screenshot posts consume a section, and only once one has
        # actually reached an audience — mirroring topic_index above, so an
        # unapproved or failed run does not silently burn a section.
        if pending.get("format") == "screenshot":
            state["shot_index"] = state.get("shot_index", 0) + 1
        state["last_run"] = pending["date"]
        save_json(STATE, state)
    else:
        print("[publish] nothing posted - topic NOT consumed, will retry")

    entry = dict(pending, channels=results,
                 x=results["x"]["id"], ig=results["ig"]["id"],
                 outcome=("posted" if posted and not failed else
                          "partial" if posted else "failed"))
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")
    if os.path.exists(PENDING):
        os.remove(PENDING)
    # Spend the approval with it, so it can never authorise a later post.
    approval.clear_approval(APPROVED)

    if failed or not posted:
        import notify
        notify.failure(pending["topic"], pending["format"], results)
        print(f"[publish] FAILED - posted={posted} failed={failed} "
              f"skipped={skipped}")
        sys.exit(1)
    print(f"[publish] done - posted to {', '.join(posted)}")

def pending_hash():
    """Print the hash of the post being sent for review.

    The prepare job hands this to the publish job, which is the only way
    the two can be compared: they are separate runs hours apart, and by
    then the file on the branch may be a different post entirely.
    """
    print(approval.compute_hash(PENDING))


def approve(expect=None):
    """Record human approval of the prepared post. Never automatic."""
    try:
        record = approval.write_approval(PENDING, APPROVED, expect=expect)
    except FileNotFoundError as e:
        print(f"[approve] {e}")
        sys.exit(1)
    except approval.ApprovalMismatch as e:
        print(f"[approve] REFUSED: {e}")
        sys.exit(1)
    print(f"[approve] approved {record['topic']} ({record['format']}) "
          f"by {record['approved_by']} at {record['approved_at']}")
    print(f"[approve] valid for {approval.TTL_HOURS}h, for this exact content")


def notify_pending():
    """Send the prepared post for review.

    Separate from --prepare because the card has to be committed and
    pushed before Slack can fetch it — at --prepare time the image URL
    would still 404.
    """
    pending = load_json(PENDING, None)
    if not pending:
        print("[notify] no pending.json - run --prepare first")
        sys.exit(1)
    import notify
    base = os.environ.get("MEDIA_BASE_URL")
    # Slack image blocks need an image. For a video format, point at the
    # poster still written beside the clip — an .mp4 URL gets the block
    # rejected and the whole review notification fails.
    rel = media.poster_for(pending["media_x"])
    if rel != pending["media_x"] and not os.path.exists(os.path.join(ROOT, rel)):
        rel = None  # no poster: send the review without an image, not with
                    # a block Slack will reject
    media_url = (f"{base.rstrip('/')}/{rel.lstrip('/')}"
                 if base and rel else None)
    sent = notify.pending_review(pending, media_url=media_url,
                                 review_url=os.environ.get("REVIEW_URL"))
    if sent:
        print(f"[notify] review request sent for {pending['topic']}")
    else:
        print("[notify] review request NOT sent - NOTIFY_WEBHOOK_URL unset")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prepare", action="store_true")
    ap.add_argument("--approve", action="store_true",
                    help="record human approval of the prepared post")
    ap.add_argument("--expect", metavar="SHA256",
                    help="refuse to approve unless pending.json hashes to "
                         "this. The prepare job supplies it, so approval "
                         "attaches to the post that was actually reviewed.")
    ap.add_argument("--pending-hash", action="store_true",
                    help="print the hash of pending.json and exit")
    ap.add_argument("--notify-pending", action="store_true",
                    help="send the prepared post to Slack for review")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-x", action="store_true")
    ap.add_argument("--skip-ig", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="LOCAL DEV ONLY: publish without approval")
    ap.add_argument("--format", dest="fmt", choices=FORMATS,
                    help="override the rotation's format for this run")
    ap.add_argument("--topic", dest="topic_id",
                    help="post THIS topic instead of the rotation's next. "
                         "Out-of-band: the rotation does not advance, so a "
                         "correction post cannot skip the topic that was "
                         "actually due.")
    args = ap.parse_args()

    if args.dry_run:
        p = prepare()
        print("--- X ---\n" + p["text_x"])
        print("--- IG ---\n" + p["text_ig"])
        return
    if args.prepare:
        prepare(force_format=args.fmt, force_topic=args.topic_id)
        return
    if args.pending_hash:
        pending_hash()
        return
    if args.approve:
        approve(expect=args.expect)
        return
    if args.notify_pending:
        notify_pending()
        return
    if args.publish:
        publish(args.skip_x, args.skip_ig, args.force)
        return
    # No flags: prepare only. Chaining straight into publish would make the
    # convenience path an accidental auto-approve.
    prepare()
    print("\n[run] prepared. Review content/pending.json, then:")
    print("        python engine/run.py --approve")
    print("        python engine/run.py --publish")

if __name__ == "__main__":
    main()
