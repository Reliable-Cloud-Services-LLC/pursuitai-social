"""Animated ads — icons, narration, and the fifth format slot."""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import adspot      # noqa: E402
import brand       # noqa: E402
import compliance  # noqa: E402
import narration   # noqa: E402
import run         # noqa: E402
import voice       # noqa: E402


@pytest.fixture(scope="module")
def cal():
    with open(os.path.join(ROOT, "content", "calendar.json")) as f:
        return json.load(f)


# ---------- icons ----------

def test_icons_are_committed():
    """No SVG rasteriser at runtime — a live one would drag libcairo into
    CI via apt on every run to re-derive assets that never change."""
    assert adspot.available_icons(), "assets/icons is empty"


def test_every_topic_has_an_icon_that_exists(cal):
    have = set(adspot.available_icons())
    for t in cal["topics"]:
        icon = t.get("icon")
        assert icon, f"{t['id']} has no icon"
        assert icon in have, f"{t['id']} wants {icon!r}, not in assets/icons"


def test_icon_is_white_on_transparent():
    """Tinting to any brand token depends on it."""
    img = adspot.load_icon(adspot.available_icons()[0])
    assert img.mode == "RGBA"
    assert img.getchannel("A").getextrema()[0] == 0, "no transparency"


def test_unknown_icon_fails_with_a_useful_message():
    with pytest.raises(FileNotFoundError) as e:
        adspot.load_icon("definitely-not-an-icon")
    assert "build_icons" in str(e.value)


def test_icon_license_ships_with_the_icons():
    assert os.path.exists(os.path.join(ROOT, "assets", "icons", "LICENSE"))


# ---------- narration ----------

def test_fallback_never_needs_the_network(cal):
    for t in cal["topics"]:
        script = narration.fallback(t, cal["brand"])
        assert script and script.endswith(".")
        assert narration.SPOKEN_URL in script


def test_fallback_passes_compliance(cal):
    """The fallback is the safe landing place, so it must itself be safe."""
    for t in cal["topics"]:
        if not compliance.is_publishable(t):
            continue
        assert not compliance.check_claims(t, narration.fallback(t, cal["brand"]))


def test_fallback_says_the_domain_rather_than_spelling_a_url(cal):
    script = narration.fallback(cal["topics"][0], cal["brand"])
    assert "https://" not in script and "pursuitai.net" not in script


def test_a_violating_draft_is_discarded(cal, monkeypatch):
    """A drafted script that trips a rule must not ship."""
    monkeypatch.setattr(narration, "_claude",
                        lambda t, b: "PursuitAI is endorsed by the SBA. "
                                     "Guaranteed to win.")
    t = cal["topics"][0]
    assert narration.build(t, cal["brand"]) == narration.fallback(t, cal["brand"])


def test_an_overlong_draft_is_discarded(cal, monkeypatch):
    monkeypatch.setattr(narration, "_claude", lambda t, b: "word " * 400)
    t = cal["topics"][0]
    assert narration.build(t, cal["brand"]) == narration.fallback(t, cal["brand"])


def test_a_clean_draft_is_used(cal, monkeypatch):
    good = "A clean spoken line about the feature. Start a free trial."
    monkeypatch.setattr(narration, "_claude", lambda t, b: good)
    assert narration.build(cal["topics"][0], cal["brand"]) == good


# ---------- voice degrades, never fails ----------

def test_voice_is_optional(tmp_path, monkeypatch):
    monkeypatch.setattr(voice, "available", lambda: False)
    assert voice.synthesize("hello", str(tmp_path / "a.wav")) is None


def test_synthesis_failure_is_not_fatal(tmp_path, monkeypatch):
    """A TTS blow-up must cost the ad its audio, not the day's post.

    Forces a real failure inside the try block rather than just claiming
    the engine is absent — with kokoro installed, faking availability is
    not enough because synthesis then genuinely succeeds.
    """
    monkeypatch.setattr(voice, "available", lambda: True)

    def explode(_):
        raise RuntimeError("model blew up")

    monkeypatch.setattr(voice, "_spoken", explode)
    assert voice.synthesize("hello", str(tmp_path / "a.wav")) is None


def test_canonical_voice_matches_the_video_catalogue():
    """The main app's README makes mixing engines a documented mistake."""
    assert voice.VOICE == "af_heart"


# ---------- the fifth format ----------

def test_ad_is_in_the_rotation():
    assert "ad" in run.FORMATS


def test_every_topic_still_sees_every_format():
    """18 publishable topics over 5 slots: gcd(18,5)=1, so no topic locks
    to one format the way they did before W1."""
    n = 18
    seen = {}
    for rc in range(n * len(run.FORMATS)):
        seen.setdefault(rc % n, set()).add(run.select_format(rc, n))
    for topic, formats in seen.items():
        assert formats == set(run.FORMATS), f"topic {topic} saw {formats}"


def test_ad_ratio_is_a_known_size():
    assert brand.size(run.AD_RATIO)


# ---------- rendering ----------

@pytest.mark.parametrize("ratio", ["square", "video", "x"])
def test_a_scene_renders_at_every_ratio(cal, ratio):
    """9:16 and 16:9 as well as 1:1 — a layout tuned for one shape breaks
    on the others, which is exactly how every 16:9 card was broken."""
    t = cal["topics"][0]
    size = brand.size(ratio)
    for scene, _ in adspot.SCENES:
        img = adspot.render_scene(t, cal["brand"], size, scene, 0.5, t=1.0)
        assert img.size == size


def test_scene_text_stays_inside_the_frame(cal):
    """The stat line is unwrappable, so it must shrink rather than overrun."""
    from PIL import Image, ImageDraw
    for ratio in ("square", "video", "x"):
        w, h = brand.size(ratio)
        d = ImageDraw.Draw(Image.new("RGB", (w, h)))
        pad = int(w * 0.09)
        for t in cal["topics"]:
            f = adspot._fit_font(d, t["stat"], w - 2 * pad - int(130 * (min(w, h) / 1080)),
                                 int(58 * (min(w, h) / 1080)))
            assert d.textlength(t["stat"], font=f) <= w - 2 * pad, \
                f"{t['id']} stat overruns at {ratio}"


def test_supersampling_is_on():
    """PIL shape primitives have no antialiasing; without this the icons
    and pill outlines render jagged."""
    assert adspot.SS >= 2
