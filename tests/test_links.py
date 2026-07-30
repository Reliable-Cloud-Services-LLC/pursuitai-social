"""W2 — UTM attribution on every clickable link.

Scope decisions locked here (see SETUP.md "Attribution"):
  * X caption CTA  -> UTM'd trial URL. Clickable, so it is tagged.
  * IG caption     -> bare readable domain. IG captions do not hyperlink,
                      so a 100-character UTM string there is unclickable
                      noise; the tagged link lives in the profile bio.
  * Rendered media -> bare domain. A UTM baked into pixels is unclickable.

X counts every URL as exactly 23 characters regardless of real length
(docs.x.com, "Counting characters"), so tagging must not shrink the body.
"""
import json
import os
import sys
from urllib.parse import parse_qs, urlparse

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import captions  # noqa: E402
import links     # noqa: E402

FORMATS = ["card", "screenshot", "ad"]
UTM_KEYS = {"utm_source", "utm_medium", "utm_campaign", "utm_content"}


@pytest.fixture(scope="module")
def cal():
    with open(os.path.join(ROOT, "content", "calendar.json")) as f:
        return json.load(f)


def params(url):
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


# ---------- build_url ----------

def test_all_four_utm_params_present(cal):
    for topic in cal["topics"]:
        for fmt in FORMATS:
            q = params(links.build_url(cal["brand"]["trial_url"], "x",
                                       topic["id"], fmt))
            assert UTM_KEYS <= set(q), f"{topic['id']}/{fmt} missing {UTM_KEYS - set(q)}"
            assert q["utm_source"] == "x"
            assert q["utm_medium"] == "organic"
            assert q["utm_campaign"] == topic["id"]
            assert q["utm_content"] == fmt


def test_merges_into_existing_query_string(cal):
    """trial_url already carries ?action=register — it must survive."""
    base = cal["brand"]["trial_url"]
    assert "action=register" in base, "fixture assumption"
    url = links.build_url(base, "x", "fit-scoring", "card")
    q = params(url)
    assert q["action"] == "register"
    assert UTM_KEYS <= set(q)
    assert "??" not in url and "&&" not in url
    assert url.count("?") == 1


def test_preserves_path_and_host(cal):
    parsed = urlparse(links.build_url(cal["brand"]["trial_url"], "x", "t", "card"))
    assert parsed.scheme == "https"
    assert parsed.netloc == "pursuitai.net"
    assert parsed.path == "/app"


def test_params_are_url_encoded():
    url = links.build_url("https://pursuitai.net", "x", "a topic&x", "card")
    assert " " not in url
    q = params(url)
    assert q["utm_campaign"] == "a topic&x", "must round-trip, not corrupt"


def test_does_not_duplicate_params_when_applied_twice():
    once = links.build_url("https://pursuitai.net", "x", "fit-scoring", "card")
    twice = links.build_url(once, "x", "fit-scoring", "card")
    assert once == twice


# ---------- X character weighting ----------

def test_url_counts_as_23_regardless_of_length():
    short, long_ = "https://a.co", "https://pursuitai.net/app?" + "x" * 300
    assert links.x_weighted_length("hi " + short, [short]) == 3 + 23
    assert links.x_weighted_length("hi " + long_, [long_]) == 3 + 23


# ---------- X captions ----------

def x_thread(topic, brand, fmt):
    """Everything one X post puts in front of a reader.

    W4 moved the link out of the body into a threaded reply, so the W2
    contract — every generated caption carries all four utm_ params — now
    holds across the pair rather than a single string.
    """
    return (captions.build_x(topic, brand, fmt=fmt, fresh=False) + "\n"
            + captions.build_x_reply(topic, brand, fmt))


def test_x_thread_carries_all_utm_params(cal):
    for topic in cal["topics"]:
        for fmt in FORMATS:
            text = x_thread(topic, cal["brand"], fmt)
            for key in UTM_KEYS:
                assert key in text, f"{topic['id']}/{fmt} thread missing {key}"
            assert f"utm_campaign={topic['id']}" in text
            assert f"utm_content={fmt}" in text


