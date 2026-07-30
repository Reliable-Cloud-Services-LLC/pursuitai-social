"""W6 — one source of truth for brand colour and canvas size.

cards.py and adspot.py each carried their own copy of the palette, so the
two could drift silently: a colour changed on the card would not change on
the video. Both now read content/brand_tokens.json, and a ratchet keeps
colour literals from creeping back in.
"""
import json
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import brand    # noqa: E402
import cards    # noqa: E402
import adspot   # noqa: E402

TOKENS_PATH = os.path.join(ROOT, "content", "brand_tokens.json")


@pytest.fixture(scope="module")
def tokens():
    with open(TOKENS_PATH) as f:
        return json.load(f)


# ---------- the tokens file ----------

def test_every_colour_is_a_hex_triplet(tokens):
    for name, value in tokens["colors"].items():
        assert re.fullmatch(r"#[0-9a-f]{6}", value), f"{name} = {value!r}"


def test_hex_to_rgb_round_trips():
    assert brand.rgb("#7c3aed") == (124, 58, 237)
    assert brand.rgb("#ffffff") == (255, 255, 255)
    assert brand.rgb("#000000") == (0, 0, 0)


def test_rgba_appends_alpha():
    assert brand.rgba("#7c3aed", 235) == (124, 58, 237, 235)


def test_sizes_are_width_height_pairs(tokens):
    for name, size in tokens["sizes"].items():
        assert len(size) == 2 and all(isinstance(n, int) and n > 0 for n in size), name


# ---------- both renderers read the same source ----------

def test_cards_and_adspot_share_one_palette():
    """The whole point: a colour cannot differ between the two renderers.

    Retargeted from video.py to adspot.py when the slide renderer was
    deleted. adspot is now the ONLY video renderer and was never covered by
    these guards — deleting video.py without moving them would have left
    the live renderer unguarded while the suite stayed green.
    """
    for name in ("VIOLET", "GREEN", "WHITE", "MUTED"):
        assert getattr(cards, name) == getattr(adspot, name), name


def test_palette_matches_the_tokens_file(tokens):
    assert cards.VIOLET == brand.rgb(tokens["colors"]["violet"])
    assert cards.GREEN == brand.rgb(tokens["colors"]["green"])
    assert adspot.MUTED == brand.rgb(tokens["colors"]["muted"])


NO_COLOUR_LITERALS = re.compile(
    r"=\s*\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*[,)]"   # NAME = (r, g, b)
    r"|fill=\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,")                  # fill=(r, g, b)


@pytest.mark.parametrize("module", ["cards.py", "adspot.py"])
def test_no_colour_literals_remain(module):
    """Ratchet: a colour hardcoded here is a colour that drifts."""
    src = open(os.path.join(ROOT, "engine", module)).read()
    offenders = [m.group(0) for m in NO_COLOUR_LITERALS.finditer(src)]
    assert not offenders, f"{module} hardcodes colour: {offenders}"


# ---------- LinkedIn ratios ----------

@pytest.mark.parametrize("name,expected", [
    ("x", (1600, 900)),
    ("ig", (1080, 1350)),
    ("square", (1080, 1080)),
    ("portrait", (1080, 1350)),
])
def test_named_sizes_resolve(name, expected):
    assert brand.size(name) == expected


def test_square_card_renders(tmp_path):
    cal = json.load(open(os.path.join(ROOT, "content", "calendar.json")))
    out = str(tmp_path / "sq.png")
    img = cards.render_card(cal["topics"][0], cal["brand"],
                            size=brand.size("square"), out_path=out)
    assert img.size == (1080, 1080)
    assert os.path.getsize(out) > 20_000


def test_every_topic_renders_at_every_ratio(tmp_path):
    """A square canvas is shorter than the 4:5 the layout was tuned for, so
    the longest copy is where it would overflow."""
    cal = json.load(open(os.path.join(ROOT, "content", "calendar.json")))
    longest = max(cal["topics"], key=lambda t: len(t["body"]) + len(t["headline"]))
    for name in ("x", "ig", "square", "portrait"):
        img = cards.render_card(longest, cal["brand"], size=brand.size(name))
        assert img.size == brand.size(name), name


