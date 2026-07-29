"""The rotation must not re-serve a topic an audience just saw.

The cursor is a bare index into the publishable list, and the list GROWS as
topics get verified — 18 to 23 in one week of audit work. The index stays
put while the list shifts under it. Caught live: slot 2 pointed at
fifty-percent-rule ~36 hours after X carried exactly that card, and only
the human approval gate stood between that and a visible duplicate.
"""
import datetime
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import run  # noqa: E402


def _topics(*ids):
    return [{"id": i} for i in ids]


def _log(tmp_path, monkeypatch, entries):
    log = tmp_path / "posted.jsonl"
    log.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    monkeypatch.setattr(run, "LOG", str(log))
    return log


def _days_ago(n):
    return (datetime.date.today() - datetime.timedelta(days=n)).isoformat()


def test_the_live_incident(tmp_path, monkeypatch):
    """Slot 2 pointed at a topic X carried the previous day."""
    _log(tmp_path, monkeypatch, [
        {"date": _days_ago(1), "topic": "fifty-percent-rule",
         "channels": {"x": {"status": "posted"}}},
    ])
    topics = _topics("a", "b", "fifty-percent-rule", "d")
    pick = run._next_fresh_topic(topics, {"topic_index": 2})
    assert pick["id"] == "d"


def test_walk_wraps_past_the_end(tmp_path, monkeypatch):
    _log(tmp_path, monkeypatch, [
        {"date": _days_ago(1), "topic": "c",
         "channels": {"x": {"status": "posted"}}},
        {"date": _days_ago(2), "topic": "d",
         "channels": {"x": {"status": "posted"}}},
    ])
    topics = _topics("a", "b", "c", "d")
    pick = run._next_fresh_topic(topics, {"topic_index": 2})
    assert pick["id"] == "a"


def test_everything_recent_repeats_rather_than_starving(tmp_path, monkeypatch):
    """Repeating beats publishing nothing — and beats crashing."""
    _log(tmp_path, monkeypatch, [
        {"date": _days_ago(1), "topic": t,
         "channels": {"x": {"status": "posted"}}}
        for t in ("a", "b")
    ])
    topics = _topics("a", "b")
    pick = run._next_fresh_topic(topics, {"topic_index": 0})
    assert pick["id"] == "a"


def test_the_cooldown_expires(tmp_path, monkeypatch):
    _log(tmp_path, monkeypatch, [
        {"date": _days_ago(run.REPEAT_COOLDOWN_DAYS + 1), "topic": "c",
         "channels": {"x": {"status": "posted"}}},
    ])
    topics = _topics("a", "b", "c")
    pick = run._next_fresh_topic(topics, {"topic_index": 2})
    assert pick["id"] == "c", "a topic outside the cooldown is fair game"


def test_failed_and_skipped_posts_do_not_block(tmp_path, monkeypatch):
    """A topic that never reached an audience was not consumed — same
    principle as the rotation's own topic-not-consumed rule."""
    _log(tmp_path, monkeypatch, [
        {"date": _days_ago(1), "topic": "c",
         "channels": {"x": {"status": "failed"},
                      "ig": {"status": "skipped"}}},
    ])
    topics = _topics("a", "b", "c")
    pick = run._next_fresh_topic(topics, {"topic_index": 2})
    assert pick["id"] == "c"


def test_legacy_entries_without_channels_still_count(tmp_path, monkeypatch):
    """Entries from before the per-channel log carry a bare x id."""
    _log(tmp_path, monkeypatch, [
        {"date": _days_ago(1), "topic": "c", "x": "12345"},
    ])
    topics = _topics("a", "b", "c")
    pick = run._next_fresh_topic(topics, {"topic_index": 2})
    assert pick["id"] == "a"


def test_out_of_band_reposts_count_as_seen(tmp_path, monkeypatch):
    """A correction reached the audience too — the repost of an ad must
    put its topic on cooldown like any other post."""
    _log(tmp_path, monkeypatch, [
        {"date": _days_ago(1), "topic": "c", "out_of_band": True,
         "channels": {"x": {"status": "posted"}}},
    ])
    topics = _topics("a", "b", "c")
    pick = run._next_fresh_topic(topics, {"topic_index": 2})
    assert pick["id"] == "a"
