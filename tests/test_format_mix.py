"""The animated/static mix must be measurable, not assumed.

Roughly a quarter of posts are meant to be `ad` — a real animated spot,
24fps across four scenes (hook / feature / stat / CTA), stretched to the
voiceover. The other three quarters are static: a rendered card or a
screenshot of the live site.

The problem is what happens when an ad build fails. An ad is the richest
format and has the most ways to break — voice synthesis, ffmpeg, a missing
asset — so prepare() falls back to the card rather than losing the day's
post. That fallback is right. What was wrong is that it rewrote `fmt` to
"card" and told nobody, so the log recorded a plain card, indistinguishable
from a card the rotation actually asked for.

That makes the mix unmeasurable in the one direction that matters. Every
ad could have been quietly failing for a month and the format histogram
would read as a deliberate editorial choice — 48% cards — rather than as a
feed that had stopped being animated. The same silent-degradation shape as
the LinkedIn truncation: not an error, just less.

It matters more right now than it did last week. Un-exiling the five
thin-data topics (teaming, mpjv, agency-affinity, fit-scoring, mobile-app)
returns five topics that have NEVER shipped an ad — their animated path is
entirely unproven, and they are about to start drawing `ad` slots.

So: record what was ASKED for alongside what SHIPPED. One extra field, and
the histogram can tell an editorial card from a failed spot.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import run  # noqa: E402


def test_formats_include_an_animated_slot():
    """The mix is a mix. A rotation of only static formats is a regression.

    Cheap to state, easy to lose: `ad` was added after card and screenshot,
    and nothing stops a future edit from dropping it back out.
    """
    assert "ad" in run.FORMATS, "no animated format in the rotation"


def test_the_mix_is_not_dominated_by_one_format():
    """No single format may exceed ~60% of the cycle.

    `card` is already half. It is also the fallback for BOTH other
    formats, so it is the one that grows when anything breaks — the share
    worth bounding explicitly.
    """
    for fmt in set(run.FORMATS):
        share = run.FORMATS.count(fmt) / len(run.FORMATS)
        assert share <= 0.6, f"{fmt} is {share:.0%} of the rotation"


def test_static_and_animated_are_both_represented():
    animated = {"ad"}
    static = {"card", "screenshot"}
    present = set(run.FORMATS)
    assert present & animated, "no animated format"
    assert present & static, "no static format"


def test_a_degraded_ad_is_recorded_as_such(monkeypatch, tmp_path):
    """The point of the file: a failed ad must not look like a chosen card.

    Asserts on the pure helper rather than driving prepare(), which would
    need a network capture and an ffmpeg render to reach the branch.
    """
    assert run.format_record("ad", "ad") == {"format": "ad"}
    assert run.format_record("card", "card") == {"format": "card"}
    degraded = run.format_record("card", "ad")
    assert degraded["format"] == "card", "must log what actually shipped"
    assert degraded["format_requested"] == "ad", "must log what was asked"


def test_an_undegraded_post_carries_no_requested_field():
    """Absence is the signal. A `format_requested` on every row would make
    the degraded ones invisible again — the thing being fixed.
    """
    assert "format_requested" not in run.format_record("card", "card")
    assert "format_requested" not in run.format_record("ad", "ad")


def test_a_degraded_screenshot_is_recorded_too():
    """Screenshots fall back to the card as well, by the same `else`.

    A topic with no live-site anchor and no usable generic section lands
    here, and it is just as invisible as the ad case.
    """
    assert run.format_record("card", "screenshot") == {
        "format": "card", "format_requested": "screenshot"}


def test_prepare_records_the_degradation_end_to_end(monkeypatch, tmp_path):
    """Drives the real fallback, not the helper.

    Added after a mutation exposed the gap: setting `fmt_requested = None`
    in prepare() — making the whole feature a no-op — left every test above
    green, because they all called format_record() directly. A helper that
    is correct and never reached is the same as no helper, and that is
    precisely the bug class this file exists for.

    The ad branch is one try/except, so failing narration.build reaches the
    fallback without needing ffmpeg, a voice model or the network.
    """
    import narration
    import run as run_mod

    monkeypatch.setattr(narration, "build",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("simulated ad failure")))
    monkeypatch.setattr(run_mod, "PENDING", str(tmp_path / "pending.json"))
    monkeypatch.setattr(run_mod, "STATE", str(tmp_path / "state.json"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    pending = run_mod.prepare(force_format="ad")
    assert pending["format"] == "card", "should fall back to the card"
    assert pending["format_requested"] == "ad", (
        "a failed ad was logged as a plain card — indistinguishable from "
        "one the rotation chose, which makes the animated share unreadable")
