"""Caption builder: turns a calendar topic into platform-ready copy.

If ANTHROPIC_API_KEY is set, asks Claude for a fresh variant so posts never
repeat verbatim; otherwise uses the hand-written hooks in calendar.json.
Every caption is validated (X <= 280 chars) and always carries the trial CTA.
"""
import os
import json
import random
import re

import links

X_LIMIT = 280
SAFETY_MARGIN = 5

_variant_warned = False


def _claude_variant(topic, platform, brand):
    """Optional: fresh copy via the Claude API. Falls back silently."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        # Say so. Without this the feed ran on template copy for 90 posts
        # and nothing anywhere recorded that the variation was switched
        # off — the same topic shipped byte-identical words six times and
        # the only evidence was comparing the log against the calendar by
        # hand. Once per process: three captions are built per run and one
        # notice is a signal where three is noise.
        global _variant_warned
        if not _variant_warned:
            _variant_warned = True
            print("[captions] ANTHROPIC_API_KEY not set — every caption "
                  "will be the hand-written template, so a topic reads "
                  "identically each time it comes round. Set the secret "
                  "to vary the copy.")
        return None
    try:
        import requests
        base = topic["hook_x"] if platform == "x" else topic["hook_ig"]
        limit = ("Hard limit 230 characters, no hashtags, no links."
                 if platform == "x" else
                 "3 short paragraphs max, no hashtags, no links.")
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={
                "model": "claude-haiku-5-5",
                "max_tokens": 400,
                "messages": [{"role": "user", "content":
                    f"Rewrite this {('tweet' if platform == 'x' else 'Instagram caption')} "
                    f"for PursuitAI (GovCon capture platform for 8(a)/SDVOSB/WOSB/HUBZone "
                    f"small businesses). Keep every factual claim EXACTLY as stated - do not "
                    f"invent numbers or capabilities. Punchy, confident, practitioner voice. "
                    f"{limit}\n\nOriginal:\n{base}\n\nReply with only the rewritten text."}],
            }, timeout=60)
        r.raise_for_status()
        # `content` is a list of BLOCKS, and the first is not guaranteed
        # to be the text one — a model may emit a thinking block ahead of
        # it. The old code indexed [0] blindly and raised KeyError('text'),
        # which the except below swallowed into a silent template
        # fallback: on the first live run two of three captions died this
        # way while the third happened to get text first and varied fine.
        txt = next((b.get("text") or "" for b in r.json().get("content") or []
                    if b.get("type") == "text" and b.get("text")), "")
        return txt.strip().strip('"') or None
    except Exception as e:
        print(f"[captions] Claude variant failed, using template: {e}")
        return None

def build_x(topic, brand, *, fmt, fresh=True):
    """The tweet body — deliberately carries no link.

    A post containing a URL is demoted in distribution, so the CTA link
    ships as a threaded reply instead (see build_x_reply). Dropping the
    CTA block also returns its ~62 characters to the copy, which is what
    makes the body budget workable.

    `fmt` is required even though the body no longer contains the tagged
    URL: it keeps the body and its reply built from one signature, so a
    caller cannot produce a body without knowing what reply pairs with it.
    """
    body = (_claude_variant(topic, "x", brand) if fresh else None) or topic["hook_x"]
    tags = " ".join(brand["hashtags_x"][:2])
    suffix = f"\n\n{tags}"
    budget = X_LIMIT - len(suffix) - SAFETY_MARGIN
    if len(body) > budget:
        body = body[:budget].rsplit(" ", 1)[0].rstrip(".,;") + "…"
    return body + suffix


def build_x_reply(topic, brand, fmt):
    """The CTA, posted as a reply to the body so the body stays link-free.

    X counts any URL as 23 characters whatever its real length, so the
    tagged URL costs the same here as a bare one would.
    """
    url = links.build_url(brand["url"], "x", topic["id"], fmt)
    return f"Free 14-day trial — no credit card, set up in under 2 minutes:\n{url}"

IG_TAG_COUNT = 8

def _ig_hashtags(brand):
    """Primary tag first, then a random sample of the rest.

    The first entry in hashtags_ig is the category's primary tag and always
    ships — sampling all 8 from 10 could drop it entirely. Mirrors build_x,
    which already takes hashtags_x[:2] deterministically.
    """
    primary, rest = brand["hashtags_ig"][0], brand["hashtags_ig"][1:]
    k = min(IG_TAG_COUNT - 1, len(rest))
    return " ".join([primary] + random.sample(rest, k=k))

# linkedin.com/help/linkedin/answer/a528176 — a post is capped at 3000
# characters. Our longest hook is well under, so nothing truncates.
LI_LIMIT = 3000
LI_HASHTAGS = 3


def build_linkedin(topic, brand, *, fmt, fresh=True):
    """LinkedIn copy for the manual-assisted path.

    Three deliberate differences from the other channels:

      * The link goes in the BODY. LinkedIn hyperlinks a URL in a post, so
        unlike Instagram there is no reason to hide it in a bio.
      * Three hashtags, not Instagram's eight. Eight reads as spam to a
        professional audience.
      * The longer Instagram hook is the base, since LinkedIn allows 3000
        characters and rewards substance over a one-liner.

    `fmt` is required, same as build_x — it becomes utm_content.
    """
    body = (_claude_variant(topic, "ig", brand) if fresh else None) or topic["hook_ig"]
    url = links.build_url(brand["url"], "linkedin", topic["id"], fmt)
    tags = " ".join([brand["hashtags_ig"][0]]
                    + random.sample(brand["hashtags_ig"][1:], k=LI_HASHTAGS - 1))
    text = (f"{body}\n\n"
            f"Start a free 14-day trial — no credit card, set up in under "
            f"two minutes:\n{url}\n\n{tags}")
    if len(text) > LI_LIMIT:
        keep = LI_LIMIT - (len(text) - len(body)) - 1
        body = body[:keep].rsplit(" ", 1)[0].rstrip(".,;") + "…"
        text = (f"{body}\n\n"
                f"Start a free 14-day trial — no credit card, set up in under "
                f"two minutes:\n{url}\n\n{tags}")
    return text


def build_ig(topic, brand, fresh=True):
    body = (_claude_variant(topic, "ig", brand) if fresh else None) or topic["hook_ig"]
    tags = _ig_hashtags(brand)
    return (f"{body}\n\n"
            f"Start your free 14-day trial — link in bio or {brand['url'].replace('https://', '')}\n"
            f"No credit card. Set up in under 2 minutes.\n\n{tags}")


# --- Public award posts ----------------------------------------------------
# A caption here is a news post, not a product hook. Every figure has to
# come from the feed record: Claude may tighten wording and may not add a
# number, a name, or a link. The award URL is appended afterwards so a
# rewrite cannot swap it for someone else's article.

_MONEY_CLAIM = re.compile(
    r"\$\s*\d[\d,]*(?:\.\d+)?(?:\s*(?:billion|million|thousand|[bmk]))?"
    r"|\d[\d,]*(?:\.\d+)?\s*(?:billion|million|thousand)\b",
    re.I)
_URL = re.compile(r"https?://\S+")
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
_MONEY_SUFFIX = (
    ("billion", 1_000_000_000),
    ("million", 1_000_000),
    ("thousand", 1_000),
)


def format_dollars(amount):
    """A dollar figure that round-trips to `amount` within a dollar.

    "$4.2 million" is used when that phrase parses back to the same
    dollars. Otherwise the exact figure is written with commas, so a
    caption never rounds $1,234,567 into $1.23 million and then calls
    the rounded number a fact.
    """
    amount = float(amount)
    if amount >= 1_000_000:
        for unit, word in ((1_000_000_000, "billion"), (1_000_000, "million")):
            if amount < unit:
                continue
            scaled = amount / unit
            text = f"{scaled:.2f}".rstrip("0").rstrip(".")
            if abs(float(text) * unit - amount) <= 1:
                return f"${text} {word}"
    if amount == int(amount):
        return f"${amount:,.0f}"
    return f"${amount:,.2f}"


def _strip_urls(text):
    return _URL.sub(" ", text or "")


def _strip_decorations(text):
    """Drop URLs and lettered hashtags before looking for figures.

    `#8aCertified` is a tag, not a claim that something is eight. A bare
    `#8` is still a number and is left in place for the check.
    """
    text = _strip_urls(text)
    return re.sub(r"#(?=\w*[A-Za-z])\w+", " ", text)


def clean_summary(text):
    """The feed's description of the work, with any URL removed.

    A summary that pastes a news link would put that outlet in our
    caption. The words can stay; the link cannot.
    """
    text = _strip_urls(text)
    return re.sub(r"\s+", " ", text).strip(" \t\r\n-–—")


def _money_phrases(text):
    """Dollar amounts parsed out of `text`, in dollars."""
    found = []
    for match in _MONEY_CLAIM.finditer(_strip_decorations(text)):
        raw = match.group(0)
        suffix = 1
        lowered = raw.lower()
        for word, mult in _MONEY_SUFFIX:
            if re.search(rf"\b{word}\b", lowered):
                suffix = mult
                break
        else:
            letter = re.search(r"\d\s*([bmk])\b", lowered)
            if letter:
                suffix = {"b": 1_000_000_000, "m": 1_000_000,
                          "k": 1_000}[letter.group(1)]
        number = _NUM.search(raw).group(0).replace(",", "")
        found.append(float(number) * suffix)
    return found


def _amount(value):
    if isinstance(value, bool) or value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        return None
    if number != number or number <= 0:  # NaN or nothing to report
        return None
    return number


def _allowed_amounts(award):
    amounts = []
    for key in ("obligation", "ceiling"):
        number = _amount(award.get(key))
        if number is not None:
            amounts.append(number)
    for key in ("summary", "recipient", "agency"):
        amounts.extend(_money_phrases(str(award.get(key) or "")))
    return amounts


def _plain_tokens(award):
    """Whole numbers the feed record itself contains.

    Matched as whole tokens, not substrings: "4" is not allowed merely
    because the obligation is 4200000.
    """
    tokens = set()
    for key in ("recipient", "agency", "piid", "naics", "summary", "slug",
                "award_type_code", "generated_unique_award_id"):
        for match in _NUM.findall(str(award.get(key) or "")):
            tokens.add(match.replace(",", ""))
    for key in ("obligation", "ceiling"):
        number = _amount(award.get(key))
        if number is None:
            continue
        if number == int(number):
            tokens.add(str(int(number)))
        tokens.add(format(number, "f").rstrip("0").rstrip("."))
        for match in _NUM.findall(format_dollars(number)):
            tokens.add(match.replace(",", ""))
    return tokens


def ungrounded_money(text, award):
    """Dollar figures in `text` that are not the obligation, the ceiling,
    or a dollar figure already written in the feed's own text."""
    allowed = _allowed_amounts(award)
    bad = []
    for found in _money_phrases(text):
        if not any(abs(found - number) <= 1.0 for number in allowed):
            bad.append(found)
    return bad


