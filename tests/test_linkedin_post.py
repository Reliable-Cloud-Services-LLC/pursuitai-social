"""Posting to the LinkedIn Page.

Built against learn.microsoft.com, verified 2026-08-27, and NOT yet run
against the live API — the Community Management API application has not been
submitted. So these tests pin the contract as documented; they cannot prove
LinkedIn agrees. That distinction is the point of validate_linkedin.py, which
exercises the real chain the moment access lands.

Three documented facts drive most of this file:

  * "A successful response returns a 201 Created HTTP status code and the ID
    in the x-restli-id response header." Reading the id from the body gives
    None, silently, and the post log would record a success nobody can find.
  * "SYNCHRONOUS_UPLOAD is not supported in Images API" — the upload is
    async, so the image must be polled to AVAILABLE.
  * "If the post is created before confirming image upload success and the
    image upload fails to process, the post won't be visible to members."
    An invisible post is worse than a failed one: nothing reports it.
"""
import json
import os
import sys

import pytest
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import post_linkedin  # noqa: E402


class Fake:
    """Scripted LinkedIn API. Records the call order so the sequencing
    invariant — image AVAILABLE strictly before post creation — is testable
    rather than assumed."""

    def __init__(self, statuses=("AVAILABLE",), post_headers=None,
                 init_ok=True, upload_ok=True, post_ok=True):
        self.statuses = list(statuses)
        self.post_headers = ({"x-restli-id": "urn:li:share:123"}
                             if post_headers is None else post_headers)
        self.init_ok, self.upload_ok, self.post_ok = init_ok, upload_ok, post_ok
        self.calls = []

    def _resp(self, ok, payload=None, headers=None, status=200):
        outer = self

        class R:
            def __init__(self):
                self.ok = ok
                self.status_code = status if ok else 400
                self.headers = headers or {}
                self.text = "boom"

            def json(self):
                return payload or {}
        return R()

    def post(self, url, headers=None, data=None, timeout=None):
        if "initializeUpload" in url:
            self.calls.append("init")
            return self._resp(self.init_ok, {"value": {
                "uploadUrl": "https://upload.test/x",
                "image": "urn:li:image:abc"}})
        self.calls.append("post")
        return self._resp(self.post_ok, {}, self.post_headers, status=201)

    def put(self, url, headers=None, data=None, timeout=None):
        self.calls.append("put")
        return self._resp(self.upload_ok)

    def get(self, url, headers=None, timeout=None):
        self.calls.append("status")
        s = (self.statuses.pop(0) if len(self.statuses) > 1
             else self.statuses[0])
        return self._resp(True, {"status": s})


@pytest.fixture
def wired(monkeypatch, tmp_path):
    img = tmp_path / "card.png"
    Image.new("RGB", (16, 9), (20, 12, 46)).save(img)
    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", "t")
    monkeypatch.setenv("LINKEDIN_ORG_ID", "555")
    monkeypatch.setattr(post_linkedin, "_abs", lambda rel: str(img))
    monkeypatch.setattr(post_linkedin.time, "sleep", lambda s: None)
    return str(img)


def _install(monkeypatch, fake):
    monkeypatch.setattr(post_linkedin.requests, "post", fake.post)
    monkeypatch.setattr(post_linkedin.requests, "put", fake.put)
    monkeypatch.setattr(post_linkedin.requests, "get", fake.get)
    return fake


def test_a_post_returns_the_id_from_the_restli_header(wired, monkeypatch):
    f = _install(monkeypatch, Fake())
    out = post_linkedin.post_image("assets/linkedin/card.png", "hello")
    assert out["id"] == "urn:li:share:123"
    assert out["url"].endswith("urn:li:share:123/")


def test_a_missing_restli_header_is_an_error_not_a_silent_none(wired,
                                                               monkeypatch):
    """The id is in a HEADER. If that ever moves, the failure must be loud —
    logging a post with id None means nobody can find what was published."""
    _install(monkeypatch, Fake(post_headers={}))
    with pytest.raises(post_linkedin.LinkedInError) as e:
        post_linkedin.post_image("assets/linkedin/card.png", "hello")
    assert "x-restli-id" in str(e.value)


