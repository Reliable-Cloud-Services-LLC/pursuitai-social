"""Live-site screenshot capture via Playwright (runs in GitHub Actions / locally).

Captures pursuitai.net at desktop resolution, crops platform-sized frames,
and drops a subtle brand footer so raw screenshots are post-ready.
"""
import os
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "assets", "screenshots")

SECTIONS = [
    # (name, url, selector-or-None, scroll_y fallback)
    ("hero", "https://pursuitai.net/", None, 0),
    ("how-it-works", "https://pursuitai.net/", None, 1400),
    ("pipeline-board", "https://pursuitai.net/", None, 3000),
    ("features", "https://pursuitai.net/#features", None, 0),
    ("pricing", "https://pursuitai.net/#pricing", None, 0),
    ("why", "https://pursuitai.net/why-pursuitai", None, 0),
]

# Height of the sticky site header, plus a little breathing room. Measured
# against the live site at both capture viewports on 2026-08-26.
HEADER_PX = 120

# How long to wait after scrolling before capturing. Generous on purpose:
# the cost of overshooting is seconds on a job that already takes minutes,
# and the cost of undershooting is a published screenshot of a half-rendered
# section that every automated gate will pass.
SETTLE_MS = 3500


def _footer(img, text="pursuitai.net · free 14-day trial"):
    d = ImageDraw.Draw(img, "RGBA")
    w, h = img.size
    bar = int(h * 0.055)
    d.rectangle([0, h - bar, w, h], fill=(23, 12, 46, 255))
    # Reuse cards.py's font resolution rather than hardcoding one path.
    # This hardcoded DejaVu with a load_default() fallback, so on any box
    # without DejaVu (every macOS dev machine) the footer rendered in a
    # tiny fixed-size bitmap font — legible in CI, illegible in the local
    # preview an operator actually looks at. Same font-fallback trap that
    # made card-overflow checks meaningless locally.
    import cards
    f = cards._font(int(bar * 0.45), bold=True)
    d.text((int(w * 0.03), h - bar + int(bar * 0.24)), text, font=f,
           fill=(196, 181, 253))
    return img

# Where the data-social-shot anchors live. They were added to the landing
# page in pursuit-ai#2146 precisely so a post can show the feature it is
# about, instead of whatever generic section a viewport crop happened to
# land on.
TOPIC_URL = "https://pursuitai.net/"

# The card is captured at the NARROW viewport for both ratios. At desktop
# width it sits in a 3-column grid and stretches to the tallest card in its
# row, so it comes back with a slab of empty space at the bottom; the mobile
# layout hugs its content. One capture, both compositions.
TOPIC_VIEWPORT = {"width": 540, "height": 675}


# An element only composes well if it is roughly card-shaped. Measured
# across all 24 anchors on 2026-08-26: every feature card falls between 0.58
# and 1.32 (height/width), and pricing-plans — which tags a whole SECTION,
# five plan cards stacked in the mobile layout — is 6.13. Scaling that to fit
# a 16:9 frame produced a ~110px-wide sliver of illegible text.
#
# The bound sits far from both, so it separates "card" from "section" rather
# than trimming a tail. An element outside it takes the generic-section
# fallback, which is a weaker post but a legible one.
USABLE_ASPECT = (0.30, 2.50)


def is_composable(card):
    """Whether this element is card-shaped enough to compose."""
    lo, hi = USABLE_ASPECT
    return lo <= card.height / card.width <= hi


# Vertical space left over in the portrait frame before a headline earns
# its place. A near-square card (8a-copilot is 0.99) fills 4:5 on its own and
# a headline would only shrink it; a wide one (mobile-app is 0.63) leaves
# roughly a third of the frame empty and floats. Measured, not guessed.
PORTRAIT_HEADLINE_MIN = 210