def ungrounded_numbers(text, award):
    """Non-money numbers that do not appear in the feed record.

    Dollar phrases are excluded; those are checked by ungrounded_money.
    URLs are excluded; the award link is built by us and carries the slug.
    """
    stripped = _MONEY_CLAIM.sub(" ", _strip_decorations(text))
    allowed = _plain_tokens(award)
    bad = []
    for match in _NUM.finditer(stripped):
        token = match.group(0).replace(",", "")
        if token not in allowed:
            bad.append(match.group(0))
    return bad


def _award_topic(award):
    """A topic-shaped record so the existing compliance gate can run.

    VERIFIED because the figures are the feed's, traced to
    generated_unique_award_id. That status is what lets a real dollar
    amount through check_claims. It does not excuse a number the feed
    never supplied — ungrounded_money is the check for that, and it runs
    even when this topic is VERIFIED.
    """
    return {
        "id": f"award-{award.get('slug') or 'unknown'}",
        "verification": {
            "status": "VERIFIED",
            "source_type": "public_awards_feed",
            "source_ref": award.get("generated_unique_award_id") or "",
        },
    }


def _lead_amount(award):
    """The figure the post leads with: obligated if we have it, else ceiling.

    Ceiling is the potential value of the contract. Leading with it when
    an obligated amount is on the record overstates what was awarded.
    """
    obligation = _amount(award.get("obligation"))
    if obligation is not None:
        return obligation, "obligated"
    return _amount(award.get("ceiling")), "ceiling"


