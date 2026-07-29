"""Text-to-speech for animated ads. Optional, and never fatal.

The instructional-video catalogue in the main app is narrated with Kokoro
`af_heart`, and its README makes mixing engines a documented mistake — one
Piper clip stuck out audibly next to the rest. Ads share that voice when
Kokoro is installed, and ship SILENT when it is not.

Silent is a deliberate fallback rather than a failure: Kokoro pulls in
torch, which is a heavy dependency to carry in CI, and every platform
autoplays muted anyway — the on-screen text carries the message either
way. So an ad without narration is still a complete ad.
"""
import os

VOICE = "af_heart"          # canonical; see video/README.md in the main app
SAMPLE_RATE = 24000


def available():
    try:
        import kokoro          # noqa: F401
        import soundfile       # noqa: F401
        return True
    except ImportError:
        return False


def _spoken(text):
    """Apply the pronunciation lexicon before synthesis.

    Owned here now (``engine/pronounce.py`` + ``content/pronunciations.json``).
    This used to import the main app's ``video/narrate.py`` from a sibling
    checkout — which does not exist on a CI runner, so it returned the raw
    text and every ad the pipeline rendered shipped with NO lexicon at all.
    """
    import pronounce
    return pronounce.spoken(text)


def synthesize(text, out_path):
    """Write a WAV and return its duration in seconds, or None if silent."""
    if not available():
        print("[voice] kokoro not installed — ad will be silent")
        return None
    try:
        import numpy as np
        import soundfile as sf
        from kokoro import KPipeline
        pipe = KPipeline(lang_code="a")
        chunks = [a for _, _, a in pipe(_spoken(text), voice=VOICE)]
        audio = np.concatenate(chunks)
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        sf.write(out_path, audio, SAMPLE_RATE)
        return len(audio) / SAMPLE_RATE
    except Exception as e:
        print(f"[voice] synthesis failed, ad will be silent: {e}")
        return None
