"""LinkedIn posts for brand-new public contract awards.

The platform publishes award pages at https://pursuitai.net/awards/<slug>
and a machine feed:

    GET {base}/api/v1/public-awards/feed
    Authorization: Bearer $PUBLIC_AWARDS_FEED_TOKEN
    ?since=<cursor>&limit=<n>&order=value_desc

    {"items": [{
        "is_new_award", "slug", "url", "first_published_at",
        "recipient", "agency", "piid", "award_type_code",
        "obligation", "ceiling", "naics", "summary",
        "generated_unique_award_id", "official_urls"
     }, ...],
     "next_since": "<cursor>"}

`base` defaults to https://api.pursuitai.net — the API host the marketing
site already calls. Override with PUBLIC_AWARDS_FEED_BASE_URL.

The route is mounted only when PUBLIC_AWARD_PAGES_ENABLED is on, so a 404
or an empty list is a normal day, not an outage. A rejected token is not:
that fails the run so a wrong secret is visible.

What this will not do:

  * Post anything whose is_new_award is not true. The platform already
    limits the feed to IT NAICS, at least $1M, and no mods, options, or
    task orders. We still drop a row that is not flagged new.
  * Post more than three a day. Largest obligated amount first (ceiling
    only when the record has no positive obligation). The caption leads
    with the same figure, and it labels a ceiling as a ceiling.
  * Publish by itself. --prepare writes content/awards_pending.json and
    stops. The awards workflow parks on the social-publish environment,
    the same human gate as the daily post, with its own file so it cannot
    overwrite content/pending.json.
  * Invent a fact. Copy is built from the feed row. A model rewrite that
    adds a number, drops the recipient, or swaps the link is thrown away.
  * Follow the cursor past awards we have not posted. next_since is saved
    only once every eligible row in the page has been posted or recorded
    as unpostable. Deduping is logs/award_posted.jsonl, keyed by
    generated_unique_award_id.

    python engine/awards.py --dry-run     # print the day's posts, write nothing
    python engine/awards.py --prepare     # queue them for approval
    python engine/awards.py --publish     # after a human approves
"""
import argparse
import datetime
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import requests

import approval
import captions
import cards
import compliance

DAILY_CAP = 3
FETCH_LIMIT = 50
DEFAULT_BASE = "https://api.pursuitai.net"
FEED_PATH = "/api/v1/public-awards/feed"

# Slugs become a path segment and a filename. Anything else is refused
# rather than percent-encoded into a URL we did not mean to publish.
_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,160}$")
_PAGE = re.compile(
    r"^https://pursuitai\.net/awards/([A-Za-z0-9][A-Za-z0-9_-]{0,160})/?$")


class FeedError(RuntimeError):
    """The feed was configured and answered with something we cannot use."""


def paths(root=None):
    root = root or ROOT
    return {
        "root": root,
        "pending": os.path.join(root, "content", "awards_pending.json"),
        "approved": os.path.join(root, "content", "awards_approved.json"),
        "posted": os.path.join(root, "logs", "award_posted.jsonl"),
        "skipped": os.path.join(root, "logs", "award_skipped.jsonl"),
        "cursor": os.path.join(root, "logs", "award_cursor.json"),
        "cards": os.path.join(root, "assets", "cards"),
    }


def feed_endpoint(base=None):
    """Join the configured origin to the feed path without doubling it.

    PUBLIC_AWARDS_FEED_BASE_URL may be the origin
    (https://api.pursuitai.net), the API root (.../api/v1), or the full
    feed URL. All three resolve to one endpoint.
    """
    raw = (base if base is not None
           else os.environ.get("PUBLIC_AWARDS_FEED_BASE_URL") or DEFAULT_BASE)
    raw = raw.strip().rstrip("/")
    if raw.endswith("/public-awards/feed"):
        return raw
    if raw.endswith("/api/v1"):
        return raw + "/public-awards/feed"
    return raw + FEED_PATH


