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

# Posts a topic needs before its mean is allowed to promote or demote it.
#
# The mean was previously taken over as little as ONE post, and the bottom
# quarter is DROPPED — so a single weak slot removed a topic from rotation
# permanently. Permanently, because exile is self-sealing: a dropped topic
# cannot post, and the only way to earn a better mean is to post. Five of
# 23 topics had been exiled that way on exactly one observation each, which
# is what shrank the feed to 18 unique topics against a 14-day cooldown.
#
# 3 comes from the live distribution rather than taste: four topics sit at
# 1 post, one at 2, then nine/six/three at 4/5/6. The gap is between 2 and
# 4, so 3 returns every thin topic while leaving all five well-evidenced
# doublings (4-6 posts each) untouched.
MIN_POSTS_FOR_WEIGHTING = 3


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
        # Pilot rows are a LinkedIn message test. They are not evidence
        # about the product topic they were prepared beside.
        if entry.get("pilot"):
            continue
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


def topic_post_counts(posted_rows):
    """topic id -> number of posts that actually SHIPPED.

    The denominator has to be posts, not metric rows. Metrics accumulate
    over days, so one post carries several samples — which is exactly why a
    single-post topic looked well-evidenced at a glance (mpjv: 1 post, 8
    samples). A run that failed or was skipped is not evidence either way.
    """
    counts = {}
    for entry in posted_rows or []:
        if entry.get("pilot"):
            continue
        topic = entry.get("topic")
        if not topic:
            continue
        channels = entry.get("channels") or {}
        shipped = any((c or {}).get("status") == "posted"
                      for c in channels.values()) or entry.get("x")
        if shipped:
            counts[topic] = counts.get(topic, 0) + 1
    return counts


def build_rotation(topics, scores, counts=None):
    """Return the cycle to index into with state["topic_index"].

    Strong topics appear twice, weak ones are omitted, everything else
    appears once — in the calendar's original order, so the feed does not
    reshuffle wholesale when one post does well.

    `counts` (topic id -> posts shipped) gates that judgement on evidence.
    A topic below MIN_POSTS_FOR_WEIGHTING is neither dropped nor doubled:
    below the threshold the honest answer is "no opinion", and one
    appearance per cycle is what no opinion looks like. Omitting `counts`
    keeps the old behaviour, so an older caller degrades rather than breaks.
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

    # Thin data earns no verdict in EITHER direction. Dropping on one post
    # is the bug that shrank the feed; doubling on one post is the same
    # error inverted, and doubling is the half that actively causes
    # repetition — it is how a topic returns in 15 days instead of 21.
    if counts:
        thin = {t for t in ids
                if counts.get(t, 0) < MIN_POSTS_FOR_WEIGHTING}
        strong -= thin
        weak -= thin

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