def test_cta_points_at_the_landing_page_not_the_app(cal):
    """pursuitai.net/app opens the sign-in screen, which shows a first-time
    visitor none of the marketing they just clicked for. Every CTA must
    land on the public page."""
    for topic in cal["topics"]:
        text = x_thread(topic, cal["brand"], "card")
        url = next(w for w in text.split() if w.startswith("http"))
        parsed = urlparse(url)
        assert parsed.netloc == "pursuitai.net"
        assert parsed.path == "/", f"{topic['id']} links to {parsed.path}"
        assert "/app" not in url


def test_empty_path_is_normalised_to_slash():
    url = links.build_url("https://pursuitai.net", "x", "fit-scoring", "card")
    assert url.startswith("https://pursuitai.net/?"), url


def test_each_part_of_the_thread_is_within_the_weighted_limit(cal):
    for topic in cal["topics"]:
        for fmt in FORMATS:
            body = captions.build_x(topic, cal["brand"], fmt=fmt, fresh=False)
            reply = captions.build_x_reply(topic, cal["brand"], fmt)
            assert len(body) <= 280, f"{topic['id']}/{fmt} body too long"
            url = [w for w in reply.split() if w.startswith("http")]
            assert len(url) == 1, "exactly one link, in the reply"
            weighted = links.x_weighted_length(reply, url)
            assert weighted <= 280, f"{topic['id']}/{fmt} reply {weighted}"


def test_tagging_does_not_shrink_the_body(cal):
    """The old budget used the literal URL length, so a ~100-char tagged URL
    would silently truncate ~80 characters of copy off every tweet."""
    brand = dict(cal["brand"])
    short = dict(brand, trial_url="https://p.co")
    long_ = dict(brand, trial_url="https://pursuitai.net/app?action=register&"
                                  + "pad=" + "x" * 200)
    for topic in cal["topics"]:
        body_short = captions.build_x(topic, short, fmt="card",
                                      fresh=False).split("\n\n")[0]
        body_long = captions.build_x(topic, long_, fmt="card",
                                     fresh=False).split("\n\n")[0]
        assert body_short == body_long, f"{topic['id']} body varied with URL length"


def test_thread_still_has_cta_and_tags(cal):
    """Guards the copy contract the pre-W2 suite asserted. W4 split it: the
    hashtags stay on the body, the trial CTA moved to the reply."""
    for topic in cal["topics"]:
        body = captions.build_x(topic, cal["brand"], fmt="card", fresh=False)
        reply = captions.build_x_reply(topic, cal["brand"], "card")
        assert "#GovCon" in body
        assert "pursuitai.net" in reply
        assert "14-day" in reply


def test_build_x_requires_an_explicit_format(cal):
    """No default: a silent fallback would mislabel every utm_content."""
    with pytest.raises(TypeError):
        captions.build_x(cal["topics"][0], cal["brand"], fresh=False)


# ---------- Instagram keeps the bare domain ----------

def test_ig_caption_has_no_utm_string(cal):
    """IG captions are not hyperlinked; a UTM there is unclickable noise."""
    for topic in cal["topics"]:
        text = captions.build_ig(topic, cal["brand"], fresh=False)
        assert "utm_" not in text
        assert "pursuitai.net" in text


def test_bio_url_is_tagged_for_instagram(cal):
    """The IG attribution point is the profile bio link, built here so the
    value in SETUP.md cannot drift from the code."""
    url = links.bio_url(cal["brand"]["url"], "instagram")
    q = params(url)
    assert q["utm_source"] == "instagram"
    assert q["utm_medium"] == "organic"
    assert q["utm_campaign"] == "bio"
    assert "utm_content" not in q, "a bio link is not per-post"
    assert urlparse(url).path == "/" and "/app" not in url
