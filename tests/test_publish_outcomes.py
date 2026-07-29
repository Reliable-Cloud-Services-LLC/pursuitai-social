"""W1 — fail loud.

Covers the three per-channel publish outcomes (posted / skipped / failed),
the exit code contract, and the rule that a topic is only consumed when it
actually reached an audience.

Failure is injected with zero production seams: the sandbox copies engine/
into tmp_path and run.py does sys.path.insert(0, HERE), so overwriting the
sandbox's own post_x.py / post_ig.py exercises the real code path with no
network access.
"""
import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

# Dummy values: presence is what gates a channel, the stubs ignore the values.
CREDS = {"x": {"X_API_KEY": "test-key"}, "ig": {"IG_USER_ID": "test-uid"}}

STUB_OK = {
    "x": 'def post(text, media_path=None, reply_text=None):\n    return {"id": "1234567890", "reply_id": "222",\n            "reply_error": None}\n',
    "ig": ('def post_image(path, caption):\n    return "ig-987"\n\n'
           'def post_reel(path, caption):\n    return "ig-987"\n'),
}
STUB_RAISES = {
    "x": ('def post(text, media_path=None, reply_text=None):\n'
          '    raise RuntimeError("simulated X outage")\n'),
    "ig": ('def post_image(path, caption):\n'
           '    raise RuntimeError("simulated IG outage")\n\n'
           'def post_reel(path, caption):\n'
           '    raise RuntimeError("simulated IG outage")\n'),
}


@pytest.fixture()
def sandbox(tmp_path):
    """Isolated copy of the project with no carried-over state."""
    dst = tmp_path / "proj"
    for d in ("engine", "content"):
        shutil.copytree(os.path.join(ROOT, d), dst / d)
    for f in ("state.json", "pending.json", "approved.json"):
        p = dst / "content" / f
        if p.exists():
            p.unlink()
    return str(dst)


def install(sandbox, channel, outcome):
    """Replace a poster module in the sandbox. outcome: 'ok' | 'raises'."""
    src = (STUB_OK if outcome == "ok" else STUB_RAISES)[channel]
    with open(os.path.join(sandbox, "engine", f"post_{channel}.py"), "w") as f:
        f.write(src)


def run(args, cwd, channels=()):
    """Invoke the engine. Only the named channels get credentials."""
    env = dict(os.environ)
    for var in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN",
                "X_ACCESS_SECRET", "IG_USER_ID", "IG_ACCESS_TOKEN",
                "ANTHROPIC_API_KEY", "NOTIFY_WEBHOOK_URL"):
        env.pop(var, None)
    for ch in channels:
        env.update(CREDS[ch])
    return subprocess.run([sys.executable, "engine/run.py"] + args,
                          cwd=cwd, env=env, capture_output=True, text=True)


def prepare(sandbox):
    """Prepare and approve.

    W3 gates publish behind human approval. These tests exercise channel
    outcomes, not the gate itself (tests/test_approval.py owns that), so
    they clear it the same way a reviewer would.
    """
    run(["--prepare"], sandbox)
    run(["--approve"], sandbox)


def state_of(sandbox):
    """Current rotation state. An absent file means nothing was consumed."""
    p = os.path.join(sandbox, "content", "state.json")
    if not os.path.exists(p):
        return {"topic_index": 0, "run_count": 0}
    with open(p) as f:
        return json.load(f)


def seed_state(sandbox, topic_index, run_count):
    """Production has a committed state.json; failures must not disturb it."""
    with open(os.path.join(sandbox, "content", "state.json"), "w") as f:
        json.dump({"topic_index": topic_index, "run_count": run_count}, f)


def log_entries(sandbox):
    with open(os.path.join(sandbox, "logs", "posted.jsonl")) as f:
        return [json.loads(ln) for ln in f if ln.strip()]


# ---------- exit codes ----------

def test_channel_failure_exits_nonzero(sandbox):
    install(sandbox, "x", "raises")
    install(sandbox, "ig", "ok")
    prepare(sandbox)
    r = run(["--publish"], sandbox, channels=("x", "ig"))
    assert r.returncode != 0, r.stdout


def test_all_skipped_exits_nonzero(sandbox):
    """No credentials at all must NOT report success — that is the live bug."""
    prepare(sandbox)
    r = run(["--publish"], sandbox, channels=())
    assert r.returncode != 0, r.stdout


def test_full_success_exits_zero(sandbox):
    install(sandbox, "x", "ok")
    install(sandbox, "ig", "ok")
    prepare(sandbox)
    r = run(["--publish"], sandbox, channels=("x", "ig"))
    assert r.returncode == 0, r.stdout + r.stderr


