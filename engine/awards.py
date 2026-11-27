"""LinkedIn posts for brand-new public contract awards.

The platform publishes award pages at https://pursuitai.net/awards/<slug>
and a machine feed:

    GET {base}/api/v1/public-awards/feed
    Authorization: Bearer $PUBLIC_AWARDS_FEED_TOKEN
    ?since=<iso>&limit=<1-100>&order=recent

    {"items": [{
        "id", "is_new_award", "slug", "url",
        "first_published_at", "published_at", "first_seen_at",
        "recipient_name", "recipient_uei",
        "awarding_agency", "awarding_sub_agency", "awarding_office",
        "piid", "parent_award_piid", "contract_vehicle", "is_task_order",
        "award_type_code", "award_category",
        "obligation", "ceiling", "action_date", "pop_start", "pop_end",
        "naics_code", "naics_description", "psc_code",
        "set_aside", "extent_competed", "offers_received", "summary",
        "description_source",
        "generated_unique_award_id",
        "official_urls": {"usaspending": "..."}
     }, ...],
     "next_since": "<iso or null>",
     "order": "recent"|"value_desc"}

`next_since` is set only for `order=recent` (oldest first by
first_published_at). `order=value_desc` always returns null and expects
the caller to pass `since` itself. This client therefore pages with
`recent`, keeps that cursor, and picks the largest awards on the page
itself. Largest means the greater of obligation and ceiling, which is
how the server orders `value_desc`.

`limit` is at most 100 (the server default is 20; we ask for the max so
the three we pick are the largest of the page, not of a 20-row slice).

`is_task_order`, `contract_vehicle`, `first_seen_at`, and a filled-in
`generated_unique_award_id` are additive. Older feeds omit them. An
FPDS-first row can have an empty `generated_unique_award_id` and an
empty `official_urls.usaspending` until USAspending has the award.
`offers_received` (FPDS numberOfOffersReceived) and `extent_competed`
are the same kind of addition. Both may be null. The caption names how
many competitors lost only when `offers_received` is an integer from 2
through 999 and the extent does not say the award was not competed,
sole source, or a follow-on. Otherwise that line is left out.
`first_seen_at` / `first_published_at` is when the record became public,
which for DoD can be about 90 days after `action_date`. Withdrawn
awards are not in the feed.

`url` is already absolute (`https://pursuitai.net/awards/<slug>`). The
post uses that URL.

`base` defaults to https://api.pursuitai.net. Override with
PUBLIC_AWARDS_FEED_BASE_URL.

The route is mounted only when PUBLIC_AWARD_PAGES_ENABLED is on, so a 404
or an empty list is a normal day, not an outage. A 401 (bad token) and a
422 (bad `since`) fail the run.

What this will not do:

  * Post anything whose is_new_award is not true. The platform already
    limits the feed to IT NAICS and at least $1M, and it leaves out
    modifications, options, and withdrawn awards. Task orders are in
    the feed. We still drop a row that is not flagged new.
  * Filter on extent_competed. Sole-source is not a skip. The caption
    still posts. It does not claim the winner beat other bidders.
  * Drop a row because `generated_unique_award_id` is empty. FPDS can
    publish the page before USAspending has that id.
  * Post more than three a day. The caption and the card lead with the
    greater of obligation and ceiling. A ceiling is "valued at up to".
    An obligation is "obligated".
  * Publish by itself. --prepare writes content/awards_pending.json and
    stops. The awards workflow parks on the social-publish environment,
    the same human gate as the daily post, with its own file so it cannot
    overwrite content/pending.json.
  * Invent a fact. Copy is built from the feed row. "Beat N other
    bidders" is offers_received minus 1, and only when that count is
    in range and the extent does not say otherwise. A task order with
    a vehicle says "Won over N other {vehicle} holders". A model rewrite
    that adds a number, drops the recipient, names a vehicle the feed
    left null, or swaps the link is thrown away.
  * Tag a company it cannot verify. A LinkedIn mention is sent only when
    exactly one organization page has a website domain equal to the
    awardee's SAM registration website. No match, or more than one, and
    the post uses the plain legal name. Confirmed mappings are reused.
    People are mentioned only after they are confirmed. See engine/mentions.py.
  * Follow the cursor past awards we have not posted. next_since is saved
    only once every eligible row in the page has been posted or recorded
    as unpostable. Deduping is logs/award_posted.jsonl, keyed by the
    feed item id (the PursuitAI page id). A row with no page id uses
    PIID + parent_award_piid + awarding agency. Entries already logged
    under generated_unique_award_id still count.

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
import mentions

DAILY_CAP = 3
# Server maximum. The default of 20 would hide a larger award sitting
# at row 21 of an oldest-first page.
FETCH_LIMIT = 100
FEED_LIMIT_MAX = 100
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


def _token(value):
    """A log or feed identity token. Bools and objects are not keys."""
    if value is None or isinstance(value, (bool, dict, list)):
        return ""
    return str(value).strip()


def fallback_key(award):
    """PIID + parent award PIID + awarding agency.

    The page id is the key we log. This is the fallback when a row has
    no page id, and it is also stored so the same PIID is not posted
    again if a later row arrives with a different id. Parent is empty
    on a definitive contract; the PIID and the agency still have to be
    there, or this is not a key.
    """
    if not isinstance(award, dict):
        return ""
    piid = _token(award.get("piid"))
    agency = _token(award.get("awarding_agency"))
    if not piid or not agency:
        return ""
    parent = _token(award.get("parent_award_piid"))
    return f"{piid}|{parent}|{agency}"


def dedupe_key(award):
    """Page id, else the PIID fallback, else a generated USAspending id.

    The last case is only a row that has none of the others. New logs
    still record a generated id when the feed sent one, so an older
    entry keyed only by that id matches a later row.
    """
    if not isinstance(award, dict):
        return ""
    page_id = _token(award.get("id"))
    if page_id:
        return page_id
    fallback = fallback_key(award)
    if fallback:
        return fallback
    return _token(award.get("generated_unique_award_id"))


def identity_keys(award):
    """Every stored token that means this award was already handled."""
    if not isinstance(award, dict):
        return set()
    keys = set()
    for field in ("id", "generated_unique_award_id", "dedupe_key",
                  "fallback_key"):
        token = _token(award.get(field))
        if token:
            keys.add(token)
    fallback = fallback_key(award)
    if fallback:
        keys.add(fallback)
    primary = dedupe_key(award)
    if primary:
        keys.add(primary)
    return keys


def _slug(item):
    slug = _text(item.get("slug"), ("slug",))
    slug = slug.strip("/")
    if _SLUG.match(slug):
        return slug
    url = _text(item.get("url"), ("url",)).split("?")[0].split("#")[0]
    match = _PAGE.match(url)
    return match.group(1) if match else ""


# Exact keys from apps/backend/app/api/public_awards.py. The earlier
# names (recipient, agency, naics) are not on the feed and are not read.
_STRING_KEYS = (
    "id", "slug", "url", "first_published_at", "published_at",
    "first_seen_at", "recipient_name", "recipient_uei", "awarding_agency",
    "awarding_sub_agency", "awarding_office", "piid", "parent_award_piid",
    "contract_vehicle", "award_type_code", "award_category", "action_date",
    "pop_start", "pop_end", "naics_code", "naics_description", "psc_code",
    "set_aside", "extent_competed", "summary", "description_source",
    "generated_unique_award_id",
    # Optional. The feed does not send these today. When it does (or a
    # row is handed in with one), the company mention can be verified
    # without SAM_API_KEY. Absent keys stay "".
    "recipient_website", "entity_website", "sam_website",
)


def _field(raw, key):
    """A scalar feed field. Dicts are not unpacked — the keys are flat."""
    value = raw.get(key)
    if value is None or isinstance(value, (bool, dict, list)):
        return ""
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value).strip()


def _official_urls(raw):
    official = raw.get("official_urls")
    if not isinstance(official, dict):
        return {}
    return {str(key): str(value) for key, value in official.items()
            if value not in (None, "")}


def normalize_item(raw):
    """One feed row, using the feed's own key names.

    A row that is not an object is dropped so one bad element cannot
    abort the page.
    """
    if not isinstance(raw, dict):
        return None
    item = {key: _field(raw, key) for key in _STRING_KEYS}
    item["slug"] = _slug(raw)
    item["is_new_award"] = _is_new(raw.get("is_new_award"))
    # Missing on the current feed. False, not a guess from the type code.
    item["is_task_order"] = _is_new(raw.get("is_task_order"))
    item["obligation"] = _money(raw.get("obligation"))
    item["ceiling"] = _money(raw.get("ceiling"))
    # Optional. Absent on older feeds, and then None — the caption
    # omits the bidder line, which is how those rows already read.
    item["offers_received"] = captions._offers_received(raw)
    item["official_urls"] = _official_urls(raw)
    return item


def rank_value(award):
    """Dollars used to order the day and to lead the caption.

    The greater of obligation and ceiling, which is the server's
    value_desc key. The caption labels a ceiling as "valued at up to"
    and an obligation as "obligated".
    """
    amounts = []
    for key in ("obligation", "ceiling"):
        number = award.get(key)
        if isinstance(number, (int, float)) and not isinstance(number, bool) and number > 0:
            amounts.append(float(number))
    return max(amounts) if amounts else None


def _eligible(award, seen):
    if not award or not award.get("is_new_award"):
        return False
    keys = identity_keys(award)
    # No page id, no PIID fallback, and no generated id: nothing to log,
    # so a later run could not tell this row from the next one.
    if not keys or keys & set(seen or ()):
        return False
    if not award.get("slug") or not award.get("recipient_name") or not award.get("awarding_agency"):
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
        keys = identity_keys(award)
        if not keys or keys & taken:
            continue
        taken.update(keys)
        pool.append(award)
    pool.sort(key=lambda award: (-rank_value(award), dedupe_key(award)))
    return pool[:limit], pool[limit:]


def fetch_feed(since=None, limit=FETCH_LIMIT, get=None, token=None, base=None):
    """One page, oldest first, so the response can carry next_since.

    404 and a missing token return an empty unavailable page: do not
    queue and do not move the cursor. 401 (bad token), 422 (bad since),
    a 5xx, or a body that is not the documented shape raises FeedError.
    """
    if token is None:
        token = os.environ.get("PUBLIC_AWARDS_FEED_TOKEN")
    if not token:
        print("[awards] PUBLIC_AWARDS_FEED_TOKEN is not set — nothing to queue")
        return {"items": [], "next_since": None, "unavailable": True,
                "status": None, "reason": "not_configured"}
    getter = get or requests.get
    capped = max(1, min(int(limit), FEED_LIMIT_MAX))
    # recent, not value_desc. value_desc leaves next_since null, so a
    # cursor cannot be advanced from it. Largest-first happens after
    # this page is in hand.
    params = {"limit": str(capped), "order": "recent"}
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
    if status == 401:
        raise FeedError(
            "award feed rejected the token (401). "
            "Check PUBLIC_AWARDS_FEED_TOKEN.")
    if status == 422:
        raise FeedError(
            "award feed rejected since (422). The stored cursor is not "
            "a value this feed accepts.")
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
            found.update(identity_keys(row))
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
    """The fields render_card draws. Facts only, and short enough to fit.

    The eyebrow is TASK ORDER or CONTRACT AWARD. The chip leads with the
    same dollars as the caption: "Up to $…" for a ceiling, "$… obligated"
    for an obligation. render_card uppercases the eyebrow.
    """
    amount, _kind = captions._lead_amount(award)
    stat = captions.lead_stat(award)
    if short:
        headline = captions.format_dollars(amount) if amount is not None else ""
        body = _clip(
            f"{award['recipient_name']}. {award['awarding_agency']}.", 120)
    else:
        headline = _clip(award["recipient_name"], 80)
        summary = captions.clean_summary(award.get("summary"))
        body = award["awarding_agency"]
        if summary:
            body = f"{body}. {summary}"
        body = _clip(body, 180)
    feature = "Task order" if award.get("is_task_order") else "Contract award"
    return {
        "id": f"award-{award['slug']}",
        "feature": feature,
        "headline": headline,
        "body": body,
        "stat": stat,
        "verification": {
            "status": "VERIFIED",
            "source_type": "public_awards_feed",
            "source_ref": dedupe_key(award),
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


def _log_row(award, **extra):
    """Identity fields every posted or skipped line has to carry.

    `id` is the page id. `generated_unique_award_id` may be empty on an
    FPDS-first row and is still written so a legacy reader can see it.
    `fallback_key` is PIID + parent + agency.
    """
    row = {
        "id": award.get("id") or "",
        "dedupe_key": dedupe_key(award),
        "fallback_key": fallback_key(award),
        "generated_unique_award_id": award.get("generated_unique_award_id") or "",
        "piid": award.get("piid") or "",
        "parent_award_piid": award.get("parent_award_piid") or "",
        "awarding_agency": award.get("awarding_agency") or "",
        "slug": award.get("slug") or "",
    }
    row.update(extra)
    return row


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
        label = dedupe_key(award) or award.get("slug")
        if built is None:
            print(f"[awards] skip {label}: "
                  f"caption is not grounded or breaks a content rule")
            if not dry_run:
                _append_jsonl(state["skipped"], _log_row(
                    award, reason="caption", at=_now()))
            continue
        decorated = mentions.decorate(award, built["text"], root,
                                      dry_run=dry_run)
        rel = f"assets/cards/award_{award['slug']}.png"
        alt = f"{award['recipient_name']} — {card_topic(award)['stat']}"
        if not dry_run:
            out = os.path.join(root, rel)
            topic = render_award(award, brand, out)
            if topic is None:
                print(f"[awards] {label}: "
                      f"card would not render — leaving it for the next run")
                blocked = True
                continue
        posts.append(_log_row(
            award,
            is_task_order=bool(award.get("is_task_order")),
            contract_vehicle=award.get("contract_vehicle") or "",
            action_date=award.get("action_date") or "",
            first_seen_at=award.get("first_seen_at") or "",
            first_published_at=award.get("first_published_at") or "",
            recipient_name=award["recipient_name"],
            recipient_uei=award.get("recipient_uei") or "",
            obligation=award.get("obligation"),
            ceiling=award.get("ceiling"),
            naics_code=award.get("naics_code") or "",
            naics_description=award.get("naics_description") or "",
            summary=award.get("summary") or "",
            set_aside=award.get("set_aside") or "",
            extent_competed=award.get("extent_competed") or "",
            offers_received=captions._offers_received(award),
            offers_review=captions.offers_review(award),
            text_linkedin=decorated["text"],
            text_linkedin_plain=decorated["plain"],
            mention_review=decorated["review"],
            mention={
                "company": decorated["company"],
                "executives": decorated["executives"],
                "people": decorated["people"],
            },
            copy_varied=built["varied"],
            media_linkedin=rel,
            url=captions.award_page_url(award),
            alt=alt,
        ))
    return posts, blocked


def _print_posts(posts, deferred, page):
    if page.get("unavailable"):
        print(f"[awards] feed unavailable ({page.get('reason')}). "
              f"No posts.")
        return
    print(f"[awards] {len(posts)} post(s); {len(deferred)} eligible "
          f"left for a later day.")
    for index, post in enumerate(posts, 1):
        print(f"\n--- {index} {post['recipient_name']} ---")
        if post.get("mention_review"):
            print(post["mention_review"])
            print()
        if post.get("offers_review"):
            print(post["offers_review"])
            print()
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
    queued_ids = set()
    for post in posts:
        queued_ids.update(identity_keys(post))
    rest, still_deferred = select_awards(page["items"], seen_ids(root))
    held = [award for award in rest
            if not (identity_keys(award) & queued_ids)]
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
    except FileNotFoundError:
        # Name the awards file. The shared gate says "pending.json", which
        # is the daily queue, and that sent the 2026-11-16 log looking at
        # the wrong path.
        print(f"[awards] REFUSED: {state['pending']} is missing, so there "
              f"is nothing to approve. Publish reads the queue prepare "
              f"pushed to the branch. Nothing was posted.")
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
        if identity_keys(post) & already:
            print(f"[awards] {post['slug']} is already in the dedupe log "
                  f"— not posting it again")
            posted.append(dedupe_key(post))
            continue
        prepared = mentions.finalize(post, root)
        text = prepared["text"]
        plain = prepared["plain"] or text
        if text != (post.get("text_linkedin") or ""):
            print(f"[awards] {post.get('slug')}: mentions updated from "
                  f"content/linkedin_mentions.json before posting")
        try:
            outcome = poster(post["media_linkedin"], text, post.get("alt"))
        except Exception as exc:
            if text != plain and mentions.tagging_failure(exc):
                print(f"[awards] tagging failed for {post.get('slug')} "
                      f"({type(exc).__name__}: {exc}); "
                      f"posting the plain caption")
                try:
                    outcome = poster(post["media_linkedin"], plain,
                                     post.get("alt"))
                except Exception as exc2:
                    failed = f"{type(exc2).__name__}: {exc2}"
                    print(f"[awards] FAILED {dedupe_key(post)}: {failed}")
                    break
            else:
                failed = f"{type(exc).__name__}: {exc}"
                print(f"[awards] FAILED {dedupe_key(post)}: {failed}")
                break
        extra = outcome if isinstance(outcome, dict) else {"id": outcome}
        _append_jsonl(state["posted"], _log_row(
            post,
            linkedin_id=extra.get("id"),
            url=extra.get("url"),
            posted_at=_now(),
        ))
        posted.append(dedupe_key(post))
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
