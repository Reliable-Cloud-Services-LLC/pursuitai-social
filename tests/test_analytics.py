"""W9 — analytics read-back and performance-weighted rotation.

The property that matters most is the boring one: with no metrics, which
is the state for the first several weeks after go-live, everything must
behave exactly as it did before.
"""
import datetime
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import analytics  # noqa: E402
import rotation   # noqa: E402

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def posted(date, topic, x=None, ig=None):
    return {"date": date, "topic": topic, "x": x, "ig": ig}


def metric(post_id, collected_at, **m):
    return {"post_id": post_id, "collected_at": collected_at, "metrics": m}


# ---------- collection scheduling ----------

def test_a_post_from_today_is_not_sampled():
    """Nothing has engaged with it yet; sampling now just records zeroes."""
    due = analytics.due_for_collection(
        [posted("2026-08-01", "a", x="1")], [], NOW)
    assert due == []


def test_a_day_old_post_is_due():
    due = analytics.due_for_collection(
        [posted("2026-07-31", "a", x="1")], [], NOW)
    assert due == [("x", "1", "a")]


def test_posts_past_the_window_are_dropped():
    due = analytics.due_for_collection(
        [posted("2026-06-01", "a", x="1")], [], NOW)
    assert due == []


def test_each_post_is_sampled_once_per_day():
    """Metrics accrue, so a post is resampled — but not twice in one day."""
    already = [metric("1", "2026-08-01T09:00:00+00:00", like_count=3)]
    assert analytics.due_for_collection(
        [posted("2026-07-31", "a", x="1")], already, NOW) == []
    # yesterday's sample does not satisfy today
    stale = [metric("1", "2026-07-31T09:00:00+00:00", like_count=3)]
    assert analytics.due_for_collection(
        [posted("2026-07-30", "a", x="1")], stale, NOW) == [("x", "1", "a")]


def test_both_channels_are_collected():
    due = analytics.due_for_collection(
        [posted("2026-07-31", "a", x="1", ig="ig-9")], [], NOW)
    assert set(due) == {("x", "1", "a"), ("ig", "ig-9", "a")}


def test_skipped_channels_are_not_collected():
    due = analytics.due_for_collection(
        [posted("2026-07-31", "a", x=None, ig=None)], [], NOW)
    assert due == []


# ---------- collection is failure-safe ----------

def test_a_platform_failure_does_not_raise(tmp_path, monkeypatch):
    log = tmp_path / "posted.jsonl"
    log.write_text(json.dumps(posted("2026-07-31", "a", x="1")) + "\n")

    def boom(_):
        raise RuntimeError("X is down")

    monkeypatch.setitem(analytics.FETCHERS, "x", boom)
    collected, failed = analytics.collect(
        str(log), str(tmp_path / "metrics.jsonl"), NOW)
    assert (collected, failed) == (0, 1)


def test_collected_rows_are_appended_not_overwritten(tmp_path, monkeypatch):
    log = tmp_path / "posted.jsonl"
    log.write_text(json.dumps(posted("2026-07-31", "a", x="1")) + "\n")
    metrics = tmp_path / "metrics.jsonl"
    metrics.write_text(json.dumps(metric("old", "2026-07-01T00:00:00+00:00")) + "\n")

    monkeypatch.setitem(analytics.FETCHERS, "x", lambda _: {"like_count": 7})
    analytics.collect(str(log), str(metrics), NOW)

    rows = analytics.load_jsonl(str(metrics))
    assert len(rows) == 2 and rows[0]["post_id"] == "old"
    assert rows[1]["metrics"]["like_count"] == 7
    assert rows[1]["topic"] == "a"


