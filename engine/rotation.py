"""Performance-weighted topic rotation.

Round-robin treats a topic that converts and a topic that bombs identically.
Once metrics exist, strong topics should come round sooner and weak ones
should drop out.

Two properties matter more than cleverness here:

  * **Deterministic.** The same metrics produce the same order every time,
    so a run is reproducible and the tests are not flaky.
  * **Degrades to round-robin.** With no metrics — which is the state for
    the first several weeks — this must behave exactly as before. A
    weighting scheme that misbehaves on thin data is worse than none.

The shape is a weighted round-robin: build one cycle in which strong
topics appear twice and weak ones are omitted, then index into it with the
existing cursor. No randomness, no starvation of the middle.
"""

# Below this many scored topics the signal is noise — stay round-robin.
MIN_SCORED = 6

# Never let the rotation shrink past this; dropping too many topics makes
# the feed repetitive, which is its own failure.
MIN_ROTATION = 8

# Fraction of scored topics treated as strong / weak.
TOP_FRACTION = 0.25
BOTTOM_FRACTION = 0.25


def engagement_score(metric_rows):
    """Collapse a post's metric samples into one number.

    Uses the LATEST sample per post (metrics accumulate over days, so the
    newest is the fullest picture), and normalises by impressions where the
    platform reports them — a post seen 10k times with 100 likes did worse
    than one seen 500 times with 50.
    """
    if not metric_rows:
        return None
    latest = max(metric_rows, key=lambda r: r.get("collected_at", ""))
    m = latest.get("metrics") or {}

    engagements = sum(float(m.get(k) or 0) for k in (
        "like_count", "reply_count", "retweet_count", "quote_count",
        "bookmark_count", "comments_count", "saved", "shares"))
    impressions = float(m.get("impression_count") or m.get("reach") or 0)
    if impressions > 0:
        return engagements / impressions
    # No denominator from this platform — fall back to raw engagements,
    # scaled down so it cannot dominate a rate-based score.
    return engagements / 1000.0


def topic_scores(posted_rows, metric_rows):
    """topic id -> mean engagement score across that topic's posts."""
    by_post = {}
    for row in metric_rows:
        by_post.setdefault(row.get("post_id"), []).append(row)

    per_topic = {}
    for entry in posted_rows:
        topic = entry.get("topic")
        if not topic:
            continue
        for channel in ("x", "ig"):
            post_id = entry.get(channel)
            if not post_id:
                continue
            score = engagement_score(by_post.get(post_id))
            if score is not None:
                per_topic.setdefault(topic, []).append(score)
    return {t: sum(v) / len(v) for t, v in per_topic.items() if v}


def build_rotation(topics, scores):
    """Return the cycle to index into with state["topic_index"].

    Strong topics appear twice, weak ones are omitted, everything else
    appears once — in the calendar's original order, so the feed does not
    reshuffle wholesale when one post does well.
    """
    ids = [t["id"] for t in topics]
    scored = {t: s for t, s in (scores or {}).items() if t in ids}

    if len(scored) < MIN_SCORED or len(topics) < MIN_ROTATION:
        return list(topics)

    ranked = sorted(scored, key=lambda t: scores[t], reverse=True)
    n_top = max(1, int(len(ranked) * TOP_FRACTION))
    n_bottom = max(1, int(len(ranked) * BOTTOM_FRACTION))
    strong = set(ranked[:n_top])
    weak = set(ranked[-n_bottom:]) - strong

    cycle = []
    for topic in topics:
        if topic["id"] in weak:
            continue
        cycle.append(topic)
        if topic["id"] in strong:
            cycle.append(topic)

    # Dropping the weak tail must not make the feed repetitive.
    if len({t["id"] for t in cycle}) < MIN_ROTATION:
        return list(topics)
    return cycle
