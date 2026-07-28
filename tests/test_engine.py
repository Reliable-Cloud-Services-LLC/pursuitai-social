"""Test suite for the PursuitAI social engine. Run: pytest tests/ -v"""
import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import captions  # noqa: E402
import cards     # noqa: E402

@pytest.fixture(scope="session")
def cal():
    with open(os.path.join(ROOT, "content", "calendar.json")) as f:
        return json.load(f)

# ---------- calendar schema ----------

REQUIRED = ["id", "feature", "headline", "body", "hook_x", "hook_ig",
            "stat", "media"]

def test_calendar_topics_complete(cal):
    assert len(cal["topics"]) >= 20
    for t in cal["topics"]:
        for k in REQUIRED:
            assert t.get(k), f"topic {t.get('id')} missing {k}"

def test_calendar_ids_unique(cal):
    ids = [t["id"] for t in cal["topics"]]
    assert len(ids) == len(set(ids))

def test_brand_config(cal):
    b = cal["brand"]
    for k in ("url", "trial_url", "x_handle", "hashtags_x", "hashtags_ig"):
        assert b.get(k)
    assert b["url"].startswith("https://pursuitai.net")

# ---------- captions ----------

def test_x_captions_within_limit(cal):
    for t in cal["topics"]:
        text = captions.build_x(t, cal["brand"], fresh=False)
        assert len(text) <= 280, f"{t['id']} X caption {len(text)} chars"
        assert "pursuitai.net" in text
        assert "14-day" in text

def test_ig_captions_have_cta_and_tags(cal):
    for t in cal["topics"]:
        text = captions.build_ig(t, cal["brand"], fresh=False)
        assert "pursuitai.net" in text
        assert "#GovCon" in text or "#FederalContracting" in text
        assert len(text) <= 2200, f"{t['id']} IG caption too long"

def test_ig_caption_always_carries_the_primary_hashtag(cal):
    """build_ig samples 8 of 10 tags; #GovCon used to be droppable.

    P(both #GovCon and #FederalContracting excluded) was 1/C(10,2) per
    topic, so ~42% of full-suite runs failed and, worse, real posts shipped
    without the category's primary tag. The first entry in hashtags_ig is
    the primary tag and is now always present — matching build_x, which
    already takes hashtags_x[:2] deterministically.
    """
    primary = cal["brand"]["hashtags_ig"][0]
    for t in cal["topics"]:
        for _ in range(25):   # sampling is random; assert over many draws
            assert primary in captions.build_ig(t, cal["brand"], fresh=False)

def test_ig_caption_tag_count_and_variety_preserved(cal):
    """Pinning the primary must not reduce the tag count or kill rotation."""
    topic, brand = cal["topics"][0], cal["brand"]
    seen = set()
    for _ in range(50):
        tags = [w for w in captions.build_ig(topic, brand, fresh=False).split()
                if w.startswith("#")]
        assert len(tags) == 8
        assert len(set(tags)) == 8, "no tag may repeat within one caption"
        assert all(tag in brand["hashtags_ig"] for tag in tags)
        seen.add(tuple(sorted(tags)))
    assert len(seen) > 1, "the non-primary tags must still vary between posts"

def test_claude_variant_fails_safe(cal, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "invalid-key-for-test")
    # must fall back to template, never raise
    text = captions.build_x(cal["topics"][0], cal["brand"], fresh=True)
    assert len(text) <= 280

# ---------- cards ----------

def test_card_renders_both_sizes(cal, tmp_path):
    t = cal["topics"][0]
    for size in ((1600, 900), (1080, 1350)):
        p = str(tmp_path / f"c_{size[0]}.png")
        img = cards.render_card(t, cal["brand"], size=size, out_path=p)
        assert img.size == size
        assert os.path.getsize(p) > 20_000

def test_all_topics_render(cal, tmp_path):
    for t in cal["topics"]:
        img = cards.render_card(t, cal["brand"], size=(1080, 1350))
        assert img.size == (1080, 1350)

# ---------- video ----------

def test_video_builds(cal, tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not available")
    import video
    out = str(tmp_path / "t.mp4")
    video.make_video(cal["topics"][0], out)
    assert os.path.getsize(out) > 100_000
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", out], capture_output=True, text=True)
    assert 10 <= float(probe.stdout.strip()) <= 20

# ---------- orchestrator state machine ----------

def _run(args, cwd, env=None):
    e = dict(os.environ)
    e.pop("X_API_KEY", None)
    e.pop("IG_USER_ID", None)
    if env:
        e.update(env)
    return subprocess.run([sys.executable, "engine/run.py"] + args,
                          cwd=cwd, env=e, capture_output=True, text=True)

@pytest.fixture()
def sandbox(tmp_path):
    """Copy of the project without state, for rotation tests."""
    dst = tmp_path / "proj"
    for d in ("engine", "content"):
        shutil.copytree(os.path.join(ROOT, d), dst / d)
    for f in ("state.json", "pending.json"):
        p = dst / "content" / f
        if p.exists():
            p.unlink()
    return str(dst)

def test_publish_without_credentials_does_not_consume_topic(sandbox):
    r = _run(["--prepare"], sandbox)
    assert r.returncode == 0, r.stderr
    pending = json.load(open(os.path.join(sandbox, "content", "pending.json")))
    assert pending["topic"] == "fit-scoring"
    assert pending["format"] == "card"
    assert os.path.exists(os.path.join(sandbox, pending["media_x"]))

    # W1: no creds -> both channels skipped -> nothing posted -> loud failure
    # and the topic is preserved for a run that can actually publish it.
    r = _run(["--publish"], sandbox)
    assert r.returncode == 1, r.stdout
    assert not os.path.exists(os.path.join(sandbox, "content", "state.json"))
    assert not os.path.exists(os.path.join(sandbox, "content", "pending.json"))
    log = open(os.path.join(sandbox, "logs", "posted.jsonl")).read().strip()
    entry = json.loads(log)
    assert entry["x"] is None and entry["ig"] is None

def test_publish_without_prepare_fails(sandbox):
    r = _run(["--publish"], sandbox)
    assert r.returncode == 1

def test_format_rotation_cycles(sandbox):
    seen = []
    for _ in range(4):
        _run(["--prepare"], sandbox)
        p = json.load(open(os.path.join(sandbox, "content", "pending.json")))
        seen.append(p["format"])
        _run(["--publish"], sandbox)
    # screenshot/video fall back to card offline; card must appear, no crash
    assert all(f in ("card", "screenshot", "video") for f in seen)
    # W1: publishing nothing consumes nothing, so state is never written.
    # Rotation under successful publishes is covered in
    # test_publish_outcomes.py::test_format_is_not_locked_to_topic.
    assert not os.path.exists(os.path.join(sandbox, "content", "state.json"))
