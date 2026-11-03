#!/usr/bin/env python3
"""One-off: rasterise Lucide icons to PNGs committed under assets/icons/.

Run manually when the icon set changes; the output is committed so the
ENGINE has no SVG dependency at all. A runtime rasteriser would mean
libcairo via apt in CI — the step that timed out at four minutes on PR #9
— paid on every single run to re-derive assets that change approximately
never.

Icons are the product's own: apps/web uses lucide-react, so pulling the
path data from node_modules keeps the ads visually identical to the app.
Lucide is ISC-licensed; see assets/icons/LICENSE.

    python scripts/build_icons.py [--lucide <path to lucide-react>]

Renders white-on-transparent at 512px so the engine can tint to any brand
token and scale down cleanly.
"""
import argparse
import os
import re
import sys

SIZE = 512
STROKE = 2          # Lucide's own stroke width, in its 24x24 viewBox
DEFAULT_LUCIDE = os.path.expanduser(
    "~/code/pursuit-ai/node_modules/lucide-react/dist/esm/icons")

# topic-facing name -> lucide icon file
ICONS = {
    "shield-check":    "shield-check",       # compliance / 50% rule
    "radar":           "radar",              # forecasts, recompete
    "trending-up":     "trending-up",        # analytics, price-to-win
    "file-text":       "file-text",          # proposals, solicitations
    "users":           "users",              # teaming, JV
    "search":          "search",             # get found, discovery
    "trophy":          "trophy",             # awards
    "clock":           "clock",              # 8(a) lifecycle
    "gauge":           "gauge",              # agency affinity
    "smartphone":      "smartphone",         # mobile
    "clipboard-check": "clipboard-check",    # post-award
    "layout-list":     "layout-list",        # pipeline board
}


def extract_svg(icon_dir, name):
    """Rebuild an <svg> from lucide-react's exported __iconNode array."""
    src = open(os.path.join(icon_dir, f"{name}.js")).read()
    node = re.search(r"__iconNode = (\[.*?\]);", src, re.S)
    if not node:
        raise SystemExit(f"{name}: could not find __iconNode")
    body = []
    for tag, attrs in re.findall(r'\[\s*"(\w+)",\s*\{(.*?)\}\s*\]', node.group(1), re.S):
        pairs = re.findall(r'(\w+):\s*"([^"]*)"', attrs)
        parts = " ".join(f'{k}="{v}"' for k, v in pairs if k != "key")
        body.append(f"<{tag} {parts}/>")
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{SIZE}" '
        f'height="{SIZE}" viewBox="0 0 24 24" fill="none" stroke="#ffffff" '
        f'stroke-width="{STROKE}" stroke-linecap="round" '
        f'stroke-linejoin="round">{"".join(body)}</svg>'
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lucide", default=DEFAULT_LUCIDE)
    args = ap.parse_args()
    if not os.path.isdir(args.lucide):
        sys.exit(f"lucide icons not found at {args.lucide}")

    import cairosvg      # one-off only; deliberately NOT in requirements.txt
    out_dir = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "assets", "icons")
    os.makedirs(out_dir, exist_ok=True)
    for name, lucide_name in sorted(ICONS.items()):
        svg = extract_svg(args.lucide, lucide_name)
        path = os.path.join(out_dir, f"{name}.png")
        cairosvg.svg2png(bytestring=svg.encode(), write_to=path,
                         output_width=SIZE, output_height=SIZE)
        print(f"  {name:<18} {os.path.getsize(path):>6} bytes")
    print(f"\n{len(ICONS)} icons -> {out_dir}")


if __name__ == "__main__":
    main()
