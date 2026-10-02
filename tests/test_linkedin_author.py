"""Who a LinkedIn post is authored by.

Posting as the Pursuit AI Page needs `w_organization_social`, which only the
vetted Community Management product grants — that request was denied and is
under appeal. The self-serve route grants `w_member_social`, which authors as
a person. So today's posts come from a profile, and LINKEDIN_ORG_ID is the
switch that moves them to the Page the moment that access lands.

Verified live 2026-10-02 against a real w_member_social token: the versioned
/rest Images API accepts a person-owned upload AND lets that token read the
status back. (LinkedIn's Images doc claims `w_member_social` "would be unable
to perform a GET call for rest/images". It did. Observed behaviour wins, and
the discrepancy is recorded rather than designed around.)
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import post_linkedin as li  # noqa: E402

SUB = "S8tpf7RIhR"
PERSON = f"urn:li:person:{SUB}"


class FakeUserinfo:
    def __init__(self, payload):
        self.payload, self.calls = payload, 0

    def get(self, url, headers=None, timeout=None):
        self.calls += 1
        outer = self

        class R:
            ok, status_code, text, headers = True, 200, "", {}

            def json(self):
                return outer.payload
        return R()


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    """The person URN is cached per process, so it must not leak between
    tests — a cached URN would make the org case pass for the wrong reason."""
    monkeypatch.setattr(li, "_PERSON_URN", None)
    monkeypatch.delenv("LINKEDIN_ORG_ID", raising=False)


def test_the_page_is_used_when_we_have_page_access(monkeypatch):
    monkeypatch.setenv("LINKEDIN_ORG_ID", "109876")
    fake = FakeUserinfo({"sub": SUB})
    monkeypatch.setattr(li, "requests", fake)
    assert li.author_urn("tok") == "urn:li:organization:109876"
    assert fake.calls == 0, \
        "with a Page configured there is nothing to look up"


def test_it_falls_back_to_the_member_profile(monkeypatch):
    monkeypatch.setattr(li, "requests", FakeUserinfo({"sub": SUB}))
    assert li.author_urn("tok") == PERSON


def test_the_member_case_announces_itself(monkeypatch, capsys):
    """Negative control on silence. Posting to a profile when someone meant
    the Page is not something to discover from the feed."""
    monkeypatch.setattr(li, "requests", FakeUserinfo({"sub": SUB}))
    li.author_urn("tok")
    out = capsys.readouterr().out
    assert PERSON in out
    assert "NOT the Pursuit AI Page" in out


@pytest.mark.parametrize("payload", [{}, {"sub": ""}, {"name": "Aqeel"}])
def test_a_token_without_profile_scope_fails_legibly(monkeypatch, payload):
    """Returning None would build "urn:li:person:None" and surface as an
    opaque 400 from LinkedIn three calls later."""
    monkeypatch.setattr(li, "requests", FakeUserinfo(payload))
    with pytest.raises(li.LinkedInError, match="profile"):
        li.author_urn("tok")


def test_the_lookup_happens_once(monkeypatch):
    fake = FakeUserinfo({"sub": SUB})
    monkeypatch.setattr(li, "requests", fake)
    li.author_urn("tok")
    li.author_urn("tok")
    assert fake.calls == 1


# ---------- the invariant that costs a 403 ----------

@pytest.mark.parametrize("org,expected", [
    (None, PERSON),
    ("109876", "urn:li:organization:109876"),
], ids=["member", "page"])
def test_the_asset_owner_matches_the_post_author(monkeypatch, org, expected):
    """"For images with member URN owners, the caller needs to match the
    image owner." Uploading as one URN and authoring as another is a 403 at
    publish — AFTER the upload has been spent."""
    if org:
        monkeypatch.setenv("LINKEDIN_ORG_ID", org)
    monkeypatch.setattr(li, "requests", FakeUserinfo({"sub": SUB}))
    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", "tok")

    seen = {}
    monkeypatch.setattr(li, "upload_image",
                        lambda rel, tok, owner: seen.update(owner=owner)
                        or "urn:li:image:X")

    class Posted:
        ok, status_code, text = True, 201, ""
        headers = {"x-restli-id": "urn:li:share:1"}

        def json(self):
            return {}

    def fake_post(url, headers=None, data=None, timeout=None):
        import json as _j
        seen["author"] = _j.loads(data)["author"]
        return Posted()

    monkeypatch.setattr(li.requests, "post", fake_post, raising=False)
    li.post_image("assets/linkedin/x.png", "copy")
    assert seen["owner"] == seen["author"] == expected


def test_video_authors_the_same_way(monkeypatch):
    """The ad format must not drift from the card format on authorship."""
    monkeypatch.setattr(li, "requests", FakeUserinfo({"sub": SUB}))
    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", "tok")
    seen = {}
    monkeypatch.setattr(li, "upload_video",
                        lambda rel, tok, owner: seen.update(owner=owner)
                        or "urn:li:video:X")

    class Posted:
        ok, status_code, text = True, 201, ""
        headers = {"x-restli-id": "urn:li:share:1"}

        def json(self):
            return {}

    def fake_post(url, headers=None, data=None, timeout=None):
        import json as _j
        seen["author"] = _j.loads(data)["author"]
        return Posted()

    monkeypatch.setattr(li.requests, "post", fake_post, raising=False)
    li.post_video("assets/video/x_ad.mp4", "copy")
    assert seen["owner"] == seen["author"] == PERSON


# ---------- the bug that was latent since August ----------

def test_the_image_status_url_encodes_the_urn():
    """`Syntax exception in path variables` (400), observed live 2026-10-02.

    We send X-Restli-Protocol-Version: 2.0.0, which requires path keys be
    encoded; an unencoded urn:li:image:... is parsed as path variables and
    rejected. This shipped in post_image in August and was never exercised
    — the doc recorded `--check-app` as "validated", which does not touch
    this path. It would have failed the first real post on EITHER route.
    """
    src = open(os.path.join(ROOT, "engine", "post_linkedin.py")).read()
    block = src[src.index("def _await_available("):]
    block = block[:block.index("\ndef ")]
    assert "quote(image_urn" in block
    assert "{API}/images/{image_urn}" not in block
