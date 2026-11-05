"""Angles — several points of view on one feature.

A topic today carries exactly one headline, one stat and one hook per
channel, so every time it comes round it renders the identical card and
makes the identical argument. The Claude rewrite varies the WORDS, which
helps, but it is still the same point: "Recompete Radar scans USAspending
for expiring contracts" phrased eight ways.

An angle is a different point. Pain ("by the time an RFP hits SAM.gov the
winner has worked it a year"), proof ("6 to 36 months of expiring contracts
in your NAICS"), audience ("if you are a sub trying to become a prime").
Each carries its own headline / body / stat / hooks.

Two things make this cheap. Everything downstream — cards.py, adspot.py,
narration.py, captions.py — reads `topic["headline"]`, `topic["stat"]` and
so on, so merging an angle into a copy of the topic dict varies the card
IMAGE and the voiceover as well as the text, with no change to any of those
modules. And a topic with no `angles` key resolves to exactly one angle —
its own fields — so the whole calendar keeps working untouched.

SELECTION is where this could go wrong. select_format's docstring is the
cautionary tale in this repo: indexing one rotation by a counter that also
drives another silently re-locks whenever the two share a factor, which is
how every topic got pinned to one format. So angles are indexed by the
topic's OWN post count — a number that means "how many times has this
topic shipped" and cannot share a period with the topic cursor or the
format cycle.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import angles  # noqa: E402

BASE = {
    "id": "demo", "feature": "Demo Feature",
    "headline": "base headline", "body": "base body", "stat": "base stat",
    "hook_x": "base x", "hook_ig": "base ig",
    "verification": {"status": "VERIFIED"},
}
EXTRA = {"headline": "angle headline", "body": "angle body",
         "stat": "angle stat", "hook_x": "angle x", "hook_ig": "angle ig"}


def test_a_topic_with_no_angles_has_exactly_one():
    """Back-compat. The whole calendar has no `angles` key today."""
    assert angles.angles_for(BASE) == [
        {k: BASE[k] for k in angles.ANGLE_FIELDS}]


def test_the_base_fields_are_always_the_first_angle():
    """Not a special case — the existing copy IS an angle, and a vetted
    one. Treating it as angle zero means nothing has to be rewritten to
    adopt this."""
    got = angles.angles_for({**BASE, "angles": [EXTRA]})
    assert got[0]["headline"] == "base headline"
    assert got[1]["headline"] == "angle headline"


def test_applying_an_angle_overrides_only_the_angle_fields():
    merged = angles.apply_angle(BASE, EXTRA)
    for f in angles.ANGLE_FIELDS:
        assert merged[f] == EXTRA[f]


def test_applying_an_angle_keeps_identity_and_verification():
    """id drives the media filename, the live-site anchor and the repeat
    cooldown; losing it would break all three silently."""
    merged = angles.apply_angle(BASE, EXTRA)
    assert merged["id"] == "demo"
    assert merged["feature"] == "Demo Feature"
    assert merged["verification"] == BASE["verification"]


def test_applying_an_angle_does_not_mutate_the_topic():
    """The calendar dict is shared across the run and gets written back."""
    original = dict(BASE)
    angles.apply_angle(BASE, EXTRA)
    assert BASE == original


def test_selection_advances_with_the_topics_own_post_count():
    three = [{"headline": f"a{i}"} for i in range(3)]
    picked = [angles.select(three, n)["headline"] for n in range(6)]
    assert picked == ["a0", "a1", "a2", "a0", "a1", "a2"]


def test_selection_is_stable_for_a_given_count():
    """Deterministic: a re-run of the same day picks the same angle."""
    three = [{"headline": f"a{i}"} for i in range(3)]
    assert angles.select(three, 4) == angles.select(three, 4)


def test_selection_handles_a_single_angle():
    one = [{"headline": "only"}]
    assert all(angles.select(one, n)["headline"] == "only" for n in range(5))


def test_selection_never_divides_by_zero():
    """An empty list means every angle failed compliance — the caller
    decides what to do, but this must not raise on the way there."""
    assert angles.select([], 3) is None


@pytest.mark.parametrize("count", [0, 1, 7, 100])
def test_selection_does_not_relock_against_the_format_cycle(count):
    """The select_format lesson, applied.

    Formats cycle over 4 and the topic cursor over 23. Indexing angles by
    EITHER of those would re-lock whenever the periods share a factor —
    which is exactly how every topic once got pinned to a single format.
    A topic's own post count shares no period with either, because it only
    advances when that topic ships.
    """
    import run as run_mod
    n_angles = 2                      # shares a factor with len(FORMATS)=4
    seen = set()
    for post in range(count, count + 8):
        fmt = run_mod.select_format(post, 23)
        angle = angles.select([{"i": 0}, {"i": 1}], post)["i"]
        seen.add((fmt, angle))
    assert len(seen) > 2, f"angle and format are locked together: {seen}"


# ---------------------------------------------------------------------------
# Wiring: prepare() must actually use an angle, and reject a dirty one aloud.
# ---------------------------------------------------------------------------

def test_a_dirty_angle_is_dropped_and_said_so(capsys):
    """Compliance applies per ANGLE, not just per topic.

    An angle carries its own headline, body and stat — the three fields
    rendered onto the card — so an unvetted one would ship a claim as
    pixels, which is precisely the hole check_rendered was added to close.

    Dropped aloud. Silently skipping a bad angle would leave a topic
    quietly posting from two of its three angles forever, and nothing
    would say which or why: the exact failure shape of the thin-data
    exile and the inert Claude variant.
    """
    import run as run_mod
    topic = {**BASE, "angles": [
        {"headline": "No competitor has this", "body": "b", "stat": "s",
         "hook_x": "x", "hook_ig": "ig"},
    ]}
    usable = run_mod.usable_angles(topic, {"hashtags_x": ["#a", "#b"],
                                           "hashtags_ig": ["#a"],
                                           "url": "https://x.test"})
    assert len(usable) == 1, "the dirty angle should have been dropped"
    assert usable[0]["headline"] == "base headline"
    out = capsys.readouterr().out
    assert "angle" in out.lower(), "a dropped angle must be reported"


def test_a_clean_angle_survives(capsys):
    """Negative control: the filter must not reject everything."""
    import run as run_mod
    topic = {**BASE, "angles": [EXTRA]}
    usable = run_mod.usable_angles(topic, {"hashtags_x": ["#a", "#b"],
                                           "hashtags_ig": ["#a"],
                                           "url": "https://x.test"})
    assert len(usable) == 2


def test_prepare_renders_the_selected_angle(monkeypatch, tmp_path):
    """End-to-end, because a correct helper that prepare never calls is
    the same as no helper — the lesson from format_requested, where
    nulling the wiring left every unit test green.
    """
    import json
    import shutil
    import run as run_mod

    root = tmp_path / "proj"
    shutil.copytree(os.path.join(ROOT, "content"), root / "content")
    (root / "assets" / "cards").mkdir(parents=True)
    for f in ("state.json", "pending.json", "approved.json"):
        if (root / "content" / f).exists():
            (root / "content" / f).unlink()

    cal = json.load(open(root / "content" / "calendar.json"))
    tid = "recompete-radar"
    for t in cal["topics"]:
        if t["id"] == tid:
            t["angles"] = [{"headline": "ANGLE HEADLINE", "body": t["body"],
                            "stat": "ANGLE STAT", "hook_x": "angle hook x",
                            "hook_ig": "angle hook ig"}]
    (root / "content" / "calendar.json").write_text(json.dumps(cal))

    (root / "logs").mkdir()
    # One prior post for this topic -> angle index 1, the added angle.
    (root / "logs" / "posted.jsonl").write_text(json.dumps({
        "date": "2026-01-01", "topic": tid, "x": "1",
        "channels": {"x": {"status": "posted", "id": "1"}}}) + "\n")

    monkeypatch.setattr(run_mod, "ROOT", str(root))
    monkeypatch.setattr(run_mod, "PENDING",
                        str(root / "content" / "pending.json"))
    monkeypatch.setattr(run_mod, "STATE", str(root / "content" / "state.json"))
    monkeypatch.setattr(run_mod, "LOG", str(root / "logs" / "posted.jsonl"))
    monkeypatch.setattr(run_mod, "METRICS", str(root / "logs" / "metrics.jsonl"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    pending = run_mod.prepare(force_format="card", force_topic=tid)
    assert pending["text_x"].startswith("angle hook x"), (
        f"prepare ignored the angle: {pending['text_x'][:60]!r}")


def test_prepare_uses_the_base_angle_on_a_topics_first_post(monkeypatch,
                                                            tmp_path):
    """Negative control for the selector.

    Without this, "always use angle 1" would pass the test above while
    making the base copy unreachable.
    """
    import json
    import shutil
    import run as run_mod

    root = tmp_path / "proj"
    shutil.copytree(os.path.join(ROOT, "content"), root / "content")
    (root / "assets" / "cards").mkdir(parents=True)
    for f in ("state.json", "pending.json", "approved.json"):
        if (root / "content" / f).exists():
            (root / "content" / f).unlink()
    cal = json.load(open(root / "content" / "calendar.json"))
    tid = "recompete-radar"
    base_hook = next(t["hook_x"] for t in cal["topics"] if t["id"] == tid)
    for t in cal["topics"]:
        if t["id"] == tid:
            t["angles"] = [{"headline": "A", "body": t["body"], "stat": "S",
                            "hook_x": "angle hook x", "hook_ig": "angle ig"}]
    (root / "content" / "calendar.json").write_text(json.dumps(cal))
    (root / "logs").mkdir()
    (root / "logs" / "posted.jsonl").write_text("")   # no prior posts

    monkeypatch.setattr(run_mod, "ROOT", str(root))
    monkeypatch.setattr(run_mod, "PENDING",
                        str(root / "content" / "pending.json"))
    monkeypatch.setattr(run_mod, "STATE", str(root / "content" / "state.json"))
    monkeypatch.setattr(run_mod, "LOG", str(root / "logs" / "posted.jsonl"))
    monkeypatch.setattr(run_mod, "METRICS", str(root / "logs" / "metrics.jsonl"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    pending = run_mod.prepare(force_format="card", force_topic=tid)
    assert pending["text_x"].startswith(base_hook[:40])
