"""W4 — the X link moves out of the post body into a threaded reply.

Link posts are demoted in distribution, so the body ships clean and the CTA
follows as a reply. Two consequences the tests pin:

  * No "http" anywhere in a body, for any topic or format.
  * The body budget grows by the whole CTA block, which is what makes the
    truncation problem tractable.

The reply is posted after the body is already public. If it fails, the post
is NOT retried — that would double-post — so the channel still counts as
posted and the reply failure is recorded alongside it.
"""
import json
import os
import sys
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import captions  # noqa: E402
import links     # noqa: E402

FORMATS = ["card", "screenshot", "video"]
UTM_KEYS = {"utm_source", "utm_medium", "utm_campaign", "utm_content"}


@pytest.fixture(scope="module")
def cal():
    with open(os.path.join(ROOT, "content", "calendar.json")) as f:
        return json.load(f)


# ---------- the body carries no link ----------

def test_no_url_in_any_x_body(cal):
    for topic in cal["topics"]:
        for fmt in FORMATS:
            body = captions.build_x(topic, cal["brand"], fmt=fmt, fresh=False)
            assert "http" not in body, f"{topic['id']}/{fmt} body has a link"
            assert "pursuitai.net" not in body


def test_x_body_keeps_its_hashtags(cal):
    for topic in cal["topics"]:
        body = captions.build_x(topic, cal["brand"], fmt="card", fresh=False)
        assert "#GovCon" in body


def test_x_body_within_limit(cal):
    for topic in cal["topics"]:
        body = captions.build_x(topic, cal["brand"], fmt="card", fresh=False)
        assert len(body) <= 280, f"{topic['id']} body {len(body)} chars"


def test_removing_the_link_widened_the_body_budget(cal):
    """The CTA block was ~62 chars of the 280. Anything under 250 should
    now survive intact — it would not have at the old 218 budget."""
    for topic in cal["topics"]:
        if len(topic["hook_x"]) <= 250:
            body = captions.build_x(topic, cal["brand"], fmt="card",
                                    fresh=False)
            assert "…" not in body, f"{topic['id']} still truncated"


# ---------- the reply carries the tagged link ----------

def test_reply_contains_all_utm_params(cal):
    for topic in cal["topics"]:
        for fmt in FORMATS:
            reply = captions.build_x_reply(topic, cal["brand"], fmt=fmt)
            for key in UTM_KEYS:
                assert key in reply, f"{topic['id']}/{fmt} reply missing {key}"
            assert f"utm_campaign={topic['id']}" in reply
            assert f"utm_content={fmt}" in reply


def test_reply_points_at_the_landing_page(cal):
    reply = captions.build_x_reply(cal["topics"][0], cal["brand"], fmt="card")
    assert "https://pursuitai.net/?" in reply
    assert "/app" not in reply


def test_reply_within_weighted_limit(cal):
    for topic in cal["topics"]:
        reply = captions.build_x_reply(topic, cal["brand"], fmt="card")
        urls = [w for w in reply.split() if w.startswith("http")]
        assert len(urls) == 1
        assert links.x_weighted_length(reply, urls) <= 280


def test_reply_mentions_the_trial(cal):
    reply = captions.build_x_reply(cal["topics"][0], cal["brand"], fmt="card")
    assert "14-day" in reply


# ---------- pending.json carries both ----------

def test_prepare_writes_body_and_reply(tmp_path):
    import shutil
    import subprocess
    dst = tmp_path / "proj"
    for d in ("engine", "content"):
        shutil.copytree(os.path.join(ROOT, d), dst / d)
    for f in ("state.json", "pending.json", "approved.json"):
        p = dst / "content" / f
        if p.exists():
            p.unlink()
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    subprocess.run([sys.executable, "engine/run.py", "--prepare"],
                   cwd=str(dst), env=env, capture_output=True, text=True)
    pending = json.load(open(dst / "content" / "pending.json"))
    assert "http" not in pending["text_x"]
    assert "utm_campaign=" in pending["text_x_reply"]


# ---------- post_x threads the reply ----------

class FakeResponse:
    def __init__(self, tweet_id):
        self.data = {"id": tweet_id}


class FakeClient:
    """Records create_tweet calls so the threading can be asserted."""

    calls = []
    reply_raises = False

    def __init__(self, **kwargs):
        pass

    def create_tweet(self, text=None, media_ids=None, in_reply_to_tweet_id=None):
        FakeClient.calls.append({"text": text, "media_ids": media_ids,
                                 "in_reply_to_tweet_id": in_reply_to_tweet_id})
        if in_reply_to_tweet_id is not None and FakeClient.reply_raises:
            raise RuntimeError("reply rejected")
        return FakeResponse(999 if in_reply_to_tweet_id else 111)


@pytest.fixture()
def fake_tweepy(monkeypatch):
    FakeClient.calls = []
    FakeClient.reply_raises = False
    module = types.ModuleType("tweepy")
    module.Client = FakeClient
    module.OAuth1UserHandler = lambda *a, **k: None
    module.API = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "tweepy", module)
    for k in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET"):
        monkeypatch.setenv(k, "test")
    return FakeClient


def test_reply_is_threaded_to_the_body(fake_tweepy):
    import post_x
    result = post_x.post("body text", reply_text="cta https://pursuitai.net/?x=1")
    body_call, reply_call = fake_tweepy.calls
    assert body_call["in_reply_to_tweet_id"] is None
    assert reply_call["in_reply_to_tweet_id"] == 111, "reply must thread"
    assert result["id"] == "111" and result["reply_id"] == "999"


def test_no_reply_posted_when_none_requested(fake_tweepy):
    import post_x
    result = post_x.post("body text")
    assert len(fake_tweepy.calls) == 1
    assert result["reply_id"] is None


def test_reply_failure_does_not_retry_the_body(fake_tweepy):
    """The body is already public. Re-raising would make publish() mark the
    channel failed, and the next run would post it a second time."""
    fake_tweepy.reply_raises = True
    import post_x
    result = post_x.post("body text", reply_text="cta https://x.test/")
    bodies = [c for c in fake_tweepy.calls if c["in_reply_to_tweet_id"] is None]
    assert len(bodies) == 1, "body must be posted exactly once"
    assert result["id"] == "111", "the post succeeded and must be recorded"
    assert result["reply_id"] is None
    assert "reply rejected" in result["reply_error"]
