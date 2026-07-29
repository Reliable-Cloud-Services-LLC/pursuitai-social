"""Animated feature spots — icons and type flowing through scenes.

Distinct from motion.py, which reveals one static card. This is a short
sequence: a problem lands, a feature answers it with an icon, the proof
highlights, then the CTA. Elements flow IN and OUT rather than stacking.

No screenshots — the platform UI dates quickly and reads as a demo rather
than an ad. Icons are drawn with PIL primitives, so there is no SVG
rasteriser dependency and every shape inherits the brand tokens.
"""
import math
import os
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFilter

import brand
import cards

FPS = 24
# PIL's line/ellipse/arc primitives have NO antialiasing — text is smooth
# but drawn shapes come out jagged. Render every frame at SS times the
# final size and downsample with LANCZOS, which antialiases everything at
# once and costs SS^2 pixels rather than a per-shape workaround.
SS = 3
VIOLET = brand.rgb("violet")
GREEN = brand.rgb("green")
WHITE = brand.rgb("white")
MUTED = brand.rgb("muted")
BODY = brand.rgb("body_text")


def ease_out(t):
    return 1 - (1 - max(0.0, min(1.0, t))) ** 3


def ease_in(t):
    return max(0.0, min(1.0, t)) ** 3


def fade(colour, a):
    return tuple(colour[:3]) + (int(255 * max(0.0, min(1.0, a))),)


# ── icons ──────────────────────────────────────────────────────────────
# Real Lucide icons, the same set apps/web uses, rasterised once by
# scripts/build_icons.py and committed under assets/icons/. Loading a PNG
# means the engine needs no SVG rasteriser — a runtime one would drag
# libcairo into CI via apt on every run to re-derive assets that never
# change.

ICON_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "assets", "icons")
_ICON_CACHE = {}


def available_icons():
    if not os.path.isdir(ICON_DIR):
        return []
    return sorted(f[:-4] for f in os.listdir(ICON_DIR) if f.endswith(".png"))


def load_icon(name):
    """White-on-transparent icon at native resolution, cached."""
    if name not in _ICON_CACHE:
        path = os.path.join(ICON_DIR, f"{name}.png")
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"icon {name!r} not in assets/icons — add it to "
                f"scripts/build_icons.py and re-run that script")
        _ICON_CACHE[name] = Image.open(path).convert("RGBA")
    return _ICON_CACHE[name]


def draw_icon(img, name, box, colour, progress):
    """Tint, scale and reveal an icon.

    A committed raster cannot self-draw its strokes the way a live SVG
    could, so entry is a scale-with-overshoot plus fade — which reads just
    as deliberately at this length and costs nothing at runtime.
    """
    x, y, w, h = box
    p = ease_out(max(0.0, min(1.0, progress)))
    if p <= 0.01:
        return
    over = 1 + 0.10 * math.sin(min(1.0, progress) * math.pi)
    side = max(1, int(w * (0.6 + 0.4 * p) * over))

    icon = load_icon(name).resize((side, side), Image.LANCZOS)
    tint = Image.new("RGBA", icon.size, tuple(colour[:3]) + (255,))
    alpha = icon.getchannel("A").point(lambda a: int(a * p))
    tint.putalpha(alpha)
    img.alpha_composite(tint, (int(x + (w - side) / 2), int(y + (h - side) / 2))) \
        if img.mode == "RGBA" else img.paste(tint, (int(x + (w - side) / 2),
                                                    int(y + (h - side) / 2)), tint)


# ── scenes ─────────────────────────────────────────────────────────────

def _wrap(d, text, font, max_w):
    return cards._wrap(d, text, font, max_w)


def _flow(p, hold=0.62):
    """One element's in/out envelope over a scene's 0..1 progress.

    Returns (alpha, y-offset). Text rises in, sits, then lifts away —
    which is what makes the sequence feel like it is moving through ideas
    rather than stacking them.
    """
    if p < 0.22:
        q = ease_out(p / 0.22)
        return q, int((1 - q) * 60)
    if p < hold:
        return 1.0, 0
    q = ease_in((p - hold) / (1 - hold))
    return 1 - q, int(-q * 50)


def _scene_text(img, d, size, lines, font, p, colour=WHITE, y0=0.38,
                stagger=0.06):
    w, h = size
    pad = int(w * 0.09)
    for i, line in enumerate(lines):
        a, dy = _flow(max(0.0, p - i * stagger))
        if a <= 0.01:
            continue
        d.text((pad, int(h * y0) + i * int(font.size * 1.22) + dy), line,
               font=font, fill=fade(colour, a))


def _fit_font(d, text, max_w, start, floor=22):
    """Largest size at which `text` fits `max_w`. The stat line is a single
    unwrappable phrase, so it must shrink rather than run off the edge —
    the same rule cards.py applies to its stat chip."""
    size = start
    while size > floor:
        f = cards._font(size)
        if d.textlength(text, font=f) <= max_w:
            return f
        size -= 2
    return cards._font(floor)