def test_render_all_emits_the_linkedin_variants(tmp_path):
    made = cards.render_all(out_dir=str(tmp_path))
    names = {os.path.basename(p) for p in made}
    first = json.load(open(os.path.join(ROOT, "content",
                                        "calendar.json")))["topics"][0]["id"]
    for suffix in ("x", "ig", "square"):
        assert f"{first}_{suffix}.png" in names, f"missing {suffix} variant"


# ---------- layout overflow ----------

# The overflow tests measure TEXT, so they measure the FONT. cards.py prefers
# DejaVu and falls back to Arial, which is what a macOS dev box has — and
# DejaVu is wider, so copy that fits locally can overflow on the runner.
# That is not hypothetical: it shipped a green local suite and a red CI twice
# in one session, on two different topics.
#
# Skipping is the honest outcome. A pass measured against the wrong font is a
# lie, and it is the lie that let the second overflow through — the first was
# "fixed" locally, against Arial, and the fix was never checked in DejaVu.
CI_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def requires_ci_font():
    if not os.path.exists(CI_FONT):
        pytest.skip(
            "DejaVu is absent, so this would measure Arial instead — a "
            "different width, and a pass here would say nothing about CI. "
            "Install DejaVu locally to make this meaningful.")


def test_no_topic_overflows_at_any_ratio():
    """Every 16:9 card the engine ever produced was broken: the scale factor
    came from width alone, so a 1600x900 canvas got 1.48x sizing inside
    0.83x the height. The headline was sliced by the CTA bar and the body
    copy and stat chip never rendered at all. Image size alone could not
    catch it — the PNG was the right dimensions and over 20KB throughout.
    """
    requires_ci_font()
    cal = json.load(open(os.path.join(ROOT, "content", "calendar.json")))
    failures = []
    for topic in cal["topics"]:
        for name in ("x", "ig", "square", "portrait"):
            try:
                cards.render_card(topic, cal["brand"], size=brand.size(name))
            except cards.LayoutOverflow as e:
                failures.append(str(e))
    assert not failures, "\n".join(failures)


def test_overflow_guard_actually_fires():
    """Negative control — the guard must be capable of failing."""
    cal = json.load(open(os.path.join(ROOT, "content", "calendar.json")))
    absurd = dict(cal["topics"][0], headline="word " * 60, body="filler " * 200)
    with pytest.raises(cards.LayoutOverflow):
        cards.render_card(absurd, cal["brand"], size=(1080, 1080))


def test_no_word_is_cut_off_horizontally():
    """The second cut-off, distinct from the vertical slicing: teaming's
    stat chip ran 111px past the right edge at 4:5 and 1:1. The stat is one
    unwrappable line, so it shrinks to fit; headline and body wrap, and an
    unbreakable word wider than the column raises instead of bleeding off."""
    from PIL import Image, ImageDraw
    cal = json.load(open(os.path.join(ROOT, "content", "calendar.json")))
    for topic in cal["topics"]:
        for name in ("x", "ig", "square", "portrait"):
            w, h = brand.size(name)
            s = min(w, h) / 1080.0
            pad = int(84 * s)
            d = ImageDraw.Draw(Image.new("RGB", (w, h)))
            head = cards._font(int(84 * s) if h > w else int(72 * s))
            body = cards._font(int(40 * s) if h > w else int(34 * s), bold=False)
            for text, font in ((topic["headline"], head), (topic["body"], body)):
                for line in cards._wrap(d, text, font, w - 2 * pad):
                    assert d.textlength(line, font=font) <= w - 2 * pad, (
                        f"{topic['id']}/{name}: {line!r} overruns the column")


def test_long_stat_shrinks_instead_of_overflowing():
    cal = json.load(open(os.path.join(ROOT, "content", "calendar.json")))
    wordy = dict(cal["topics"][0],
                 stat="14 GWACs · 9,000+ holders · live exclusion screening")
    img = cards.render_card(wordy, cal["brand"], size=brand.size("square"))
    assert img.size == (1080, 1080)
