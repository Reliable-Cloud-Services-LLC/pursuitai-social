"""Message-test pilot.

Off unless PILOT_MODE=1 or content/pilot.json says "enabled": true.
PILOT_MODE=0 forces it off even when the file is enabled. Any other value
(including unset and "true") leaves the file in charge, and the file ships
with enabled false.

While it is on, each run publishes the next angle to LinkedIn, X, and
Instagram, after the same social-publish approval as every other post.
The product topic cursor does not move: this is a pause for a message
test, not a turn of the normal feed.

Each channel can be held on its own. PILOT_X, PILOT_INSTAGRAM, and
PILOT_LINKEDIN use the same "1" / "0" contract as PILOT_MODE. Unset
defers to content/pilot.json "channels", which ships all true.

The angle id is the experiment's unit. It is written on the post log and
it is utm_content, so a click and a post can be tied back to the message
that produced them. utm_source is the channel: linkedin, x, or instagram.
"""
import datetime
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CONFIG = os.path.join(ROOT, "content", "pilot.json")

# Statuses that mean "this angle was issued." A draft reached nobody and
# must not burn the message — the same rule the topic cursor uses.
SHIPPED = frozenset({"posted", "handoff"})

# What X reports as engagement. reply_count is in here on purpose, and the
# caller subtracts the account's own CTA reply before trusting it.
ENGAGEMENT_KEYS = (
    "like_count", "retweet_count", "quote_count", "bookmark_count",
    "comments_count", "saved", "shares",
)
IMPRESSION_KEYS = ("impression_count", "impressions", "reach")
CLICK_KEYS = ("click_count", "clicks", "url_link_clicks")

WINDOW_DAYS = 7


def load(path=None):
    path = path or CONFIG
    with open(path) as f:
        return json.load(f)


def active(path=None):
    """True only when the pilot is deliberately on.

    "1" and "0" are the whole env contract, matching LINKEDIN_DRAFT and
    LINKEDIN_ALLOW_MEMBER_POST. "true" and "yes" do not count: a flag that
    accepts several spellings is a flag someone will set to the spelling
    the code does not check, and the run will look like it obeyed.
    """
    env = os.environ.get("PILOT_MODE")
    if env == "1":
        return True
    if env == "0":
        return False
    try:
        return bool(load(path).get("enabled"))
    except (OSError, ValueError):
        return False


def angles(path=None):
    """The angle list, in rotation order. Ids must be unique."""
    rows = list(load(path).get("angles") or [])
    ids = [a.get("id") for a in rows]
    if any(not i for i in ids):
        raise ValueError("every pilot angle needs an id")
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate pilot angle ids: {ids}")
    return rows


# The 21-day test's UTM campaign. content/pilot.json carries the same value;
# this is the fallback when the file omits it, so a missing key cannot
# silently publish a different campaign.
CAMPAIGN = "pilot21"


def campaign(path=None):
    try:
        return load(path).get("campaign") or CAMPAIGN
    except (OSError, ValueError):
        return CAMPAIGN


# Engine channel -> the env var that overrides content/pilot.json.
# utm_source for Instagram is the word "instagram", not the engine's "ig"
# key. The dashboard groups on that string, and "ig" would be a second
# name for the same channel.
CHANNEL_ENV = {
    "x": "PILOT_X",
    "ig": "PILOT_INSTAGRAM",
    "linkedin": "PILOT_LINKEDIN",
}
UTM_SOURCE = {
    "x": "x",
    "ig": "instagram",
    "linkedin": "linkedin",
}
# Keys a person edits. "instagram" is the spelling in the file; "ig" is
# accepted so the engine's own name holds the channel too.
CHANNEL_JSON = {
    "x": ("x",),
    "ig": ("instagram", "ig"),
    "linkedin": ("linkedin",),
}

# Present before a pilot publish is attempted. A missing name skips that
# channel and is printed. The run must not look like it posted somewhere
# it could not authenticate.
REQUIRED_SECRETS = {
    "x": ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET"),
    "ig": ("IG_USER_ID", "IG_ACCESS_TOKEN", "MEDIA_BASE_URL"),
}


