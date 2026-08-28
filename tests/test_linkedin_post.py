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


# --- the validator entry point ---------------------------------------------

def _run_validator(*args):
    import subprocess
    env = {k: v for k, v in os.environ.items()
           if k not in ("LINKEDIN_ACCESS_TOKEN", "LINKEDIN_ORG_ID")}
    return subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "validate_linkedin.py"),
         *args], capture_output=True, text=True, env=env)


@pytest.mark.parametrize("flag", ["--discover", "--upload", ""])
def test_the_validator_runs_and_stops_at_the_credential_check(flag):
    """Every mode reaches argument parsing and exits cleanly without
    credentials. This proves the script RUNS; it does not prove the module
    is ordered correctly — see the next test for why that needed its own."""
    r = _run_validator(*([flag] if flag else []))
    assert "Traceback" not in r.stderr, r.stderr
    assert "LINKEDIN_ACCESS_TOKEN not set" in r.stdout


def test_the_entry_point_is_the_last_statement_in_the_file():
    """Python executes a module top to bottom, so a __main__ guard placed
    mid-file calls main() before anything below it is defined. discover()
    was appended past the guard and became a NameError that fired only when
    the script was RUN with a token — imports were fine, and the run-it
    test above passes either way, because main() exits at the credential
    check long before it reaches discover().

    So this asserts the ordering itself, via the AST rather than a string
    match: the __main__ guard must be the final top-level statement.
    """
    import ast
    src = open(os.path.join(ROOT, "scripts", "validate_linkedin.py")).read()
    body = ast.parse(src).body
    last = body[-1]
    assert isinstance(last, ast.If), (
        f"last top-level statement is {type(last).__name__}, not the "
        f"__main__ guard — anything after it is undefined when main() runs")
    assert "__main__" in ast.unparse(last.test)


def test_the_org_urn_is_read_under_either_field_name():
    """LinkedIn's own samples disagree: the roleAssignee example returns the
    URN under "organization", the paginated example under "organizationTarget".
    Reading only one reports "member does NOT administer" against a Page they
    demonstrably do — a confusing failure at exactly the wrong moment."""
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import validate_linkedin as v
    assert v._orgs_in({"elements": [{"organization": "urn:li:organization:1"}]}) \
        == ["urn:li:organization:1"]
    assert v._orgs_in({"elements": [{"organizationTarget":
                                     "urn:li:organization:2"}]}) \
        == ["urn:li:organization:2"]
    assert v._orgs_in({"elements": [{"role": "ADMINISTRATOR"}]}) == []
    assert v._orgs_in({}) == []


# --- token introspection ---------------------------------------------------
#
# Storing the app's client credentials buys two things a stored expiry
# timestamp cannot provide, which is the whole reason they are worth storing:
# revocation (a revoked token keeps a FUTURE expires_at) and the scopes the
# member actually consented to.

class _Introspect:
    def __init__(self, payload, status=200):
        self.payload, self.status = payload, status
        self.seen = {}

    def __call__(self, url, data=None, headers=None, timeout=None):
        self.seen = dict(data or {})
        outer = self

        class R:
            ok = outer.status < 400
            status_code = outer.status
            text = "err"

            def json(self):
                return outer.payload
        return R()


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setenv("LINKEDIN_CLIENT_ID", "cid")
    monkeypatch.setenv("LINKEDIN_CLIENT_SECRET", "sec")
    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", "tok")


def test_introspection_sends_the_three_documented_fields(creds, monkeypatch):
    fake = _Introspect({"active": True, "status": "active",
                        "expires_at": 2_000_000, "scope": "w_organization_social"})
    monkeypatch.setattr(post_linkedin.requests, "post", fake)
    post_linkedin.introspect()
    assert set(fake.seen) == {"client_id", "client_secret", "token"}


def test_a_revoked_token_is_reported_revoked_not_healthy(creds, monkeypatch):
    """THE reason the client credentials earn their storage. A revoked token
    keeps a future expires_at, so the timestamp path calls it healthy right
    up until the post fails."""
    monkeypatch.setattr(post_linkedin.requests, "post", _Introspect(
        {"active": False, "status": "revoked",
         "expires_at": 9_000_000_000, "scope": "w_organization_social"}))
    days, status, _ = post_linkedin.token_state(now=1_000_000)
    assert status == "revoked"
    assert days > 0, "the expiry really is in the future — that is the point"


