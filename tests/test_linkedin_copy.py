"""W7 — LinkedIn copy for the manual-assisted path.

We are not posting to LinkedIn via the API yet: Community Management
Standard tier requires a screencast demonstrating application users, an
OAuth consent flow, and member profile data displayed in a UI — none of
which a first-party publishing bot has. The approval gate already puts a
human in front of every post daily, so the marginal cost of pasting is
near zero.

This module produces the copy that gets pasted, and the preview sheet
renders it with a copy button.
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

# linkedin.com/help/linkedin/answer/a528176 — a post is capped at 3000 chars.
LI_LIMIT = 3000


@pytest.fixture(scope="module")
def cal():
    with open(os.path.join(ROOT, "content", "calendar.json")) as f:
        return json.load(f)


def test_every_topic_fits_the_linkedin_limit(cal):
    for t in cal["topics"]:
        text = captions.build_linkedin(t, cal["brand"], fmt="card")
        assert len(text) <= LI_LIMIT, f"{t['id']} is {len(text)} chars"


def test_link_is_tagged_for_linkedin(cal):
    for t in cal["topics"]:
        text = captions.build_linkedin(t, cal["brand"], fmt="card")
        url = next(w for w in text.split() if w.startswith("http"))
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        assert q["utm_source"] == "linkedin"
        assert q["utm_medium"] == "organic"
        assert q["utm_campaign"] == t["id"]


def test_link_is_in_the_body_not_omitted(cal):
    """Unlike Instagram, LinkedIn hyperlinks a URL in the post body, so the
    link belongs here rather than in a bio."""
    text = captions.build_linkedin(cal["topics"][0], cal["brand"], fmt="card")
    assert "https://pursuitai.net/?" in text


def test_hashtags_are_restrained(cal):
    """Instagram takes 8. Dumping 8 tags on LinkedIn reads as spam to a
    professional audience — 3, primary first."""
    for t in cal["topics"]:
        tags = [w for w in captions.build_linkedin(t, cal["brand"],
                                                   fmt="card").split()
                if w.startswith("#")]
        assert len(tags) == 3, f"{t['id']} has {len(tags)} hashtags"
        assert tags[0] == cal["brand"]["hashtags_ig"][0]


def test_no_instagram_isms(cal):
    """'link in bio' is meaningless on LinkedIn."""
    for t in cal["topics"]:
        text = captions.build_linkedin(t, cal["brand"], fmt="card").lower()
        assert "link in bio" not in text


def test_linkedin_copy_passes_compliance(cal):
    import compliance
    for t in cal["topics"]:
        if not compliance.is_publishable(t):
            continue
        text = captions.build_linkedin(t, cal["brand"], fmt="card")
        assert not compliance.check_claims(t, text), t["id"]


def test_requires_explicit_format(cal):
    with pytest.raises(TypeError):
        captions.build_linkedin(cal["topics"][0], cal["brand"])
