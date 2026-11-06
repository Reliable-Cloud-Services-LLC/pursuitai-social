"""LinkedIn 21-day message test.

Off unless PILOT_MODE=1 or content/pilot.json says enabled. While on, X and
Instagram are not published, LinkedIn rotates five angles, and every link
uses the same UTM quartet: source linkedin, medium organic, campaign
pilot21, content = the angle id.
"""
import json
import os
import shutil
import subprocess
import sys
from urllib.parse import parse_qs, urlparse

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import cards     # noqa: E402
import compliance  # noqa: E402
import notify    # noqa: E402
import pilot     # noqa: E402
import rotation  # noqa: E402
import stale_channel  # noqa: E402

ANGLE_IDS = ["fifty-percent", "sam-search", "teaming",
             "proposal-load", "recompete"]
UTM = {
    "utm_source": "linkedin",
    "utm_medium": "organic",
    "utm_campaign": "pilot21",
}


def _params(url):
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


# ---------- the switch ----------

def test_shipped_config_is_off_and_uses_the_agreed_slugs():
    cfg = json.load(open(os.path.join(ROOT, "content", "pilot.json")))
    assert cfg["enabled"] is False
    assert cfg["campaign"] == "pilot21"
    assert [a["id"] for a in cfg["angles"]] == ANGLE_IDS


def test_off_by_default(monkeypatch):
    monkeypatch.delenv("PILOT_MODE", raising=False)
    assert pilot.active() is False


def test_only_the_digit_one_enables(monkeypatch):
    monkeypatch.setenv("PILOT_MODE", "1")
    assert pilot.active() is True
    monkeypatch.setenv("PILOT_MODE", "true")
    assert pilot.active() is False
    monkeypatch.setenv("PILOT_MODE", "")
    assert pilot.active() is False


def test_zero_beats_an_enabled_file(monkeypatch, tmp_path):
    cfg = tmp_path / "pilot.json"
    cfg.write_text(json.dumps({"enabled": True, "campaign": "pilot21",
                               "angles": []}))
    monkeypatch.setattr(pilot, "CONFIG", str(cfg))
    monkeypatch.setenv("PILOT_MODE", "0")
    assert pilot.active() is False
    monkeypatch.delenv("PILOT_MODE")
    assert pilot.active() is True


# ---------- links, log fields, cards ----------

def test_every_angle_link_uses_the_pilot_utm():
    with open(os.path.join(ROOT, "content", "calendar.json")) as f:
        brand = json.load(f)["brand"]
    for angle in pilot.angles():
        tags = pilot.utm_for(angle)
        assert tags["utm_source"] == "linkedin"
        assert tags["utm_medium"] == "organic"
        assert tags["utm_campaign"] == "pilot21"
        assert tags["utm_content"] == angle["id"]
        text = pilot.linkedin_text(angle, brand, "fit-scoring")
        url = next(w for w in text.split() if w.startswith("http"))
        assert _params(url) == tags
        assert url.startswith("https://pursuitai.net/")


def test_pilot_copy_passes_compliance():
    with open(os.path.join(ROOT, "content", "calendar.json")) as f:
        cal = json.load(f)
    topic = next(t for t in cal["topics"] if compliance.is_publishable(t))
    for angle in pilot.angles():
        text = pilot.linkedin_text(angle, cal["brand"], topic["id"])
        fields = [text, angle["headline"], angle["body"], angle["stat"]]
        for field in fields:
            problems = compliance.check_claims(topic, field)
            assert not problems, (angle["id"], problems)


def test_pilot_cards_fit_the_linkedin_canvas(tmp_path):
    with open(os.path.join(ROOT, "content", "calendar.json")) as f:
        brand = json.load(f)["brand"]
    for angle in pilot.angles():
        img = cards.render_card(
            pilot.card_topic(angle), brand, size=(1600, 900),
            out_path=str(tmp_path / f"{angle['id']}.png"))
        assert img.size == (1600, 900)


def test_angles_rotate_in_config_order():
    rows = [{"id": i} for i in ANGLE_IDS]
    assert [pilot.pick(rows, n)["id"] for n in range(6)] == ANGLE_IDS + [
        "fifty-percent"]