def _text(raw, keys):
    if isinstance(raw, dict):
        for key in keys:
            value = raw.get(key)
            if value not in (None, ""):
                return str(value).strip()
        return ""
    if raw is None:
        return ""
    return str(raw).strip()


def _money(raw):
    if isinstance(raw, dict):
        raw = raw.get("amount", raw.get("value"))
    if isinstance(raw, bool) or raw is None or raw == "":
        return None
    if isinstance(raw, str):
        raw = raw.replace("$", "").replace(",", "").strip()
        if not raw:
            return None
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def _is_new(value):
    if value is True:
        return True
    if isinstance(value, str) and value.strip().lower() == "true":
        return True
    return False


def _slug(item):
    slug = _text(item.get("slug"), ("slug",))
    slug = slug.strip("/")
    if _SLUG.match(slug):
        return slug
    url = _text(item.get("url"), ("url",)).split("?")[0].split("#")[0]
    match = _PAGE.match(url)
    return match.group(1) if match else ""


def normalize_item(raw):
    """One feed row as flat fields. Nested name/amount objects are accepted.

    The documented shape is flat. A row that is not an object is dropped
    here so a single bad element cannot abort the page.
    """
    if not isinstance(raw, dict):
        return None
    official = raw.get("official_urls")
    return {
        "is_new_award": _is_new(raw.get("is_new_award")),
        "slug": _slug(raw),
        "url": _text(raw.get("url"), ("url",)),
        "first_published_at": _text(raw.get("first_published_at"),
                                    ("first_published_at",)),
        "recipient": _text(raw.get("recipient"),
                           ("name", "recipient_name", "legal_business_name")),
        "agency": _text(raw.get("agency"),
                        ("name", "agency_name", "toptier_name")),
        "piid": _text(raw.get("piid"), ("piid",)),
        "award_type_code": _text(raw.get("award_type_code"),
                                 ("award_type_code", "code")),
        "obligation": _money(raw.get("obligation")),
        "ceiling": _money(raw.get("ceiling")),
        "naics": _text(raw.get("naics"), ("code", "naics_code", "naics")),
        "summary": _text(raw.get("summary"),
                         ("text", "description", "summary")),
        "generated_unique_award_id": _text(
            raw.get("generated_unique_award_id"),
            ("generated_unique_award_id",)),
        "official_urls": [str(u) for u in official] if isinstance(official, list) else [],
    }


def rank_value(award):
    """Dollars used to order the day.

    The obligated amount is the award. The ceiling is only the ranking
    key when the record has no positive obligation, and the caption then
    says "ceiling" rather than presenting it as the amount awarded.
    """
    obligation = award.get("obligation")
    if isinstance(obligation, (int, float)) and obligation > 0:
        return float(obligation)
    ceiling = award.get("ceiling")
    if isinstance(ceiling, (int, float)) and ceiling > 0:
        return float(ceiling)
    return None


def _eligible(award, seen):
    if not award or not award.get("is_new_award"):
        return False
    aid = award.get("generated_unique_award_id") or ""
    if not aid or aid in seen:
        return False
    if not award.get("slug") or not award.get("recipient") or not award.get("agency"):
        return False
    if rank_value(award) is None:
        return False
    return True


def select_awards(items, seen_ids=(), limit=DAILY_CAP):
    """(chosen, deferred) from one feed page.

    chosen is at most `limit` rows, largest rank_value first.
    deferred is every other eligible row — the reason not to advance the
    cursor. Already-posted ids, rows that are not new, and rows missing
    the facts a caption needs are neither.
    """
    seen = set(seen_ids or ())
    pool = []
    taken = set()
    for raw in items or []:
        award = normalize_item(raw)
        if not _eligible(award, seen):
            continue
        aid = award["generated_unique_award_id"]
        if aid in taken:
            continue
        taken.add(aid)
        pool.append(award)
    pool.sort(key=lambda award: (-rank_value(award),
                                 award["generated_unique_award_id"]))
    return pool[:limit], pool[limit:]


