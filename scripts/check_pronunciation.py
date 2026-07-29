#!/usr/bin/env python3
"""Hear-what-the-synthesizer-hears, without rendering a video.

Prints the phonemes Kokoro will actually produce for every lexicon term,
before and after its rule. A term whose raw and respelled phonemes are the
same has a rule that does nothing; a respelling that still looks wrong is
one you can fix in seconds instead of after a five-minute render.

    python scripts/check_pronunciation.py              # the whole lexicon
    python scripts/check_pronunciation.py GovCon CO    # specific terms
    python scripts/check_pronunciation.py --scripts    # every ad narration
    python scripts/check_pronunciation.py --drift      # vs the main app

Needs the voice stack (requirements-voice.txt). Without it, everything but
the phoneme columns still works.
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "engine"))

import pronounce  # noqa: E402

MAIN_APP = os.path.expanduser("~/code/pursuit-ai/video/narrate.py")


def phonemizer():
    """The g2p Kokoro actually uses — WITH the espeak fallback.

    Without the fallback every out-of-dictionary word returns a ❓ and the
    whole lexicon looks broken. It is not; KPipeline passes an
    EspeakFallback, so an unlisted word is guessed rather than dropped.
    """
    try:
        from misaki import en, espeak
        return en.G2P(trf=False, british=False,
                      fallback=espeak.EspeakFallback(british=False), unk="")
    except Exception as e:
        print(f"[voice stack unavailable: {e}]\n", file=sys.stderr)
        return None


def show(terms=None):
    g2p = phonemizer()
    with open(pronounce.LEXICON) as f:
        data = json.load(f)
    print(f"{'TERM':<22} {'RAW':<30} {'SPOKEN AS':<22} PHONEMES")
    print("-" * 104)
    for rule in data["rules"]:
        # a readable stand-in for the regex, for the display column
        sample = re.sub(r"\\b|\\s|[\\\\()\[\]?+*]", "", rule["term"])
        if terms and not any(t.lower() in sample.lower() for t in terms):
            continue
        raw = after = ""
        if g2p:
            raw = g2p(sample)[0]
            after = g2p(rule["say"])[0]
        note = "  <-- NO CHANGE" if g2p and raw == after else ""
        print(f"{sample:<22} {raw:<30} {rule['say']:<22} {after}{note}")


def scripts():
    """Every ad narration, as the synthesizer will receive it."""
    import narration
    import compliance
    with open(os.path.join(ROOT, "content", "calendar.json")) as f:
        cal = json.load(f)
    for topic in cal["topics"]:
        if not compliance.is_publishable(topic):
            continue
        text = narration.fallback(topic, cal["brand"])
        fired = pronounce.applied(text)
        print(f"\n=== {topic['id']} ===")
        print(f"  as written: {text}")
        print(f"  as spoken : {pronounce.spoken(text)}")
        print(f"  rules fired: {len(fired)}"
              + ("" if fired else "  <-- none; check for untreated jargon"))


def drift():
    """Terms the main app treats that we do not, and vice versa."""
    if not os.path.exists(MAIN_APP):
        print("main app not checked out alongside — nothing to compare")
        return
    import importlib.util
    spec = importlib.util.spec_from_file_location("_narrate", MAIN_APP)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    theirs = {p for p, _ in mod._PRONUNCIATIONS}
    ours = {term for _, _, term, _ in pronounce.rules()}
    only_theirs = sorted(theirs - ours)
    only_ours = sorted(ours - theirs)
    print(f"in the main app but not here ({len(only_theirs)}):")
    for t in only_theirs:
        print(f"  {t}")
    print(f"\nhere but not in the main app ({len(only_ours)}):")
    for t in only_ours:
        print(f"  {t}")
    if not only_theirs:
        print("\n(nothing missing — the vendored copy is current)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("terms", nargs="*", help="filter to these terms")
    ap.add_argument("--scripts", action="store_true",
                    help="show every ad narration as spoken")
    ap.add_argument("--drift", action="store_true",
                    help="compare against the main app's lexicon")
    args = ap.parse_args()
    if args.drift:
        return drift()
    if args.scripts:
        return scripts()
    show(args.terms)


if __name__ == "__main__":
    main()