# ---------- topic consumption ----------

def test_topic_not_consumed_on_total_failure(sandbox):
    install(sandbox, "x", "raises")
    install(sandbox, "ig", "raises")
    seed_state(sandbox, topic_index=5, run_count=5)
    prepare(sandbox)
    run(["--publish"], sandbox, channels=("x", "ig"))
    assert state_of(sandbox) == {"topic_index": 5, "run_count": 5}


def test_topic_not_consumed_when_all_channels_skipped(sandbox):
    seed_state(sandbox, topic_index=5, run_count=5)
    prepare(sandbox)
    run(["--publish"], sandbox, channels=())
    assert state_of(sandbox) == {"topic_index": 5, "run_count": 5}


def test_failed_run_replays_the_same_topic_and_format(sandbox):
    """Freezing both counters means the next run retries identical content."""
    install(sandbox, "x", "raises")
    install(sandbox, "ig", "raises")
    prepare(sandbox)
    first = json.load(open(os.path.join(sandbox, "content", "pending.json")))
    run(["--publish"], sandbox, channels=("x", "ig"))
    prepare(sandbox)
    second = json.load(open(os.path.join(sandbox, "content", "pending.json")))
    assert (first["topic"], first["format"]) == (second["topic"],
                                                 second["format"])


def test_topic_advances_on_partial_success(sandbox):
    install(sandbox, "x", "ok")
    install(sandbox, "ig", "raises")
    prepare(sandbox)
    r = run(["--publish"], sandbox, channels=("x", "ig"))
    s = state_of(sandbox)
    assert s["topic_index"] == 1 and s["run_count"] == 1
    assert r.returncode != 0, "a failed channel must still be loud"
    assert log_entries(sandbox)[0]["outcome"] == "partial"


def test_topic_advances_on_full_success(sandbox):
    install(sandbox, "x", "ok")
    install(sandbox, "ig", "ok")
    prepare(sandbox)
    run(["--publish"], sandbox, channels=("x", "ig"))
    s = state_of(sandbox)
    assert s["topic_index"] == 1 and s["run_count"] == 1
    assert log_entries(sandbox)[0]["outcome"] == "posted"


# ---------- three-state logging ----------

def test_log_distinguishes_posted_skipped_failed(sandbox):
    # run 1: x posts, ig has no credentials -> posted + skipped
    install(sandbox, "x", "ok")
    prepare(sandbox)
    run(["--publish"], sandbox, channels=("x",))
    # run 2: x now throws -> failed
    install(sandbox, "x", "raises")
    prepare(sandbox)
    run(["--publish"], sandbox, channels=("x",))

    first, second = log_entries(sandbox)
    assert first["channels"]["x"]["status"] == "posted"
    assert first["channels"]["x"]["id"]
    assert first["channels"]["ig"]["status"] == "skipped"
    assert second["channels"]["x"]["status"] == "failed"
    assert "simulated X outage" in second["channels"]["x"]["error"]


def test_missing_credentials_logged_as_skipped_not_failed(sandbox):
    prepare(sandbox)
    run(["--publish"], sandbox, channels=())
    ch = log_entries(sandbox)[0]["channels"]
    assert ch["x"]["status"] == "skipped" and "X_API_KEY" in ch["x"]["error"]
    assert ch["ig"]["status"] == "skipped" and "IG_USER_ID" in ch["ig"]["error"]


def test_explicit_skip_flag_is_disabled_not_failure(sandbox):
    install(sandbox, "ig", "ok")
    prepare(sandbox)
    r = run(["--publish", "--skip-x"], sandbox, channels=("ig",))
    assert r.returncode == 0, r.stdout
    assert log_entries(sandbox)[0]["channels"]["x"]["status"] == "disabled"


def test_legacy_log_keys_preserved(sandbox):
    """posted.jsonl consumers still read top-level x / ig ids."""
    install(sandbox, "x", "ok")
    install(sandbox, "ig", "ok")
    prepare(sandbox)
    run(["--publish"], sandbox, channels=("x", "ig"))
    e = log_entries(sandbox)[0]
    assert e["x"] == "1234567890" and e["ig"] == "ig-987"


# ---------- notify ----------

def test_notify_noop_without_webhook(monkeypatch):
    import notify
    monkeypatch.delenv("NOTIFY_WEBHOOK_URL", raising=False)
    called = []
    monkeypatch.setattr(notify.requests, "post",
                        lambda *a, **k: called.append(a))
    assert notify.failure("t", "card", {"x": {"status": "failed", "id": None,
                                              "error": "boom"}}) is False
    assert called == [], "must not reach the network when unconfigured"