def award_facts_body(award, *, include_summary=True):
    """Template copy. Every sentence is a field from the feed, labeled."""
    amount, kind = _lead_amount(award)
    lines = [f"{award['recipient']} was awarded work by {award['agency']}."]
    if kind == "obligated":
        lines.append(f"The obligated amount is {format_dollars(amount)}.")
        ceiling = _amount(award.get("ceiling"))
        if ceiling is not None:
            lines.append(f"The contract ceiling is {format_dollars(ceiling)}.")
    elif kind == "ceiling":
        lines.append(
            f"The public record lists a ceiling of {format_dollars(amount)}. "
            f"It does not list a positive obligated amount, so this post "
            f"does not state one.")
    details = []
    if award.get("naics"):
        details.append(f"NAICS {award['naics']}")
    if award.get("piid"):
        details.append(f"PIID {award['piid']}")
    if details:
        lines.append(". ".join(details) + ".")
    if include_summary:
        summary = clean_summary(award.get("summary"))
        if summary:
            lines.append(summary)
    return "\n\n".join(lines)


def award_page_url(award):
    """The public award page, with the same UTM tags as every other post."""
    page = f"https://pursuitai.net/awards/{award['slug']}"
    return links.build_url(page, "linkedin", award["slug"], "card",
                           campaign="award")