def test_a_handoff_advances_the_angle_and_a_draft_does_not():
    rows = [
        {"angle_id": "fifty-percent",
         "channels": {"linkedin": {"status": "handoff"}}},
        {"angle_id": "sam-search",
         "channels": {"linkedin": {"status": "drafted"}}},
        {"angle_id": "teaming",
         "channels": {"linkedin": {"status": "posted", "id": "urn:1"}}},
    ]
    assert pilot.shipped_count(rows) == 2
    assert pilot.pick(pilot.angles(), pilot.shipped_count(rows))["id"] == (
        ANGLE_IDS[2])


# ---------- winner report ----------

def _metric(post_id, collected_at, channel="linkedin", **m):
    return {"post_id": post_id, "collected_at": collected_at,
            "channel": channel, "metrics": m}


def test_self_replies_are_not_engagement():
    assert pilot.engagement_total(
        {"reply_count": 1, "like_count": 0}, self_replies=1) == 0
    assert pilot.engagement_total(
        {"reply_count": 1, "like_count": 2}, self_replies=1) == 2


def test_the_winner_ignores_the_bots_own_reply_and_names_utm_content():
    """reply_count 1 with a recorded reply_id is the CTA the bot posted
    under itself. Counting it would crown the angle nobody answered.

    The reply tweet's own metrics must not be added either — that row is
    the self-reply, not a second post.
    """
    posted = [
        {"date": "2026-11-05", "angle_id": "fifty-percent", "pilot": True,
         "utm": {**UTM, "utm_content": "fifty-percent"},
         "channels": {
             "x": {"status": "posted", "id": "100", "reply_id": "101"},
             "linkedin": {"status": "posted", "id": "urn:1"}}},
        {"date": "2026-11-05", "angle_id": "sam-search", "pilot": True,
         "utm": {**UTM, "utm_content": "sam-search"},
         "channels": {"linkedin": {"status": "posted", "id": "urn:2"}}},
        # Not part of the test. Must not move either total.
        {"date": "2026-11-05", "topic": "fit-scoring",
         "channels": {"x": {"status": "posted", "id": "999"}}},
    ]
    metrics = [
        _metric("100", "2026-11-06T00:00:00+00:00", channel="x",
                impression_count=10, like_count=0, reply_count=1),
        _metric("101", "2026-11-06T00:00:00+00:00", channel="x",
                impression_count=50, like_count=4, reply_count=0),
        _metric("urn:2", "2026-11-06T00:00:00+00:00",
                impression_count=100, like_count=5, click_count=2),
    ]
    summary = pilot.summarize(posted, metrics, pilot.angles(),
                              today="2026-11-06")
    by_id = {row["id"]: row for row in summary["angles"]}
    assert by_id["fifty-percent"]["week"]["impressions"] == 10
    assert by_id["fifty-percent"]["week"]["engagement"] == 0
    assert by_id["fifty-percent"]["week"]["clicks"] is None
    assert by_id["sam-search"]["week"]["engagement"] == 5
    assert by_id["sam-search"]["week"]["clicks"] == 2
    assert summary["winner"] == "sam-search"
    assert summary["campaign"] == "pilot21"
    text = pilot.render_report(summary)
    assert "utm_campaign `pilot21`" in text
    assert "utm_content=sam-search" in text
    assert "Winner this week: `sam-search`" in text
    assert "own replies" in text
    # fifty-percent would win if the self-reply counted (rate 0.1 > 0.05).
    assert "fifty-percent" in text and "WINNER" not in text.split(
        "fifty-percent")[1].split("\n")[0]


def test_clicks_are_omitted_when_the_platform_has_none():
    posted = [{
        "date": "2026-11-05", "angle_id": "teaming", "pilot": True,
        "channels": {"linkedin": {"status": "posted", "id": "urn:9"}},
    }]
    metrics = [_metric("urn:9", "2026-11-06T00:00:00+00:00",
                       impression_count=4, like_count=1)]
    summary = pilot.summarize(posted, metrics, [{"id": "teaming"}],
                              today="2026-11-06")
    stats = summary["angles"][0]["week"]
    assert stats["clicks"] is None
    assert "n/a clicks" in pilot.render_report(summary)


def test_report_stays_quiet_when_nothing_has_an_angle(tmp_path, monkeypatch):
    log = tmp_path / "posted.jsonl"
    log.write_text(json.dumps({"date": "2026-11-01", "topic": "a"}) + "\n")
    called = []
    monkeypatch.setattr(notify, "pilot_report",
                        lambda text: called.append(text) or True)
    assert pilot.deliver_report(str(log), str(tmp_path / "m.jsonl")) is False
    assert called == []


