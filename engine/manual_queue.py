"""Cursor for hand-posted channels.

LinkedIn is pasted by a person (see docs/LINKEDIN_ACCESS.md), so it can
never confirm a post back to the engine — and the automated rotation only
advances on a confirmed post. Without a separate record you would have to
remember what you had already posted.

Deliberately independent of content/state.json. LinkedIn should not stall
because X is out of credits, and should not skip ahead because X posted.
Append-only, matching posted.jsonl: the file is a history, not a set.
"""
import datetime
import json
import os


def posted_ids(log_path):
    """Topic ids already posted, oldest first."""
    if not os.path.exists(log_path):
        return []
    out = []
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line)["topic"])
            except (ValueError, KeyError):
                continue
    return out


def next_unposted(topics, log_path):
    """The next topic to post by hand, or None if there are none at all.

    Calendar order until everything has been posted once, then the least
    recently posted — so a second cycle does not simply restart at the top
    and re-run the same few.
    """
    if not topics:
        return None
    history = posted_ids(log_path)
    seen = set(history)

    for topic in topics:
        if topic["id"] not in seen:
            return topic

    # Everything posted at least once: oldest first. A topic in the history
    # that is no longer in the calendar is ignored rather than fatal.
    by_id = {t["id"]: t for t in topics}
    for topic_id in history:
        if topic_id in by_id:
            return by_id[topic_id]
    return topics[0]


def mark_posted(topic_id, log_path, now=None):
    """Record that a topic was posted by hand."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
    with open(log_path, "a") as f:
        f.write(json.dumps({"topic": topic_id,
                            "posted_at": now.isoformat(),
                            "channel": "linkedin"}) + "\n")
