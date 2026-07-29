"""Narration scripts for animated ads.

Claude drafts the spoken script; a deterministic script derived from the
topic is the fallback. Same discipline as captions.py: the model may
rephrase, never invent — and whatever comes back still passes the same
compliance gate as every other channel before it can ship.

Spoken copy is not written copy. The card says "evaluated across the base
period AND each option year"; a person says "across the base period and
every option year". The prompt asks for that, but the fallback is written
that way too, so an unavailable API degrades to something sayable rather
than to card text read aloud.
"""
import json
import os
import re

import compliance
import pronounce

# Kokoro reads a bare URL as a string of letters. Say the domain.
SPOKEN_URL = "pursuit A.I. dot net"
MAX_WORDS = 60          # ~22 seconds at a natural pace


def fallback(topic, brand):
    """Deterministic script from the topic's own fields. Always available."""
    hook = topic["hook_x"].split(".")[0].strip() + "."
    body = re.sub(r"\s*—\s*", ", ", topic["body"])
    body = body.split(".")[0].strip() + "."
    return f"{hook} {body} Start a free trial at {SPOKEN_URL}."


def _claude(topic, brand):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    try:
        import requests
        prompt = (
            "Write a voiceover script for a 15-second product ad.\n\n"
            f"PRODUCT: PursuitAI, capture-management software for small "
            f"businesses pursuing U.S. federal contracts.\n"
            f"FEATURE: {topic['feature']}\n"
            f"WHAT IT DOES: {topic['body']}\n"
            f"PROOF POINT: {topic['stat']}\n\n"
            "RULES — all mandatory:\n"
            "- Keep every factual claim EXACTLY as given. Invent no numbers, "
            "capabilities, customers, or comparisons.\n"
            "- Claim nothing about competitors, and never say 'only' or 'no "
            "other platform'.\n"
            "- Promise no outcome. Never say a user will win anything.\n"
            "- Imply no government endorsement.\n"
            f"- Under {MAX_WORDS} words. Spoken English, short sentences, "
            "no bullet points, no hashtags, no emoji.\n"
            f"- End with: Start a free trial at {SPOKEN_URL}.\n\n"
            "Reply with only the script.")
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": "claude-haiku-4-5-20251001", "max_tokens": 400,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=60)
        r.raise_for_status()
        return r.json()["content"][0]["text"].strip().strip('"') or None
    except Exception as e:
        print(f"[narration] Claude draft failed, using fallback: {e}")
        return None


def build(topic, brand, fresh=True):
    """Return a narration script that has passed the compliance gate.

    A drafted script that trips a rule is discarded rather than published —
    the fallback is derived from copy already verified, so it is the safe
    landing place.
    """
    draft = _claude(topic, brand) if fresh else None
    if draft:
        untreated = pronounce.untreated(draft)
        if len(draft.split()) > MAX_WORDS * 1.4:
            print("[narration] draft too long, using fallback")
        elif compliance.check_claims(topic, draft):
            rules = [v.rule for v in compliance.check_claims(topic, draft)]
            print(f"[narration] draft violated {rules}, using fallback")
        elif untreated:
            # The model reaches for jargon the topic never used. Nothing
            # downstream would notice: the script is synthesized and thrown
            # away, so a mangled acronym only ever surfaces by someone
            # listening. The deterministic fallback is covered by a test, so
            # it is the safe landing place.
            print(f"[narration] draft has unpronounceable jargon "
                  f"{untreated}, using fallback — add a rule to "
                  f"content/pronunciations.json to keep drafts like this")
        else:
            return draft
    return fallback(topic, brand)