def test_handoff_message_carries_the_utm(monkeypatch):
    sent = {}
    monkeypatch.setenv("NOTIFY_WEBHOOK_URL", "https://hooks.slack.test/xyz")
    monkeypatch.setattr(notify, "_send",
                        lambda text: sent.update(text=text) or True)
    assert notify.pilot_handoff({
        "angle_id": "proposal-load", "topic": "fit-scoring",
        "text_linkedin": "paste me",
        "utm": {**UTM, "utm_content": "proposal-load"},
    }, "LINKEDIN_ACCESS_TOKEN not set") is True
    body = sent["text"]
    assert "post this by hand" in body
    assert "utm_source=linkedin" in body
    assert "utm_medium=organic" in body
    assert "utm_campaign=pilot21" in body
    assert "utm_content=proposal-load" in body
    assert "paste me" in body


# ---------- the product rotation is not the message test ----------

def test_pilot_posts_do_not_count_as_topic_evidence():
    rows = [{"topic": "fit-scoring", "pilot": True,
             "channels": {"linkedin": {"status": "posted", "id": "u"}}}]
    assert rotation.topic_post_counts(rows) == {}
    assert rotation.topic_scores(rows, [{
        "post_id": "u", "collected_at": "2026-11-06T00:00:00+00:00",
        "metrics": {"like_count": 10, "impression_count": 10}}]) == {}


def test_only_a_pilot_handoff_counts_as_a_confirmed_post():
    assert not stale_channel.is_confirmed(
        {"channels": {"linkedin": {"status": "handoff"}}})
    assert stale_channel.is_confirmed({
        "pilot": True,
        "channels": {"linkedin": {"status": "handoff"}}})
    assert not stale_channel.is_confirmed(
        {"pilot": True,
         "channels": {"linkedin": {"status": "skipped"}}})


# ---------- publish wiring ----------

def _sandbox(tmp_path):
    dst = tmp_path / "proj"
    for name in ("engine", "content"):
        shutil.copytree(os.path.join(ROOT, name), dst / name)
    for filename in ("state.json", "pending.json", "approved.json"):
        path = dst / "content" / filename
        if path.exists():
            path.unlink()
    return str(dst)


def _run(args, cwd, extra=None):
    env = dict(os.environ)
    for var in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN",
                "X_ACCESS_SECRET", "IG_USER_ID", "IG_ACCESS_TOKEN",
                "ANTHROPIC_API_KEY", "NOTIFY_WEBHOOK_URL",
                "LINKEDIN_ACCESS_TOKEN", "LINKEDIN_ORG_ID",
                "LINKEDIN_ALLOW_MEMBER_POST", "LINKEDIN_DRAFT",
                "PILOT_MODE", "GITHUB_ACTIONS"):
        env.pop(var, None)
    env.update(extra or {})
    return subprocess.run([sys.executable, "engine/run.py", *args],
                          cwd=cwd, env=env, capture_output=True, text=True)


def _install(sandbox, channel, source):
    with open(os.path.join(sandbox, "engine", f"post_{channel}.py"), "w") as f:
        f.write(source)


def test_prepare_without_pilot_does_not_stamp_an_angle(tmp_path):
    sandbox = _sandbox(tmp_path)
    r = _run(["--prepare"], sandbox)
    assert r.returncode == 0, r.stdout + r.stderr
    pending = json.load(open(os.path.join(sandbox, "content", "pending.json")))
    assert "angle_id" not in pending
    assert "utm" not in pending
    assert pending["text_x"] and pending["text_ig"] and pending["text_linkedin"]