def channel_on(name, path=None):
    """Whether this pilot channel should publish.

    "1" and "0" are the whole env contract, matching PILOT_MODE. Any other
    value, including unset, defers to content/pilot.json. A missing key
    publishes the channel: the test's default is all three.
    """
    if name not in CHANNEL_ENV:
        raise ValueError(f"unknown pilot channel {name!r}")
    env = os.environ.get(CHANNEL_ENV[name])
    if env == "1":
        return True
    if env == "0":
        return False
    try:
        channels = load(path).get("channels") or {}
    except (OSError, ValueError):
        return True
    for key in CHANNEL_JSON[name]:
        if key in channels:
            return bool(channels[key])
    return True


def enabled_channels(path=None):
    """Public names, for the log and the review."""
    return {
        "x": channel_on("x", path),
        "instagram": channel_on("ig", path),
        "linkedin": channel_on("linkedin", path),
    }


def missing_secrets(name):
    """Secret names this channel still needs, in listed order."""
    return [var for var in REQUIRED_SECRETS.get(name, ())
            if not os.environ.get(var)]


def utm_for(angle, path=None, *, source="linkedin"):
    """The four UTM params for one channel's link, and for the post log.

    utm_content is the angle id. The weekly report groups by that same id,
    so a click, a row in posted.jsonl, and a line in the winner report are
    the same message. utm_source is the channel the click came from.
    `source` defaults to linkedin so existing callers keep that quartet.
    """
    import links
    return {
        "utm_source": source,
        "utm_medium": links.MEDIUM,
        "utm_campaign": campaign(path),
        "utm_content": angle["id"],
    }


def utm_channels(angle, path=None):
    """One quartet per engine channel. The ig entry's source is instagram."""
    return {ch: utm_for(angle, path, source=src)
            for ch, src in UTM_SOURCE.items()}


def _issued(row):
    """True when this angle reached someone, or was handed to the founder.

    Any channel counts. A LinkedIn-only row from before X and Instagram
    joined the test still counts, and a row whose only delivery was X
    counts too. Holding LinkedIn would otherwise repeat the same angle.
    """
    for payload in (row.get("channels") or {}).values():
        if isinstance(payload, dict) and payload.get("status") in SHIPPED:
            return True
    return False


def shipped_count(rows):
    """How many pilot angles have already been issued.

    Indexed by this count, the same way a topic's creative angle is indexed
    by that topic's own post count: the counter advances only when an angle
    ships, so it shares no period with the product cursor.
    """
    n = 0
    for row in rows or []:
        if row.get("angle_id") and _issued(row):
            n += 1
    return n


def pick(angle_rows, count):
    """The angle for the next post. Empty input is the caller's problem."""
    if not angle_rows:
        return None
    return angle_rows[count % len(angle_rows)]


def next_angle(log_rows, path=None):
    rows = angles(path)
    return pick(rows, shipped_count(log_rows))


def card_topic(angle):
    """The dict cards.render_card reads. Not a calendar topic."""
    return {
        "id": f"pilot-{angle['id']}",
        "feature": angle.get("feature") or angle["id"],
        "headline": angle.get("headline") or "",
        "body": angle.get("body") or "",
        "stat": angle.get("stat") or "",
    }


def _hashtags(brand):
    primary = (brand.get("hashtags_ig") or ["#GovCon"])[0]
    extras = [t for t in ("#8aCertified", "#SAMgov") if t != primary]
    return " ".join([primary, *extras[:2]])


def _tagged_url(angle, brand, topic_id, source, path=None):
    import links
    utm = utm_for(angle, path, source=source)
    url = links.build_url(
        brand["url"], utm["utm_source"], topic_id, "card",
        content=utm["utm_content"], campaign=utm["utm_campaign"])
    return url, utm


