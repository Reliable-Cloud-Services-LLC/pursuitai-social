"""Fresh-copy variation: wired, safe, and (until now) silently inert.

`_claude_variant` rewrites each caption through Claude so the same topic
does not ship identical words every time it comes round. It is the single
most direct answer to "we look repetitive" — and in 90 posts it has run
ZERO times. Every one of those posts shipped the raw template hook;
subaward-intel went out six times byte-identical.

The cause is not a bug in the rewrite. ANTHROPIC_API_KEY is referenced by
daily.yml but is not among the repository's secrets, so it arrives as an
empty string, `if not key: return None` fires, and the caption silently
falls back to the template. No warning, no log line, nothing in the post
record to show the feed had been running on templates the whole time.

That silence is the defect worth fixing in code. Whether the key gets set
is a decision; a feature that cannot tell you it is switched off is not.

The safety design around it was already right: a Claude rewrite can invent
a claim, so prepare() re-checks the RENDERED text and falls back to the
vetted template on a violation rather than losing the post. One surface
was missed — text_linkedin is built with fresh=True but was absent from
both the check and the assert, so an AI-rewritten LinkedIn caption would
have been the one unvetted thing we published. Harmless only while the
feature was inert, which is exactly the kind of latent gap that goes live
the moment someone flips a switch for an unrelated reason.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import captions  # noqa: E402


def test_no_key_falls_back_to_the_template(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    topic = {"hook_x": "template hook", "hook_ig": "ig hook"}
    assert captions._claude_variant(topic, "x", {}) is None


def test_no_key_says_so(monkeypatch, capsys):
    """The actual fix: an inert feature must announce itself.

    Returning None in silence is how this ran on templates for 90 posts
    without anyone noticing. Once per process, so it is a signal rather
    than noise in the log.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    captions._variant_warned = False
    captions._claude_variant({"hook_x": "h", "hook_ig": "h"}, "x", {})
    out = capsys.readouterr().out
    assert "ANTHROPIC_API_KEY" in out
    assert "template" in out.lower()


def test_the_no_key_warning_does_not_repeat(monkeypatch, capsys):
    """Three captions per run; one notice, not three."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    captions._variant_warned = False
    t = {"hook_x": "h", "hook_ig": "h"}
    for _ in range(3):
        captions._claude_variant(t, "x", {})
    assert capsys.readouterr().out.count("ANTHROPIC_API_KEY") == 1


def test_the_variant_model_is_current():
    """A pinned model id rots. Haiku 4.5 predates the 5 family.

    Worth a test rather than a comment: this call is the one place in the
    repo that names a model, and nothing else would notice it ageing out.
    """
    src = open(os.path.join(ROOT, "engine", "captions.py")).read()
    assert "claude-haiku-4-5" not in src, "pinned to a superseded model"
    assert "claude-haiku-5-5" in src


# ---------------------------------------------------------------------------
# The LinkedIn caption must be vetted like the other two.
# ---------------------------------------------------------------------------

def _gate_fields():
    """The caption fields prepare() actually claim-checks.

    Parses the tuple driving the check rather than grepping the region.
    A substring test passed happily when the tuple was narrowed back to
    (text_x, text_ig), because `pending["text_linkedin"]` still appeared
    in the fallback assignment a few lines below — a test satisfied by
    code adjacent to the thing it was checking.
    """
    import re
    src = open(os.path.join(ROOT, "engine", "run.py")).read()
    m = re.search(r'for f in \(([^)]*)\)', src)
    return {x.strip().strip('"\'') for x in m.group(1).split(",") if x.strip()}


def _assert_publishable_channels():
    """The channels handed to the hard assert."""
    import re
    src = open(os.path.join(ROOT, "engine", "run.py")).read()
    blob = src[src.index("compliance.assert_publishable("):]
    blob = blob[:blob.index(")")]
    return set(re.findall(r'"(\w+)":', blob))


def test_linkedin_copy_is_claim_checked_in_prepare():
    """text_linkedin is AI-rewritten (fresh=True) and was unvetted.

    prepare() re-checks the RENDERED caption rather than the template,
    because a Claude rewrite can introduce a claim the topic never made —
    but the check named only text_x and text_ig. LinkedIn is the surface
    we publish as the company Page, which makes it the worst one to omit.
    """
    assert "text_linkedin" in _gate_fields()


def test_every_ai_rewritten_caption_field_is_in_the_gate():
    """Derived, so a fourth channel cannot repeat the omission."""
    assert _gate_fields() >= {"text_x", "text_ig", "text_linkedin"}


def test_linkedin_reaches_the_hard_assert_too():
    """The claim-check falls back; assert_publishable is what blocks.

    Passing only x and ig there would let an unvetted LinkedIn caption
    through even with the check above in place.
    """
    assert "linkedin" in _assert_publishable_channels()
