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
    """Apply the GovCon pronunciation lexicon if the main app is present.

    USASpending, NAICS, GWAC, 8(a) and friends are mangled by a generic
    grapheme-to-phoneme pass. The main app already curated these for its
    video catalogue; reuse rather than re-derive, and fall back to the raw
    text when that repo is not checked out alongside.
    """
    path = os.path.expanduser("~/code/pursuit-ai/video/narrate.py")
    if not os.path.exists(path):
        return text
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("_narrate", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod._spoken(text)
    except Exception:
        return text


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