def linkedin_text(angle, brand, topic_id, path=None):
    """The LinkedIn caption for one angle.

    The link is the marketing site (brand url, https://pursuitai.net/), not
    a per-angle landing page — the calendar does not map these messages to
    one. Tags come from utm_for so the caption and the log cannot diverge.
    """
    url, _utm = _tagged_url(angle, brand, topic_id, "linkedin", path)
    return (f"{(angle.get('post') or '').strip()}\n\n"
            f"Start a free 14-day trial — no credit card, set up in under "
            f"two minutes:\n{url}\n\n{_hashtags(brand)}")


# X weighs every URL as 23 characters. The hook gives when the sum would
# pass 280. The URL is never cut.
X_LIMIT = 280


def _fit_x(hook, suffix, url):
    import links
    hook = " ".join((hook or "").split())
    if hook and links.x_weighted_length(f"{hook}{suffix}", [url]) <= X_LIMIT:
        return f"{hook}{suffix}"
    overhead = links.x_weighted_length(suffix, [url])
    budget = X_LIMIT - overhead - 1
    if budget < 1 or not hook:
        return suffix.lstrip("\n")
    cut = hook[:budget].rsplit(" ", 1)[0].rstrip(".,; ")
    if not cut:
        cut = hook[:budget].rstrip()
    text = f"{cut}…{suffix}"
    while cut and links.x_weighted_length(text, [url]) > X_LIMIT:
        cut = cut.rsplit(" ", 1)[0] if " " in cut else ""
        text = f"{cut}…{suffix}" if cut else suffix.lstrip("\n")
    return text


def x_text(angle, brand, topic_id, path=None):
    """The X post. The link is in the body, and the weighted length fits.

    Product posts keep the link in a reply. This test needs the click on
    the post itself, attributed to the angle, so the tagged URL is the
    last line and there is no second reply.
    """
    url, _utm = _tagged_url(angle, brand, topic_id, "x", path)
    hook = (angle.get("hook_x") or angle.get("post") or "").strip()
    suffix = "\n\nFree 14-day trial — no credit card:\n" + url
    return _fit_x(hook, suffix, url)


# Instagram captions cap at 2,200 characters.
IG_LIMIT = 2200


def ig_text(angle, brand, topic_id, path=None):
    """Instagram caption. The link is copy-paste, because captions do not link.

    "Link in bio" points at the standing profile link. The URL under it is
    this post, with this angle's utm_content, so a copied link still
    attributes the click.
    """
    url, _utm = _tagged_url(angle, brand, topic_id, "instagram", path)
    body = (angle.get("post") or "").strip()
    cta = (
        "Start your free 14-day trial — link in bio, or copy this link "
        "(captions are not tappable):\n"
        f"{url}\n"
        "No credit card. Set up in under 2 minutes."
    )
    text = f"{body}\n\n{cta}\n\n{_hashtags(brand)}"
    if len(text) <= IG_LIMIT:
        return text
    keep = IG_LIMIT - (len(text) - len(body)) - 1
    trimmed = body[:max(keep, 0)].rsplit(" ", 1)[0].rstrip(".,;")
    if trimmed and len(trimmed) < len(body):
        trimmed += "…"
    return f"{trimmed}\n\n{cta}\n\n{_hashtags(brand)}"


def render_assets(angle, brand, root=None):
    """Landscape for X and LinkedIn, portrait JPEG for Instagram.

    Instagram's publishing reference accepts JPEG only. A missing portrait
    fails prepare. Publish then refuses an Instagram post that has no graphic.
    """
    import cards
    import media
    root = root or ROOT
    topic = card_topic(angle)
    landscape = os.path.join("assets", "cards", f"pilot-{angle['id']}_x.png")
    portrait = os.path.join("assets", "cards", f"pilot-{angle['id']}_ig.png")
    cards.render_card(topic, brand, size=(1600, 900),
                      out_path=os.path.join(root, landscape))
    cards.render_card(topic, brand, size=(1080, 1350),
                      out_path=os.path.join(root, portrait))
    jpeg = media.as_jpeg(os.path.join(root, portrait))

    def rel(path):
        return os.path.relpath(path, root).replace(os.sep, "/")

    return {
        "x": rel(os.path.join(root, landscape)),
        "ig": rel(jpeg),
        "linkedin": rel(os.path.join(root, landscape)),
    }


