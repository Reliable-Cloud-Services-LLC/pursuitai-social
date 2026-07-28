"""UTM tagging for every clickable link the engine emits.

Without these the admin dashboard cannot separate a visit driven by a post
from any other direct traffic, so nothing downstream — which topics land,
which formats convert — is answerable.

Where tags are deliberately NOT applied:

  * Rendered media (cards, video, screenshot footers) shows the bare
    domain. A UTM string baked into pixels is unclickable and ugly.
  * Instagram captions show the bare domain. IG captions are not
    hyperlinked, so a ~100-character tagged URL is something a human would
    have to retype. Instagram's attribution point is the profile bio link,
    which is what bio_url() builds — see SETUP.md.

Standard library only.
"""
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

MEDIUM = "organic"

# X wraps every link in t.co and counts it as exactly 23 characters,
# whatever its real length or protocol.
# https://docs.x.com/resources/fundamentals/counting-characters
X_URL_WEIGHT = 23


def _apply(base, extra):
    """Merge `extra` into base's query string.

    A base may already carry parameters, so these are merged rather than
    appended blindly, and applying twice is a no-op. An empty path is
    normalised to "/" — "https://pursuitai.net?utm_source=x" is legal but
    non-canonical, and the canonical form is what analytics should see.
    """
    parts = urlparse(base)
    if not parts.path:
        parts = parts._replace(path="/")
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update(extra)
    return urlunparse(parts._replace(query=urlencode(query)))


def build_url(base, platform, topic_id, fmt):
    """Tag a per-post link.

    `base` is the public landing page, not the app: the app URL opens the
    sign-in screen, which shows a first-time visitor none of the marketing
    they just clicked through for.
    """
    return _apply(base, {
        "utm_source": platform,
        "utm_medium": MEDIUM,
        "utm_campaign": topic_id,
        "utm_content": fmt,
    })


def bio_url(base, platform="instagram"):
    """The link that goes in a profile bio. Not per-post, so no format."""
    return _apply(base, {"utm_source": platform, "utm_medium": MEDIUM,
                         "utm_campaign": "bio"})


def x_weighted_length(text, urls=()):
    """Length of `text` as X counts it: each URL weighs 23, not its length.

    Budgeting on the literal length would silently truncate ~80 characters
    of copy off every tweet once the URL carries UTM parameters.
    """
    n = len(text)
    for u in urls:
        n += X_URL_WEIGHT - len(u)
    return n


if __name__ == "__main__":
    import json
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "content", "calendar.json")) as f:
        brand = json.load(f)["brand"]
    print("Instagram bio link:\n  " + bio_url(brand["url"], "instagram"))
    print("\nX bio link (profile Website field):\n  "
          + bio_url(brand["url"], "x"))
    print("\nExample X post link:\n  "
          + build_url(brand["url"], "x", "fit-scoring", "card"))
