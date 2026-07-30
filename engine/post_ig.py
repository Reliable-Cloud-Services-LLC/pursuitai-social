"""Post to Instagram via the Instagram Graph API (Business/Creator account).

Instagram's API only accepts a PUBLIC URL for media - it fetches the file
itself. The prepare job syncs assets/ to object storage and MEDIA_BASE_URL
points at that bucket, so anything Instagram must fetch has to exist before
prepare finishes; a file created during publish is never uploaded.

Meta's content-publishing reference: "JPEG is the only image format
supported." Everything we render is PNG, so run.py converts the Instagram
variant before recording it in pending.json.

Env vars required:
  IG_USER_ID       - Instagram professional account ID (from Graph API)
  IG_ACCESS_TOKEN  - long-lived Page/system-user token with
                     instagram_basic + instagram_content_publish
  MEDIA_BASE_URL   - public base URL where uploaded assets are reachable,
                     e.g. https://<bucket>.s3.amazonaws.com
"""
import os
import time
import requests

import media

GRAPH = "https://graph.facebook.com/v21.0"


class InstagramError(RuntimeError):
    """A Meta API rejection, carrying what Meta actually said."""


def _check(response, what):
    """raise_for_status() with Meta's explanation kept.

    Meta returns a JSON body naming the exact problem — error.message,
    error_user_title, error_user_msg, and an fbtrace_id support can look
    up. Bare raise_for_status() discards ALL of it and leaves
    "400 Client Error: Bad Request", which is unactionable: it cannot
    distinguish a bad aspect ratio from an expired token from an
    unreachable URL. That is exactly the hole the first live Reels
    failure fell into.
    """
    if response.ok:
        return response
    try:
        err = (response.json() or {}).get("error", {})
    except ValueError:
        err = {}
    bits = [err.get("error_user_title"), err.get("error_user_msg"),
            err.get("message")]
    detail = " — ".join(b for b in bits if b) or response.text[:400]
    trace = err.get("fbtrace_id")
    raise InstagramError(
        f"{what} failed ({response.status_code}): {detail}"
        + (f" [fbtrace_id {trace}]" if trace else ""))


def _public_url(repo_rel_path):
    """Delegates to engine/media.py so the host lives in one place."""
    return media.public_url(repo_rel_path)

def post_image(repo_rel_path, caption):
    uid, tok = os.environ["IG_USER_ID"], os.environ["IG_ACCESS_TOKEN"]
    r = requests.post(f"{GRAPH}/{uid}/media", data={
        "image_url": _public_url(repo_rel_path),
        "caption": caption, "access_token": tok}, timeout=120)
    _check(r, "image container creation")
    return _publish(uid, tok, r.json()["id"])

# Where to take the cover from when no poster image is available. Meta's
# default is 0 — the first frame — and our spots open on a bare gradient
# before the entrance animation, so the default is a blank square in the
# profile grid. Text lands by 0.1s; a second in is safely past the fade
# for any clip length. cover_url is the real answer, this is the floor.
COVER_FALLBACK_MS = 1000


def post_reel(repo_rel_path, caption, cover_rel_path=None):
    uid, tok = os.environ["IG_USER_ID"], os.environ["IG_ACCESS_TOKEN"]
    payload = {
        "media_type": "REELS", "video_url": _public_url(repo_rel_path),
        "caption": caption, "share_to_feed": "true",
        "access_token": tok}
    if cover_rel_path:
        # "cover_url ... We will cURL the image using the URL that you
        # specify so the image must be on a public server." It takes
        # precedence over thumb_offset, so only one is ever sent.
        payload["cover_url"] = _public_url(cover_rel_path)
    else:
        payload["thumb_offset"] = str(COVER_FALLBACK_MS)
    r = requests.post(f"{GRAPH}/{uid}/media", data=payload, timeout=120)
    _check(r, "reel container creation")
    container = r.json()["id"]
    # videos process asynchronously - poll until FINISHED
    for _ in range(40):
        s = requests.get(f"{GRAPH}/{container}",
                         params={"fields": "status_code", "access_token": tok},
                         timeout=60).json()
        if s.get("status_code") == "FINISHED":
            break
        if s.get("status_code") == "ERROR":
            # status_error_message names the transcode complaint; the bare
            # status dict alone rarely does.
            raise InstagramError(
                f"reel processing failed: "
                f"{s.get('status_error_message') or s}")
        time.sleep(15)
    else:
        raise InstagramError(
            "reel processing never reached FINISHED after 10 minutes — "
            "the container expired unpublished rather than silently "
            "publishing something unverified")
    return _publish(uid, tok, container)

def _publish(uid, tok, creation_id):
    r = requests.post(f"{GRAPH}/{uid}/media_publish", data={
        "creation_id": creation_id, "access_token": tok}, timeout=120)
    _check(r, "publish")
    media_id = r.json()["id"]
    print(f"[ig] published media id {media_id}")
    return media_id
