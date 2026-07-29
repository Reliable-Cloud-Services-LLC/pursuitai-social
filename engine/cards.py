"""Branded feature-card image generator for PursuitAI social posts.

Renders 1080x1350 (Instagram feed) and 1600x900 (X) cards with the
brand's violet gradient, feature headline, body copy, stat chip, and CTA.
Pure PIL - no network required.
"""
import json
import math
import os
from PIL import Image, ImageDraw, ImageFont, ImageFilter

import brand
import brand as brand_module

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Palette comes from content/brand_tokens.json — never hardcode a colour
# here, or it drifts away from video.py (see tests/test_brand_tokens.py).
VIOLET = brand.rgb("violet")
DEEP = brand.rgb("deep")
DEEP2 = brand.rgb("deep2")
GLOW = brand.rgb("glow")
WHITE = brand.rgb("white")
MUTED = brand.rgb("muted")
BODY_TEXT = brand.rgb("body_text")
GREEN = brand.rgb("green")
CTA_BAR = brand.rgba("cta_bar", 255)
CHIP_FILL = brand.rgba("violet", brand.ALPHA["chip_fill"])
GRID_LINE = brand.rgba("white", brand.ALPHA["grid_line"])

def _font(size, bold=True):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]
    for c in candidates:
        if os.path.exists(c):
            return ImageFont.truetype(c, size)
    return ImageFont.load_default()

def _fits(draw, text, font, max_w):
    return draw.textlength(text, font=font) <= max_w


def _wrap(draw, text, font, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) <= max_w:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines

