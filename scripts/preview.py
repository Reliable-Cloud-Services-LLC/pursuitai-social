#!/usr/bin/env python3
"""Build a visual preview of every post the engine can publish.

Nothing goes out that hasn't been looked at. Test output proves the code
runs; it says nothing about whether a card is legible or a caption reads
well. This renders the real artifacts — same code path as a live run — and
lays them out next to the copy that ships with them.

    python scripts/preview.py            # every publishable topic
    python scripts/preview.py --all      # include blocked topics, with reasons
    python scripts/preview.py --topic mpjv

Writes a single self-contained HTML file (images inlined as data URIs) so
it can be opened anywhere or attached to a review.
"""
import argparse
import base64
import html
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "engine"))

import brand        # noqa: E402
import captions     # noqa: E402
import cards        # noqa: E402
import compliance   # noqa: E402

RATIOS = ["x", "ig", "square"]
OUT = os.path.join(ROOT, "assets", "preview", "index.html")


def data_uri(image):
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def render_topic(topic, cal):
    """Everything one topic would put in front of a reader."""
    fmt = "card"
    return {
        "topic": topic,
        "images": [(name, data_uri(cards.render_card(
            topic, cal["brand"], size=brand.size(name)))) for name in RATIOS],
        "x_body": captions.build_x(topic, cal["brand"], fmt=fmt, fresh=False),
        "x_reply": captions.build_x_reply(topic, cal["brand"], fmt),
        "ig": captions.build_ig(topic, cal["brand"], fresh=False),
        "violations": sorted({
            v.rule for c in (captions.build_x(topic, cal["brand"], fmt=fmt,
                                              fresh=False),
                             captions.build_ig(topic, cal["brand"], fresh=False))
            for v in compliance.check_claims(topic, c)}),
    }


CSS = """
:root { color-scheme: dark }
body { margin:0; padding:32px; background:#12121f; color:#f0f0ff;
       font:15px/1.55 ui-sans-serif,-apple-system,Segoe UI,Roboto,sans-serif }
h1 { font-size:22px; margin:0 0 4px }
.sub { color:#b8b8d4; margin-bottom:28px }
.post { border:1px solid rgba(130,130,255,.14); border-radius:14px;
        padding:20px; margin-bottom:24px; background:#1c1c32 }
.head { display:flex; gap:10px; align-items:baseline; flex-wrap:wrap;
        margin-bottom:14px }
.id { font-weight:650; font-size:17px }
.pill { font-size:11px; letter-spacing:.04em; text-transform:uppercase;
        padding:3px 9px; border-radius:999px; border:1px solid }
.ok { color:#4ade80; border-color:#4ade8055; background:#4ade8014 }
.no { color:#f43f5e; border-color:#f43f5e55; background:#f43f5e14 }
.meta { color:#8a8aa8; font-size:12px }
.shots { display:flex; gap:14px; flex-wrap:wrap; margin-bottom:16px }
.shot { flex:0 1 320px }
.shot img { width:100%; border-radius:8px; display:block;
            border:1px solid rgba(130,130,255,.14) }
.shot span { display:block; color:#8a8aa8; font-size:11px; margin-top:6px }
.caps { display:grid; gap:12px; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)) }
.cap { background:#16162a; border:1px solid rgba(130,130,255,.10);
       border-radius:10px; padding:12px 14px }
.cap b { display:block; color:#a78bfa; font-size:11px; letter-spacing:.05em;
         text-transform:uppercase; margin-bottom:7px }
.cap pre { margin:0; white-space:pre-wrap; word-break:break-word;
           font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace }
.count { color:#8a8aa8; font-size:11px; margin-top:8px }
"""


def build_html(rows, cal):
    parts = [f"<style>{CSS}</style>",
             "<h1>PursuitAI social — post preview</h1>",
             f"<p class='sub'>{len(rows)} topic(s), rendered by the same code "
             "that publishes them. Cards, both X parts, and the Instagram "
             "caption exactly as they would ship.</p>"]
    for row in rows:
        t = row["topic"]
        v = t.get("verification", {})
        blocked = row["violations"] or not compliance.is_publishable(t)
        pill = ("<span class='pill no'>blocked</span>" if blocked
                else "<span class='pill ok'>publishes</span>")
        parts.append(
            f"<div class='post'><div class='head'><span class='id'>"
            f"{html.escape(t['id'])}</span>{pill}"
            f"<span class='meta'>{html.escape(v.get('status',''))}"
            f"{' · ' + html.escape(','.join(row['violations'])) if row['violations'] else ''}"
            f"</span></div><div class='shots'>")
        for name, uri in row["images"]:
            w, h = brand.size(name)
            parts.append(f"<div class='shot'><img src='{uri}' alt='{name}'>"
                         f"<span>{name} · {w}×{h}</span></div>")
        parts.append("</div><div class='caps'>")
        x_len = len(row["x_body"])
        for label, text, note in (
                ("X — post", row["x_body"], f"{x_len}/280 characters"),
                ("X — threaded reply", row["x_reply"], "link rides here"),
                ("Instagram", row["ig"], f"{len(row['ig'])}/2200 characters")):
            parts.append(f"<div class='cap'><b>{label}</b><pre>"
                         f"{html.escape(text)}</pre>"
                         f"<div class='count'>{note}</div></div>")
        parts.append("</div></div>")
    return "\n".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true",
                    help="include topics that cannot publish")
    ap.add_argument("--topic", help="preview a single topic id")
    args = ap.parse_args()

    with open(os.path.join(ROOT, "content", "calendar.json")) as f:
        cal = json.load(f)

    topics = cal["topics"]
    if args.topic:
        topics = [t for t in topics if t["id"] == args.topic]
        if not topics:
            sys.exit(f"no such topic: {args.topic}")
    elif not args.all:
        topics = [t for t in topics if compliance.is_publishable(t)]

    rows = [render_topic(t, cal) for t in topics]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        f.write(build_html(rows, cal))
    shown = sum(1 for r in rows if not r["violations"]
                and compliance.is_publishable(r["topic"]))
    print(f"[preview] {len(rows)} topic(s), {shown} publishable "
          f"-> {os.path.relpath(OUT, ROOT)}")


if __name__ == "__main__":
    main()
