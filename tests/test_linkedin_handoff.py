"""The Slack review carries the LinkedIn post, because a human sends it.

LinkedIn is the one channel the engine cannot publish to. Posting as the
Page needs `w_organization_social`, which only the vetted Community
Management product grants, and that access was denied and is under appeal.
So LinkedIn is posted by hand — and until this change the review message
did not include the LinkedIn copy AT ALL, which meant retyping a caption
whose em dashes and line breaks are exactly what retyping destroys.

Two constraints shape what is possible here, and both are tested:

  * An incoming webhook cannot upload a file. The asset ships as a LINK to
    the public bucket, which already serves Instagram.
  * An .mp4 in a Slack image block makes Slack reject the block and lose
    the whole notification. So the preview stays the poster still for a
    video format, and the link is the only route to the actual video.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import notify  # noqa: E402

CARD = {"topic": "resume-deepdive", "format": "card",
        "text_x": "x copy", "text_ig": "ig copy",
        "text_linkedin": "Evaluators don't grade résumés.\n\nStart a free "
                         "14-day trial — no credit card:\nhttps://pursuitai.net/",
        "media_linkedin": "assets/cards/resume-deepdive_x.png"}

AD = {"topic": "pipeline", "format": "ad",
      "text_x": "x copy", "text_ig": "ig copy",
      "text_linkedin": "Capture teams don't lose to competitors.",
      "media_linkedin": "assets/video/pipeline_ad.mp4"}


def sent(monkeypatch, pending, **kw):
    captured = {}
    monkeypatch.setattr(notify, "_send_blocks",
                        lambda text, blocks: captured.update(
                            text=text, blocks=blocks) or True)
    notify.pending_review(pending, **kw)
    return captured["blocks"]


def texts(blocks):
    return "\n".join(b["text"]["text"] for b in blocks
                     if b.get("type") == "section")


# ---------- the copy ----------

@pytest.mark.parametrize("pending", [CARD, AD], ids=["card", "ad"])
def test_the_linkedin_copy_is_in_the_message(monkeypatch, pending):
    assert pending["text_linkedin"] in texts(sent(monkeypatch, pending))


@pytest.mark.parametrize("pending", [CARD, AD], ids=["card", "ad"])
def test_the_copy_ships_in_a_fenced_block(monkeypatch, pending):
    """Slack gives a fenced block tap-to-copy on mobile and a copy button on
    desktop. Plain text would be hand-selected, which is where the line
    breaks and em dashes get mangled."""
    body = texts(sent(monkeypatch, pending))
    fenced = body.split("*LinkedIn — post this by hand*\n")[1]
    assert fenced.startswith("```")


def test_it_says_the_post_is_manual(monkeypatch):
    """The reviewer has to know this one needs them. An unlabelled block of
    copy beside two channels that publish themselves reads as informational."""
    assert "post this by hand" in texts(sent(monkeypatch, CARD))


# ---------- the asset link ----------

def test_the_asset_link_is_included(monkeypatch):
    body = texts(sent(monkeypatch, CARD,
                      linkedin_url="https://cdn.test/card.png?v=abc"))
    assert "<https://cdn.test/card.png?v=abc|" in body


def test_the_ad_links_to_the_VIDEO_not_the_poster(monkeypatch):
    """The whole point for an ad. The image block above can only show the
    still; if this link also pointed at the still there would be no route
    to the video at all."""
    body = texts(sent(monkeypatch, AD,
                      linkedin_url="https://cdn.test/pipeline_ad.mp4?v=xyz"))
    assert "pipeline_ad.mp4" in body
    assert "_poster" not in body


@pytest.mark.parametrize("rel,expected", [
    ("assets/video/pipeline_ad.mp4", "MP4 · narrated video"),
    ("assets/video/x.MOV", "MP4 · narrated video"),
    ("assets/cards/resume-deepdive_x.png", "PNG · landscape card"),
    (None, "PNG · landscape card"),
])
def test_the_label_matches_the_asset(rel, expected):
    """AD_RATIO is "square" while a card's x-variant is 1600x900, so one
    hardcoded shape would be wrong a quarter of the time. Named from the
    extension rather than measured: notify must never raise, and opening a
    file it may not have is a failure mode for a cosmetic label."""
    assert notify.asset_label(rel) == expected


def test_no_link_block_when_there_is_no_url(monkeypatch):
    """MEDIA_BASE_URL unset is a real local state. A dead "Download" that
    goes nowhere is worse than no link."""
    body = texts(sent(monkeypatch, CARD))
    assert "Download the LinkedIn asset" not in body


# ---------- it stays out of the way ----------

def test_a_post_without_linkedin_copy_gets_no_linkedin_block(monkeypatch):
    """Negative control. An unconditional header would print an empty
    section for any pending.json that predates text_linkedin."""
    body = texts(sent(monkeypatch, {"topic": "t", "format": "card",
                                    "text_x": "x", "text_ig": "ig"}))
    assert "LinkedIn" not in body


def test_the_existing_blocks_are_untouched(monkeypatch):
    """The X and Instagram copy and the approval link are what the reviewer
    already relies on; this change adds beside them, never in place of."""
    blocks = sent(monkeypatch, CARD, media_url="https://cdn.test/c.jpg",
                  review_url="https://github.test/run/1")
    body = texts(blocks)
    assert "*X*" in body and "x copy" in body
    assert "*Instagram*" in body and "ig copy" in body
    assert "https://github.test/run/1|Approve or reject" in body
    assert any(b.get("type") == "image" for b in blocks)


def test_linkedin_comes_after_the_automated_channels(monkeypatch):
    """Reading order mirrors doing order: the two that post themselves, then
    the one that needs you, then the approval link."""
    body = texts(sent(monkeypatch, CARD,
                      linkedin_url="https://cdn.test/c.png",
                      review_url="https://github.test/run/1"))
    assert body.index("*Instagram*") < body.index("*LinkedIn") \
        < body.index("Approve or reject")
