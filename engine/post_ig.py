"""Post to Instagram via the Instagram Graph API (Business/Creator account).

Instagram's API only accepts a PUBLIC URL for media - it fetches the file
itself. The GitHub Actions workflow commits assets to the repo first, so we
publish raw.githubusercontent.com URLs.

Env vars required:
  IG_USER_ID       - Instagram professional account ID (from Graph API)
  IG_ACCESS_TOKEN  - long-lived Page/system-user token with
                     instagram_basic + instagram_content_publish
  MEDIA_BASE_URL   - public base URL where committed assets are reachable,
                     e.g. https://raw.githubusercontent.com/<user>/<repo>/main
"""
import os
import time
import requests

import media

GRAPH = "https://graph.facebook.com/v21.0"

def _public_url(repo_rel_path):
    """Delegates to engine/media.py so the host lives in one place."""
    return media.public_url(repo_rel_path)

def post_image(repo_rel_path, caption):
    uid, tok = os.environ["IG_USER_ID"], os.environ["IG_ACCESS_TOKEN"]
    r = requests.post(f"{GRAPH}/{uid}/media", data={
        "image_url": _public_url(repo_rel_path),
        "caption": caption, "access_token": tok}, timeout=120)
    r.raise_for_status()
    return _publish(uid, tok, r.json()["id"])

def post_reel(repo_rel_path, caption):
    uid, tok = os.environ["IG_USER_ID"], os.environ["IG_ACCESS_TOKEN"]
    r = requests.post(f"{GRAPH}/{uid}/media", data={
        "media_type": "REELS", "video_url": _public_url(repo_rel_path),
        "caption": caption, "share_to_feed": "true",
        "access_token": tok}, timeout=120)
    r.raise_for_status()
    container = r.json()["id"]
    # videos process asynchronously - poll until FINISHED
    for _ in range(40):
        s = requests.get(f"{GRAPH}/{container}",
                         params={"fields": "status_code", "access_token": tok},
                         timeout=60).json()
        if s.get("status_code") == "FINISHED":
            break
        if s.get("status_code") == "ERROR":
            raise RuntimeError(f"IG video processing failed: {s}")
        time.sleep(15)
    return _publish(uid, tok, container)

def _publish(uid, tok, creation_id):
    r = requests.post(f"{GRAPH}/{uid}/media_publish", data={
        "creation_id": creation_id, "access_token": tok}, timeout=120)
    r.raise_for_status()
    media_id = r.json()["id"]
    print(f"[ig] published media id {media_id}")
    return media_id
