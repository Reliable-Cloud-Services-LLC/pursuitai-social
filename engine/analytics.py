"""Read post performance back from the platforms.

Closes the loop: logs/posted.jsonl records what went out and with which
post ids; this fetches how those posts did and appends to
logs/metrics.jsonl, which rotation.py then weights by.

Design constraints that shaped this:

  * **Metrics accrue over days**, so a post is sampled repeatedly — once
    it is at least MIN_AGE_HOURS old, then daily until MAX_AGE_DAYS. Each
    sample is a new append-only row; nothing is overwritten.
  * **X reads cost money** under pay-per-use, so collection runs weekly on
    its own schedule, never on the publish path.
  * **Never raises.** A metrics failure must not affect publishing, which
    is why this is a separate entry point with no caller in run.py.
  * **Metric names churn.** Meta has repeatedly renamed and deprecated
    Instagram insight metrics, so the basic like/comment counts come from
    the stable fields endpoint and richer insights are best-effort.
"""
import datetime
import json
import os

MIN_AGE_HOURS = 24
MAX_AGE_DAYS = 30
GRAPH = "https://graph.facebook.com/v21.0"


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def fetch_x_metrics(post_id):
    """docs.x.com — GET /2/tweets/{id}?tweet.fields=public_metrics."""
    import tweepy
    client = tweepy.Client(
        consumer_key=os.environ["X_API_KEY"],
        consumer_secret=os.environ["X_API_SECRET"],
        access_token=os.environ["X_ACCESS_TOKEN"],
        access_token_secret=os.environ["X_ACCESS_SECRET"])
    resp = client.get_tweet(post_id, tweet_fields=["public_metrics"],
                            user_auth=True)
    if not resp or not resp.data:
        return None
    return dict(resp.data.get("public_metrics") or {})


def fetch_ig_metrics(post_id):
    """Stable counts from the media fields; insights are best-effort.

    Meta has renamed and deprecated Instagram insight metrics more than
    once, so a hard dependency on a metric name is a future outage. The
    fields endpoint carries like_count and comments_count reliably.
    """
    import requests
    token = os.environ["IG_ACCESS_TOKEN"]
    out = {}
    r = requests.get(f"{GRAPH}/{post_id}",
                     params={"fields": "like_count,comments_count",
                             "access_token": token}, timeout=30)
    if r.status_code == 200:
        out.update({k: v for k, v in r.json().items() if k != "id"})

    try:
        r = requests.get(f"{GRAPH}/{post_id}/insights",
                         params={"metric": "reach", "access_token": token},
                         timeout=30)
        if r.status_code == 200:
            for row in r.json().get("data", []):
                values = row.get("values") or [{}]
                out[row.get("name")] = values[0].get("value")
    except Exception as e:
        print(f"[analytics] ig insights unavailable for {post_id}: {e}")
    return out or None


FETCHERS = {"x": fetch_x_metrics, "ig": fetch_ig_metrics}


def load_jsonl(path):
    if not os.path.exists(path):
        return []
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    return rows


def due_for_collection(posted_rows, metric_rows, now=None):
    """(channel, post_id, topic) tuples that should be sampled now."""
    now = now or _now()
    today = now.date().isoformat()
    already_today = {(r.get("post_id"), r.get("collected_at", "")[:10])
                     for r in metric_rows}
    due = []
    for entry in posted_rows:
        try:
            posted_on = datetime.date.fromisoformat(entry.get("date", ""))
        except ValueError:
            continue
        age_days = (now.date() - posted_on).days
        if age_days > MAX_AGE_DAYS:
            continue
        # A post published today has nothing to report yet.
        if age_days * 24 < MIN_AGE_HOURS:
            continue
        for channel in FETCHERS:
            post_id = entry.get(channel)
            if not post_id or (post_id, today) in already_today:
                continue
            due.append((channel, str(post_id), entry.get("topic")))
    return due


def collect(log_path, metrics_path, now=None):
    """Sample every due post. Returns (collected, failed). Never raises."""
    now = now or _now()
    posted = load_jsonl(log_path)
    existing = load_jsonl(metrics_path)
    due = due_for_collection(posted, existing, now)
    if not due:
        print("[analytics] nothing due")
        return 0, 0

    collected = failed = 0
    os.makedirs(os.path.dirname(metrics_path), exist_ok=True)
    with open(metrics_path, "a") as f:
        for channel, post_id, topic in due:
            try:
                metrics = FETCHERS[channel](post_id)
            except Exception as e:
                print(f"[analytics] {channel} {post_id} FAILED: "
                      f"{type(e).__name__}: {e}")
                failed += 1
                continue
            if not metrics:
                failed += 1
                continue
            f.write(json.dumps({
                "collected_at": now.isoformat(),
                "channel": channel,
                "post_id": post_id,
                "topic": topic,
                "metrics": metrics,
            }) + "\n")
            collected += 1
    print(f"[analytics] collected {collected}, failed {failed}, "
          f"due {len(due)}")
    return collected, failed


if __name__ == "__main__":
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    collect(os.path.join(root, "logs", "posted.jsonl"),
            os.path.join(root, "logs", "metrics.jsonl"))
