"""Short-video generator: branded 1080x1920 vertical clip from card frames.

Builds 4 slides (hook -> feature -> proof -> CTA) with PIL, then assembles a
~14s MP4 with ffmpeg zoompan (Ken Burns) + crossfades. Works for IG Reels and
X video. No external footage or network needed.
"""
import os
import subprocess
import tempfile
from PIL import Image, ImageDraw, ImageFont, ImageFilter

import brand

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Palette + canvas come from content/brand_tokens.json — never hardcode a
# colour here, or it drifts away from cards.py.
W, H = brand.size("video")
VIOLET = brand.rgb("violet")
DEEP = brand.rgb("deep")
DEEP2 = brand.rgb("deep2")
GLOW = brand.rgb("glow")
WHITE = brand.rgb("white")
MUTED = brand.rgb("muted")
BODY_TEXT = brand.rgb("body_text")
GREEN = brand.rgb("green")

def _font(size, bold=True):
    p = ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
         else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    try:
        return ImageFont.truetype(p, size)
    except OSError:
        return ImageFont.load_default()

def _bg():
    img = Image.new("RGB", (W, H), DEEP)
    top = Image.new("RGB", (W, H), DEEP2)
    mask = Image.new("L", (W, H))
    md = ImageDraw.Draw(mask)
    for y in range(H):
        md.line([(0, y), (W, y)], fill=int(255 * (1 - y / H) * 0.9))
    img = Image.composite(top, img, mask)
    glow = Image.new("RGB", (W, H), (0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([W * 0.3, -H * 0.2, W * 1.4, H * 0.35], fill=GLOW)
    glow = glow.filter(ImageFilter.GaussianBlur(140))
    return Image.blend(img, Image.blend(img, glow, 0.55), 0.8)

def _wrap(d, text, font, max_w):
    words, lines, cur = text.split(), [], ""
    for w_ in words:
        t = (cur + " " + w_).strip()
        if d.textlength(t, font=font) <= max_w:
            cur = t
        else:
            lines.append(cur)
            cur = w_
    if cur:
        lines.append(cur)
    return lines

def _brand_row(d, y=110):
    f = _font(58)
    d.text((90, y), "PursuitAI", font=f, fill=WHITE)
    d.text((90 + d.textlength("PursuitAI", font=f) + 28, y + 18),
           "AIM · PURSUE · WIN", font=_font(30, False), fill=MUTED)

def _slide(lines_big, lines_small=None, chip=None, cta=False, screenshot=None):
    img = _bg()
    d = ImageDraw.Draw(img, "RGBA")
    _brand_row(d)
    y = 420
    if chip:
        cf = _font(38)
        cw = d.textlength(chip.upper(), font=cf)
        d.rounded_rectangle([90, y, 90 + cw + 60, y + 78], radius=39, fill=VIOLET)
        d.text((120, y + 17), chip.upper(), font=cf, fill=WHITE)
        y += 150
    bf = _font(96)
    for ln in lines_big:
        for sub in _wrap(d, ln, bf, W - 180):
            d.text((90, y), sub, font=bf, fill=WHITE)
            y += 112
    if lines_small:
        y += 40
        sf = _font(46, False)
        for ln in lines_small:
            for sub in _wrap(d, ln, sf, W - 180):
                d.text((90, y), sub, font=sf, fill=BODY_TEXT)
                y += 66
    if screenshot and os.path.exists(screenshot):
        shot = Image.open(screenshot).convert("RGB")
        tw = W - 180
        th = int(shot.height * tw / shot.width)
        shot = shot.resize((tw, th), Image.LANCZOS)
        my = min(y + 60, H - th - 320)
        img.paste(shot, (90, my))
        d.rounded_rectangle([88, my - 2, 92 + tw, my + th + 2], radius=8,
                            outline=VIOLET, width=4)
    if cta:
        y = H - 620
        d.rounded_rectangle([90, y, W - 90, y + 150], radius=75, fill=VIOLET)
        cf = _font(56)
        t = "Start free 14-day trial"
        d.text(((W - d.textlength(t, font=cf)) / 2, y + 42), t, font=cf, fill=WHITE)
        uf = _font(44)
        u = "pursuitai.net"
        d.text(((W - d.textlength(u, font=uf)) / 2, y + 210), u, font=uf, fill=GREEN)
        nf = _font(34, False)
        n = "No credit card · set up in under 2 minutes"
        d.text(((W - d.textlength(n, font=nf)) / 2, y + 290), n, font=nf, fill=MUTED)
    return img

def make_video(topic, out_path, screenshot=None):
    """topic: dict from calendar.json. Returns out_path."""
    slides = [
        _slide([topic["headline"]], chip=topic["feature"]),
        _slide(["Here's how it works."],
               lines_small=[topic["body"]], chip=topic["feature"],
               screenshot=screenshot),
        _slide(["The edge:"], lines_small=[topic["stat"]], chip=topic["feature"]),
        _slide(["Win more.", "Guess less."], cta=True),
    ]
    tmp = tempfile.mkdtemp()
    segs = []
    for i, s in enumerate(slides):
        p = os.path.join(tmp, f"s{i}.png")
        s.save(p)
        seg = os.path.join(tmp, f"seg{i}.mp4")
        dur = 3.5
        # gentle Ken Burns zoom
        subprocess.run([
            "ffmpeg", "-y", "-loop", "1", "-i", p, "-t", str(dur),
            "-filter_complex",
            f"[0:v]scale=8000:-1,zoompan=z='min(zoom+0.0009,1.08)':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={int(dur * 30)}:s={W}x{H}:fps=30",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", seg,
        ], check=True, capture_output=True)
        segs.append(seg)
    # crossfade-concat
    fc, cur, off = "", "[0:v]", 0.0
    for i in range(1, len(segs)):
        off += 3.5 - 0.5
        nxt = f"[v{i}]"
        fc += (f"{cur}[{i}:v]xfade=transition=fade:duration=0.5:"
               f"offset={off:.2f}{nxt};")
        cur = nxt
    fc = fc.rstrip(";")
    cmd = ["ffmpeg", "-y"]
    for s in segs:
        cmd += ["-i", s]
    cmd += ["-filter_complex", fc, "-map", cur, "-c:v", "libx264",
            "-pix_fmt", "yuv420p", "-preset", "fast", "-movflags", "+faststart",
            out_path]
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path

if __name__ == "__main__":
    import json
    with open(os.path.join(ROOT, "content", "calendar.json")) as f:
        cal = json.load(f)
    out = os.path.join(ROOT, "assets", "video", "sample_fit-scoring.mp4")
    print("made", make_video(cal["topics"][0], out))
