"""Daily orchestrator - the autonomous loop.

Two phases (so Instagram can fetch media from committed public URLs):

  python engine/run.py --prepare   # pick topic, build assets + captions,
                                   # write content/pending.json (no posting)
  python engine/run.py --publish   # read pending.json, post to X + IG,
                                   # advance state, append logs/posted.jsonl

  python engine/run.py             # both phases in one go (local use)
  python engine/run.py --dry-run   # prepare only, print captions

Rotation: topics round-robin through content/calendar.json; formats cycle
card -> screenshot -> card -> video so the feed never looks templated.
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
import cards
import captions

STATE = os.path.join(ROOT, "content", "state.json")
PENDING = os.path.join(ROOT, "content", "pending.json")
APPROVED = os.path.join(ROOT, "content", "approved.json")
LOG = os.path.join(ROOT, "logs", "posted.jsonl")
FORMATS = ["card", "screenshot", "card", "video"]

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
    FORMATS[run_count % 4] over 24 topics gives gcd(24, 4) = 4 and every
    topic renders in the same format forever (fit-scoring was always a
    card, subaward-intel always a video). Offsetting by the completed-cycle
    count shifts the phase each time round, so each topic works through all
    four formats over four cycles.
    """
    cycle = run_count // topic_count
    return FORMATS[(run_count + cycle) % len(FORMATS)]

def prepare():
    cal = load_json(os.path.join(ROOT, "content", "calendar.json"), None)
    brand, topics = cal["brand"], cal["topics"]
    state = load_json(STATE, {"topic_index": 0, "run_count": 0})
    topic = topics[state["topic_index"] % len(topics)]
    fmt = select_format(state["run_count"], len(topics))
    today = datetime.date.today().isoformat()
    print(f"[prepare] {today} topic={topic['id']} format={fmt}")

    shot_x = shot_ig = None
    if fmt in ("screenshot", "video"):
        try:
            import screenshots
            screenshots.capture_all()
        except Exception as e:
            print(f"[prepare] screenshot refresh failed ({e})")
        sdir = os.path.join(ROOT, "assets", "screenshots")
        for c in ("hero", "features", "pricing", "why"):
            xp, ip = (os.path.join(sdir, f"{c}_x.png"),
                      os.path.join(sdir, f"{c}_ig.png"))
            if os.path.exists(xp) and os.path.exists(ip):
                shot_x, shot_ig = xp, ip
                break

    card_x = os.path.join(ROOT, "assets", "cards", f"{topic['id']}_x.png")
    card_ig = os.path.join(ROOT, "assets", "cards", f"{topic['id']}_ig.png")
    cards.render_card(topic, brand, (1600, 900), card_x)
    cards.render_card(topic, brand, (1080, 1350), card_ig)

    video_path = None
    if fmt == "video":
        try:
            import video
            video_path = os.path.join(ROOT, "assets", "video",
                                      f"{topic['id']}.mp4")
            video.make_video(topic, video_path, screenshot=shot_x)
        except Exception as e:
            print(f"[prepare] video build failed ({e}); using card")
            video_path, fmt = None, "card"

    if fmt == "screenshot" and shot_x:
        media_x, media_ig = shot_x, shot_ig
    elif fmt == "video" and video_path:
        media_x, media_ig = video_path, video_path
    else:
        fmt = "card"
        media_x, media_ig = card_x, card_ig

    pending = {
        "date": today, "topic": topic["id"], "format": fmt,
        "text_x": captions.build_x(topic, brand, fmt=fmt),
        "text_ig": captions.build_ig(topic, brand),
        "media_x": os.path.relpath(media_x, ROOT),
        "media_ig": os.path.relpath(media_ig, ROOT),
    }
    save_json(PENDING, pending)
    print("[prepare] wrote content/pending.json")
    return pending

def _post_x(pending):
    import post_x
    return str(post_x.post(pending["text_x"],
                           os.path.join(ROOT, pending["media_x"])))

def _post_ig(pending):
    import post_ig
    if pending["media_ig"].endswith(".mp4"):
        return post_ig.post_reel(pending["media_ig"], pending["text_ig"])
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
                results[ch] = {"status": "posted", "id": poster(pending),
                               "error": None}
            except Exception as e:
                results[ch] = {"status": "failed", "id": None,
                               "error": f"{type(e).__name__}: {e}"}
                print(f"[publish] {ch} FAILED: {type(e).__name__}: {e}")

    def by(status):
        return [c for c in POSTERS if results[c]["status"] == status]
    posted, failed, skipped = by("posted"), by("failed"), by("skipped")

    # A topic is only consumed once it has actually reached an audience.
    if posted:
        state["topic_index"] = (state["topic_index"] + 1) % len(cal["topics"])
        state["run_count"] = state.get("run_count", 0) + 1
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

def approve():
    """Record human approval of the prepared post. Never automatic."""
    try:
        record = approval.write_approval(PENDING, APPROVED)
    except FileNotFoundError as e:
        print(f"[approve] {e}")
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
    media_url = (f"{base.rstrip('/')}/{pending['media_x'].lstrip('/')}"
                 if base else None)
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
    ap.add_argument("--notify-pending", action="store_true",
                    help="send the prepared post to Slack for review")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-x", action="store_true")
    ap.add_argument("--skip-ig", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="LOCAL DEV ONLY: publish without approval")
    args = ap.parse_args()

    if args.dry_run:
        p = prepare()
        print("--- X ---\n" + p["text_x"])
        print("--- IG ---\n" + p["text_ig"])
        return
    if args.prepare:
        prepare()
        return
    if args.approve:
        approve()
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
