"""W3 — the human approval gate.

Two independent gates protect a publish, and they defend different things:

  * GitHub's required-reviewer environment is the SECURITY boundary. It is
    enforced by GitHub, not by this code, and cannot be bypassed by an env
    var or a timeout.
  * approved.json is the CONTENT-INTEGRITY check, and it earns its place on
    one scenario: Monday's post is never approved, Tuesday's cron overwrites
    pending.json, and someone then clicks approve on Monday's notification.
    Without the hash that publishes content nobody reviewed.

Nothing here may auto-approve, and --force must be impossible in CI.
"""
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import approval  # noqa: E402

UTC = datetime.timezone.utc

# Records that it was called, so a test can prove the gate ran first.
STUB_RECORDING = '''import os
HERE = os.path.dirname(os.path.abspath(__file__))
MARK = os.path.join(HERE, "..", "CHANNEL_WAS_CALLED")

def _mark():
    with open(MARK, "w") as f:
        f.write("called")

def post(text, media_path=None, reply_text=None):
    _mark()
    return 111

def post_image(path, caption):
    _mark()
    return "ig-1"

def post_reel(path, caption):
    _mark()
    return "ig-1"
'''


@pytest.fixture()
def sandbox(tmp_path):
    dst = tmp_path / "proj"
    for d in ("engine", "content"):
        shutil.copytree(os.path.join(ROOT, d), dst / d)
    for f in ("state.json", "pending.json", "approved.json"):
        p = dst / "content" / f
        if p.exists():
            p.unlink()
    for ch in ("x", "ig"):
        (dst / "engine" / f"post_{ch}.py").write_text(STUB_RECORDING)
    return str(dst)


def run(args, cwd, creds=True, env=None):
    e = dict(os.environ)
    for var in ("ANTHROPIC_API_KEY", "NOTIFY_WEBHOOK_URL", "GITHUB_ACTIONS",
                "MEDIA_BASE_URL"):
        e.pop(var, None)
    if creds:
        e.update(X_API_KEY="k", IG_USER_ID="u")
    else:
        for var in ("X_API_KEY", "IG_USER_ID"):
            e.pop(var, None)
    e.update(env or {})
    return subprocess.run([sys.executable, "engine/run.py"] + args,
                          cwd=cwd, env=e, capture_output=True, text=True)


def paths(sandbox):
    c = os.path.join(sandbox, "content")
    return os.path.join(c, "pending.json"), os.path.join(c, "approved.json")


def any_channel_called(sandbox):
    return os.path.exists(os.path.join(sandbox, "CHANNEL_WAS_CALLED"))


def write_approval_at(sandbox, when, pending_hash=None):
    """Forge an approval with a chosen timestamp, as the CLI would write it."""
    pending, approved = paths(sandbox)
    with open(approved, "w") as f:
        json.dump({
            "pending_sha256": pending_hash or approval.compute_hash(pending),
            "approved_at": when.isoformat(),
            "approved_by": "tester",
            "topic": "price-to-win",
            "format": "card",
        }, f, indent=2)


# ---------- approval.py units ----------

def test_hash_is_sha256_of_the_exact_bytes(tmp_path):
    p = tmp_path / "pending.json"
    p.write_bytes(b'{"a": 1}')
    assert approval.compute_hash(str(p)) == hashlib.sha256(b'{"a": 1}').hexdigest()


def test_any_byte_change_breaks_the_hash(tmp_path):
    p = tmp_path / "pending.json"
    p.write_bytes(b'{"a": 1}')
    before = approval.compute_hash(str(p))
    p.write_bytes(b'{"a": 2}')
    assert approval.compute_hash(str(p)) != before


def test_verify_rejects_expired(tmp_path):
    p, a = tmp_path / "p.json", tmp_path / "a.json"
    p.write_bytes(b"{}")
    now = datetime.datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    a.write_text(json.dumps({
        "pending_sha256": approval.compute_hash(str(p)),
        "approved_at": (now - datetime.timedelta(hours=24, minutes=1)).isoformat()}))
    ok, reason = approval.verify_approval(str(p), str(a), now=now)
    assert not ok and "expired" in reason.lower()


