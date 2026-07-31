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


def pending_review(pending, media_url=None, review_url=None):
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
        blocks.append({"type": "image", "image_url": media_url,
                       "alt_text": f"{topic} card"})
    blocks.append(_section("*X*\n```" + (pending.get("text_x") or "") + "```"))
    blocks.append(_section("*Instagram*\n```"
                           + (pending.get("text_ig") or "") + "```"))
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

    Counts a run only when a channel actually reported "posted" — a run
    that logged nothing but failures is not a sign of life.
    """
    if not os.path.exists(log_path):
        return 0, None
    today = (datetime.date.fromisoformat(today) if today
             else datetime.date.today())
    cutoff = today - datetime.timedelta(days=HEARTBEAT_WINDOW_DAYS)
    count, last = 0, None
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                when = datetime.date.fromisoformat(entry["date"])
            except (ValueError, KeyError):
                continue
            channels = entry.get("channels") or {}
            if not any(c.get("status") == "posted" for c in channels.values()):
                continue
            if last is None or when > datetime.date.fromisoformat(last):
                last = entry["date"]
            if when > cutoff:
                count += 1
    return count, last


def heartbeat(log_path, today=None):
    count, last = heartbeat_stats(log_path, today)
    state = "OK" if count else "NO POSTS"
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
