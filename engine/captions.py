"""Caption builder: turns a calendar topic into platform-ready copy.

If ANTHROPIC_API_KEY is set, asks Claude for a fresh variant so posts never
repeat verbatim; otherwise uses the hand-written hooks in calendar.json.
Every caption is validated (X <= 280 chars) and always carries the trial CTA.
"""
import os
import json
import random

import links

X_LIMIT = 280
SAFETY_MARGIN = 5

def _claude_variant(topic, platform, brand):
    """Optional: fresh copy via the Claude API. Falls back silently."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    try:
        import requests
        base = topic["hook_x"] if platform == "x" else topic["hook_ig"]
        limit = ("Hard limit 230 characters, no hashtags, no links."
                 if platform == "x" else
                 "3 short paragraphs max, no hashtags, no links.")
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 400,
                "messages": [{"role": "user", "content":
                    f"Rewrite this {('tweet' if platform == 'x' else 'Instagram caption')} "
                    f"for PursuitAI (GovCon capture platform for 8(a)/SDVOSB/WOSB/HUBZone "
                    f"small businesses). Keep every factual claim EXACTLY as stated - do not "
                    f"invent numbers or capabilities. Punchy, confident, practitioner voice. "
                    f"{limit}\n\nOriginal:\n{base}\n\nReply with only the rewritten text."}],
            }, timeout=60)
        r.raise_for_status()
        txt = r.json()["content"][0]["text"].strip().strip('"')
        return txt or None
    except Exception as e:
        print(f"[captions] Claude variant failed, using template: {e}")
        return None

def build_x(topic, brand, *, fmt, fresh=True):
    """Build the tweet. `fmt` is required — it becomes utm_content, and a
    default would silently mislabel the attribution of every post."""
    body = (_claude_variant(topic, "x", brand) if fresh else None) or topic["hook_x"]
    tags = " ".join(brand["hashtags_x"][:2])
    url = links.build_url(brand["url"], "x", topic["id"], fmt)
    cta = f"\n\nFree 14-day trial → {url}\n{tags}"
    # X counts any URL as 23 chars, so budget on the weighted length.
    # Measuring the literal tagged URL (~96 chars) instead would eat ~73
    # chars of copy off every tweet and truncate all 24 topics.
    budget = X_LIMIT - links.x_weighted_length(cta, [url]) - SAFETY_MARGIN
    if len(body) > budget:
        body = body[:budget].rsplit(" ", 1)[0].rstrip(".,;") + "…"
    return body + cta

IG_TAG_COUNT = 8

def _ig_hashtags(brand):
    """Primary tag first, then a random sample of the rest.

    The first entry in hashtags_ig is the category's primary tag and always
    ships — sampling all 8 from 10 could drop it entirely. Mirrors build_x,
    which already takes hashtags_x[:2] deterministically.
    """
    primary, rest = brand["hashtags_ig"][0], brand["hashtags_ig"][1:]
    k = min(IG_TAG_COUNT - 1, len(rest))
    return " ".join([primary] + random.sample(rest, k=k))

def build_ig(topic, brand, fresh=True):
    body = (_claude_variant(topic, "ig", brand) if fresh else None) or topic["hook_ig"]
    tags = _ig_hashtags(brand)
    return (f"{body}\n\n"
            f"Start your free 14-day trial — link in bio or {brand['url'].replace('https://', '')}\n"
            f"No credit card. Set up in under 2 minutes.\n\n{tags}")

if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(os.path.dirname(here), "content", "calendar.json")) as f:
        cal = json.load(f)
    t = cal["topics"][0]
    print("--- X ---\n" + build_x(t, cal["brand"], fmt="card", fresh=False))
    print("\n--- IG ---\n" + build_ig(t, cal["brand"], fresh=False))
