#!/usr/bin/env python3
"""Instagram Graph API pre-flight validator.

Run this BEFORE trusting the daily automation. It verifies every link in the
chain without publishing anything visible:

  1. Token is valid and identifies your app/user
  2. Token carries instagram_basic + instagram_content_publish scopes
  3. IG_USER_ID resolves to your Instagram professional account
  4. Publishing quota is available
  5. (--container) creates a REAL media container from a committed asset to
     prove Instagram can fetch your MEDIA_BASE_URL — but does NOT publish it.
     Unpublished containers simply expire after 24h. Nothing appears on the
     profile.

Usage:
  export IG_USER_ID=... IG_ACCESS_TOKEN=... MEDIA_BASE_URL=...
  python scripts/validate_ig.py               # checks 1-4
  python scripts/validate_ig.py --container   # checks 1-5

Exit code 0 = ready for autonomous posting.
"""
import argparse
import json
import os
import sys

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRAPH = "https://graph.facebook.com/v21.0"

def fail(msg):
    print(f"  ✗ {msg}")
    sys.exit(1)

def ok(msg):
    print(f"  ✓ {msg}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--container", action="store_true",
                    help="also create (but never publish) a test container")
    args = ap.parse_args()

    tok = os.environ.get("IG_ACCESS_TOKEN") or fail("IG_ACCESS_TOKEN not set")
    uid = os.environ.get("IG_USER_ID") or fail("IG_USER_ID not set")

    print("[1] token validity")
    r = requests.get(f"{GRAPH}/me", params={"access_token": tok}, timeout=30)
    if r.status_code != 200:
        fail(f"token rejected: {r.json().get('error', {}).get('message')}")
    ok(f"token valid — identity: {r.json().get('name', r.json().get('id'))}")

    print("[2] token scopes")
    d = requests.get(f"{GRAPH}/debug_token",
                     params={"input_token": tok, "access_token": tok},
                     timeout=30).json().get("data", {})
    scopes = set(d.get("scopes", []))
    needed = {"instagram_basic", "instagram_content_publish"}
    missing = needed - scopes
    if missing and d.get("type") != "SYSTEM_USER":
        fail(f"missing scopes: {missing} (have: {sorted(scopes)})")
    ok(f"scopes ok ({'system user' if d.get('type')=='SYSTEM_USER' else ', '.join(sorted(needed))})")
    token_days_left = None
    if d.get("expires_at", 0):
        import datetime
        exp = datetime.datetime.fromtimestamp(d["expires_at"])
        days = token_days_left = (exp - datetime.datetime.now()).days
        print(f"    note: token expires {exp:%Y-%m-%d} ({days} days) — "
              "use a System User token for never-expiring")

    print("[3] IG user id")
    r = requests.get(f"{GRAPH}/{uid}",
                     params={"fields": "id,username,followers_count,media_count",
                             "access_token": tok}, timeout=30)
    if r.status_code != 200:
        fail(f"IG_USER_ID invalid: {r.json().get('error', {}).get('message')}")
    j = r.json()
    ok(f"resolves to @{j.get('username')} "
       f"({j.get('followers_count', '?')} followers, {j.get('media_count', '?')} posts)")

    print("[4] publishing quota")
    r = requests.get(f"{GRAPH}/{uid}/content_publishing_limit",
                     params={"access_token": tok}, timeout=30)
    if r.status_code == 200 and r.json().get("data"):
        u = r.json()["data"][0]
        used = u.get("quota_usage", 0)
        ok(f"quota used {used}/100 in current 24h window")
    else:
        print("    (quota endpoint unavailable — not fatal)")

    if args.container:
        print("[5] container creation (fetch test — will NOT publish)")
        base = os.environ.get("MEDIA_BASE_URL") or fail("MEDIA_BASE_URL not set")
        # Test an asset that PROVABLY SHIPPED, not one we hope exists.
        # prepare only syncs the assets it renders that run, so the bucket
        # holds exactly what past runs produced — and S3 answers 403 (not
        # 404) for a missing key when listing is private. The previous
        # version derived a plausible path from the calendar; on a bucket
        # that had never rendered that topic it 403'd and read as a
        # credentials problem, mid-credential-setup, to the first person
        # who ran it. posted.jsonl is the ledger of what actually reached
        # the bucket: walk it newest-first for a JPEG (Meta accepts no
        # other image format — see media.as_jpeg).
        test_asset = None
        log_path = os.path.join(ROOT, "logs", "posted.jsonl")
        if os.path.exists(log_path):
            with open(log_path) as f:
                entries = [json.loads(line) for line in f if line.strip()]
            for entry in reversed(entries):
                for key in ("cover_ig", "media_ig"):
                    rel = entry.get(key) or ""
                    if rel.lower().endswith((".jpg", ".jpeg")):
                        test_asset = rel
                        break
                if test_asset:
                    break
        if not test_asset:
            # Fresh clone / empty log: fall back to the first publishable
            # topic's card. May not be uploaded yet — the error below says
            # exactly that instead of implying broken credentials.
            sys.path.insert(0, os.path.join(ROOT, "engine"))
            import compliance
            with open(os.path.join(ROOT, "content", "calendar.json")) as f:
                topics = json.load(f)["topics"]
            publishable = [t for t in topics if compliance.is_publishable(t)]
            if not publishable:
                fail("no publishable topic to test with")
            test_asset = f"assets/cards/{publishable[0]['id']}_ig.jpg"
        url = f"{base.rstrip('/')}/{test_asset}"
        head = requests.head(url, timeout=30)
        if head.status_code != 200:
            fail(f"asset not publicly reachable ({head.status_code}): {url}\n"
                 f"       NB S3 returns 403 for a MISSING key when listing is\n"
                 f"       private — this usually means the file was never\n"
                 f"       uploaded (run a prepare), not that credentials are\n"
                 f"       wrong.\n"
                 "    push the repo (public) first, or fix MEDIA_BASE_URL")
        ok(f"asset publicly reachable: {url}")
        r = requests.post(f"{GRAPH}/{uid}/media", data={
            "image_url": url,
            "caption": "[validation container - never published, expires in 24h]",
            "access_token": tok}, timeout=120)
        if r.status_code != 200:
            fail(f"container creation failed: {r.json().get('error', {}).get('message')}")
        ok(f"container {r.json()['id']} created — Instagram fetched your media. "
           "NOT publishing; it expires harmlessly in 24h.")

    # An expiring token passes every CHAIN check and still cannot run a
    # daily cron — it dies mid-schedule and the failure lands days later,
    # looking like a regression. The verdict must not say "autonomously"
    # about a token that will not survive the week. (The operator hit
    # exactly this: a 0-days token, every check green, rocket emoji.)
    if token_days_left is not None and token_days_left < 7:
        print(f"\nChain verified — but this token expires in "
              f"{token_days_left} day(s), so the engine can NOT post "
              f"autonomously on it. Mint a System User token "
              f"(Business Settings → Users → System users) and update "
              f"IG_ACCESS_TOKEN before relying on the schedule.")
        sys.exit(1)
    print("\nAll checks passed — the engine can post autonomously. 🚀")

if __name__ == "__main__":
    main()