def render_scene(topic, brand_cfg, size, scene, p, t=0.0):
    """One frame. `scene` selects what is on screen; `p` is 0..1 within it.

    `t` is elapsed seconds across the whole spot, used for continuous
    background motion so the frame is never completely still.
    """
    w, h = size
    img = cards._gradient(w, h).convert("RGBA")
    d = ImageDraw.Draw(img, "RGBA")
    pad = int(w * 0.09)
    s = min(w, h) / 1080.0

    # Slow drifting glow, blurred. An unblurred ellipse has a hard edge and
    # reads as a ball crossing the frame rather than light.
    gx = math.sin(t * 0.55) * w * 0.09
    gy = math.cos(t * 0.40) * h * 0.05
    glow = Image.new("L", (w, h), 0)
    ImageDraw.Draw(glow).ellipse(
        [w * 0.30 + gx, -h * 0.25 + gy, w * 1.05 + gx, h * 0.40 + gy], fill=70)
    glow = glow.filter(ImageFilter.GaussianBlur(int(w * 0.10)))
    img.paste(Image.new("RGB", (w, h), VIOLET), (0, 0), glow)
    d = ImageDraw.Draw(img, "RGBA")

    if scene == "hook":
        f = cards._font(int(78 * s))
        hook = topic["hook_x"].split(".")[0] + "."
        _scene_text(img, d, size, _wrap(d, hook, f, w - 2 * pad), f, p)

    elif scene == "feature":
        a, dy = _flow(p)
        if a > 0.01:
            side = int(min(w, h) * 0.20)
            draw_icon(img, topic.get("icon", "shield-check"),
                      (pad, int(h * 0.26) + dy, side, side),
                      VIOLET, min(1.0, p / 0.45) * a)
            d = ImageDraw.Draw(img, "RGBA")

            f = cards._font(int(64 * s))
            _scene_text(img, d, size, _wrap(d, topic["feature"], f,
                                            w - 2 * pad), f, p, y0=0.56)
            # accent rule wipes out under the feature name
            if p > 0.30:
                q = ease_out(min(1.0, (p - 0.30) / 0.35))
                ry = int(h * 0.56) + int(f.size * 1.35)
                d.line([(pad, ry), (pad + (w - 2 * pad) * 0.42 * q, ry)],
                       fill=fade(GREEN, a), width=max(3, int(5 * s)))

            fb = cards._font(int(38 * s), bold=False)
            _scene_text(img, d, size, _wrap(d, topic["body"], fb,
                                            w - 2 * pad), fb, p,
                        colour=BODY, y0=0.70, stagger=0.035)

    elif scene == "stat":
        a, dy = _flow(p)
        if a > 0.01:
            txt = topic["stat"]
            inner = w - 2 * pad - int(130 * s)
            f = _fit_font(d, txt, inner, int(58 * s), floor=int(26 * s))
            tw = d.textlength(txt, font=f)
            bx, by = pad, int(h * 0.44) + dy
            pill_w = min(tw + int(125 * s), w - 2 * pad)
            grow = ease_out(min(1.0, p / 0.32))
            d.rounded_rectangle([bx, by, bx + pill_w * grow, by + int(110 * s)],
                                radius=int(24 * s), outline=fade(GREEN, a),
                                width=max(3, int(4 * s)))
            if p > 0.25:
                # text wipes in behind the growing pill edge
                q = ease_out(min(1.0, (p - 0.25) / 0.35))
                d.text((bx + int(78 * s), by + int(26 * s)), txt, font=f,
                       fill=fade(GREEN, a * q))
            if p > 0.30:
                # bullet at the LEFT, mirroring the static card's stat chip —
                # on the right it collided with the text
                r = int(9 * s) * (1 + 0.25 * math.sin(p * 14))
                cxp, cyp = bx + int(44 * s), by + int(55 * s)
                d.ellipse([cxp - r, cyp - r, cxp + r, cyp + r],
                          fill=fade(GREEN, a))

    elif scene == "cta":
        a, dy = _flow(p, hold=0.999)
        lf = cards._font(int(72 * s))
        d.text((pad, int(h * 0.40) + dy), "PursuitAI", font=lf,
               fill=fade(WHITE, a))
        cf = cards._font(int(46 * s))
        d.text((pad, int(h * 0.52) + dy), "Start your free 14-day trial",
               font=cf, fill=fade(WHITE, a))
        uf = cards._font(int(40 * s), bold=False)
        d.text((pad, int(h * 0.60) + dy),
               brand_cfg["url"].replace("https://", "") + "  ·  no credit card",
               font=uf, fill=fade(MUTED, a))
    return img


SCENES = [("hook", 3.2), ("feature", 5.0), ("stat", 3.0), ("cta", 3.0)]


def make_ad(topic, brand_cfg, out_path, size=(1080, 1080), scenes=None,
            voice_wav=None, narration=None):
    """Render the spot. With `voice_wav`, scene lengths stretch to the
    voiceover so the CTA lands as the last words are spoken."""
    scenes = scenes or SCENES
    if voice_wav and os.path.exists(voice_wav):
        secs = _wav_seconds(voice_wav)
        if secs:
            k = secs / sum(d for _, d in scenes)
            scenes = [(n, d * k) for n, d in scenes]
    tmp = tempfile.mkdtemp()
    big = (size[0] * SS, size[1] * SS)
    n, elapsed = 0, 0.0
    for name, secs in scenes:
        frames = int(secs * FPS)
        for i in range(frames):
            img = render_scene(topic, brand_cfg, big, name, i / frames,
                               t=elapsed + i / FPS)
            img.resize(size, Image.LANCZOS).convert("RGB").save(
                os.path.join(tmp, f"f{n:05d}.png"))
            n += 1
        elapsed += secs
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    cmd = ["ffmpeg", "-y", "-v", "error", "-framerate", str(FPS),
           "-i", os.path.join(tmp, "f%05d.png")]
    if voice_wav and os.path.exists(voice_wav):
        cmd += ["-i", voice_wav, "-c:a", "aac", "-b:a", "128k", "-shortest"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "medium",
            "-crf", "20", "-movflags", "+faststart", out_path]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


def _wav_seconds(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True, check=True).stdout.strip()
        return float(out)
    except Exception:
        return None