def render_asset(angle, brand, root=None):
    """The landscape card. LinkedIn and X both use it."""
    return render_assets(angle, brand, root)["x"]


def apply_pending(pending, angle, topic, brand, root=None):
    """Stamp the angle onto a prepared post.

    Replaces the product captions. The X reply is cleared: a product reply
    would thread a different campaign under this post, and the pilot link
    is already in the body.
    """
    pending["pilot"] = True
    pending["angle_id"] = angle["id"]
    channels = utm_channels(angle)
    # The flat dict is the LinkedIn quartet. Rows written before X and
    # Instagram joined the test have this shape, and the handoff message
    # reads it. utm_channels is the same tags per channel, which is what
    # each caption was built from.
    pending["utm"] = channels["linkedin"]
    pending["utm_channels"] = channels
    pending["pilot_channels"] = enabled_channels()
    pending["text_linkedin"] = linkedin_text(angle, brand, topic["id"])
    pending["text_x"] = x_text(angle, brand, topic["id"])
    pending["text_ig"] = ig_text(angle, brand, topic["id"])
    pending["text_x_reply"] = None
    assets = render_assets(angle, brand, root)
    pending["media_x"] = assets["x"]
    pending["media_linkedin"] = assets["linkedin"]
    pending["media_ig"] = assets["ig"]
    pending["cover_ig"] = None
    return pending


def self_reply_count(entry):
    """How many of this post's replies are the account's own CTA.

    X threads a link under the body. That reply is the bot talking to
    itself, and public_metrics.reply_count includes it — which is why a
    post nobody answered still shows reply_count 1.

    Channel records win. The top-level reply_id is only the legacy place
    the same fact lived; counting both would subtract the reply twice.
    """
    n = 0
    for ch in (entry.get("channels") or {}).values():
        if isinstance(ch, dict) and ch.get("reply_id"):
            n += 1
    if n:
        return n
    return 1 if entry.get("reply_id") else 0


def reply_ids(rows):
    """Post ids that ARE the bot's own replies, so they are not posts."""
    found = set()
    for entry in rows or []:
        for ch in (entry.get("channels") or {}).values():
            if isinstance(ch, dict) and ch.get("reply_id"):
                found.add(str(ch["reply_id"]))
        if entry.get("reply_id"):
            found.add(str(entry["reply_id"]))
    return found


def engagement_total(metrics, self_replies=0):
    """Audience engagement. The account's own replies are removed first."""
    m = metrics or {}
    replies = float(m.get("reply_count") or 0)
    if self_replies:
        replies = max(0.0, replies - float(self_replies))
    total = replies
    for key in ENGAGEMENT_KEYS:
        total += float(m.get(key) or 0)
    return total


def _first(metrics, keys):
    m = metrics or {}
    for key in keys:
        if key in m and m[key] is not None:
            return float(m[key])
    return None


def _latest_by_post(metric_rows):
    best = {}
    for row in metric_rows or []:
        key = (row.get("channel"), str(row.get("post_id")))
        prev = best.get(key)
        if prev is None or (row.get("collected_at") or "") >= (prev.get("collected_at") or ""):
            best[key] = row
    return best


def _channel_ids(entry):
    """(channel, post_id) for the posts this entry actually published."""
    out = []
    seen = set()
    channels = entry.get("channels") or {}
    for channel, payload in channels.items():
        if not isinstance(payload, dict):
            continue
        post_id = payload.get("id") or entry.get(channel)
        if not post_id:
            continue
        key = (channel, str(post_id))
        if key not in seen:
            seen.add(key)
            out.append(key)
    for channel in ("linkedin", "x", "ig"):
        post_id = entry.get(channel)
        key = (channel, str(post_id)) if post_id else None
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def _blank_stats():
    return {"posts": 0, "impressions": None, "engagement": 0.0,
            "clicks": None, "rate": None, "measured": False}


