"""The pronunciation lexicon for ad narration.

Kokoro's grapheme-to-phoneme pass mangles federal jargon it has never seen —
NAICS spells out letter by letter, USAspending becomes "you-ESS ASS-pending",
GovCon comes out "GAHV KAHN". Each rule in ``content/pronunciations.json``
respells a term phonetically FOR THE SYNTHESIZER ONLY; on-screen text and
captions never see this.

This module owns the lexicon rather than importing it. The previous
implementation loaded ``~/code/pursuit-ai/video/narrate.py`` from a sibling
checkout, which meant it silently did nothing on a CI runner — where every
ad the pipeline actually renders is built. The rules are seeded from that
curated list, which was validated by ear across the instructional-video
catalogue; keeping them here costs a vendoring drift risk and buys the
lexicon actually applying where it matters. ``scripts/check_pronunciation.py``
reports drift when the main app is checked out alongside.

Pure and dependency-free: no torch, no network, no filesystem beyond one
JSON read. The tests exercise it without installing the voice stack.
"""
import functools
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEXICON = os.path.join(ROOT, "content", "pronunciations.json")


@functools.lru_cache(maxsize=1)
def rules():
    """[(compiled pattern, replacement, term, why)] in file order.

    Order is load-bearing: a plural rule must precede its singular, or the
    singular consumes the stem and leaves a stray 's'.
    """
    with open(LEXICON) as f:
        data = json.load(f)
    out = []
    for rule in data["rules"]:
        out.append((re.compile(rule["term"], re.IGNORECASE),
                    rule["say"], rule["term"], rule.get("why", "")))
    return out


def spoken(text):
    """Text as the synthesizer should receive it. Captions are unaffected."""
    for pattern, say, _term, _why in rules():
        text = pattern.sub(say, text)
    return text


def applied(text):
    """Which rules fire on this text — for review, not for synthesis."""
    return [(term, say) for pattern, say, term, _why in rules()
            if pattern.search(text)]


# A token the synthesizer will read as an acronym: any run of capitals, or a
# word carrying an interior capital. Deliberately loose — a false positive
# costs one line in reads_correctly; a false negative ships a mangled ad.
_JARGON = re.compile(r"\b[A-Z][A-Za-z]*[A-Z][A-Za-z0-9+]*\b|\b[A-Z]{2,}\b")


@functools.lru_cache(maxsize=1)
def reads_correctly():
    """Tokens that need no rule because Kokoro already says them right."""
    with open(LEXICON) as f:
        return frozenset(json.load(f).get("reads_correctly", []))


@functools.lru_cache(maxsize=1)
def _covered():
    """Literal forms the rules handle, for coverage checks."""
    return frozenset(re.sub(r"\\b|[\\\\()\[\]?+*]", "", term)
                     for _, _, term, _ in rules())


def untreated(text):
    """Acronyms that would reach the synthesizer with no rule behind them.

    Run against a script BEFORE it is synthesized. The narration for an ad
    is drafted by Claude at render time and was never recorded anywhere, so
    a model that reached for an unlisted acronym produced a mangled ad that
    nothing in the repo could later explain. This is the check that stops
    that; ``narration.build`` falls back to the deterministic script when it
    returns anything.

    Checked against the text AFTER the lexicon runs, because that is what
    the synthesizer actually receives.
    """
    seen = spoken(text)
    return sorted(t for t in _JARGON.findall(seen)
                  if t not in reads_correctly() and t not in _covered())