def test_notify_never_raises(monkeypatch):
    import notify

    def explode(*a, **k):
        raise RuntimeError("webhook down")

    monkeypatch.setenv("NOTIFY_WEBHOOK_URL", "https://hooks.slack.test/xyz")
    monkeypatch.setattr(notify.requests, "post", explode)
    assert notify.failure("t", "card", {}) is False


def test_notify_never_logs_the_url(monkeypatch, capsys):
    import notify
    secret = "https://hooks.slack.test/T000/B000/supersecrettoken"

    def explode(*a, **k):
        raise RuntimeError("webhook down")

    monkeypatch.setenv("NOTIFY_WEBHOOK_URL", secret)
    monkeypatch.setattr(notify.requests, "post", explode)
    notify.failure("t", "card", {})
    out = capsys.readouterr()
    assert secret not in out.out and secret not in out.err
    assert "supersecrettoken" not in out.out


def test_notify_sends_slack_text_payload(monkeypatch):
    import notify
    sent = {}

    class Resp:
        status_code = 200

    def fake_post(url, json=None, timeout=None):
        sent.update(url=url, json=json)
        return Resp()

    monkeypatch.setenv("NOTIFY_WEBHOOK_URL", "https://hooks.slack.test/xyz")
    monkeypatch.setattr(notify.requests, "post", fake_post)
    ok = notify.failure("fit-scoring", "card",
                        {"x": {"status": "failed", "id": None,
                               "error": "RuntimeError: boom"}})
    assert ok is True
    assert set(sent["json"]) == {"text"}, "Slack incoming-webhook shape"
    assert "fit-scoring" in sent["json"]["text"]
    assert "failed" in sent["json"]["text"]


def test_heartbeat_counts_only_confirmed_posts(tmp_path):
    import notify
    log = tmp_path / "posted.jsonl"
    rows = [
        {"date": "2026-07-20", "channels": {"x": {"status": "posted"}}},
        {"date": "2026-07-22", "channels": {"x": {"status": "failed"},
                                            "ig": {"status": "skipped"}}},
        {"date": "2026-07-25", "channels": {"ig": {"status": "posted"}}},
        {"date": "2026-06-01", "channels": {"x": {"status": "posted"}}},
    ]
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    count, last = notify.heartbeat_stats(str(log), today="2026-07-26")
    assert count == 2, "failed/skipped rows and rows older than 7d excluded"
    assert last == "2026-07-25"


def test_heartbeat_stats_handles_missing_log(tmp_path):
    import notify
    count, last = notify.heartbeat_stats(str(tmp_path / "nope.jsonl"),
                                         today="2026-07-26")
    assert count == 0 and last is None


# ---------- format rotation (item 8: topic/format lock) ----------

def test_format_is_not_locked_to_topic():
    """24 topics over 4 slots: gcd(24,4)=4 locked each topic to one format.

    fit-scoring was always a card, subaward-intel always a video. Every
    topic must now work through the whole format vocabulary.
    """
    import run as engine_run
    n_topics = 24
    every_format = set(engine_run.FORMATS)
    seen = {}
    for run_count in range(n_topics * len(engine_run.FORMATS)):
        topic = run_count % n_topics
        seen.setdefault(topic, set()).add(
            engine_run.select_format(run_count, n_topics))
    for topic, formats in seen.items():
        assert formats == every_format, (
            f"topic {topic} only ever renders as {sorted(formats)}")


def test_format_selection_is_deterministic():
    import run as engine_run
    assert (engine_run.select_format(0, 24)
            == engine_run.select_format(0, 24) == engine_run.FORMATS[0])


def test_format_is_not_locked_at_any_topic_count():
    """The offset must survive ANY topic count, not just today's.

    `(run_count + cycle)` expands to `index + cycle*(topic_count + 1)`, so
    it re-locks whenever topic_count + 1 is a multiple of len(FORMATS).
    Adding a fifth format made 24 topics hit exactly that.
    """
    import run as engine_run
    every = set(engine_run.FORMATS)
    for n_topics in range(4, 40):
        seen = {}
        for rc in range(n_topics * len(engine_run.FORMATS)):
            seen.setdefault(rc % n_topics, set()).add(
                engine_run.select_format(rc, n_topics))
        for topic, formats in seen.items():
            assert formats == every, (
                f"{n_topics} topics: topic {topic} locked to {sorted(formats)}")