def test_scopes_come_from_the_token_not_from_what_we_meant_to_tick(creds,
                                                                   monkeypatch):
    monkeypatch.setattr(post_linkedin.requests, "post", _Introspect(
        {"active": True, "status": "active", "expires_at": 2_000_000,
         "scope": "r_basicprofile,rw_organization_admin"}))
    _, _, scopes = post_linkedin.token_state(now=1_000_000)
    assert post_linkedin.REQUIRED_SCOPE not in scopes


def test_days_left_is_computed_from_the_real_expiry(creds, monkeypatch):
    monkeypatch.setattr(post_linkedin.requests, "post", _Introspect(
        {"active": True, "status": "active",
         "expires_at": 1_000_000 + 86400 * 12, "scope": "w_organization_social"}))
    days, status, _ = post_linkedin.token_state(now=1_000_000)
    assert days == 12 and status == "active"


def test_without_client_credentials_it_degrades_to_the_stored_timestamp(
        monkeypatch):
    """Introspection is an upgrade, not a dependency — the channel must still
    work for anyone who has not stored the client credentials."""
    monkeypatch.delenv("LINKEDIN_CLIENT_ID", raising=False)
    monkeypatch.delenv("LINKEDIN_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("LINKEDIN_TOKEN_EXPIRES_AT", str(1_000_000 + 86400 * 5))
    assert post_linkedin.introspect() is None
    days, status, scopes = post_linkedin.token_state(now=1_000_000)
    assert (days, status, scopes) == (5, "unknown", [])


def test_a_failed_introspection_never_breaks_the_caller(creds, monkeypatch):
    """A credential check must not be able to take down a publish."""
    def boom(*a, **k):
        raise RuntimeError("network")
    monkeypatch.setattr(post_linkedin.requests, "post", boom)
    monkeypatch.setenv("LINKEDIN_TOKEN_EXPIRES_AT", str(1_000_000 + 86400 * 3))
    days, status, _ = post_linkedin.token_state(now=1_000_000)
    assert (days, status) == (3, "unknown")


# --- validating the app credentials alone ----------------------------------
#
# Before the API application is approved there is no access token, so the
# only checkable thing is the app credentials. Introspection needs a token
# argument, but its credential checks run FIRST — so a junk token still
# produces a meaningful answer about the client id and secret.

def _check_app_with(monkeypatch, status, body=""):
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import validate_linkedin as v
    monkeypatch.setenv("LINKEDIN_CLIENT_ID", "cid12345")
    monkeypatch.setenv("LINKEDIN_CLIENT_SECRET", "secret")

    class R:
        status_code = status
        text = body
    monkeypatch.setattr(v.requests, "post", lambda *a, **k: R())
    return v


def test_a_401_is_reported_as_a_bad_client_secret(monkeypatch, capsys):
    """LinkedIn documents 401 as "Invalid client secret" specifically — the
    one status that identifies a single credential."""
    v = _check_app_with(monkeypatch, 401)
    with pytest.raises(SystemExit):
        v.check_app()
    assert "CLIENT SECRET" in capsys.readouterr().out


def test_a_400_means_the_secret_was_accepted(monkeypatch, capsys):
    """The probe token is junk, so 400 ("Invalid client id or token") is the
    EXPECTED healthy answer: the request got past secret validation."""
    v = _check_app_with(monkeypatch, 400)
    v.check_app()
    out = capsys.readouterr().out
    assert "client secret accepted" in out
    # The limit must be stated, not implied — 400 cannot separate a wrong
    # client id from the junk token.
    assert "cannot distinguish a wrong client ID" in out


def test_the_probe_token_cannot_be_a_real_one(monkeypatch):
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import validate_linkedin as v
    assert "not-a-real" in v._SENTINEL_TOKEN


def test_an_unexpected_status_is_not_reported_as_success(monkeypatch, capsys):
    """A 500 or a redirect must not read as healthy — silence about a
    credential is worse than a wrong answer about it."""
    v = _check_app_with(monkeypatch, 503, "gateway")
    with pytest.raises(SystemExit):
        v.check_app()
    assert "unexpected 503" in capsys.readouterr().out