def test_the_image_is_available_before_the_post_is_created(wired, monkeypatch):
    """The documented failure: post first and the members see nothing."""
    f = _install(monkeypatch, Fake(statuses=("PROCESSING", "AVAILABLE")))
    post_linkedin.post_image("assets/linkedin/card.png", "hello")
    assert f.calls.index("post") > f.calls.index("status")
    assert f.calls == ["init", "put", "status", "status", "post"]


def test_a_failed_image_never_becomes_a_post(wired, monkeypatch):
    f = _install(monkeypatch, Fake(statuses=("PROCESSING_FAILED",)))
    with pytest.raises(post_linkedin.LinkedInError):
        post_linkedin.post_image("assets/linkedin/card.png", "hello")
    assert "post" not in f.calls


def test_an_image_stuck_processing_never_becomes_a_post(wired, monkeypatch):
    monkeypatch.setattr(post_linkedin, "IMAGE_TRIES", 2)
    f = _install(monkeypatch, Fake(statuses=("PROCESSING",)))
    with pytest.raises(post_linkedin.LinkedInError):
        post_linkedin.post_image("assets/linkedin/card.png", "hello")
    assert "post" not in f.calls


def test_the_post_body_matches_the_documented_shape(wired, monkeypatch):
    sent = {}

    class Capture(Fake):
        def post(self, url, headers=None, data=None, timeout=None):
            if "posts" in url and "initializeUpload" not in url:
                sent.update(json.loads(data), _headers=headers)
            return super().post(url, headers=headers, data=data,
                                timeout=timeout)

    _install(monkeypatch, Capture())
    post_linkedin.post_image("assets/linkedin/card.png", "hello")
    assert sent["author"] == "urn:li:organization:555"
    assert sent["lifecycleState"] == "PUBLISHED"
    assert sent["visibility"] == "PUBLIC"
    assert sent["distribution"]["feedDistribution"] == "MAIN_FEED"
    assert sent["content"]["media"]["id"] == "urn:li:image:abc"
    # "All API requests require the header X-Restli-Protocol-Version: 2.0.0",
    # and "an error response is returned when the version header is missing".
    assert sent["_headers"]["X-Restli-Protocol-Version"] == "2.0.0"
    assert sent["_headers"]["Linkedin-Version"] == post_linkedin.LINKEDIN_VERSION


def test_the_version_header_is_pinned_to_a_real_release():
    """LinkedIn "expects every versioned API call to specify a version; the
    latest version is not applied by default", and versions are supported
    "for a minimum of one (1) year" — so this is a date we must revisit, not
    a constant we can forget."""
    v = post_linkedin.LINKEDIN_VERSION
    assert len(v) == 6 and v.isdigit()
    assert 2022 <= int(v[:4]) <= 2100 and 1 <= int(v[4:]) <= 12


def test_an_unsupported_file_type_is_refused_locally(wired, monkeypatch):
    """Cheaper and clearer than a 415 after an upload has been registered."""
    _install(monkeypatch, Fake())
    with pytest.raises(post_linkedin.LinkedInError) as e:
        post_linkedin.check_image("/tmp/thing.mp4")
    assert "Images API supports" in str(e.value)


def test_token_days_left_is_none_when_unset(monkeypatch):
    monkeypatch.delenv("LINKEDIN_TOKEN_EXPIRES_AT", raising=False)
    assert post_linkedin.token_days_left() is None


def test_token_days_left_counts_down(monkeypatch):
    monkeypatch.setenv("LINKEDIN_TOKEN_EXPIRES_AT", str(1_000_000 + 86400 * 9))
    assert post_linkedin.token_days_left(now=1_000_000) == 9


def test_linkedin_is_registered_and_skippable():
    """Every channel must be skippable — the single-channel re-post path
    depends on it, and publish() asserts the same invariant at runtime."""
    import run
    assert "linkedin" in run.POSTERS
    assert run.POSTERS["linkedin"][0] == "LINKEDIN_ACCESS_TOKEN"
    import inspect
    assert "skip_linkedin" in inspect.signature(run.publish).parameters
