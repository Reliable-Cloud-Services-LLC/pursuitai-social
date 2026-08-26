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
