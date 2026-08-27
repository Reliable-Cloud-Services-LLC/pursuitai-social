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


class ContainerProcessingError(InstagramError):
    """Meta reported status_code ERROR on a container.

    Its own type so the retry can catch THIS and nothing else. The
    alternative — matching on the message text — would silently start
    retrying a container that merely never finished, which is a different
    failure with a different cost: an ERROR is a fast definite negative,
    while a stalled container burns the full ten-minute poll before we
    learn anything, and two of those do not fit in the publish job's
    thirty-minute budget.
    """


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


def _error_detail(container, tok, fallback):
    """Meta's reason for an ERROR container, best-effort.

    A SECOND call, made only on failure, because `status` is undocumented
    (the reference specifies status_code alone). Asking for an unknown
    field can 400 the request — and doing that on the hot path would turn
    a diagnostic nicety into an outage on every poll. Here the worst case
    is that we fall back to the dict we already had.

    Exists because the 2026-08-17 failure logged only
    ``{'status_code': 'ERROR', 'id': '...'}``, which cannot tell a bad file
    from a transient transcode — the two need opposite responses.
    """
    try:
        r = requests.get(
            f"{GRAPH}/{container}",
            params={"fields": "status", "access_token": tok}, timeout=30)
        detail = (r.json() or {}).get("status")
        if detail:
            return detail
    except Exception as exc:
        print(f"[ig] could not fetch error detail ({exc})")
    return fallback


def _await_ready(container, tok, tries, delay, what):
    """Block until Meta says the container can be published.

    Returns on FINISHED; raises on ERROR or on running out of tries. There
    is deliberately no fall-through: publishing a container whose status was
    never confirmed is how something unverified ships.
    """
    for attempt in range(tries):
        # status_code is the only DOCUMENTED field, so the hot path asks
        # for nothing else. Detail is fetched separately, and only on
        # failure — see _error_detail.
        s = requests.get(f"{GRAPH}/{container}",
                         params={"fields": "status_code", "access_token": tok},
                         timeout=60).json()
        status = s.get("status_code")
        if status == "FINISHED":
            return
        if status == "ERROR":
            raise ContainerProcessingError(
                f"{what} processing failed: {_error_detail(container, tok, s)}")
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

# A container that comes back ERROR is re-created, not given up on.
#
# 2026-08-17: a reel failed with `2207085`, a code outside Meta's documented
# 2207001-2207057 range — so no file-shaped complaint fired (2207026 is
# "video format is not supported", 2207052 is "could not be fetched"), and
# nothing said whether the file or the transcode was at fault. The 2026-08-24
# ad then published from the same renderer and the same pipeline, and a fresh
# container for that file transcodes clean today. One-off, not a defect.
#
# Meta treats this class as retryable in its own reference: 2207032 is
# "Create media fail, please try to re-create media", and for 2207008 the
# advice is "Try again 1-2 times in the next 30 seconds to 2 minutes".
#
# TWO attempts, not three. Each poll runs up to ten minutes and the publish
# job's budget is thirty, which has to cover X as well. Retrying is also
# quota-safe: the publishing limit counts published posts, and an ERROR
# container never published anything.
REEL_ATTEMPTS = 2
REEL_RETRY_DELAY = 45

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
    for attempt in range(1, REEL_ATTEMPTS + 1):
        r = requests.post(f"{GRAPH}/{uid}/media", data=payload, timeout=120)
        # NOT retried: a rejected creation is a 4xx about credentials,
        # permissions or the payload, and re-sending an identical request
        # cannot fix any of them.
        _check(r, "reel container creation")
        container = r.json()["id"]
        try:
            _await_ready(container, tok, REEL_TRIES, REEL_DELAY, "reel")
        except ContainerProcessingError as e:
            if attempt == REEL_ATTEMPTS:
                raise
            print(f"[ig] {e} — re-creating the container "
                  f"(attempt {attempt + 1}/{REEL_ATTEMPTS})")
            time.sleep(REEL_RETRY_DELAY)
            continue
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