def test_malformed_log_lines_are_skipped(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text('{"ok": 1}\nnot json\n\n{"ok": 2}\n')
    assert analytics.load_jsonl(str(p)) == [{"ok": 1}, {"ok": 2}]


# ---------- scoring ----------

def test_score_is_an_engagement_rate_when_impressions_exist():
    score = rotation.engagement_score([
        metric("1", "2026-08-01T00:00:00+00:00",
               like_count=50, impression_count=500)])
    assert score == pytest.approx(0.1)


def test_a_widely_seen_but_ignored_post_scores_below_a_small_engaged_one():
    seen_ignored = rotation.engagement_score([
        metric("1", "2026-08-01T00:00:00+00:00",
               like_count=100, impression_count=10_000)])
    small_engaged = rotation.engagement_score([
        metric("2", "2026-08-01T00:00:00+00:00",
               like_count=50, impression_count=500)])
    assert small_engaged > seen_ignored


def test_latest_sample_wins():
    """Metrics accumulate, so the newest sample is the fullest picture."""
    score = rotation.engagement_score([
        metric("1", "2026-08-01T00:00:00+00:00", like_count=90,
               impression_count=100),
        metric("1", "2026-07-31T00:00:00+00:00", like_count=1,
               impression_count=100),
    ])
    assert score == pytest.approx(0.9)


def test_no_metrics_scores_none():
    assert rotation.engagement_score([]) is None


def test_topic_scores_average_across_a_topics_posts():
    rows = [posted("2026-07-01", "a", x="1"), posted("2026-07-08", "a", x="2")]
    metrics = [
        metric("1", "2026-08-01T00:00:00+00:00", like_count=10, impression_count=100),
        metric("2", "2026-08-01T00:00:00+00:00", like_count=30, impression_count=100),
    ]
    assert rotation.topic_scores(rows, metrics)["a"] == pytest.approx(0.2)


# ---------- rotation ----------

def topics(n):
    return [{"id": f"t{i}"} for i in range(n)]


def test_no_metrics_means_plain_round_robin():
    """The state for the first several weeks. Must not change behaviour."""
    ts = topics(16)
    assert rotation.build_rotation(ts, {}) == ts


def test_thin_metrics_stay_round_robin():
    ts = topics(16)
    scores = {"t0": 0.5, "t1": 0.1}          # below MIN_SCORED
    assert rotation.build_rotation(ts, scores) == ts


def test_strong_topics_recur_and_weak_ones_drop():
    ts = topics(16)
    scores = {f"t{i}": (16 - i) / 16 for i in range(16)}   # t0 best, t15 worst
    cycle = [t["id"] for t in rotation.build_rotation(ts, scores)]
    assert cycle.count("t0") == 2, "a strong topic should come round sooner"
    assert "t15" not in cycle, "a weak topic should drop out"
    assert cycle.count("t8") == 1, "the middle is untouched"


def test_calendar_order_is_preserved():
    """Winners recur; the feed does not reshuffle wholesale."""
    ts = topics(16)
    scores = {f"t{i}": (16 - i) / 16 for i in range(16)}
    cycle = [t["id"] for t in rotation.build_rotation(ts, scores)]
    firsts = [c for i, c in enumerate(cycle) if c not in cycle[:i]]
    assert firsts == sorted(firsts, key=lambda c: int(c[1:]))


def test_rotation_never_shrinks_below_the_floor():
    """Dropping too many topics makes the feed repetitive — its own failure."""
    ts = topics(8)
    scores = {f"t{i}": (8 - i) / 8 for i in range(8)}
    cycle = rotation.build_rotation(ts, scores)
    assert len({t["id"] for t in cycle}) >= min(len(ts), rotation.MIN_ROTATION)


def test_rotation_is_deterministic():
    ts = topics(16)
    scores = {f"t{i}": (16 - i) / 16 for i in range(16)}
    assert rotation.build_rotation(ts, scores) == rotation.build_rotation(ts, scores)


def test_unknown_topics_in_scores_are_ignored():
    """A retired topic still has metrics on file; it must not resurrect."""
    ts = topics(16)
    scores = {f"t{i}": 0.5 for i in range(16)}
    scores["retired-topic"] = 9.9
    cycle = [t["id"] for t in rotation.build_rotation(ts, scores)]
    assert "retired-topic" not in cycle
