"""The narration pronunciation lexicon.

Two defects motivated this file.

The reported one: "GovCon" came out "GAHV KAHN" — a hard-A vowel, split into
two stressed words — because no rule covered it.

The one found while fixing it, which is larger: the lexicon was loaded from
``~/code/pursuit-ai/video/narrate.py``, a sibling checkout that does not
exist on a CI runner. Every ad the pipeline rendered therefore shipped with
NO lexicon at all — NAICS spelled out letter by letter, USAspending as
"you-ESS ASS-pending", the brand itself as "pursoo-tye". The ad already
published to X was rendered that way. Local previews looked fine, because
locally the path resolves.

So the tests here care about two things: the rules are right, and they
actually apply in an environment that has never heard of the main app.
"""
import json
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import compliance  # noqa: E402
import narration  # noqa: E402
import pronounce  # noqa: E402


@pytest.fixture(scope="module")
def cal():
    with open(os.path.join(ROOT, "content", "calendar.json")) as f:
        return json.load(f)


# ---------- it applies where it matters ----------

def test_lexicon_does_not_depend_on_a_sibling_checkout(monkeypatch, tmp_path):
    """The defect: on a runner there is no ~/code/pursuit-ai, so the old
    implementation returned the text unchanged and said nothing about it."""
    monkeypatch.setenv("HOME", str(tmp_path))
    import voice
    spoken = voice._spoken("PursuitAI scores NAICS for GovCon firms.")
    assert "Pursuit A.I." in spoken
    assert "nayks" in spoken
    assert "Guv-Con" in spoken


def test_voice_delegates_to_the_lexicon():
    import voice
    text = "USAspending and GWACs"
    assert voice._spoken(text) == pronounce.spoken(text)


# ---------- the reported defect ----------

@pytest.mark.parametrize("raw,expected", [
    ("GovCon", "Guv-Con"),
    ("govcon", "Guv-Con"),
    ("GovCons", "Guv Cons"),
    ("the GovCon market", "the Guv-Con market"),
])
def test_govcon_is_respelled(raw, expected):
    assert expected in pronounce.spoken(raw)


def test_contracting_officer_is_not_read_as_a_word():
    """Raw 'CO' phonemizes to kˈO — 'koh'. It is C-O."""
    assert pronounce.spoken("send it to the CO") == "send it to the C O"
    assert pronounce.spoken("two COs reviewed it") == "two C O's reviewed it"


# ---------- ordering ----------

def test_plurals_precede_their_singulars():
    """A singular rule placed first eats the stem and leaves a stray s —
    'GovCons' would become 'Guv-Cons' with the compound already applied."""
    terms = [term for _, _, term, _ in pronounce.rules()]
    for singular, plural in (("GovCon", "GovCons"), ("CO", "COs"),
                             ("GWAC", "GWACs"), ("CLIN", "CLINs"),
                             ("RFP", "RFPs"), ("LCAT", "LCATs")):
        s_at = next(i for i, t in enumerate(terms) if singular in t
                    and plural not in t)
        p_at = next(i for i, t in enumerate(terms) if plural in t)
        assert p_at < s_at, f"{plural} must be listed before {singular}"


def test_the_mentor_protege_compound_rule_runs_first():
    """Otherwise 'Mentor-Protégé's' splits on the possessive and the
    compound is never de-glued."""
    assert pronounce.spoken("Mentor-Protégé") == "Mentor protégé"
    assert "Mentor protégé" in pronounce.spoken("Mentor-Protégé's")


# ---------- the data is well-formed ----------

def test_every_rule_compiles_and_explains_itself():
    with open(pronounce.LEXICON) as f:
        data = json.load(f)
    for rule in data["rules"]:
        re.compile(rule["term"])          # raises on a bad pattern
        assert rule["say"], rule["term"]
        assert rule.get("why"), f"{rule['term']} has no rationale"


def test_no_rule_is_a_no_op():
    """A rule whose replacement is byte-identical to its own literal does
    nothing but look reassuring in a diff.

    Compared case-SENSITIVELY on purpose: lowercasing is itself a technique.
    "CLINs" phonemizes to sˌiˌɛlˌIˈɛnz — spelled out, because the capitals
    read as an acronym — while "clins" gives klˈɪnz. Same letters, different
    word.
    """
    for pattern, say, term, _why in pronounce.rules():
        literal = re.sub(r"\\b|[\\\\()\[\]?+*]", "", term)
        assert literal != say, term


def test_lowercasing_an_acronym_is_a_real_rule():
    """Guards the exception above: if someone "tidies" the CLIN rules to
    match their term's casing, the acronym starts spelling itself out
    again and no other test would notice."""
    says = {term: say for _, say, term, _ in pronounce.rules()}
    assert says[r"\bCLINs\b"] == "clins"
    assert says[r"\bCLIN\b"] == "clin"


def test_spoken_is_idempotent():
    """Narration is passed through once, but a rule that re-fires on its own
    output would corrupt text if it ever ran twice."""
    for text in ("PursuitAI scores NAICS", "GovCon and GWACs", "ask the CO"):
        once = pronounce.spoken(text)
        assert pronounce.spoken(once) == once, text


# ---------- coverage over the real scripts ----------

# Tokens that read correctly as-is — verified against the phonemes Kokoro
# produces WITH its espeak fallback, via scripts/check_pronunciation.py.
# This is not a wishlist: each was checked, not assumed.
READS_CORRECTLY = {
    "AI", "SBA", "JV", "ODC", "CDRL", "MPA", "BLS", "CALC", "FAR", "CFR",
    "PDF", "XLSX", "DOCX", "NDA", "NDAs", "USA", "SAM", "CO", "COs",
    # emphasis capitals, not acronyms — these phonemize as ordinary words
    "YOUR", "AND", "ANY", "ACTUALLY", "NOT", "AI-drafted",
}


def _jargon(text):
    return set(re.findall(r"\b[A-Z][A-Za-z]*[A-Z][A-Za-z0-9+]*\b|\b[A-Z]{2,}\b",
                          text))


def test_every_jargon_token_in_every_script_is_handled(cal):
    """The ratchet. A new topic introducing an untreated acronym would
    otherwise be caught only by someone listening to the finished ad."""
    covered = {re.sub(r"\\b|[\\\\()\[\]?+*]", "", t)
               for _, _, t, _ in pronounce.rules()}
    unhandled = {}
    for topic in cal["topics"]:
        if not compliance.is_publishable(topic):
            continue
        text = narration.fallback(topic, cal["brand"])
        # what survives the lexicon is what the synthesizer actually sees
        for token in _jargon(pronounce.spoken(text)):
            if token in READS_CORRECTLY or token in covered:
                continue
            unhandled.setdefault(token, []).append(topic["id"])
    assert not unhandled, (
        "untreated jargon reaches the synthesizer — run "
        f"scripts/check_pronunciation.py and add a rule or an entry to "
        f"READS_CORRECTLY: {unhandled}")