def test_verify_accepts_just_under_the_ttl(tmp_path):
    p, a = tmp_path / "p.json", tmp_path / "a.json"
    p.write_bytes(b"{}")
    now = datetime.datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    a.write_text(json.dumps({
        "pending_sha256": approval.compute_hash(str(p)),
        "approved_at": (now - datetime.timedelta(hours=23, minutes=59)).isoformat()}))
    ok, _ = approval.verify_approval(str(p), str(a), now=now)
    assert ok


def test_verify_rejects_future_dated_approval(tmp_path):
    """Clock skew or tampering must not mint a never-expiring approval."""
    p, a = tmp_path / "p.json", tmp_path / "a.json"
    p.write_bytes(b"{}")
    now = datetime.datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    a.write_text(json.dumps({
        "pending_sha256": approval.compute_hash(str(p)),
        "approved_at": (now + datetime.timedelta(hours=2)).isoformat()}))
    ok, reason = approval.verify_approval(str(p), str(a), now=now)
    assert not ok and "future" in reason.lower()


def test_verify_rejects_malformed_approval(tmp_path):
    p, a = tmp_path / "p.json", tmp_path / "a.json"
    p.write_bytes(b"{}")
    a.write_text("not json at all")
    ok, reason = approval.verify_approval(str(p), str(a))
    assert not ok and reason


def test_verify_rejects_naive_timestamp(tmp_path):
    """An approval without a timezone is ambiguous; refuse rather than guess."""
    p, a = tmp_path / "p.json", tmp_path / "a.json"
    p.write_bytes(b"{}")
    a.write_text(json.dumps({"pending_sha256": approval.compute_hash(str(p)),
                             "approved_at": "2026-07-28T12:00:00"}))
    ok, reason = approval.verify_approval(str(p), str(a))
    assert not ok and reason


# ---------- the gate, end to end ----------

def test_publish_refuses_without_approval(sandbox):
    run(["--prepare"], sandbox)
    r = run(["--publish"], sandbox)
    assert r.returncode == 1
    assert "approv" in (r.stdout + r.stderr).lower()


def test_unapproved_publish_reaches_no_channel(sandbox):
    """The gate must run before any poster, not after."""
    run(["--prepare"], sandbox)
    run(["--publish"], sandbox)
    assert not any_channel_called(sandbox)


def test_unapproved_publish_preserves_pending(sandbox):
    """Deleting the post awaiting review would destroy the thing being gated."""
    run(["--prepare"], sandbox)
    pending, _ = paths(sandbox)
    run(["--publish"], sandbox)
    assert os.path.exists(pending)


def test_unapproved_publish_does_not_consume_topic(sandbox):
    run(["--prepare"], sandbox)
    run(["--publish"], sandbox)
    assert not os.path.exists(os.path.join(sandbox, "content", "state.json"))


def test_approve_then_publish_proceeds(sandbox):
    run(["--prepare"], sandbox)
    r = run(["--approve"], sandbox)
    assert r.returncode == 0, r.stdout + r.stderr
    r = run(["--publish"], sandbox)
    assert r.returncode == 0, r.stdout + r.stderr
    assert any_channel_called(sandbox)
    state = json.load(open(os.path.join(sandbox, "content", "state.json")))
    assert state["topic_index"] == 1


def test_publish_refuses_when_pending_changed_after_approval(sandbox):
    """The Monday-approved / Tuesday-content scenario."""
    run(["--prepare"], sandbox)
    run(["--approve"], sandbox)
    pending, _ = paths(sandbox)
    doc = json.load(open(pending))
    doc["text_x"] = "a completely different tweet nobody reviewed"
    with open(pending, "w") as f:
        json.dump(doc, f, indent=2)
    r = run(["--publish"], sandbox)
    assert r.returncode == 1
    assert not any_channel_called(sandbox)


def test_publish_refuses_expired_approval(sandbox):
    run(["--prepare"], sandbox)
    write_approval_at(sandbox,
                      datetime.datetime.now(UTC) - datetime.timedelta(hours=25))
    r = run(["--publish"], sandbox)
    assert r.returncode == 1
    assert not any_channel_called(sandbox)


def test_publish_accepts_fresh_approval(sandbox):
    run(["--prepare"], sandbox)
    write_approval_at(sandbox,
                      datetime.datetime.now(UTC) - datetime.timedelta(hours=1))
    assert run(["--publish"], sandbox).returncode == 0


