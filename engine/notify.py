"""Failure + heartbeat notifications via a Slack-compatible incoming webhook.

Set NOTIFY_WEBHOOK_URL to enable. Unset, every function is a silent no-op,
so local runs and tests never touch the network.

Two rules this module must never break:
  1. It never raises. A broken notifier must not become a second outage.
  2. It never prints the webhook URL. That value is a secret.

Heartbeat (run weekly from .github/workflows/heartbeat.yml):
    python engine/notify.py --heartbeat
Silence is only detectable if something speaks on a schedule, so this
reports the confirmed-post count even when it is zero.
"""
import datetime
import json
import os
import sys

import requests

import stale_channel

TIMEOUT = 15
HEARTBEAT_WINDOW_DAYS = 7


def _send(text):
    """POST to the webhook. Returns True only on a confirmed 2xx."""
    url = os.environ.get("NOTIFY_WEBHOOK_URL")
    if not url:
        return False
    try:
        r = requests.post(url, json={"text": text}, timeout=TIMEOUT)
    except Exception as e:
        # Deliberately excludes the URL — it is a secret.
        print(f"[notify] send failed: {type(e).__name__}: {e}")
        return False
    if r.status_code >= 300:
        print(f"[notify] webhook returned {r.status_code}")
        return False
    return True


# Slack rejects an oversized text object. Stay well inside it — these are
# review previews, and the authoritative copy is in pending.json.
SLACK_TEXT_LIMIT = 3000


def _section(text):
    body = text if len(text) <= SLACK_TEXT_LIMIT else text[:SLACK_TEXT_LIMIT - 1] + "…"
    return {"type": "section", "text": {"type": "mrkdwn", "text": body}}


def _send_blocks(text, blocks):
    """Slack needs `text` too: it is the notification/fallback line."""
    url = os.environ.get("NOTIFY_WEBHOOK_URL")
    if not url:
        return False
    try:
        r = requests.post(url, json={"text": text, "blocks": blocks},
                          timeout=TIMEOUT)
    except Exception as e:
        print(f"[notify] send failed: {type(e).__name__}: {e}")
        return False
    if r.status_code >= 300:
        print(f"[notify] webhook returned {r.status_code}")
        return False
    return True


# What the LinkedIn asset IS, named from its extension rather than measured.
# Measuring would mean stat-ing or opening a file this module may not have —
# notify runs after the commit step and must never raise, and a cosmetic
# label is not worth a failure mode. Named per FORMAT rather than hardcoded:
# a card's x-variant is landscape (1600x900) but AD_RATIO is "square", so a
# single "16:9" label would be wrong one run in four.
def asset_label(rel_path):
    return ("MP4 · narrated video" if (rel_path or "").lower()
            .endswith((".mp4", ".mov")) else "PNG · landscape card")


def pending_review(pending, media_url=None, review_url=None,
                   linkedin_url=None):
    """Ask a human to review the prepared post.

    There are no approve/reject buttons: interactive Slack actions need an
    app with a request URL, i.e. a server to receive the callback, and
    there isn't one. `review_url` links to the Actions run where the
    required reviewer approves instead.
    """
    topic, fmt = pending.get("topic"), pending.get("format")
    blocks = [
        _section(f"*PursuitAI social — ready for review*\n"
                 f"topic `{topic}` · format `{fmt}`"),
    ]
    if media_url:
        # The format, not a hardcoded "card". The label said card over a
        # screenshot on 2026-08-26, which is precisely when a label costs
        # something: the reviewer is being asked to check the image, and
        # the caption told them it was a kind of image it was not.
        blocks.append({"type": "image", "image_url": media_url,
                       "alt_text": f"{topic} {fmt}"})
    blocks.append(_section("*X*\n```" + (pending.get("text_x") or "") + "```"))
    blocks.append(_section("*Instagram*\n```"
                           + (pending.get("text_ig") or "") + "```"))

    # LinkedIn is posted BY HAND — the API needs w_organization_social, which
    # only the vetted Community Management product grants, and that access is
    # under appeal. Until it lands, this block is the whole handoff: the copy
    # in a fenced block (Slack gives it tap-to-copy on mobile, so the line
    # breaks and em dashes survive, which is exactly what retyping destroys)
    # and a link to the real asset.
    #
    # A LINK rather than an attachment because an incoming webhook cannot
    # upload a file. The rendered media is already public — Instagram fetches
    # it from the same bucket — so the link downloads the genuine asset.
    #
    # NB the image block above shows the POSTER STILL for a video format; an
    # .mp4 in an image block makes Slack reject the block and lose the whole
    # notification. So for an `ad` the preview is a still and this link is the
    # only route to the video.
    text_li = pending.get("text_linkedin")
    if text_li:
        blocks.append(_section("*LinkedIn — post this by hand*\n```"
                               + text_li + "```"))
        if linkedin_url:
            blocks.append(_section(
                f"<{linkedin_url}|⬇ Download the LinkedIn asset> · "
                f"{asset_label(pending.get('media_linkedin'))}"))

    if review_url:
        blocks.append(_section(f"<{review_url}|Approve or reject this run →>"))
    return _send_blocks(f"Ready for review: {topic} ({fmt})", blocks)


