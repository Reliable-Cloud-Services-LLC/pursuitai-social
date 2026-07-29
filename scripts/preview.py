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

import brand         # noqa: E402
import manual_queue  # noqa: E402
import captions     # noqa: E402
import cards        # noqa: E402
import compliance    # noqa: E402

RATIOS = ["x", "ig", "square"]
# LinkedIn is pasted by hand (see docs/LINKEDIN_ACCESS.md), so its
# ratios are called out separately for the person doing the pasting.
LINKEDIN_RATIOS = ["square", "portrait"]
OUT = os.path.join(ROOT, "assets", "preview", "index.html")
MANUAL_LOG = os.path.join(ROOT, "logs", "linkedin_posted.jsonl")

# Per-channel manual posting. Each keeps its own cursor in MANUAL_LOG.
MANUAL = {
    "linkedin": {"ratios": ["square", "portrait"],
                 "caption": lambda t, b: captions.build_linkedin(
                     t, b, fmt="card", fresh=False),
                 "limit": 3000},
    "instagram": {"ratios": ["ig"],
                  "caption": lambda t, b: captions.build_ig(t, b, fresh=False),
                  "limit": 2200},
}


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
        "linkedin": captions.build_linkedin(topic, cal["brand"], fmt=fmt,
                                            fresh=False),
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
.cap b { display:flex; align-items:center; justify-content:space-between }
.copy { font:inherit; font-size:10px; letter-spacing:.04em; cursor:pointer;
        text-transform:uppercase; color:#c4b5fd; background:transparent;
        border:1px solid rgba(196,181,253,.35); border-radius:6px;
        padding:2px 8px }
.copy:hover { background:rgba(196,181,253,.12) }
.copy.done { color:#4ade80; border-color:#4ade8066 }
"""

# navigator.clipboard needs a secure context and the sheet is opened over
# file://, so fall back to a hidden textarea + execCommand.
COPY_JS = """
<script>
document.addEventListener('click', function (e) {
  var btn = e.target.closest('.copy');
  if (!btn) return;
  var text = btn.closest('.cap').querySelector('pre').innerText;
  var done = function () {
    btn.textContent = 'copied'; btn.classList.add('done');
    setTimeout(function () {
      btn.textContent = 'copy'; btn.classList.remove('done');
    }, 1400);
  };
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(done, function () {});
    return;
  }
  var ta = document.createElement('textarea');
  ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
  document.body.appendChild(ta); ta.select();
  try { document.execCommand('copy'); done(); } catch (err) {}
  document.body.removeChild(ta);
});
</script>
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
            li = " · LinkedIn" if name in LINKEDIN_RATIOS else ""
            parts.append(f"<div class='shot'><img src='{uri}' alt='{name}'>"
                         f"<span>{name} · {w}×{h}{li}</span></div>")
        parts.append("</div><div class='caps'>")
        x_len = len(row["x_body"])
        for label, text, note in (
                ("X — post", row["x_body"], f"{x_len}/280 characters"),
                ("X — threaded reply", row["x_reply"], "link rides here"),
                ("Instagram", row["ig"], f"{len(row['ig'])}/2200 characters"),
                ("LinkedIn — paste this",
                 row["linkedin"], f"{len(row['linkedin'])}/3000 characters "
                 f"· use the 1:1 or 4:5 card above")):
            parts.append(
                f"<div class='cap'><b>{label}"
                f"<button class='copy' type='button'>copy</button></b>"
                f"<pre>{html.escape(text)}</pre>"
                f"<div class='count'>{note}</div></div>")
        parts.append("</div></div>")
    parts.append(COPY_JS)
    return "\n".join(parts)


def manual_next(cal, channel):
    """Everything needed for one hand-posted update on `channel`.

    Writes real PNG files rather than only embedding them in the HTML —
    a data: URI saves with a junk filename, and a composer wants a file to
    drag or browse to.
    """
    spec = MANUAL[channel]
    publishable = [t for t in cal["topics"] if compliance.is_publishable(t)]
    clean = [t for t in publishable
             if not compliance.check_claims(t, spec["caption"](t, cal["brand"]))]
    topic = manual_queue.next_unposted(clean, MANUAL_LOG, channel)
    if not topic:
        print(f"[{channel}] no publishable topics")
        return

    out_dir = os.path.join(ROOT, "assets", channel)
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    for name in spec["ratios"]:
        p = os.path.join(out_dir, f"{topic['id']}_{name}.png")
        cards.render_card(topic, cal["brand"], size=brand.size(name),
                          out_path=p)
        w, h = brand.size(name)
        paths.append((p, name, f"{name} {w}x{h}"))

    text = spec["caption"](topic, cal["brand"])
    bar = "=" * 72
    print(f"\n{bar}\n  {channel.upper()} — {topic['id']}\n{bar}\n")
    print(text)
    print(f"\n  ({len(text)}/{spec['limit']} characters)")
    print(f"\n{bar}\n  IMAGE"
          f"{'S — attach ONE of these' if len(paths) > 1 else ''}\n{bar}")
    base = os.environ.get("MEDIA_BASE_URL", "").rstrip("/")
    for p, name, label in paths:
        print(f"  {label:<16} {p}")
        if base:
            # Instagram is usually posted from a phone; the public URL is
            # the easiest way to get the card onto one.
            print(f"  {'':<16} {base}/assets/{channel}/{os.path.basename(p)}"
                  "  (upload separately to use)")
    done = len(manual_queue.posted_ids(MANUAL_LOG, channel))
    print(f"\n  {done} posted so far · {len(clean)} in rotation")
    print(f"\n  When posted:  python scripts/preview.py --posted "
          f"{topic['id']} --channel {channel}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true",
                    help="include topics that cannot publish")
    ap.add_argument("--topic", help="preview a single topic id")
    ap.add_argument("--linkedin", action="store_true",
                    help="next unposted topic: write real PNGs + print the copy")
    ap.add_argument("--instagram", action="store_true",
                    help="same, for the Instagram queue")
    ap.add_argument("--posted", metavar="TOPIC_ID",
                    help="mark a topic as posted")
    ap.add_argument("--channel", default="linkedin",
                    choices=sorted(MANUAL), help="channel for --posted")
    args = ap.parse_args()

    with open(os.path.join(ROOT, "content", "calendar.json")) as f:
        cal = json.load(f)

    if args.posted:
        manual_queue.mark_posted(args.posted, MANUAL_LOG, args.channel)
        print(f"[{args.channel}] marked {args.posted} as posted")
        return

    if args.linkedin:
        return manual_next(cal, "linkedin")
    if args.instagram:
        return manual_next(cal, "instagram")

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