def test_approval_is_consumed_after_publish(sandbox):
    """A spent approval must not authorise tomorrow's post."""
    run(["--prepare"], sandbox)
    run(["--approve"], sandbox)
    run(["--publish"], sandbox)
    _, approved = paths(sandbox)
    assert not os.path.exists(approved)


def test_approve_without_pending_fails(sandbox):
    r = run(["--approve"], sandbox)
    assert r.returncode == 1


def test_approve_records_hash_topic_and_utc_timestamp(sandbox):
    run(["--prepare"], sandbox)
    run(["--approve"], sandbox)
    pending, approved = paths(sandbox)
    doc = json.load(open(approved))
    assert doc["pending_sha256"] == approval.compute_hash(pending)
    pending_doc = json.load(open(pending))
    assert doc["topic"] == pending_doc["topic"]
    assert doc["format"] == pending_doc["format"]
    stamp = datetime.datetime.fromisoformat(doc["approved_at"])
    assert stamp.tzinfo is not None, "must be timezone-aware"


# ---------- --force is local-only ----------

def test_force_publishes_without_approval_and_warns(sandbox):
    run(["--prepare"], sandbox)
    r = run(["--publish", "--force"], sandbox)
    assert r.returncode == 0, r.stdout + r.stderr
    assert any_channel_called(sandbox)
    assert "WARNING" in r.stdout.upper()


def test_force_is_refused_under_github_actions(sandbox):
    """No production bypass: --force must be impossible in CI."""
    run(["--prepare"], sandbox)
    r = run(["--publish", "--force"], sandbox, env={"GITHUB_ACTIONS": "true"})
    assert r.returncode == 1
    assert not any_channel_called(sandbox)


def test_no_arg_run_does_not_silently_publish(sandbox):
    """The convenience path must not become an accidental auto-approve."""
    r = run([], sandbox)
    assert r.returncode != 0 or not any_channel_called(sandbox)
    assert not any_channel_called(sandbox)


# ---------- notification payload ----------

def test_pending_review_payload_is_slack_shaped(monkeypatch):
    import notify
    sent = {}

    class Resp:
        status_code = 200

    monkeypatch.setenv("NOTIFY_WEBHOOK_URL", "https://hooks.slack.test/xyz")
    monkeypatch.setattr(notify.requests, "post",
                        lambda url, json=None, timeout=None: (
                            sent.update(json=json), Resp())[1])
    ok = notify.pending_review(
        {"topic": "fit-scoring", "format": "card", "text_x": "tweet body",
         "text_ig": "ig body", "media_x": "assets/cards/fit-scoring_x.png"},
        media_url="https://example.test/card.png",
        review_url="https://github.test/run/1")
    assert ok is True
    payload = sent["json"]
    assert payload["text"], "text fallback required for notifications"
    kinds = [b["type"] for b in payload["blocks"]]
    assert "image" in kinds
    assert "fit-scoring" in json.dumps(payload)
    assert "https://github.test/run/1" in json.dumps(payload)


def test_pending_review_omits_image_when_no_media_url(monkeypatch):
    import notify
    sent = {}

    class Resp:
        status_code = 200

    monkeypatch.setenv("NOTIFY_WEBHOOK_URL", "https://hooks.slack.test/xyz")
    monkeypatch.setattr(notify.requests, "post",
                        lambda url, json=None, timeout=None: (
                            sent.update(json=json), Resp())[1])
    notify.pending_review({"topic": "t", "format": "card", "text_x": "a",
                           "text_ig": "b", "media_x": "x.png"},
                          media_url=None, review_url=None)
    assert "image" not in [b["type"] for b in sent["json"]["blocks"]]


def test_long_caption_is_truncated_for_slack(monkeypatch):
    import notify
    sent = {}

    class Resp:
        status_code = 200

    monkeypatch.setenv("NOTIFY_WEBHOOK_URL", "https://hooks.slack.test/xyz")
    monkeypatch.setattr(notify.requests, "post",
                        lambda url, json=None, timeout=None: (
                            sent.update(json=json), Resp())[1])
    notify.pending_review({"topic": "t", "format": "card",
                           "text_x": "x" * 5000, "text_ig": "y" * 5000,
                           "media_x": "x.png"},
                          media_url=None, review_url=None)
    for block in sent["json"]["blocks"]:
        text = (block.get("text") or {}).get("text", "")
        assert len(text) <= 3000, "Slack rejects oversized text objects"