def _gradient(w, h):
    img = Image.new("RGB", (w, h), DEEP)
    top = Image.new("RGB", (w, h), DEEP2)
    mask = Image.new("L", (w, h))
    md = ImageDraw.Draw(mask)
    for y in range(h):
        md.line([(0, y), (w, y)], fill=int(255 * (1 - y / h) * 0.9))
    img = Image.composite(top, img, mask)
    # violet glow, upper right
    glow = Image.new("RGB", (w, h), (0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([w * 0.45, -h * 0.35, w * 1.25, h * 0.45], fill=GLOW)
    glow = glow.filter(ImageFilter.GaussianBlur(w // 9))
    img = Image.blend(img, Image.blend(img, glow, 0.55), 0.8)
    # subtle grid
    d = ImageDraw.Draw(img, "RGBA")
    step = w // 14
    for x in range(0, w, step):
        d.line([(x, 0), (x, h)], fill=GRID_LINE)
    for y in range(0, h, step):
        d.line([(0, y), (w, y)], fill=GRID_LINE)
    return img

class LayoutOverflow(Exception):
    """Copy ran past the CTA bar. Fail rather than post a broken card."""


# Element order for a staged reveal. motion.py animates these; a static
# card draws them all at once.
REVEAL_ORDER = ("brand", "chip", "headline", "body", "stat", "cta")


def _ease(t):
    """Ease-out cubic. Motion that decelerates reads as deliberate; linear
    reads as mechanical."""
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def _fade(colour, alpha):
    """RGB(A) at a given 0..1 alpha, for the reveal."""
    a = int(255 * max(0.0, min(1.0, alpha)))
    return tuple(colour[:3]) + (a,)


def render_card(topic, brand, size=(1080, 1350), out_path=None, reveal=None):
    w, h = size
    # Scale by the CONSTRAINING dimension, not the width. Scaling on width
    # alone gave a 1600x900 card 1.48x sizing inside 0.83x the height, so
    # every 16:9 card rendered with its headline sliced by the CTA bar and
    # the body copy and stat chip pushed off-canvas entirely. 4:5 and 1:1
    # are unaffected (their smaller side is 1080), so this only repairs the
    # landscape ratio.
    s = min(w, h) / 1080.0
    img = _gradient(w, h)
    d = ImageDraw.Draw(img, "RGBA")
    pad = int(84 * s)

    # reveal maps element -> 0..1 opacity/offset progress. None means a
    # finished card, which is what every static render wants.
    rv = {k: 1.0 for k in REVEAL_ORDER}
    if reveal:
        rv.update(reveal)

    def slide(name, distance=40):
        """Vertical offset for an element still animating in."""
        return int((1 - _ease(rv[name])) * distance * s)

    # top brand row
    logo_f = _font(int(46 * s))
    d.text((pad, pad + slide("brand")), "PursuitAI", font=logo_f,
           fill=_fade(WHITE, rv["brand"]))
    lw = d.textlength("PursuitAI", font=logo_f)
    tag_f = _font(int(26 * s), bold=False)
    d.text((pad + lw + int(24 * s), pad + int(14 * s) + slide("brand")),
           brand["tagline"], font=tag_f, fill=_fade(MUTED, rv["brand"]))

    # feature eyebrow chip
    y = pad + int(150 * s) if h > w else pad + int(120 * s)
    chip_f = _font(int(30 * s))
    brand_alpha_chip = brand_module.ALPHA["chip_fill"] / 255.0
    chip_txt = topic["feature"].upper()
    cw = d.textlength(chip_txt, font=chip_f)
    cy = y + slide("chip")
    d.rounded_rectangle([pad, cy, pad + cw + int(48 * s), cy + int(62 * s)],
                        radius=int(31 * s),
                        fill=_fade(VIOLET, rv["chip"] * brand_alpha_chip))
    d.text((pad + int(24 * s), cy + int(13 * s)), chip_txt, font=chip_f,
           fill=_fade(WHITE, rv["chip"]))

    # headline
    y += int(120 * s)
    head_f = _font(int(84 * s) if h > w else int(72 * s))
    for line in _wrap(d, topic["headline"], head_f, w - 2 * pad):
        if not _fits(d, line, head_f, w - 2 * pad):
            raise LayoutOverflow(
                f"{topic.get('id')} headline word too long for {w}x{h}: {line!r}")
        d.text((pad, y + slide("headline")), line, font=head_f,
               fill=_fade(WHITE, rv["headline"]))
        y += int((head_f.size) * 1.16)

    # body
    y += int(36 * s)
    body_f = _font(int(40 * s) if h > w else int(34 * s), bold=False)
    for line in _wrap(d, topic["body"], body_f, w - 2 * pad):
        if not _fits(d, line, body_f, w - 2 * pad):
            raise LayoutOverflow(
                f"{topic.get('id')} body word too long for {w}x{h}: {line!r}")
        d.text((pad, y + slide("body")), line, font=body_f,
               fill=_fade(BODY_TEXT, rv["body"]))
        y += int(body_f.size * 1.42)

    # stat chip. The gap is tight rather than generous: on a 1:1 canvas the
    # longest topics clear the CTA bar by only a few pixels.
    y += int(30 * s)
    # The stat is one unwrappable line, so shrink it to fit rather than
    # letting the chip run past the edge (teaming's stat overran by 111px
    # at 4:5 and 1:1).
    stat = "  " + topic["stat"] + "  "
    stat_size = int(36 * s)
    chip_room = w - 2 * pad - int(70 * s)
    while stat_size > int(22 * s):
        stat_f = _font(stat_size)
        if d.textlength(stat, font=stat_f) <= chip_room:
            break
        stat_size -= 1
    stat_f = _font(stat_size)
    sw = d.textlength(stat, font=stat_f)
    if sw > chip_room:
        raise LayoutOverflow(
            f"{topic.get('id')} stat too long for {w}x{h}: {topic['stat']!r}")
    sy = y + slide("stat")
    d.rounded_rectangle([pad, sy, pad + sw + int(70 * s), sy + int(84 * s)],
                        radius=int(18 * s), outline=_fade(GREEN, rv["stat"]),
                        width=max(2, int(3 * s)))
    d.ellipse([pad + int(26 * s), sy + int(32 * s), pad + int(46 * s),
               sy + int(52 * s)], fill=_fade(GREEN, rv["stat"]))
    d.text((pad + int(56 * s), sy + int(20 * s)), stat, font=stat_f,
           fill=_fade(GREEN, rv["stat"]))

    # A shorter canvas (1:1) gives the copy less room than the 4:5 the
    # layout was tuned for. Refuse to emit a card whose content collides
    # with the CTA bar — a broken card is worse than a missed post, and
    # the preview sheet is where this should be caught.
    bar_h = int(150 * s)
    content_bottom = y + int(84 * s)
    if content_bottom > h - bar_h:
        raise LayoutOverflow(
            f"{topic.get('id')} overflows at {w}x{h}: content reaches "
            f"{content_bottom}px, CTA bar starts at {h - bar_h}px")

    # bottom CTA bar
    d.rectangle([0, h - bar_h, w, h], fill=_fade(CTA_BAR, rv["cta"]))
    cta_f = _font(int(40 * s))
    d.text((pad, h - bar_h + int(30 * s)), "Start your free 14-day trial",
           font=cta_f, fill=_fade(WHITE, rv["cta"]))
    url_f = _font(int(32 * s), bold=False)
    d.text((pad, h - bar_h + int(86 * s)), brand["url"].replace("https://", "")
           + "  ·  no credit card", font=url_f, fill=_fade(MUTED, rv["cta"]))
    # arrow button
    bx = w - pad - int(96 * s)
    d.ellipse([bx, h - bar_h + int(28 * s), bx + int(94 * s),
               h - bar_h + int(122 * s)], fill=_fade(VIOLET, rv["cta"]))
    ar_f = _font(int(52 * s))
    d.text((bx + int(28 * s), h - bar_h + int(40 * s)), "→", font=ar_f,
           fill=_fade(WHITE, rv["cta"]))

    if out_path:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        img.save(out_path, quality=92)
    return img

def render_all(calendar_path=None, out_dir=None):
    calendar_path = calendar_path or os.path.join(ROOT, "content", "calendar.json")
    out_dir = out_dir or os.path.join(ROOT, "assets", "cards")
    with open(calendar_path) as f:
        cal = json.load(f)
    made = []
    for t in cal["topics"]:
        # square is the LinkedIn 1:1; portrait (4:5) shares ig's canvas so
        # it is rendered once, under the ig name.
        for suffix in ("ig", "x", "square"):
            size = brand.size(suffix)
            p = os.path.join(out_dir, f"{t['id']}_{suffix}.png")
            render_card(t, cal["brand"], size=size, out_path=p)
            made.append(p)
    return made

if __name__ == "__main__":
    for p in render_all():
        print("made", p)