def fetch_feed(since=None, limit=FETCH_LIMIT, get=None, token=None, base=None):
    """One page. 404 and a missing token return an empty unavailable page.

    Both mean "do not queue and do not move the cursor". A 401, a 5xx,
    or a body that is not the documented shape raises FeedError.
    """
    if token is None:
        token = os.environ.get("PUBLIC_AWARDS_FEED_TOKEN")
    if not token:
        print("[awards] PUBLIC_AWARDS_FEED_TOKEN is not set — nothing to queue")
        return {"items": [], "next_since": None, "unavailable": True,
                "status": None, "reason": "not_configured"}
    getter = get or requests.get
    params = {"limit": str(limit), "order": "value_desc"}
    if since:
        params["since"] = since
    url = feed_endpoint(base)
    try:
        response = getter(
            url,
            headers={"Authorization": f"Bearer {token}",
                     "Accept": "application/json",
                     "User-Agent": "pursuitai-social-awards"},
            params=params,
            timeout=30)
    except Exception as exc:
        raise FeedError(
            f"award feed request failed ({type(exc).__name__}: {exc})") from exc
    status = getattr(response, "status_code", None)
    if status == 404:
        print("[awards] feed returned 404. Public award pages are probably "
              "still off (PUBLIC_AWARD_PAGES_ENABLED). Nothing to queue.")
        return {"items": [], "next_since": None, "unavailable": True,
                "status": 404, "reason": "not_found"}
    if status in (401, 403):
        raise FeedError(
            f"award feed rejected the token ({status}). "
            f"Check PUBLIC_AWARDS_FEED_TOKEN.")
    if status is None or status >= 300:
        raise FeedError(f"award feed returned {status}")
    try:
        data = response.json()
    except Exception as exc:
        raise FeedError(
            f"award feed returned non-JSON ({type(exc).__name__})") from exc
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise FeedError("award feed JSON has no items list")
    items = []
    for raw in data["items"]:
        item = normalize_item(raw)
        if item is not None:
            items.append(item)
    nxt = data.get("next_since")
    if nxt is not None and nxt != "":
        nxt = str(nxt)
    else:
        nxt = None
    return {"items": items, "next_since": nxt, "unavailable": False,
            "status": status, "reason": None}


def _read_jsonl_ids(path):
    found = set()
    if not os.path.exists(path):
        return found
    with open(path) as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            aid = row.get("generated_unique_award_id")
            if aid:
                found.add(aid)
    return found


def posted_ids(root=None):
    return _read_jsonl_ids(paths(root)["posted"])


def seen_ids(root=None):
    """Posted, plus rows we already recorded as impossible to caption."""
    state = paths(root)
    return _read_jsonl_ids(state["posted"]) | _read_jsonl_ids(state["skipped"])


def load_cursor(root=None):
    path = paths(root)["cursor"]
    if not os.path.exists(path):
        return None
    try:
        with open(path) as handle:
            value = json.load(handle).get("since")
    except (ValueError, OSError):
        return None
    return str(value) if value else None