def test_pilot_skips_x_and_ig_and_hands_linkedin_off(tmp_path):
    sandbox = _sandbox(tmp_path)
    _install(sandbox, "x",
             "def post(*a, **k):\n"
             "    raise AssertionError('X must not post in pilot mode')\n")
    _install(sandbox, "ig",
             "def post_image(*a, **k):\n"
             "    raise AssertionError('IG must not post in pilot mode')\n"
             "def post_reel(*a, **k):\n"
             "    raise AssertionError('IG must not post in pilot mode')\n")
    env = {"PILOT_MODE": "1", "X_API_KEY": "k", "IG_USER_ID": "u"}
    r = _run(["--prepare"], sandbox, env)
    assert r.returncode == 0, r.stdout + r.stderr
    pending = json.load(open(os.path.join(sandbox, "content", "pending.json")))
    assert pending["angle_id"] == "fifty-percent"
    assert pending["pilot"] is True
    assert pending["utm"] == {**UTM, "utm_content": "fifty-percent"}
    url = next(w for w in pending["text_linkedin"].split() if w.startswith("http"))
    assert _params(url) == pending["utm"]
    assert pending["text_x"] and pending["text_ig"]
    assert pending["media_linkedin"].endswith("pilot-fifty-percent_x.png")

    r = _run(["--approve"], sandbox, env)
    assert r.returncode == 0, r.stdout + r.stderr
    r = _run(["--publish"], sandbox, env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "X must not post" not in r.stdout + r.stderr
    entry = json.loads(open(os.path.join(
        sandbox, "logs", "posted.jsonl")).read().splitlines()[-1])
    assert entry["angle_id"] == "fifty-percent"
    assert entry["utm"]["utm_campaign"] == "pilot21"
    assert entry["utm"]["utm_content"] == "fifty-percent"
    assert entry["channels"]["x"]["status"] == "disabled"
    assert entry["channels"]["ig"]["status"] == "disabled"
    assert entry["channels"]["linkedin"]["status"] == "handoff"
    assert entry["outcome"] == "handoff"
    assert "LINKEDIN_ACCESS_TOKEN" in entry["channels"]["linkedin"]["error"]
    assert not os.path.exists(os.path.join(sandbox, "content", "state.json"))

    r = _run(["--prepare"], sandbox, env)
    assert r.returncode == 0, r.stdout + r.stderr
    nxt = json.load(open(os.path.join(sandbox, "content", "pending.json")))
    assert nxt["angle_id"] == "sam-search"
    assert nxt["utm"]["utm_content"] == "sam-search"


def test_pilot_posts_as_the_member_when_there_is_no_org(tmp_path):
    sandbox = _sandbox(tmp_path)
    _install(sandbox, "x",
             "def post(*a, **k):\n    raise AssertionError('X')\n")
    _install(sandbox, "ig",
             "def post_image(*a, **k):\n    raise AssertionError('IG')\n"
             "def post_reel(*a, **k):\n    raise AssertionError('IG')\n")
    _install(sandbox, "linkedin",
             "def post_media(path, caption, alt_text=None):\n"
             "    return {'id': 'urn:li:share:555', 'url': 'https://li/555'}\n")
    env = {"PILOT_MODE": "1", "X_API_KEY": "k", "IG_USER_ID": "u",
           "LINKEDIN_ACCESS_TOKEN": "tok"}
    assert _run(["--prepare"], sandbox, env).returncode == 0
    assert _run(["--approve"], sandbox, env).returncode == 0
    r = _run(["--publish"], sandbox, env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "member" in r.stdout
    entry = json.loads(open(os.path.join(
        sandbox, "logs", "posted.jsonl")).read().splitlines()[-1])
    assert entry["channels"]["linkedin"]["status"] == "posted"
    assert entry["linkedin"] == "urn:li:share:555"
    assert entry["channels"]["x"]["status"] == "disabled"
    assert entry["angle_id"] == "fifty-percent"
    assert not os.path.exists(os.path.join(sandbox, "content", "state.json"))


def test_docs_and_workflows_name_the_switch():
    readme = open(os.path.join(ROOT, "README.md")).read()
    ops = open(os.path.join(ROOT, "docs", "OPERATIONS.md")).read()
    for doc in (readme, ops):
        assert "PILOT_MODE" in doc
        assert "pilot21" in doc
        assert "content/pilot.json" in doc or "content/pilot.json" in doc
        for angle_id in ANGLE_IDS:
            assert angle_id in doc
        assert "utm_content" in doc
    daily = open(os.path.join(ROOT, ".github", "workflows", "daily.yml")).read()
    analytics = open(os.path.join(
        ROOT, ".github", "workflows", "analytics.yml")).read()
    assert daily.count("PILOT_MODE:") >= 2
    assert "PILOT_MODE:" in analytics
    assert "NOTIFY_WEBHOOK_URL" in analytics
    assert "LINKEDIN_ACCESS_TOKEN" in analytics
