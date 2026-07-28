"""LinkedIn is posted by hand, so it needs its own cursor.

The automated rotation advances only when a channel confirms a post, and a
manual paste can never confirm anything. Without a separate record you
would have to remember which topics you had already posted — which means
you would repeat some and never post others.

Deliberately independent of state.json: LinkedIn should not stall because
X is out of credits, and it should not skip ahead because X posted.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import manual_queue  # noqa: E402


def topics(*ids):
    return [{"id": i} for i in ids]


def test_first_call_returns_the_first_topic(tmp_path):
    log = str(tmp_path / "q.jsonl")
    assert manual_queue.next_unposted(topics("a", "b", "c"), log)["id"] == "a"


def test_a_marked_topic_is_skipped(tmp_path):
    log = str(tmp_path / "q.jsonl")
    manual_queue.mark_posted("a", log)
    assert manual_queue.next_unposted(topics("a", "b", "c"), log)["id"] == "b"


def test_order_follows_the_calendar(tmp_path):
    log = str(tmp_path / "q.jsonl")
    seen = []
    for _ in range(3):
        t = manual_queue.next_unposted(topics("a", "b", "c"), log)
        seen.append(t["id"])
        manual_queue.mark_posted(t["id"], log)
    assert seen == ["a", "b", "c"]


def test_it_wraps_once_everything_is_posted(tmp_path):
    """A full cycle should start again rather than returning nothing."""
    log = str(tmp_path / "q.jsonl")
    for i in ("a", "b", "c"):
        manual_queue.mark_posted(i, log)
    assert manual_queue.next_unposted(topics("a", "b", "c"), log)["id"] == "a"


def test_wrapping_prefers_the_least_recently_posted(tmp_path):
    log = str(tmp_path / "q.jsonl")
    for i in ("b", "a", "c"):
        manual_queue.mark_posted(i, log)
    # b is oldest, so it comes round first on the second cycle
    assert manual_queue.next_unposted(topics("a", "b", "c"), log)["id"] == "b"


def test_marking_is_append_only(tmp_path):
    log = str(tmp_path / "q.jsonl")
    manual_queue.mark_posted("a", log)
    manual_queue.mark_posted("a", log)
    rows = [json.loads(x) for x in open(log) if x.strip()]
    assert len(rows) == 2, "history is a record, not a set"
    assert all(r["topic"] == "a" and r["posted_at"] for r in rows)


def test_a_missing_log_is_not_an_error(tmp_path):
    assert manual_queue.posted_ids(str(tmp_path / "nope.jsonl")) == []


def test_a_retired_topic_in_the_log_is_ignored(tmp_path):
    """A topic removed from the calendar must not break the cursor."""
    log = str(tmp_path / "q.jsonl")
    manual_queue.mark_posted("retired", log)
    assert manual_queue.next_unposted(topics("a", "b"), log)["id"] == "a"


def test_no_topics_returns_none(tmp_path):
    assert manual_queue.next_unposted([], str(tmp_path / "q.jsonl")) is None