def save_cursor(root, since):
    if not since:
        return
    path = paths(root)["cursor"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        json.dump({"since": since,
                   "updated_at": _now()}, handle, indent=2)
        handle.write("\n")


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def load_brand():
    with open(os.path.join(ROOT, "content", "calendar.json")) as handle:
        return json.load(handle)["brand"]


def _clip(text, limit):
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(".,;")
    return (cut or text[:limit]).rstrip() + "…"


def card_topic(award, short=False):
    """The fields render_card draws. Facts only, and short enough to fit."""
    amount, kind = captions._lead_amount(award)
    stat = f"{captions.format_dollars(amount)} {kind}"
    if short:
        headline = captions.format_dollars(amount)
        body = _clip(f"{award['recipient']}. {award['agency']}.", 120)
    else:
        headline = _clip(award["recipient"], 80)
        summary = captions.clean_summary(award.get("summary"))
        body = award["agency"]
        if summary:
            body = f"{body}. {summary}"
        body = _clip(body, 180)
    return {
        "id": f"award-{award['slug']}",
        "feature": "Contract award",
        "headline": headline,
        "body": body,
        "stat": stat,
        "verification": {
            "status": "VERIFIED",
            "source_type": "public_awards_feed",
            "source_ref": award.get("generated_unique_award_id") or "",
        },
    }


def _card_ok(topic):
    return not compliance.check_rendered(topic)


def render_award(award, brand, out_path):
    """Draw the square card. Returns the topic that was drawn, or None."""
    topic = card_topic(award)
    if not _card_ok(topic):
        topic = card_topic(award, short=True)
    if not _card_ok(topic):
        return None
    try:
        cards.render_award_card(topic, brand, out_path)
    except cards.LayoutOverflow:
        topic = card_topic(award, short=True)
        if not _card_ok(topic):
            return None
        try:
            cards.render_award_card(topic, brand, out_path)
        except cards.LayoutOverflow:
            return None
    return topic


def _append_jsonl(path, row):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as handle:
        handle.write(json.dumps(row) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _build_posts(chosen, brand, root, dry_run):
    """Captions (and, unless this is a dry run, cards) for the chosen rows.

    A row whose template cannot be grounded is recorded in the skip log
    so it does not sit at the front of the page forever. A render failure
    is not: that is our bug, and the row stays eligible so the cursor
    does not move past it.
    """
    state = paths(root)
    posts = []
    blocked = False
    for award in chosen:
        # Same builder prepare would queue, including a model rewrite when
        # the key is set. The key check lives in captions; with no key this
        # is the template and does not touch the network.
        built = captions.build_award_linkedin(award, brand, fresh=True)
        if built is None:
            print(f"[awards] skip {award['generated_unique_award_id']}: "
                  f"caption is not grounded or breaks a content rule")
            if not dry_run:
                _append_jsonl(state["skipped"], {
                    "generated_unique_award_id": award["generated_unique_award_id"],
                    "slug": award["slug"],
                    "reason": "caption",
                    "at": _now(),
                })
            continue
        rel = f"assets/cards/award_{award['slug']}.png"
        alt = f"{award['recipient']} — {card_topic(award)['stat']}"
        if not dry_run:
            out = os.path.join(root, rel)
            topic = render_award(award, brand, out)
            if topic is None:
                print(f"[awards] {award['generated_unique_award_id']}: "
                      f"card would not render — leaving it for the next run")
                blocked = True
                continue
        posts.append({
            "generated_unique_award_id": award["generated_unique_award_id"],
            "slug": award["slug"],
            "recipient": award["recipient"],
            "agency": award["agency"],
            "obligation": award.get("obligation"),
            "ceiling": award.get("ceiling"),
            "naics": award.get("naics") or "",
            "piid": award.get("piid") or "",
            "text_linkedin": built["text"],
            "copy_varied": built["varied"],
            "media_linkedin": rel,
            "url": captions.award_page_url(award),
            "alt": alt,
        })
    return posts, blocked


def _print_posts(posts, deferred, page):
    if page.get("unavailable"):
        print(f"[awards] feed unavailable ({page.get('reason')}). "
              f"No posts.")
        return
    print(f"[awards] {len(posts)} post(s); {len(deferred)} eligible "
          f"left for a later day.")
    for index, post in enumerate(posts, 1):
        print(f"\n--- {index} {post['recipient']} ---")
        print(post["text_linkedin"])


def prepare(root=None, dry_run=False, get=None):
    """Select today's awards. Write the queue unless this is a dry run.

    Returns the pending document (posts may be empty).
    """
    root = root or ROOT
    state = paths(root)
    current = load_cursor(root)
    page = fetch_feed(since=current, get=get)
    if page["unavailable"]:
        if dry_run:
            _print_posts([], [], page)
        elif os.path.exists(state["pending"]):
            # A feed that is off must not leave yesterday's queue sitting
            # where the next publish job would treat it as today's.
            os.remove(state["pending"])
        return {"date": _today(), "topic": "awards", "format": "card",
                "kind": "awards", "posts": [], "unavailable": True}

    chosen, deferred = select_awards(page["items"], seen_ids(root))
    brand = load_brand()
    posts, render_blocked = _build_posts(chosen, brand, root, dry_run)
    if dry_run:
        print("[awards] dry-run: nothing queued, nothing posted")
        _print_posts(posts, deferred, page)
        return {"date": _today(), "topic": "awards", "format": "card",
                "kind": "awards", "posts": posts, "dry_run": True}

    # Advance only when a later run, seeing the skips we just recorded,
    # would find nothing left on this page that we did not queue. A card
    # that failed to render is still eligible, so it holds the cursor.
    queued_ids = {post["generated_unique_award_id"] for post in posts}
    rest, still_deferred = select_awards(page["items"], seen_ids(root))
    held = [award for award in rest
            if award["generated_unique_award_id"] not in queued_ids]
    unfinished = bool(still_deferred or render_blocked or held)
    advance_to = (page["next_since"]
                  if not unfinished and page.get("next_since")
                  and page["next_since"] != current else None)

    pending = {
        "date": _today(),
        "topic": "awards",
        "format": "card",
        "kind": "awards",
        "cursor_advance_to": advance_to if posts else None,
        "posts": posts,
    }
    if not posts:
        if os.path.exists(state["pending"]):
            os.remove(state["pending"])
        if advance_to:
            save_cursor(root, advance_to)
            print(f"[awards] nothing to queue; cursor advanced")
        else:
            print("[awards] nothing to queue")
        return pending

    os.makedirs(os.path.dirname(state["pending"]), exist_ok=True)
    with open(state["pending"], "w") as handle:
        json.dump(pending, handle, indent=2)
        handle.write("\n")
    print(f"[awards] queued {len(posts)} post(s) in content/awards_pending.json")
    if deferred or held:
        print("[awards] eligible awards remain on this page; "
              "the cursor stays where it is")
    return pending


def _today():
    return datetime.datetime.now(datetime.timezone.utc).date().isoformat()


def pending_hash(root=None):
    return approval.compute_hash(paths(root)["pending"])


def approve(expect=None, root=None):
    state = paths(root)
    try:
        record = approval.write_approval(state["pending"], state["approved"],
                                         expect=expect)
    except FileNotFoundError as exc:
        print(f"[awards] {exc}")
        return 1
    except approval.ApprovalMismatch as exc:
        print(f"[awards] REFUSED: {exc}")
        return 1
    print(f"[awards] approved {record['topic']} ({record['format']}) "
          f"by {record['approved_by']} at {record['approved_at']}")
    return 0


def _load_pending(root):
    path = paths(root)["pending"]
    if not os.path.exists(path):
        return None
    with open(path) as handle:
        return json.load(handle)


def publish(root=None, poster=None, force=False):
    """Post the approved batch to the LinkedIn Page.

    `poster` defaults to post_linkedin.post_media — the same image path
    the daily run uses. One failure stops the batch. Ids that did post
    are already in the dedupe log, so the next run will not repeat them.
    The cursor moves only when this batch was the last eligible row on
    the page and every post in it succeeded.
    """
    root = root or ROOT
    state = paths(root)
    pending = _load_pending(root)
    if not pending or not pending.get("posts"):
        print("[awards] nothing queued — run --prepare first")
        return 1
    if force:
        if os.environ.get("GITHUB_ACTIONS"):
            print("[awards] REFUSED: --force is local-only and cannot be "
                  "used in CI. Approve the run in the social-publish "
                  "environment instead.")
            return 1
        print("[awards] WARNING: --force bypasses human review")
    else:
        ok, reason = approval.verify_approval(state["pending"], state["approved"])
        if not ok:
            print(f"[awards] BLOCKED: {reason}")
            return 1

    if poster is None:
        import post_linkedin
        poster = post_linkedin.post_media

    failed = None
    posted = []
    # A previous attempt may have published the first of the batch and
    # then died before deleting the queue. The log is the source of
    # truth for "already on the Page"; posting those rows again would
    # duplicate them.
    already = posted_ids(root)
    for post in pending["posts"]:
        if post["generated_unique_award_id"] in already:
            print(f"[awards] {post['slug']} is already in the dedupe log "
                  f"— not posting it again")
            posted.append(post["generated_unique_award_id"])
            continue
        try:
            outcome = poster(post["media_linkedin"], post["text_linkedin"],
                             post.get("alt"))
        except Exception as exc:
            failed = f"{type(exc).__name__}: {exc}"
            print(f"[awards] FAILED {post['generated_unique_award_id']}: "
                  f"{failed}")
            break
        extra = outcome if isinstance(outcome, dict) else {"id": outcome}
        _append_jsonl(state["posted"], {
            "generated_unique_award_id": post["generated_unique_award_id"],
            "slug": post["slug"],
            "linkedin_id": extra.get("id"),
            "url": extra.get("url"),
            "posted_at": _now(),
        })
        posted.append(post["generated_unique_award_id"])
        print(f"[awards] posted {post['slug']} ({extra.get('id')})")

    if failed is None and pending.get("cursor_advance_to"):
        save_cursor(root, pending["cursor_advance_to"])
        print(f"[awards] cursor advanced")
    elif failed is None:
        print("[awards] cursor held — eligible awards remain on this page")

    if os.path.exists(state["pending"]):
        os.remove(state["pending"])
    approval.clear_approval(state["approved"])

    if failed or len(posted) != len(pending["posts"]):
        _alert_failure(pending, failed, posted)
        return 1
    return 0


def _alert_failure(pending, failed, posted):
    try:
        import notify
        notify.alert(
            f":warning: *Award posts did not all publish*\n"
            f"posted `{len(posted)}` of `{len(pending.get('posts') or [])}`\n"
            f"{failed or 'incomplete'}\n"
            f"Posted ids are in logs/award_posted.jsonl and will not be "
            f"repeated. The rest stay eligible.")
    except Exception as exc:
        print(f"[awards] failure alert did not send ({type(exc).__name__}: {exc})")


def notify_pending(root=None):
    root = root or ROOT
    pending = _load_pending(root)
    if not pending or not pending.get("posts"):
        print("[awards] no awards_pending.json — nothing to review")
        return 1
    import media
    import notify
    base = os.environ.get("MEDIA_BASE_URL")
    urls = []
    for post in pending["posts"]:
        if base and post.get("media_linkedin"):
            try:
                urls.append(media.public_url(post["media_linkedin"],
                                             cache_bust=True))
            except Exception as exc:
                print(f"[awards] media url skipped: {type(exc).__name__}: {exc}")
                urls.append(None)
        else:
            urls.append(None)
    sent = notify.awards_review(
        pending, review_url=os.environ.get("REVIEW_URL"), media_urls=urls)
    if sent:
        print(f"[awards] review request sent ({len(pending['posts'])} posts)")
    else:
        print("[awards] review request NOT sent — NOTIFY_WEBHOOK_URL unset")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the posts that would be queued; "
                             "write nothing and post nothing")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--approve", action="store_true")
    parser.add_argument("--expect", metavar="SHA256")
    parser.add_argument("--pending-hash", action="store_true")
    parser.add_argument("--notify-pending", action="store_true")
    parser.add_argument("--force", action="store_true",
                        help="LOCAL DEV ONLY: publish without approval")
    args = parser.parse_args(argv)

    if args.dry_run or args.prepare:
        try:
            prepare(dry_run=args.dry_run)
        except FeedError as exc:
            print(f"[awards] {exc}")
            return 1
        return 0
    if args.pending_hash:
        try:
            print(pending_hash())
        except FileNotFoundError:
            print("[awards] no awards_pending.json")
            return 1
        return 0
    if args.approve:
        return approve(expect=args.expect)
    if args.notify_pending:
        return notify_pending()
    if args.publish:
        return publish(force=args.force)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
