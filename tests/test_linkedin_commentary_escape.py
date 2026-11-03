"""LinkedIn silently truncates commentary at an unescaped reserved character.

Found 2026-11-03, after the Page's first post published as:

    Upload the RFP packet

instead of:

    Upload the RFP packet (PDF + XLSX + DOCX), get a compliance matrix in
    one pass, then draft.

    Start a free 14-day trial — no credit card, set up in under two minutes:
    https://lnkd.in/e4GZTKAK

    #GovCon #HUBZone #WOSB

The post stopped at character 286 of 570. The first "(" sits at 287. Every
character from the paren onward — 284 of them, including the whole CTA and
the hashtags — was dropped by LinkedIn, with a 201 Created and no warning.

`commentary` is "little text" (learn.microsoft.com, little-text-format),
whose grammar reserves  | { } @ [ ] ( ) < > # \\ * _ ~  and states: "All
reserved characters need to be escaped with a backslash, even if those
characters are not used in one of the supported elements or templates."
A paren is mention syntax there — MentionElement ::= '@' FallbackText? '('
'Urn' ')' — so an unescaped one sends the parser looking for a URN and it
discards the rest of the post. LinkedIn does NOT document this consequence;
the truncation point is our own measurement, confirmed twice.

The second confirmation is why this file exists rather than a one-line fix:
the exec-analytics post of 2026-10-02 had been truncated in exactly the
same way for a month and nobody noticed. It still reads, live, as

    ...top contributors, at-risk pursuits

cut precisely where "(each with the reason it's flagged)" begins — 348 of
595 characters gone. A silent 58% loss survived a month of review because
the surviving text ends on a plausible-looking phrase. That is the failure
mode these tests exist to catch: not a crash, not an error, just less.

WHY # AND _ ARE DELIBERATELY NOT ESCAPED
The grammar's blanket rule covers them, but escaping them would break two
things that demonstrably work, and the evidence is specific:

  * recompete-radar (2026-10-06) carries "_" at character 430 and three
    hashtags, and published COMPLETE — the UTM link was linkified and
    shortened to lnkd.in, the hashtags render as hashtags.
  * posts-api documents sending "Follow best practices #coding" unescaped
    and LinkedIn returning "{hashtag|\\#|coding}" — it converts for you.

Escaping "_" would put backslashes inside a UTM query string, and escaping
"#" would turn clickable hashtags into literal text. Both are a worse post
than the one we have. So the escape set is the reserved set MINUS those
two, and these tests pin that boundary in both directions.
"""
import json
import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
import post_linkedin  # noqa: E402


# The exact caption that was truncated in production, abridged to the part
# that matters. Keeping the real text means this test fails for the real
# reason rather than for a synthetic one.
LIVE_CAPTION = (
    "Upload the RFP packet (PDF + XLSX + DOCX), get a compliance matrix "
    "in one pass, then draft.\n\n"
    "Start a free 14-day trial:\n"
    "https://pursuitai.net/?utm_source=linkedin&utm_medium=organic\n\n"
    "#GovCon #HUBZone #WOSB"
)


def test_parens_are_escaped():
    """The actual bug: an unescaped "(" cost us 284 characters."""
    out = post_linkedin.escape_commentary(LIVE_CAPTION)
    assert r"\(PDF + XLSX + DOCX\)" in out


def test_nothing_after_the_paren_is_lost():
    """A regression guard shaped like the failure, not like the fix.

    Asserting on the escape of "(" alone would still pass if a future
    change dropped the tail for some other reason, which is precisely what
    LinkedIn did to us.
    """
    out = post_linkedin.escape_commentary(LIVE_CAPTION)
    for tail in ("compliance matrix", "free 14-day trial",
                 "utm_source=linkedin", "#WOSB"):
        assert tail in out, f"lost {tail!r} — the exact production failure"


def test_hashtags_are_left_alone():
    """Escaping these would render literal "#GovCon" instead of a hashtag."""
    out = post_linkedin.escape_commentary(LIVE_CAPTION)
    assert "#GovCon #HUBZone #WOSB" in out
    assert r"\#" not in out


def test_underscores_in_urls_are_left_alone():
    """Escaping these would put backslashes inside a UTM query string."""
    out = post_linkedin.escape_commentary(LIVE_CAPTION)
    assert "utm_source=linkedin&utm_medium=organic" in out
    assert r"\_" not in out


@pytest.mark.parametrize("ch", list("|{}@[]()<>*~"))
def test_every_dangerous_reserved_character_is_escaped(ch):
    """The documented reserved set, minus the two proven-safe exceptions."""
    out = post_linkedin.escape_commentary(f"a{ch}b")
    assert out == f"a\\{ch}b"


def test_backslash_is_escaped_first():
    """Order matters: escape the backslash last and you escape your own.

    "a\\b" must become "a\\\\b". If "\\" were handled after "(", the
    backslash this function just added to "\\(" would itself be escaped,
    yielding "\\\\(" — a literal backslash followed by mention syntax,
    which is both wrong and the original bug.
    """
    assert post_linkedin.escape_commentary("a\\b") == "a\\\\b"
    assert post_linkedin.escape_commentary("a\\(b") == "a\\\\\\(b"


def test_plain_text_is_untouched():
    plain = "No reserved characters here — just an em dash and a comma."
    assert post_linkedin.escape_commentary(plain) == plain


def test_idempotence_is_not_assumed():
    """Escaping twice SHOULD double-escape; callers must escape once.

    Pinned deliberately. A function that silently tolerated a second pass
    would hide a double-call at the payload boundary, and the visible
    result of that is backslashes in the published post.
    """
    once = post_linkedin.escape_commentary("(x)")
    assert post_linkedin.escape_commentary(once) != once


def test_post_image_escapes_at_the_payload_boundary(monkeypatch):
    """The seam that matters: every publish path must escape, unprompted.

    Escaping in the caption builder instead would be wrong — X and
    Instagram share those captions and would carry stray backslashes.
    """
    sent = {}

    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", "t")
    monkeypatch.setattr(post_linkedin, "author_urn",
                        lambda tok: "urn:li:organization:1")
    monkeypatch.setattr(post_linkedin, "upload_image",
                        lambda p, t, a: "urn:li:image:1")

    class _Resp:
        ok = True
        status_code = 201
        headers = {"x-restli-id": "urn:li:share:1"}

        def json(self):
            return {}

    def _fake_post(url, headers=None, data=None, timeout=None):
        sent["payload"] = json.loads(data)
        return _Resp()

    monkeypatch.setattr(post_linkedin.requests, "post", _fake_post)
    post_linkedin.post_image("x.png", LIVE_CAPTION)

    commentary = sent["payload"]["commentary"]
    assert r"\(PDF" in commentary, "post_image published an unescaped paren"
    assert "#WOSB" in commentary, "post_image lost the tail"