def blocked(topic, fmt, reason):
    """A publish was refused by the approval gate."""
    return _send(f"*PursuitAI social — publish BLOCKED*\n"
                 f"topic `{topic}` · format `{fmt}`\n{reason}")


def alert(text):
    """Send an arbitrary operational alert.

    `failure()` is shaped around a post's per-channel results, which a
    missed RUN has none of — there was no post to report on. This is the
    plain-text door for that class: something is wrong, here is what, here
    is where to look.
    """
    return _send(text)


def failure(topic, fmt, results):
    """Alert that a publish run did not fully succeed.

    results: {channel: {"status": ..., "id": ..., "error": ...}}
    """
    lines = ["*PursuitAI social — publish did not complete*",
             f"topic `{topic}` · format `{fmt}`"]
    for ch, r in sorted(results.items()):
        detail = r.get("id") or r.get("error") or ""
        lines.append(f"• {ch}: {r.get('status')} {detail}".rstrip())
    return _send("\n".join(lines))


def heartbeat_stats(log_path, today=None):
    """(confirmed posts in the last 7 days, date of the most recent one).

    What counts as a confirmed post is defined ONCE, in stale_channel, and
    imported here. The heartbeat and the staleness alarm answering that
    question differently would mean the two disagreed about whether the
    channel was alive — in the middle of an outage, which is the worst
    possible moment to be ambiguous.
    """
    today = (datetime.date.fromisoformat(today) if today
             else datetime.date.today())
    cutoff = today - datetime.timedelta(days=HEARTBEAT_WINDOW_DAYS)
    dates = stale_channel.confirmed_dates(log_path)
    count = sum(1 for d in dates if d > cutoff)
    return count, (dates[-1].isoformat() if dates else None)


def heartbeat(log_path, today=None):
    """Weekly sign of life — and of DEATH.

    The verdict used to be "OK" whenever the trailing 7-day count was
    non-zero, which is how it reported OK on 2026-09-28 while the channel
    had been dark since the 23rd: two posts landed inside the window before
    the outage started, and a trailing count cannot tell the difference
    between "posting" and "stopped five days ago". Staleness is measured
    from the LAST post, not from the window's total.
    """
    count, last = heartbeat_stats(log_path, today)
    on = (datetime.date.fromisoformat(today) if today
          else datetime.date.today())
    dark = stale_channel.dark_days(
        datetime.date.fromisoformat(last) if last else None, on)
    if last is None:
        # Nothing is stale if nothing ever posted. A fresh clone reporting
        # "STALE — 26 days dark" is technically arithmetic and practically
        # a lie about what happened.
        state = "NO POSTS"
    elif len(dark) >= stale_channel.STALE_AFTER_DAYS:
        state = f"STALE — {len(dark)} scheduled day(s) dark"
    elif count:
        state = "OK"
    else:
        state = "NO POSTS"
    return _send(f"*PursuitAI social heartbeat — {state}*\n"
                 f"{count} confirmed post(s) in the last "
                 f"{HEARTBEAT_WINDOW_DAYS} days. "
                 f"Last confirmed post: {last or 'never'}.")


if __name__ == "__main__":
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    log = os.path.join(root, "logs", "posted.jsonl")
    n, latest = heartbeat_stats(log)
    print(f"[heartbeat] {n} confirmed post(s) in the last "
          f"{HEARTBEAT_WINDOW_DAYS} days; last: {latest or 'never'}")
    if "--heartbeat" in sys.argv:
        heartbeat(log)
