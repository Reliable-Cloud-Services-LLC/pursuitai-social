"""The Page-chain validator must probe with scopes the Page chain HAS.

Step [1] used to call ``/v2/userinfo`` — a MEMBER endpoint requiring
openid/profile. Once member posting was retired (#93) those scopes stopped
being minted, so on 2026-11-03 the validator failed the very first real
Page token with::

    ✗ token rejected (403): ACCESS_DENIED ... userinfo.GET.NO_VERSION

on a token that was completely healthy. That is the worst kind of check:
one that fails for a reason unrelated to what it claims to verify, at the
exact moment you are relying on it to tell you whether to trust a new
credential.
"""
import importlib.util
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))

ORG = "118784170"


def _load():
    spec = importlib.util.spec_from_file_location(
        "validate_linkedin", ROOT / "scripts" / "validate_linkedin.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeResp:
    def __init__(self, status, payload=None, text=""):
        self.status_code, self._payload, self.text = status, payload or {}, text

    def json(self):
        return self._payload


def _run(monkeypatch, acl_resp):
    """Run the Page path up to the pre-upload checks. Returns (urls, exit)."""
    mod = _load()
    urls = []

    def fake_get(url, headers=None, timeout=None, **kw):
        urls.append(url)
        if "organizationAcls" in url:
            return acl_resp
        raise AssertionError(f"unexpected GET {url}")

    monkeypatch.setattr(mod.requests, "get", fake_get)
    # token_state is introspection; not what this test is about.
    monkeypatch.setattr(mod.post_linkedin, "token_state",
                        lambda: (59, "active", ["w_organization_social"]))
    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("LINKEDIN_ORG_ID", ORG)
    monkeypatch.setattr(sys, "argv", ["validate_linkedin.py"])

    code = 0
    try:
        mod.main()
    except SystemExit as e:
        code = e.code
    return urls, code


def test_page_path_never_calls_the_member_userinfo_endpoint(monkeypatch, capsys):
    """The regression. A Page-only token cannot reach /v2/userinfo and
    should never be asked to."""
    acl = FakeResp(200, {"elements": [
        {"role": "ADMINISTRATOR", "state": "APPROVED",
         "organization": f"urn:li:organization:{ORG}"}]})
    urls, code = _run(monkeypatch, acl)
    assert not any("userinfo" in u for u in urls), \
        f"Page path called a member endpoint: {urls}"
    assert any("organizationAcls" in u for u in urls), \
        "liveness must be probed with a call the Page chain is scoped for"
    assert code == 0, capsys.readouterr().out


def test_a_dead_token_is_fatal(monkeypatch, capsys):
    """401 means the credential itself is bad — stop, loudly."""
    _, code = _run(monkeypatch, FakeResp(401, text="INVALID_ACCESS_TOKEN"))
    assert code == 1
    assert "token rejected" in capsys.readouterr().out


def test_a_missing_admin_scope_is_not_fatal(monkeypatch, capsys):
    """403 means the token is LIVE but lacks rw_organization_admin. The
    upload is the real test, so this must warn rather than abort — the
    negative control for the 401 case above, so the two cannot collapse
    into one verdict."""
    _, code = _run(monkeypatch, FakeResp(403, text="ACCESS_DENIED"))
    out = capsys.readouterr().out
    assert code == 0, out
    assert "NOT fatal" in out


def test_wrong_org_still_fails(monkeypatch, capsys):
    """Role on SOME page is not role on THIS page."""
    acl = FakeResp(200, {"elements": [
        {"role": "ADMINISTRATOR", "state": "APPROVED",
         "organization": "urn:li:organization:12961055"}]})
    _, code = _run(monkeypatch, acl)
    assert code == 1
    assert "does NOT administer" in capsys.readouterr().out
