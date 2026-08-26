"""The review notification must describe the post it is showing.

On 2026-08-26 Slack captioned a landing-page screenshot "8a-copilot card".
The alt text was a hardcoded f"{topic} card" regardless of format. A label
is only load-bearing at the moment a human is being asked to look at the
thing it labels, which is exactly this moment.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import notify  # noqa: E402


def _blocks_for(monkeypatch, fmt):
    captured = {}
    monkeypatch.setattr(notify, "_send_blocks",
                        lambda text, blocks: captured.update(
                            text=text, blocks=blocks) or True)
    notify.pending_review({"topic": "8a-copilot", "format": fmt,
                           "text_x": "x copy", "text_ig": "ig copy"},
                          media_url="https://example.test/shot.png")
    return captured


def _alt(blocks):
    return next(b["alt_text"] for b in blocks if b.get("type") == "image")


def test_alt_text_names_the_actual_format(monkeypatch):
    for fmt in ("card", "screenshot", "ad"):
        alt = _alt(_blocks_for(monkeypatch, fmt)["blocks"])
        assert fmt in alt, f"{fmt!r} post labelled {alt!r}"


def test_a_screenshot_is_not_called_a_card(monkeypatch):
    """The negative control — the exact wording that shipped."""
    alt = _alt(_blocks_for(monkeypatch, "screenshot")["blocks"])
    assert alt != "8a-copilot card"
    assert "card" not in alt