def _fold(stats, entry, latest, own_replies):
    """Add one post's latest samples into a stats bucket."""
    stats["posts"] += 1
    impressions = []
    clicks = []
    engagement = 0.0
    measured = False
    # Subtract the self-reply from the sample that actually reports
    # reply_count. Zeroing the budget on the first sample would drop it
    # on a LinkedIn row (no reply_count) and then count the X reply in full.
    remaining = self_reply_count(entry)
    for channel, post_id in _channel_ids(entry):
        if post_id in own_replies:
            continue
        row = latest.get((channel, post_id))
        if not row:
            continue
        m = row.get("metrics") or {}
        measured = True
        replies = float(m.get("reply_count") or 0)
        used = min(remaining, replies) if remaining and "reply_count" in m else 0
        engagement += engagement_total(m, used)
        remaining -= used
        imp = _first(m, IMPRESSION_KEYS)
        if imp is not None:
            impressions.append(imp)
        clk = _first(m, CLICK_KEYS)
        if clk is not None:
            clicks.append(clk)
    stats["engagement"] += engagement
    if impressions:
        stats["impressions"] = (stats["impressions"] or 0) + sum(impressions)
    if clicks:
        stats["clicks"] = (stats["clicks"] or 0) + sum(clicks)
    if measured:
        stats["measured"] = True
    if stats["impressions"]:
        stats["rate"] = stats["engagement"] / stats["impressions"]
    return stats


def _in_window(entry, today, window_days):
    try:
        posted_on = datetime.date.fromisoformat(entry.get("date") or "")
    except ValueError:
        return False
    cutoff = today - datetime.timedelta(days=window_days)
    return posted_on > cutoff and posted_on <= today


def summarize(posted_rows, metric_rows, angle_rows, today=None,
              window_days=WINDOW_DAYS):
    """Per-angle totals for the trailing window and for the whole test.

    `today` is a date. Posts with no angle_id are not part of the test.
    Metric rows whose post id is a recorded self-reply are not a post.
    """
    today = today or datetime.date.today()
    if isinstance(today, str):
        today = datetime.date.fromisoformat(today)
    latest = _latest_by_post(metric_rows)
    own_replies = reply_ids(posted_rows)
    order = []
    titles = {}
    for angle in angle_rows or []:
        order.append(angle["id"])
        titles[angle["id"]] = angle.get("feature") or angle["id"]

    buckets = {}

    def bucket(angle_id):
        if angle_id not in buckets:
            if angle_id not in order:
                order.append(angle_id)
            buckets[angle_id] = {
                "id": angle_id,
                "title": titles.get(angle_id) or angle_id,
                "week": _blank_stats(),
                "total": _blank_stats(),
            }
        return buckets[angle_id]

    for entry in posted_rows or []:
        angle_id = entry.get("angle_id")
        if not angle_id:
            continue
        status = ((entry.get("channels") or {}).get("linkedin") or {}).get("status")
        # Issued angles only. A failed prepare that still logged a row
        # without a shipped LinkedIn status is not a sample.
        if status not in SHIPPED and not _channel_ids(entry):
            continue
        slot = bucket(angle_id)
        _fold(slot["total"], entry, latest, own_replies)
        if _in_window(entry, today, window_days):
            _fold(slot["week"], entry, latest, own_replies)

    rows = [buckets[i] for i in order if i in buckets]
    return {
        "today": today.isoformat(),
        "window_days": window_days,
        "angles": rows,
        "campaign": campaign(),
        "winner": _winner(rows, "week"),
        "running": _winner(rows, "total"),
    }


def _rank_key(stats):
    """Higher is better. Unmeasured angles sort last, not as a fake zero."""
    rate = stats.get("rate")
    clicks = stats.get("clicks")
    return (
        rate is not None,
        rate or 0.0,
        clicks is not None,
        clicks or 0.0,
        stats.get("engagement") or 0.0,
        stats.get("impressions") or 0.0,
    )


