"""Motion cards — the static card, animated.

A short silent loop that builds the card element by element, then holds the
finished composition. Three properties make this the right shape for a B2B
feed rather than a longer narrative video:

  * **The last frame IS the static card.** Feeds show a poster frame when a
    clip is paused, scrolled past, or fails to autoplay, so the resting
    state has to be the complete message rather than a mid-animation blur.
  * **Silent by design.** LinkedIn and X autoplay muted, so meaning has to
    live in burned-in text — which a card already is.
  * **Reuses cards.render_card.** Same layout, same tokens, same overflow
    guards. A motion card cannot drift from the static one because there is
    only one renderer.
"""
import os
import subprocess
import tempfile

import cards

FPS = 24
# Per-element entry: (start second, duration). Staggered so the eye is led
# down the card rather than everything appearing at once.
TIMELINE = {
    "brand":    (0.0, 0.5),
    "chip":     (0.3, 0.5),
    "headline": (0.6, 0.7),
    "body":     (1.2, 0.7),
    "stat":     (1.9, 0.6),
    "cta":      (2.4, 0.6),
}
HOLD_SECONDS = 2.5


def _reveal_at(t):
    return {name: 0.0 if t <= start else min(1.0, (t - start) / dur)
            for name, (start, dur) in TIMELINE.items()}


def duration():
    last = max(start + dur for start, dur in TIMELINE.values())
    return last + HOLD_SECONDS


def make_motion_card(topic, brand, out_path, size=(1080, 1080)):
    """Render an MP4 that builds the card, then holds it. Returns out_path."""
    total = duration()
    frames = int(total * FPS)
    tmp = tempfile.mkdtemp()
    for i in range(frames):
        img = cards.render_card(topic, brand, size=size,
                                reveal=_reveal_at(i / FPS))
        img.save(os.path.join(tmp, f"f{i:04d}.png"))

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-framerate", str(FPS), "-i", os.path.join(tmp, "f%04d.png"),
        # yuv420p + even dimensions: the combination every platform decodes.
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "medium",
        "-crf", "20", "-movflags", "+faststart", out_path,
    ], check=True, capture_output=True)
    return out_path
