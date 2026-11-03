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


# --- the stat chip fits its column -----------------------------------------
#
# 2026-09-04: the resume-deepdive spotlight rendered its stat pill 695px wide
# against a 686px column, overlapping the product card. Caught by a human
# looking at the review image, which is the third time that has been the
# thing that caught it.
#
# The chip is the ONE piece of left-column text that does not wrap, so it is
# the only one that can reach the card. When an earlier test of mine proved
# toothless I concluded "the text column and the card do not overlap
# horizontally" — true of the headline, which wraps to col_w, and I
# generalised it to text that does not wrap. This is that gap.

# Card aspects measured against the live anchors on 2026-09-04. The spread
# is the point: 0.58 to 1.32, which is why a single hardcoded column was
# never going to be representative.
REAL_ASPECTS = (0.58, 0.63, 0.74, 0.84, 1.00, 1.32)


def _geom(aspect, size=(1600, 900)):
    return screenshots.spotlight_geometry((900, int(900 * aspect)), size)


def _dejavu(size):
    """The font CI renders with, if it is installed here."""
    from PIL import ImageFont
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/tmp/dejavu-fonts-ttf-2.37/ttf/DejaVuSans-Bold.ttf"):
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return None


@pytest.mark.parametrize("aspect", REAL_ASPECTS)
def test_the_text_column_is_never_squeezed(aspect):
    """The card used to be scaled to fill the HEIGHT with no width cap, so a
    wide card ate the canvas: columns ran from 175px to 1284px, and at 175px
    the chip overflowed by 186px even at its floor while the headline wrapped
    to seven lines."""
    assert _geom(aspect)["col_w"] >= screenshots.MIN_TEXT_COL


@pytest.mark.parametrize("aspect", REAL_ASPECTS)
def test_the_card_never_reaches_the_text_column(aspect):
    g = _geom(aspect)
    assert g["shot_x"] > g["pad"] + g["col_w"]


@pytest.mark.parametrize("aspect", REAL_ASPECTS)
def test_a_width_capped_card_is_centred_not_hung_from_the_top(aspect):
    """A capped card no longer fills the height; top-aligning it would leave
    it hanging off the top edge with all the slack below."""
    g = _geom(aspect)
    slack = 900 - g["bar"] - 2 * g["pad"] - g["shot_h"]
    assert g["shot_y"] == g["pad"] + max(0, slack // 2)


def test_fit_chip_never_exceeds_the_column(monkeypatch):
    """Font-independent, so it means something on any machine."""
    from PIL import ImageDraw
    d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    col = _geom(1.0)["col_w"]
    for text in ("short", "Requirement→evidence matrix, zero invention",
                 "an absurdly long stat line " * 4):
        f = screenshots.fit_chip(d, text, col)
        assert d.textlength(text, font=f) + screenshots.CHIP_PAD <= col \
            or f.size == 15


def test_a_short_chip_keeps_its_full_size():
    """Shrinking must respond to overflow, not tax every chip."""
    from PIL import ImageDraw
    d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    assert screenshots.fit_chip(
        d, "Stage-aware tracking", _geom(1.0)["col_w"]).size == 25


def test_every_stat_fits_at_every_real_aspect_in_the_font_CI_uses():
    """The whole matrix, in DejaVu — the font the runner has and this machine
    does not. Testing in Arial would have passed on the broken build, so this
    SKIPS rather than pretends: a visible skip beats a false green."""
    import json
    from PIL import ImageDraw
    if _dejavu(25) is None:
        pytest.skip("DejaVu not installed — cannot check the runner's font")
    import cards
    original, cards._font = cards._font, lambda size, bold=True: _dejavu(size)
    try:
        d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
        with open(os.path.join(ROOT, "content", "calendar.json")) as fh:
            topics = json.load(fh)["topics"]
        over = []
        for t in topics:
            for aspect in REAL_ASPECTS:
                col = _geom(aspect)["col_w"]
                f = screenshots.fit_chip(d, t["stat"], col)
                w = d.textlength(t["stat"], font=f) + screenshots.CHIP_PAD
                if w > col:
                    over.append((t["id"], aspect, round(w), col))
        assert not over, f"chips overflow: {over[:5]}"
    finally:
        cards._font = original
