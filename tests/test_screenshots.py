"""Screenshot capture for the `screenshot` format.

The bug this file exists for shipped to Instagram on 2026-07-31: the page
was captured once at a 1600x1000 DESKTOP viewport, then centre-cropped to
4:5 for Instagram. Reaching 4:5 from 1.6:1 keeps 800px of 1600 — exactly
half the width — so the hero headline "Win More Set-Asides. Stop Guessing."
rendered as "Set-Asides." / "ssing.", sliced mid-word.

X was unaffected because a 16:9 crop of that capture trims height, not
width, which is why it looked fine on one channel and broken on the other.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

SRC = open(os.path.join(ROOT, "engine", "screenshots.py")).read()


def test_instagram_is_captured_at_a_portrait_viewport():
    """A portrait viewport lets the responsive site reflow to its mobile
    layout, which fits 4:5 by construction. Cropping a desktop capture
    cannot — there is no crop of a 1.6:1 image that keeps a full-width
    headline at 0.8:1."""
    viewports = re.findall(r'viewport=\{"width":\s*(\d+),\s*"height":\s*(\d+)\}',
                           SRC)
    assert viewports, "no viewport found"
    ratios = [int(w) / int(h) for w, h in viewports]
    assert any(r < 1.0 for r in ratios), (
        f"no portrait viewport among {viewports} — Instagram will be a "
        f"centre-crop of a landscape capture again")


def test_both_platforms_get_their_own_capture():
    assert SRC.count("browser.new_page(") >= 2, (
        "one page cannot serve both viewports")


def test_postprocess_takes_per_platform_raws():
    """Signature guard: it used to take a single raw path, which is what
    forced both crops to come from one capture."""
    import inspect
    import screenshots
    params = inspect.signature(screenshots._postprocess).parameters
    assert "raws" in params, "_postprocess still takes a single capture"


def test_footer_does_not_hardcode_one_font_path():
    """It hardcoded DejaVu with a load_default() fallback, so on any box
    without DejaVu — every macOS dev machine — the footer rendered in a
    tiny bitmap font. Legible in CI, illegible in the local preview an
    operator actually reviews before posting."""
    footer = SRC[SRC.index("def _footer"):SRC.index("def capture_all")]
    # Strip comments first. A previous version of this assertion matched
    # the COMMENT explaining the bug and failed against the fixed code —
    # a test reading its own prose as evidence.
    code = "\n".join(line.split("#")[0] for line in footer.splitlines())
    assert "load_default" not in code, (
        "footer still falls back to the unscalable default font")
    assert "cards._font" in code, "footer should reuse cards.py's resolver"
