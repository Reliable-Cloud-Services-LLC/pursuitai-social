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


def _li_count(payload):
    elements = (payload or {}).get("elements") or []
    if elements and "count" in elements[0]:
        return elements[0]["count"]
    return None


def _li_org_stats(urn, org, headers):
    """Page share stats. clickCount lives here and not on the member API."""
    import requests
    from urllib.parse import quote
    org_urn = quote(f"urn:li:organization:{org}", safe="")
    share = quote(urn, safe="")
    url = ("https://api.linkedin.com/rest/organizationalEntityShareStatistics"
           f"?q=organizationalEntity&organizationalEntity={org_urn}"
           f"&shares=List({share})")
    try:
        r = requests.get(url, headers=headers, timeout=30)
    except Exception as e:
        print(f"[analytics] linkedin org stats unavailable: {e}")
        return None
    if r.status_code != 200:
        return None
    elements = (r.json() or {}).get("elements") or []
    if not elements:
        return None
    stats = elements[0].get("totalShareStatistics") or {}
    mapping = (
        ("impressionCount", "impression_count"),
        ("likeCount", "like_count"),
        ("commentCount", "comments_count"),
        ("shareCount", "shares"),
        ("clickCount", "click_count"),
    )
    out = {}
    for src, dest in mapping:
        if src in stats and stats[src] is not None:
            out[dest] = stats[src]
    return out or None


def fetch_linkedin_metrics(post_id):
    """Best-effort LinkedIn counts. A missing scope returns None, not a raise.

    Member posts use memberCreatorPostAnalytics (impressions, reactions,
    comments, reshares). When LINKEDIN_ORG_ID is set, organizational share
    stats are merged on top — that response is where clickCount is documented.
    """
    import requests
    from urllib.parse import quote
    import post_linkedin
    token = os.environ.get("LINKEDIN_ACCESS_TOKEN")
    if not token or not post_id:
        return None
    headers = post_linkedin._headers(token, json_body=False)
    urn = (post_id if str(post_id).startswith("urn:")
           else f"urn:li:share:{post_id}")
    kind = "ugc" if "ugcPost" in urn else "share"
    entity = f"({kind}:{quote(urn, safe='')})"
    metrics = {}
    for query_type, key in (
            ("IMPRESSION", "impression_count"),
            ("REACTION", "like_count"),
            ("COMMENT", "comments_count"),
            ("RESHARE", "shares")):
        url = ("https://api.linkedin.com/rest/memberCreatorPostAnalytics"
               f"?q=entity&entity={entity}&queryType={query_type}"
               f"&aggregation=TOTAL")
        try:
            r = requests.get(url, headers=headers, timeout=30)
        except Exception as e:
            print(f"[analytics] linkedin {query_type} unavailable for "
                  f"{post_id}: {e}")
            continue
        if r.status_code != 200:
            continue
        count = _li_count(r.json())
        if count is not None:
            metrics[key] = count
    org = os.environ.get("LINKEDIN_ORG_ID")
    if org:
        org_metrics = _li_org_stats(urn, org, headers)
        if org_metrics:
            metrics.update(org_metrics)
    return metrics or None


FETCHERS = {"x": fetch_x_metrics, "ig": fetch_ig_metrics,
            "linkedin": fetch_linkedin_metrics}


def _post_id(entry, channel):
    """The id analytics should read back, from the top level or the channel."""
    if entry.get(channel):
        return entry.get(channel)
    payload = (entry.get("channels") or {}).get(channel)
    if isinstance(payload, dict) and payload.get("id"):
        return payload["id"]
    return None


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
            post_id = _post_id(entry, channel)
            if not post_id or (str(post_id), today) in already_today:
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

    # So a metric row can carry the same angle id and UTM dict the post log
    # already has. The winner report joins on angle id either way.
    meta = {}
    for entry in posted:
        for channel in FETCHERS:
            post_id = _post_id(entry, channel)
            if post_id:
                meta[(channel, str(post_id))] = entry

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
            row = {
                "collected_at": now.isoformat(),
                "channel": channel,
                "post_id": post_id,
                "topic": topic,
                "metrics": metrics,
            }
            entry = meta.get((channel, post_id)) or {}
            if entry.get("angle_id"):
                row["angle_id"] = entry["angle_id"]
            if entry.get("utm"):
                row["utm"] = entry["utm"]
            f.write(json.dumps(row) + "\n")
            collected += 1
    print(f"[analytics] collected {collected}, failed {failed}, "
          f"due {len(due)}")
    return collected, failed


if __name__ == "__main__":
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    log = os.path.join(root, "logs", "posted.jsonl")
    metrics = os.path.join(root, "logs", "metrics.jsonl")
    collect(log, metrics)
    # After the read, so the report sees this week's samples. Quiet when
    # no angle has ever shipped — pilot mode off must not grow a new alert.
    try:
        import pilot
        pilot.deliver_report(log, metrics)
    except Exception as e:
        print(f"[pilot] winner report failed: {type(e).__name__}: {e}")