def _assemble_award(body, award, brand):
    url = award_page_url(award)
    tags = " ".join(brand["hashtags_ig"][:LI_HASHTAGS])
    text = f"{body.rstrip()}\n\nAward details:\n{url}\n\n{tags}"
    if len(text) > LI_LIMIT:
        # The link and the tags stay. Only the feed-derived body gives.
        keep = LI_LIMIT - (len(text) - len(body)) - 1
        body = body[:max(keep, 0)].rsplit(" ", 1)[0].rstrip(".,;") + "…"
        text = f"{body}\n\nAward details:\n{url}\n\n{tags}"
    return text


def _award_body_ok(body, award):
    if not body or not str(body).strip():
        return False
    folded = body.casefold()
    # The caption is written from the feed. A line that names another
    # outlet is someone else's writeup, whatever else it says.
    if "orangeslices" in folded.replace(" ", ""):
        return False
    if award["recipient"].casefold() not in folded:
        return False
    if award["agency"].casefold() not in folded:
        return False
    if ungrounded_money(body, award) or ungrounded_numbers(body, award):
        return False
    if compliance_check(award, body):
        return False
    return True


def compliance_check(award, text):
    """The existing content rules, pointed at one award caption."""
    import compliance
    return compliance.check_claims(_award_topic(award), text)


def _acceptable_body(award):
    """The template, dropping the summary if that sentence cannot ship.

    The summary is the feed's description of the work, but it can still
    trip a content rule (a company name plus "won", for instance). The
    post can say who, which agency, and the dollars without it. It
    cannot say those without being grounded.
    """
    for include_summary in (True, False):
        body = award_facts_body(award, include_summary=include_summary)
        if _award_body_ok(body, award):
            return body
    return None


def build_award_linkedin(award, brand, *, fresh=True):
    """LinkedIn caption for one award.

    Returns {"text", "varied"} or None when the template itself cannot
    be posted without an invented figure or a content-rule violation.
    A Claude rewrite that fails the same checks is discarded and the
    template is what ships — the same fallback the daily captions use.
    """
    body = _acceptable_body(award)
    if body is None:
        return None
    chosen = body
    if fresh:
        rewrite = _claude_variant(
            {"hook_ig": body, "hook_x": body}, "ig", brand)
        # The rewrite is not allowed to bring its own link. Ours is
        # appended below, from the slug.
        if rewrite:
            # Drop any link the model added. The award URL is appended
            # from the slug and is the only one a post is allowed to carry.
            # Paragraph breaks stay; clean_summary is for the one-line
            # feed summary, and it would flatten the caption.
            rewrite = _URL.sub("", rewrite).strip()
        if rewrite and _award_body_ok(rewrite, award):
            chosen = rewrite
        elif rewrite:
            print("[awards] Claude caption is not grounded in the feed "
                  "record — using the template")
    return {"text": _assemble_award(chosen, award, brand),
            "varied": chosen != body}

if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(os.path.dirname(here), "content", "calendar.json")) as f:
        cal = json.load(f)
    t = cal["topics"][0]
    print("--- X ---\n" + build_x(t, cal["brand"], fmt="card", fresh=False))
    print("\n--- IG ---\n" + build_ig(t, cal["brand"], fresh=False))
