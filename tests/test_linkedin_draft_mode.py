"""LINKEDIN_DRAFT=1 publishes to the Page as a draft instead of live.

Why this exists: PR #96 fixed LinkedIn silently truncating commentary at an
unescaped "(", but the fix could not be verified. Our token carries
w_organization_social and not r_organization_social, so GET /rest/posts
returns 403 and there is no way to read back how LinkedIn stored the text.
The only way to confirm that "\\(" renders as "(" is to put a post in front
of LinkedIn's parser and look at it — and the whole reason we are here is a
bug that made a post worse without making it fail, so "publish and check"
means publishing something possibly broken to the Page.

lifecycleState: "DRAFT" is the way out. The post is created, parsed, and
rendered by LinkedIn, visible to Page admins, and seen by nobody else.

THE RISK THIS MODE INTRODUCES, AND WHAT GUARDS IT
A draft mode that is easy to leave on is a silent-failure generator: every
run would appear to succeed while the Page published nothing. That is the
same shape as the bug that prompted it, so the guards are deliberate:

  * It is opt-in and off by default — an unset or malformed flag publishes.
  * A draft is NOT "posted". It reaches no audience, so it does not consume
    the topic, advance the rotation, or burn a screenshot section.
  * It gets its own status, "drafted", rather than being folded into
    "posted" with a sidecar boolean. A status nobody has to remember to
    look at is a status that gets missed.
  * It does not fail the run either, so a deliberate draft does not page
    anyone at 2am.

These tests pin all four, and pin the default in the safe direction.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
import post_linkedin  # noqa: E402


@pytest.fixture(autouse=True)
def _no_draft_leak():
    """LINKEDIN_DRAFT must not escape a test.

    run.main() sets it directly in os.environ, and monkeypatch cannot undo
    a variable that did not exist beforehand — delenv(raising=False) on an
    absent name records nothing to restore. Without this fixture the CLI
    tests leave draft mode armed for the whole session, and every later
    LinkedIn test starts asserting against DRAFT payloads.

    That is not hypothetical: it is how this file broke
    tests/test_linkedin_post.py on its first full-suite run while passing
    in isolation. Leaving the leak in place would have meant a flag whose
    danger (staying on unnoticed) was demonstrated first by its own tests.
    """
    before = os.environ.get("LINKEDIN_DRAFT")
    yield
    if before is None:
        os.environ.pop("LINKEDIN_DRAFT", None)
    else:
        os.environ["LINKEDIN_DRAFT"] = before


class _Resp:
    ok = True
    status_code = 201
    headers = {"x-restli-id": "urn:li:share:1"}

    def json(self):
        return {}


@pytest.fixture
def sent(monkeypatch):
    """Capture the payload post_image/post_video would send."""
    box = {}
    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", "t")
    monkeypatch.setattr(post_linkedin, "author_urn",
                        lambda tok: "urn:li:organization:1")
    monkeypatch.setattr(post_linkedin, "upload_image",
                        lambda p, t, a: "urn:li:image:1")
    monkeypatch.setattr(post_linkedin, "upload_video",
                        lambda p, t, a: "urn:li:video:1")
    monkeypatch.setattr(post_linkedin.requests, "post",
                        lambda url, headers=None, data=None, timeout=None:
                        (box.__setitem__("payload", json.loads(data)),
                         _Resp())[1])
    return box


def test_default_publishes(sent, monkeypatch):
    """The safe default. An absent flag must never mean "draft"."""
    monkeypatch.delenv("LINKEDIN_DRAFT", raising=False)
    post_linkedin.post_image("x.png", "hello")
    assert sent["payload"]["lifecycleState"] == "PUBLISHED"


def test_flag_set_to_one_drafts(sent, monkeypatch):
    monkeypatch.setenv("LINKEDIN_DRAFT", "1")
    post_linkedin.post_image("x.png", "hello")
    assert sent["payload"]["lifecycleState"] == "DRAFT"


@pytest.mark.parametrize("value", ["", "0", "true", "yes", "DRAFT", " 1"])
def test_only_exactly_one_drafts(sent, monkeypatch, value):
    """Anything other than "1" publishes.

    Matching loosely would be the friendlier choice everywhere except here:
    the cost of a false positive is a Page that silently stops publishing,
    and the cost of a false negative is a post that goes out live — which
    is what the flag was already doing. "1" matches LINKEDIN_ALLOW_MEMBER_POST
    in this same module, so there is one convention to remember.
    """
    monkeypatch.setenv("LINKEDIN_DRAFT", value)
    post_linkedin.post_image("x.png", "hello")
    assert sent["payload"]["lifecycleState"] == "PUBLISHED"


def test_video_path_drafts_too(sent, monkeypatch):
    """Both publish paths, or the `ad` format quietly goes out live."""
    monkeypatch.setenv("LINKEDIN_DRAFT", "1")
    post_linkedin.post_video("x.mp4", "hello")
    assert sent["payload"]["lifecycleState"] == "DRAFT"


def test_draft_is_reported_to_the_caller(sent, monkeypatch):
    """run.publish() needs to tell a draft from a post. A dict key is how."""
    monkeypatch.setenv("LINKEDIN_DRAFT", "1")
    out = post_linkedin.post_image("x.png", "hello")
    assert out["draft"] is True


def test_a_real_post_says_so_explicitly(sent, monkeypatch):
    """draft=False, not a missing key.

    An absent key reads the same as "nobody thought about it", and the
    caller would have to use .get(..., False) — which silently publishes a
    draft as posted if this ever regresses.
    """
    monkeypatch.delenv("LINKEDIN_DRAFT", raising=False)
    out = post_linkedin.post_image("x.png", "hello")
    assert out["draft"] is False


def test_draft_still_escapes_commentary(sent, monkeypatch):
    """The entire point: the draft must carry the text we are validating."""
    monkeypatch.setenv("LINKEDIN_DRAFT", "1")
    post_linkedin.post_image("x.png", "Upload the packet (PDF + DOCX)")
    assert r"\(PDF + DOCX\)" in sent["payload"]["commentary"]


def test_draft_url_is_not_offered_as_public(sent, monkeypatch):
    """A draft has no public permalink; the feed URL 404s for non-admins.

    Returning one anyway would put a dead link in the run log and in Slack,
    which is how a draft gets mistaken for a published post.
    """
    monkeypatch.setenv("LINKEDIN_DRAFT", "1")
    out = post_linkedin.post_image("x.png", "hello")
    assert out.get("url") is None


# ---------------------------------------------------------------------------
# run.publish() half: a draft is not a post, and must not be treated as one.
# ---------------------------------------------------------------------------

# Reuses the real end-to-end harness rather than calling publish() in
# process: the assertion that matters here is about rotation state on disk
# after a real run, and an in-process stub would not exercise that.
from test_publish_outcomes import (  # noqa: E402
    log_entries, prepare, run, sandbox, state_of)

_STUB = ('def post_media(path, caption, alt_text=None):\n'
         '    return {"id": "urn:li:share:555", "url": %s,\n'
         '            "draft": %s}\n')


@pytest.fixture()
def draft_sandbox(sandbox):
    """Publish once to LinkedIn only, as a draft or for real."""
    def _go(draft, with_state=False):
        stub = _STUB % ("None" if draft else '"https://x/"', draft)
        with open(os.path.join(sandbox, "engine", "post_linkedin.py"),
                  "w") as f:
            f.write(stub)
        prepare(sandbox)
        before = state_of(sandbox)
        run(["--publish", "--skip-x", "--skip-ig"], sandbox,
            channels=("linkedin",))
        after = state_of(sandbox)
        entry = log_entries(sandbox)[-1]
        return (before, entry, after) if with_state else entry
    return _go


@pytest.fixture()
def draft_sandbox_proc(sandbox):
    """Same run, but hand back what the operator actually sees on stdout."""
    def _go(draft):
        stub = _STUB % ("None" if draft else '"https://x/"', draft)
        with open(os.path.join(sandbox, "engine", "post_linkedin.py"),
                  "w") as f:
            f.write(stub)
        prepare(sandbox)
        return run(["--publish", "--skip-x", "--skip-ig"], sandbox,
                   channels=("linkedin",))
    return _go


def test_drafted_is_its_own_status(draft_sandbox):
    """Not "posted" with a boolean nobody reads."""
    entry = draft_sandbox(draft=True)
    assert entry["channels"]["linkedin"]["status"] == "drafted"


def test_a_draft_does_not_consume_the_topic(draft_sandbox):
    """The existing rule: a topic is consumed once it reaches an audience.

    A draft reaches nobody. If it advanced the rotation, validating the
    escaping fix would silently burn a topic — and the next real post would
    be a different one, so the thing we validated never goes out.
    """
    before, entry, after = draft_sandbox(draft=True, with_state=True)
    assert after["topic_index"] == before["topic_index"]
    assert after.get("shot_index", 0) == before.get("shot_index", 0)


def test_a_real_post_still_consumes_the_topic(draft_sandbox):
    """Negative control: the guard above must not break normal publishing."""
    before, entry, after = draft_sandbox(draft=False, with_state=True)
    assert after["topic_index"] != before["topic_index"]


def test_a_draft_does_not_fail_the_run(draft_sandbox):
    """A deliberate draft is not an incident and must not page anyone."""
    entry = draft_sandbox(draft=True)
    assert entry["outcome"] == "drafted"


def test_the_run_does_not_claim_it_posted(draft_sandbox_proc):
    """The final line a human reads must not say "posted" after a draft.

    publish() ends with "done - posted to {channels}". On a draft-only run
    that renders as "done - posted to " — trailing space, no channels, the
    word "posted" — which is the misleading-success shape this whole branch
    of work exists to eliminate. If the summary line lies, the status field
    being right does not help anyone.
    """
    r = draft_sandbox_proc(draft=True)
    out = r.stdout + r.stderr
    assert "posted to" not in out
    assert "draft" in out.lower()


def test_a_draft_only_run_exits_zero(draft_sandbox_proc):
    """The exit code, not just the log entry.

    Added after a mutation exposed the gap: reverting the exit-code guard to
    `if failed or not posted` left every other test green, because the log
    entry is written BEFORE sys.exit(1). So "outcome == drafted" in the log
    proves nothing about whether the run went red and paged someone.

    A deliberate draft is a successful run. CI going red for it would train
    everyone to ignore a red draft job, which is worse than no job.
    """
    assert draft_sandbox_proc(draft=True).returncode == 0


def test_a_draft_only_run_does_not_notify_failure(draft_sandbox_proc):
    """Same guard, from the other side: no failure alert for a draft."""
    r = draft_sandbox_proc(draft=True)
    assert "FAILED" not in (r.stdout + r.stderr)


def test_cli_flag_arms_draft_mode(monkeypatch):
    """`--draft` is the operator's door in; the env var is the mechanism.

    A CLI flag rather than "remember to export LINKEDIN_DRAFT", because the
    whole hazard of this mode is leaving it on and an argv flag cannot
    outlive the command that typed it. publish() is stubbed: this is about
    argument wiring, and the real one would try to post.
    """
    import run
    monkeypatch.delenv("LINKEDIN_DRAFT", raising=False)
    monkeypatch.setattr(run, "publish", lambda *a, **k: None)
    run.main(["--publish", "--draft"])
    assert os.environ["LINKEDIN_DRAFT"] == "1"


def test_no_cli_flag_leaves_the_env_alone(monkeypatch):
    """Negative control: --publish alone must never arm draft mode."""
    import run
    monkeypatch.delenv("LINKEDIN_DRAFT", raising=False)
    monkeypatch.setattr(run, "publish", lambda *a, **k: None)
    run.main(["--publish"])
    assert "LINKEDIN_DRAFT" not in os.environ


def test_draft_is_armed_before_publish_runs(monkeypatch):
    """Order matters: post_linkedin reads the env var at payload time.

    Arming after publish() returns would be a flag that does nothing, and
    every test above would still pass because they set the env directly.
    """
    import run
    monkeypatch.delenv("LINKEDIN_DRAFT", raising=False)
    seen = {}
    monkeypatch.setattr(run, "publish",
                        lambda *a, **k: seen.update(
                            env=os.environ.get("LINKEDIN_DRAFT")))
    run.main(["--publish", "--draft"])
    assert seen["env"] == "1"
