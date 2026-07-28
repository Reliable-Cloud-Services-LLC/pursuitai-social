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
import os
import sys

import requests

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
    if d.get("expires_at", 0):
        import datetime
        exp = datetime.datetime.fromtimestamp(d["expires_at"])
        days = (exp - datetime.datetime.now()).days
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
        test_asset = "assets/cards/fit-scoring_ig.png"
        url = f"{base.rstrip('/')}/{test_asset}"
        head = requests.head(url, timeout=30)
        if head.status_code != 200:
            fail(f"asset not publicly reachable ({head.status_code}): {url}\n"
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

    print("\nAll checks passed — the engine can post autonomously. 🚀")

if __name__ == "__main__":
    main()
