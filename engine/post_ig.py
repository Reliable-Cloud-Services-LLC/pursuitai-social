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

# Meta builds the container asynchronously and rejects media_publish until
# it reports FINISHED — "The media is not ready for publishing, please wait
# for a moment". BOTH routes have to wait for it. An image is only a fetch
# of one file and is usually ready on the first check, so it polls fast and
# briefly; a reel is a transcode and gets ten minutes.
IMAGE_TRIES, IMAGE_DELAY = 20, 3     # 60s
REEL_TRIES, REEL_DELAY = 40, 15      # 10min


def _await_ready(container, tok, tries, delay, what):
    """Block until Meta says the container can be published.

    Returns on FINISHED; raises on ERROR or on running out of tries. There
    is deliberately no fall-through: publishing a container whose status was
    never confirmed is how something unverified ships.
    """
    for attempt in range(tries):
        s = requests.get(f"{GRAPH}/{container}",
                         params={"fields": "status_code", "access_token": tok},
                         timeout=60).json()
        status = s.get("status_code")
        if status == "FINISHED":
            return
        if status == "ERROR":
            # status_error_message names the complaint; the bare status dict
            # alone rarely does.
            raise InstagramError(
                f"{what} processing failed: "
                f"{s.get('status_error_message') or s}")
        if attempt < tries - 1:
            time.sleep(delay)
    raise InstagramError(
        f"{what} processing never reached FINISHED after {tries * delay}s — "
        f"the container expired unpublished rather than silently publishing "
        f"something unverified")


def post_image(repo_rel_path, caption):
    uid, tok = os.environ["IG_USER_ID"], os.environ["IG_ACCESS_TOKEN"]
    r = requests.post(f"{GRAPH}/{uid}/media", data={
        "image_url": _public_url(repo_rel_path),
        "caption": caption, "access_token": tok}, timeout=120)
    _check(r, "image container creation")
    container = r.json()["id"]
    _await_ready(container, tok, IMAGE_TRIES, IMAGE_DELAY, "image")
    return _publish(uid, tok, container)

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
    _await_ready(container, tok, REEL_TRIES, REEL_DELAY, "reel")
    return _publish(uid, tok, container)

def _permalink(media_id, tok):
    """The public URL. Unlike X — where the id slots straight into
    x.com/<handle>/status/<id> — an Instagram post lives at a SHORTCODE
    unrelated to its media id, so the only way to get the URL is to ask.

    Best-effort: a post that succeeded must never be reported as failed
    because a follow-up read did.
    """
    try:
        r = requests.get(f"{GRAPH}/{media_id}",
                         params={"fields": "permalink", "access_token": tok},
                         timeout=30)
        return r.json().get("permalink")
    except Exception as e:
        print(f"[ig] permalink lookup failed ({e}) — the post is fine")
        return None


def _publish(uid, tok, creation_id):
    r = requests.post(f"{GRAPH}/{uid}/media_publish", data={
        "creation_id": creation_id, "access_token": tok}, timeout=120)
    _check(r, "publish")
    media_id = r.json()["id"]
    url = _permalink(media_id, tok)
    print(f"[ig] posted {url}" if url
          else f"[ig] published media id {media_id} (no permalink)")
    return {"id": media_id, "url": url}