def _winner(rows, scope):
    """The best measured angle in `scope`, or None when nothing is measured."""
    measured = [r for r in rows if r[scope]["posts"] and r[scope]["measured"]]
    if not measured:
        return None
    best = max(measured, key=lambda r: _rank_key(r[scope]))
    # A measured zero still counts: "we saw it and nobody engaged" is data.
    # An angle whose samples were empty dicts has measured True only when a
    # row existed; engagement_total of an empty sample is 0 and measured is
    # True, which is correct (the platform answered).
    return best["id"]


def _fmt_num(value):
    if value is None:
        return "n/a"
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.1f}"


def _fmt_rate(rate):
    if rate is None:
        return "n/a"
    return f"{rate * 100:.1f}%"


def _fmt_stats(stats):
    if not stats["posts"]:
        return "no posts"
    if not stats["measured"]:
        return f"{stats['posts']} post(s) · metrics not back"
    return (f"{stats['posts']} post(s) · "
            f"{_fmt_num(stats['impressions'])} impressions · "
            f"{_fmt_num(stats['engagement'])} engagement "
            f"({_fmt_rate(stats['rate'])}) · "
            f"{_fmt_num(stats['clicks'])} clicks")


def render_report(summary):
    """The Slack/text body. Names the winner in the first lines."""
    winner = summary.get("winner")
    running = summary.get("running")
    lines = [
        "*PursuitAI pilot — weekly winner*",
        f"Week ending {summary['today']}. "
        f"utm_medium=organic · "
        f"utm_campaign `{summary.get('campaign') or CAMPAIGN}` · "
        f"utm_content is the angle id. "
        f"utm_source is the channel the click came from "
        f"(linkedin, x, or instagram). "
        f"Engagement excludes this account's own replies.",
        "",
        ("Winner this week: none yet — metrics are not back."
         if not winner else f"Winner this week: `{winner}`"),
        ("Running leader: none yet."
         if not running else f"Running leader: `{running}`"),
        "",
    ]
    if not summary.get("angles"):
        lines.append("No pilot posts in the log.")
        return "\n".join(lines)
    for row in summary["angles"]:
        mark = " — WINNER" if row["id"] == winner else ""
        lines.append(f"`{row['id']}` {row['title']}{mark} · "
                     f"utm_content={row['id']}")
        lines.append(f"  this week: {_fmt_stats(row['week'])}")
        lines.append(f"  to date: {_fmt_stats(row['total'])}")
    if winner:
        lines.append("")
        lines.append(f"Boost `{winner}`. The other angles stay in the log "
                     f"for the next weekly read.")
    return "\n".join(lines)


def deliver_report(log_path, metrics_path, today=None, path=None):
    """Send the winner report. Prints it either way. Never raises.

    No angled posts means the pilot has not started (or is off and never
    ran). Stay quiet then — a weekly "nothing" from a mode that is off is
    how alerts get muted.
    """
    import analytics
    try:
        posted = analytics.load_jsonl(log_path)
        metrics = analytics.load_jsonl(metrics_path)
        if not any(r.get("angle_id") for r in posted):
            print("[pilot] no angled posts — winner report not sent")
            return False
        try:
            catalog = angles(path)
        except (OSError, ValueError):
            catalog = []
        summary = summarize(posted, metrics, catalog, today=today)
        text = render_report(summary)
        print(text)
        import notify
        return bool(notify.pilot_report(text))
    except Exception as e:
        print(f"[pilot] winner report failed: {type(e).__name__}: {e}")
        return False


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Message-test pilot")
    ap.add_argument("--report", action="store_true",
                    help="send the weekly winner report via notify")
    args = ap.parse_args()
    if args.report:
        deliver_report(os.path.join(ROOT, "logs", "posted.jsonl"),
                       os.path.join(ROOT, "logs", "metrics.jsonl"))
    else:
        state = "ON" if active() else "OFF"
        print(f"[pilot] {state}")
        if active():
            for name, on in enabled_channels().items():
                print(f"  channel {name}: {'on' if on else 'off'}")
        try:
            for index, angle in enumerate(angles()):
                print(f"  {index} {angle['id']} — {angle.get('feature')}")
        except (OSError, ValueError) as e:
            print(f"[pilot] config unreadable: {e}")