def fit_headline(draw, text, col_w, max_h, start=52, floor=28, step=4,
                 line_ratio=1.24):
    """(font, lines, line_height) for a headline that fits `max_h`.

    Shrinks rather than overflowing. Both compositions place fixed elements
    BELOW the headline — the stat chip and the bottom-anchored price line in
    the spotlight, the card itself in the portrait — so a headline that
    grows past its budget does not clip harmlessly, it collides with them.

    Returns the floor size when even that does not fit: a slightly cramped
    headline still beats raising an exception on a post that is otherwise
    ready.
    """
    import cards
    size_px = start
    while True:
        f = cards._font(size_px, bold=True)
        lines = cards._wrap(draw, text, f, col_w)
        lh = int(size_px * line_ratio)
        if len(lines) * lh <= max_h or size_px <= floor:
            return f, lines, lh
        size_px -= step


def compose_fill(card, size, topic=None):
    """Portrait: the card on the brand gradient, headline above it if the
    card does not already fill the frame."""
    import cards
    w, h = size
    bar = int(h * 0.055)
    pad = int(w * 0.055)
    inner_h = h - bar - 2 * pad

    scale = min((w - 2 * pad) / card.width, inner_h / card.height)
    shot = card.resize((int(card.width * scale), int(card.height * scale)),
                       Image.LANCZOS)
    canvas = cards._gradient(w, h)
    leftover = inner_h - shot.height

    if not topic or leftover < PORTRAIT_HEADLINE_MIN:
        canvas.paste(shot, ((w - shot.width) // 2,
                            (h - bar - shot.height) // 2))
        return _footer(canvas)

    d = ImageDraw.Draw(canvas, "RGBA")
    col_w = w - 2 * pad
    f, lines, lh = fit_headline(d, topic["headline"], col_w, leftover - 40,
                                start=46)
    block = len(lines) * lh
    # Headline and card must read as ONE group, so the gap between them is
    # small and FIXED, and only the slack outside the pair is distributed.
    # Splitting the slack evenly instead pushed the headline to the top of
    # the frame with a void under it, which reads as two unrelated things.
    gap = 44
    slack = leftover - block - gap
    y = pad + int(slack * 0.45)
    for line in lines:
        d.text((pad, y), line, font=f, fill=(240, 240, 255))
        y += lh
    canvas.paste(shot, ((w - shot.width) // 2, y + gap))
    return _footer(canvas)


def compose_spotlight(card, topic, brand, size):
    """Landscape: headline + stat + price on the left, the card on the right.

    16:9 is nothing like a feature card's shape, so centring one leaves it
    adrift in empty gradient — it reads as a mistake rather than as space.
    Pairing it with the topic's own headline uses the width for something.
    """
    import cards
    w, h = size
    bar = int(h * 0.055)
    pad = int(w * 0.046)
    scale = (h - bar - 2 * pad) / card.height
    shot = card.resize((int(card.width * scale), int(card.height * scale)),
                       Image.LANCZOS)
    shot_x = w - pad - shot.width

    canvas = cards._gradient(w, h)
    canvas.paste(shot, (shot_x, pad))
    d = ImageDraw.Draw(canvas, "RGBA")
    col_w = shot_x - pad - int(w * 0.035)

    # Headline, shrinking to a budget: the stat chip sits directly below it
    # and the price line is anchored to the bottom of the column, so an
    # over-long headline collides with them rather than clipping harmlessly.
    f, lines, lh = fit_headline(d, topic["headline"], col_w, int(h * 0.42))
    y = pad + int(h * 0.045)
    for line in lines:
        d.text((pad, y), line, font=f, fill=(240, 240, 255))
        y += lh

    # Stat chip.
    y += int(h * 0.029)
    f_s = cards._font(25, bold=True)
    tw = d.textlength(topic["stat"], font=f_s)
    d.rounded_rectangle([pad, y, pad + tw + 44, y + 52], radius=26,
                        fill=(124, 58, 237, 46), outline=(167, 139, 250, 150))
    d.text((pad + 22, y + 12), topic["stat"], font=f_s, fill=(196, 181, 253))

    # Price line, baseline-aligned with the bottom of the card so the column
    # reads as headline-top / CTA-bottom rather than a cluster with a void
    # under it. Deliberately price_line and NOT trial_line: the footer bar
    # already says "free 14-day trial" a few pixels below, and running both
    # repeats the same words twice in one image.
    f_t = cards._font(27, bold=True)
    t_lines = cards._wrap(d, brand["price_line"], f_t, col_w)
    ty = pad + shot.height - len(t_lines) * 38
    for line in t_lines:
        d.text((pad, ty), line, font=f_t, fill=(196, 181, 253))
        ty += 38
    return _footer(canvas)


# Anything pinned to the top of the viewport is painted OVER the element we
# are capturing, and an element screenshot takes the pixels as rendered — so
# the site nav landed inside the frame, clipping the icon of any card taller
# than the viewport gap. Matched by computed position rather than by tag:
# the first attempt at this hid `header`, and the site's nav is a <nav>, so
# it silently did nothing.
_HIDE_PINNED = """() => {
  for (const el of document.querySelectorAll('body *')) {
    const s = getComputedStyle(el);
    if ((s.position === 'fixed' || s.position === 'sticky')
        && el.getBoundingClientRect().top <= 8) {
      el.style.visibility = 'hidden';
    }
  }
}"""


def _hide_pinned_chrome(page):
    try:
        page.evaluate(_HIDE_PINNED)
    except Exception as e:      # never lose a post over cosmetics
        print(f"[screenshots] could not hide pinned chrome ({e})")


def capture_topic(topic, brand, out_dir=OUT, sizes=None):
    """Capture the topic's OWN feature card. (x_path, ig_path), or (None,
    None) when the anchor is absent.

    Absent is a real case, not a defensive flourish: a topic added here
    before the site ships its anchor, or a capture run against a deploy that
    predates them. Returning None lets prepare fall back to a generic
    section, which is a weaker post but still a post.
    """
    from playwright.sync_api import sync_playwright
    slug = topic["id"]
    sizes = sizes or {"x": (1600, 900), "ig": (1080, 1350)}
    os.makedirs(out_dir, exist_ok=True)
    raw = os.path.join(out_dir, f"{slug}_card.png")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        # reduced_motion is what makes this deterministic. The page fades
        # sections in and counts numbers up, and a plain capture races all
        # of it — one earlier attempt caught a counter mid-count showing 0
        # where it should have read 97.
        page = browser.new_page(viewport=TOPIC_VIEWPORT,
                                device_scale_factor=2,
                                reduced_motion="reduce")
        try:
            page.goto(TOPIC_URL, wait_until="load", timeout=45000)
            page.wait_for_timeout(3000)
            el = page.locator(f'[data-social-shot="{slug}"]')
            if not el.count():
                print(f"[screenshots] no anchor for {slug} — generic fallback")
                return None, None
            el.first.scroll_into_view_if_needed()
            page.wait_for_timeout(SETTLE_MS)
            _hide_pinned_chrome(page)
            el.first.screenshot(path=raw)
        except Exception as e:
            print(f"[screenshots] {slug} capture failed: {e}")
            return None, None
        finally:
            browser.close()

    card = Image.open(raw).convert("RGB")
    if not is_composable(card):
        print(f"[screenshots] {slug} anchor is {card.height / card.width:.2f} "
              f"tall/wide — not card-shaped, generic fallback")
        os.remove(raw)
        return None, None
    xp = os.path.join(out_dir, f"{slug}_shot_x.png")
    ip = os.path.join(out_dir, f"{slug}_shot_ig.png")
    compose_spotlight(card, topic, brand, sizes["x"]).save(xp, quality=95)
    compose_fill(card, sizes["ig"], topic).save(ip, quality=95)
    os.remove(raw)
    return xp, ip


def capture_all(out_dir=OUT):
    from playwright.sync_api import sync_playwright
    os.makedirs(out_dir, exist_ok=True)
    made = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        # Capture TWICE, at the viewport each platform's crop actually
        # wants. Cropping one desktop capture to 4:5 kept 800px of 1600 —
        # exactly half the width, centred — which sliced the hero headline
        # mid-word ("Win More Set-Asides." rendered as "Set-Asides."). The
        # site is responsive, so a portrait viewport reflows to the mobile
        # layout, which fits 4:5 by construction and is the right layout
        # for a mobile-first platform anyway.
        desktop = browser.new_page(viewport={"width": 1600, "height": 1000},
                                   device_scale_factor=2)
        portrait = browser.new_page(viewport={"width": 540, "height": 675},
                                    device_scale_factor=2)
        for name, url, selector, scroll_y in SECTIONS:
            raws = {}
            for kind, page in (("x", desktop), ("ig", portrait)):
                try:
                    page.goto(url, wait_until="load", timeout=45000)
                    page.wait_for_timeout(3500)  # let animations settle
                    if scroll_y:
                        page.mouse.wheel(0, scroll_y)
                        # Sections animate in on scroll (IntersectionObserver),
                        # and 1500ms was not enough for the staggered ones:
                        # how-it-works captured card 02 with an EMPTY body on
                        # 2026-08-26 because its contents had not populated.
                        page.wait_for_timeout(SETTLE_MS)
                    if "#" in url or scroll_y:
                        # The sticky header overlays the top of whatever sits
                        # at scroll position 0. On "/" that is harmless, the
                        # hero begins below it — but an anchor URL scrolls its
                        # target to y=0 and the header then slices the section
                        # heading ("Simple, transparent pricing" and
                        # "Everything you need to capture and win" both lost
                        # their top edge). Nudge back up so the heading clears.
                        page.mouse.wheel(0, -HEADER_PX)
                        page.wait_for_timeout(600)
                    raw = os.path.join(out_dir, f"{name}_raw_{kind}.png")
                    if selector:
                        page.locator(selector).first.screenshot(path=raw)
                    else:
                        page.screenshot(path=raw)
                    raws[kind] = raw
                except Exception as e:  # best-effort; keep going
                    print(f"[screenshots] {name} ({kind}) failed: {e}")
            if raws:
                made.append(_postprocess(raws, name, out_dir))
        browser.close()
    return [m for m in made if m]

def _postprocess(raws, name, out_dir):
    """raws: {"x": path, "ig": path} — each already at its own viewport."""
    outs = []
    if raws.get("x"):
        img = Image.open(raws["x"]).convert("RGB")
        x_img = _center_crop(img, 16 / 9).resize((1600, 900), Image.LANCZOS)
        xp = os.path.join(out_dir, f"{name}_x.png")
        _footer(x_img).save(xp, quality=92)
        outs.append(xp)
    if raws.get("ig"):
        # A portrait capture is already ~4:5, so this crop trims a little
        # height rather than half the width.
        img = Image.open(raws["ig"]).convert("RGB")
        ig_img = _center_crop(img, 4 / 5).resize((1080, 1350), Image.LANCZOS)
        igp = os.path.join(out_dir, f"{name}_ig.png")
        _footer(ig_img).save(igp, quality=92)
        outs.append(igp)
    for path in raws.values():
        if os.path.exists(path):
            os.remove(path)
    return outs

def _center_crop(img, ratio):
    w, h = img.size
    if w / h > ratio:
        nw = int(h * ratio)
        x = (w - nw) // 2
        return img.crop((x, 0, x + nw, h))
    nh = int(w / ratio)
    return img.crop((0, 0, w, nh))  # top-anchored: page headers matter

if __name__ == "__main__":
    for f in capture_all():
        print("made", f)
