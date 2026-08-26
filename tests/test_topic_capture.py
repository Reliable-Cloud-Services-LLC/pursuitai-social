"""Per-topic capture: the post shows the feature it is about.

The whole arc this file guards. Screenshot posts used to take a viewport
crop of whatever generic section a cursor landed on, so an 8(a) Lifecycle
Copilot post shipped a picture of Opportunity Discovery, Grants and AI Fit
Scoring. Rotating sections fixed the repetition and made relevance WORSE.

pursuit-ai#2146 added a data-social-shot anchor per topic; these compose the
captured element into the two post ratios.
"""
import os
import sys

import pytest
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import screenshots  # noqa: E402

TOPIC = {"id": "t", "headline": "Know exactly where you stand.",
         "stat": "Stage-aware tracking"}
BRAND = {"price_line": "No credit card · from $249/mo"}


def _card(w=900, h=890, colour=(30, 30, 50)):
    return Image.new("RGB", (w, h), colour)


# --- the aspect guard ------------------------------------------------------

@pytest.mark.parametrize("aspect", [0.58, 0.63, 0.79, 0.99, 1.32])
def test_real_card_shapes_are_composable(aspect):
    """Every anchor measured against the live site on 2026-08-26 sat in this
    band. The guard must not clip the real range it exists to admit."""
    assert screenshots.is_composable(_card(900, int(900 * aspect)))


def test_a_section_shaped_element_is_rejected():
    """pricing-plans tags a whole SECTION — five plan cards stacked in the
    mobile layout, 6.13 tall/wide. Composed, it became a ~110px sliver of
    illegible text in a 16:9 frame. It must take the generic fallback."""
    assert not screenshots.is_composable(_card(900, int(900 * 6.13)))


# --- composition -----------------------------------------------------------

@pytest.mark.parametrize("size", [(1600, 900), (1080, 1350)])
def test_compositions_return_the_requested_size(size):
    assert screenshots.compose_fill(_card(), size, TOPIC).size == size
    assert screenshots.compose_spotlight(_card(), TOPIC, BRAND, size).size == size


def test_portrait_adds_a_headline_only_when_the_card_leaves_room():
    """A near-square card fills 4:5 on its own and a headline would only
    shrink it; a wide one floats with a third of the frame empty."""
    square = screenshots.compose_fill(_card(900, 890), (1080, 1350), TOPIC)
    bare = screenshots.compose_fill(_card(900, 890), (1080, 1350), None)
    assert list(square.getdata()) == list(bare.getdata()), \
        "a card that already fills the frame should not gain a headline"

    wide = screenshots.compose_fill(_card(900, 560), (1080, 1350), TOPIC)
    wide_bare = screenshots.compose_fill(_card(900, 560), (1080, 1350), None)
    assert list(wide.getdata()) != list(wide_bare.getdata()), \
        "a card leaving a third of the frame empty should gain a headline"


# --- headline shrink-to-fit ------------------------------------------------

LONG = ("Know exactly where your firm stands across the entire nine year "
        "program term including every annual review milestone and the "
        "graduation runway that follows it")
COL_W, BUDGET = 686, int(900 * 0.42)


def _draw():
    from PIL import ImageDraw
    return ImageDraw.Draw(Image.new("RGB", (10, 10)))


def test_a_short_headline_keeps_its_full_size():
    """Shrinking must be a response to overflow, not a permanent tax on
    every headline."""
    f, _, _ = screenshots.fit_headline(_draw(), TOPIC["headline"], COL_W,
                                       BUDGET)
    assert f.size == 52


def test_a_long_headline_shrinks_into_its_budget():
    f, lines, lh = screenshots.fit_headline(_draw(), LONG, COL_W, BUDGET)
    assert len(lines) * lh <= BUDGET
    assert f.size < 52, "it fit without shrinking — budget is not binding"


def test_the_budget_would_be_blown_without_shrinking():
    """Negative control. An earlier version of this test asserted the long
    headline did not paint over the CARD — which it never could, because the
    text column and the card do not overlap horizontally. It passed with the
    shrink loop deliberately disabled: green, and guarding nothing.

    The real constraint is vertical. The stat chip sits under the headline
    and the price line is anchored to the bottom of the column, so an
    unshrunk headline collides with them.
    """
    import cards
    f = cards._font(52, bold=True)
    lines = cards._wrap(_draw(), LONG, f, COL_W)
    assert len(lines) * int(52 * 1.24) > BUDGET


def test_an_unfittable_headline_returns_the_floor_rather_than_raising():
    """A cramped headline still beats an exception on a post that is
    otherwise ready to go out."""
    f, lines, lh = screenshots.fit_headline(_draw(), LONG * 4, COL_W, 40)
    assert f.size == 28 and lines


# --- the fallback ----------------------------------------------------------

@pytest.fixture
def repo(tmp_path, monkeypatch):
    import json
    import shutil
    dst = tmp_path / "repo"
    dst.mkdir()
    for d in ("engine", "content", "assets", "logs"):
        src = os.path.join(ROOT, d)
        if os.path.exists(src):
            shutil.copytree(src, dst / d)
        else:
            (dst / d).mkdir()
    import run
    monkeypatch.setattr(run, "ROOT", str(dst))
    for name in ("STATE", "PENDING", "APPROVED", "LOG", "METRICS"):
        rel = os.path.relpath(getattr(run, name), ROOT)
        monkeypatch.setattr(run, name, str(dst / rel))
    with open(run.STATE, "w") as f:
        json.dump({"topic_index": 0, "run_count": 0, "shot_index": 0}, f)
    return dst


def _seed_sections(sdir):
    import run
    os.makedirs(sdir, exist_ok=True)
    for name in run.SECTION_ORDER:
        for kind, size in (("x", (16, 9)), ("ig", (8, 10))):
            Image.new("RGB", size, (20, 12, 46)).save(
                os.path.join(sdir, f"{name}_{kind}.png"))


def _prepare(repo, monkeypatch, capture_result):
    import json
    import run
    monkeypatch.setattr(screenshots, "capture_topic",
                        lambda *a, **k: capture_result)
    monkeypatch.setattr(screenshots, "capture_all", lambda *a, **k: [])
    _seed_sections(str(repo / "assets" / "screenshots"))
    run.prepare(force_format="screenshot")
    with open(run.PENDING) as f:
        return json.load(f)


def test_a_missing_anchor_falls_back_to_a_generic_section(repo, monkeypatch):
    """A topic added here before the site ships its anchor, or a capture run
    against a deploy that predates them. A weaker post beats no post — and
    beats a crash on the morning it was due."""
    pending = _prepare(repo, monkeypatch, (None, None))
    assert pending["format"] == "screenshot"
    assert "screenshots/hero" in pending["media_x"]


def test_the_topic_card_is_preferred_when_the_anchor_resolves(repo,
                                                              monkeypatch):
    shot = str(repo / "assets" / "screenshots" / "t_shot_x.png")
    shot_ig = str(repo / "assets" / "screenshots" / "t_shot_ig.png")
    _seed_sections(str(repo / "assets" / "screenshots"))
    for pth in (shot, shot_ig):
        Image.new("RGB", (16, 9), (20, 12, 46)).save(pth)
    pending = _prepare(repo, monkeypatch, (shot, shot_ig))
    assert pending["media_x"].endswith("t_shot_x.png"), \
        "the generic section won over a resolved topic card"
