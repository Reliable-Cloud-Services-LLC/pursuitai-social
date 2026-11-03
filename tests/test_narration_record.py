"""What the ad actually says, and whether it can be said.

The narration for an animated ad is drafted by Claude at render time. It was
never recorded anywhere — synthesized, muxed, and discarded — so after an ad
shipped, nothing in the repo could tell you what it had said. A mispronounced
acronym or an off-message line surfaced only if a human listened, and then
could not be traced.

Two changes, tested here:

  * the script is recorded on pending.json and therefore in posted.jsonl,
    in both the written and the spoken form
  * a draft carrying jargon the lexicon has never seen falls back to the
    deterministic script rather than being synthesized
"""
import json
import os
import shutil
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import narration  # noqa: E402
import pronounce  # noqa: E402
import run  # noqa: E402


@pytest.fixture
def fast_ad(monkeypatch):
    """Skip the render. These tests are about what gets RECORDED, not about
    pixels — and a real render costs two minutes and needs ffmpeg + torch,
    neither of which the main CI job has."""
    import adspot
    import voice

    def fake_ad(topic, brand, out_path, **kw):
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "wb") as f:
            f.write(b"not a real mp4")
        return out_path

    monkeypatch.setattr(adspot, "make_ad", fake_ad)
    monkeypatch.setattr(voice, "synthesize", lambda text, path: None)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    dst = tmp_path / "repo"
    dst.mkdir()
    for d in ("engine", "content", "assets", "logs"):
        src = os.path.join(ROOT, d)
        if os.path.exists(src):
            shutil.copytree(src, dst / d)
        else:
            (dst / d).mkdir()
    monkeypatch.setattr(run, "ROOT", str(dst))
    for name in ("STATE", "PENDING", "APPROVED", "LOG", "METRICS"):
        rel = os.path.relpath(getattr(run, name), ROOT)
        monkeypatch.setattr(run, name, str(dst / rel))
    return dst


@pytest.fixture
def cal():
    with open(os.path.join(ROOT, "content", "calendar.json")) as f:
        return json.load(f)


def _topic(cal, index=0):
    import compliance
    return [t for t in cal["topics"] if compliance.is_publishable(t)][index]


# ---------- the untreated-jargon gate ----------

def test_a_draft_with_unknown_jargon_is_rejected(cal, monkeypatch, capsys):
    """The motivating case: the model reaches for an acronym the topic never
    used, and nothing downstream notices."""
    topic = _topic(cal)
    monkeypatch.setattr(
        narration, "_claude",
        lambda t, b: "Our CMMC and DCAA posture wins work. "
                     f"Start a free trial at {narration.SPOKEN_URL}.")
    script = narration.build(topic, cal["brand"])
    assert script == narration.fallback(topic, cal["brand"])
    out = capsys.readouterr().out
    assert "CMMC" in out and "DCAA" in out, "the rejection must name the terms"


def test_a_clean_draft_survives(cal, monkeypatch):
    topic = _topic(cal)
    clean = ("PursuitAI scores your NAICS space in seconds. "
             f"Start a free trial at {narration.SPOKEN_URL}.")
    monkeypatch.setattr(narration, "_claude", lambda t, b: clean)
    assert narration.build(topic, cal["brand"]) == clean


def test_a_draft_using_a_lexicon_term_survives(cal, monkeypatch):
    """A term WITH a rule is not jargon — rejecting it would push every
    draft to the fallback and quietly disable Claude."""
    topic = _topic(cal)
    draft = ("Track GWACs and CLINs across your NAICS space. "
             f"Start a free trial at {narration.SPOKEN_URL}.")
    monkeypatch.setattr(narration, "_claude", lambda t, b: draft)
    assert narration.build(topic, cal["brand"]) == draft


def test_the_fallback_itself_is_always_sayable(cal):
    """The gate falls back — so the fallback had better be clean, or a
    rejected draft lands somewhere equally broken."""
    for topic in cal["topics"]:
        import compliance
        if not compliance.is_publishable(topic):
            continue
        script = narration.fallback(topic, cal["brand"])
        assert not pronounce.untreated(script), topic["id"]


def test_untreated_runs_after_the_lexicon():
    """A term with a rule must not be reported: the check has to see what
    the synthesizer sees, not the raw text."""
    assert pronounce.untreated("NAICS and GWACs and GovCon") == []


# ---------- recording it ----------

def test_pending_records_both_forms_of_the_script(repo, fast_ad, monkeypatch):
    monkeypatch.setattr(narration, "_claude", lambda t, b: None)  # no network
    run.prepare(force_format="ad")
    with open(run.PENDING) as f:
        pending = json.load(f)
    assert pending["format"] == "ad", "the ad path did not run"
    assert pending["narration"], "the script was not recorded"
    assert pending["narration_spoken"] == \
        pronounce.spoken(pending["narration"])


def test_a_card_records_no_narration(repo):
    """A card has no audio. A script logged against one would be a line in
    the post log that was never spoken."""
    run.prepare(force_format="card")
    with open(run.PENDING) as f:
        pending = json.load(f)
    assert pending["narration"] is None
    assert pending["narration_spoken"] is None


def test_the_script_reaches_the_post_log(repo, fast_ad, monkeypatch):
    """pending.json is transient; posted.jsonl is the record that outlives
    the run, and it is the one worth being able to search."""
    monkeypatch.setattr(narration, "_claude", lambda t, b: None)
    run.prepare(force_format="ad")
    with open(run.PENDING) as f:
        expected = json.load(f)["narration"]
    run.approve()
    monkeypatch.setenv("X_API_KEY", "test")
    monkeypatch.delenv("IG_USER_ID", raising=False)
    monkeypatch.setattr(run, "POSTERS", {
        "x": ("X_API_KEY", lambda pending: "fake-id"),
        "ig": ("IG_USER_ID", lambda pending: "unreachable"),
    })
    try:
        run.publish()
    except SystemExit:
        pass
    with open(run.LOG) as f:
        entry = json.loads(f.read().strip().split("\n")[-1])
    assert entry["narration"] == expected
    assert entry["narration_spoken"]
