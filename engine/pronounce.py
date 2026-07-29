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
