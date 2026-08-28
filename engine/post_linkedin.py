"""Post to a LinkedIn Page via the Community Management (Posts) API.

Verified against learn.microsoft.com on 2026-08-27. Nothing here is from
memory; the shapes below are quoted in the comments where they matter.

Three ways this differs from the other two channels, all of which shaped
this file:

  * **LinkedIn takes BYTES, not a URL.** Instagram fetches media from our
    bucket; here we upload the file ourselves, so nothing has to be public
    for a LinkedIn post to work.

  * **The image must be AVAILABLE before the post is created.** The Images
    API is asynchronous — "SYNCHRONOUS_UPLOAD is not supported in Images
    API" — and the docs are explicit about the cost of not waiting: "If the
    post is created before confirming image upload success and the image
    upload fails to process, the post won't be visible to members." A post
    that exists and cannot be seen is worse than a failed post, because
    nothing reports it.

  * **The post id comes back in a HEADER.** "A successful response returns a
    201 Created HTTP status code and the ID in the x-restli-id response
    header." Reading it from the body yields None, silently.

Env vars required:
  LINKEDIN_ACCESS_TOKEN  - member token with w_organization_social, whose
                           member holds an ADMINISTRATOR role on the Page
  LINKEDIN_ORG_ID        - numeric organization id (urn:li:organization:N)
  LINKEDIN_TOKEN_EXPIRES_AT - optional epoch seconds, for the expiry alarm

Tokens last 60 days and refreshing is a browser flow, so this channel needs
a human roughly every two months. See docs/LINKEDIN_ACCESS.md.
"""
import json
import os
import time

import requests

API = "https://api.linkedin.com/rest"

# "Include a request header with the key 'Linkedin-Version' and set the value
# to the version (format: YYYYMM)". Latest at time of writing; versions are
# "supported for a minimum of one (1) year" and LinkedIn "expects every
# versioned API call to specify a version; the latest version is not applied
# by default" — so this is pinned, not omitted, and migrating is a standing
# annual cost the other channels do not have.
LINKEDIN_VERSION = "202608"

# Poll budget for image processing. Generous relative to a still image
# because the failure mode of giving up early is not a failed post — it is
# publishing a post whose image is not ready, which members cannot see.
IMAGE_TRIES, IMAGE_DELAY = 30, 4     # 2min

# Documented Images API limits, checked locally so an oversized asset fails
# here with a legible message rather than as a 413 mid-publish.
MAX_PIXELS = 36_152_320
ALLOWED_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif")


class LinkedInError(RuntimeError):
    """A LinkedIn API rejection, carrying what LinkedIn actually said."""


def _headers(token, json_body=True):
    h = {"Authorization": f"Bearer {token}",
         "Linkedin-Version": LINKEDIN_VERSION,
         # "All API requests require the header
         #  X-Restli-Protocol-Version: 2.0.0"
         "X-Restli-Protocol-Version": "2.0.0"}
    if json_body:
        h["Content-Type"] = "application/json"
    return h


def _check(response, what):
    """raise_for_status() with LinkedIn's explanation kept.

    Same reasoning as post_ig._check: a bare "400 Client Error" cannot
    distinguish an expired token from a missing Page role from a malformed
    body, and those need completely different responses.
    """
    if response.ok:
        return response
    try:
        body = response.json() or {}
    except ValueError:
        body = {}
    detail = (body.get("message") or body.get("error_description")
              or response.text[:400])
    code = body.get("serviceErrorCode")
    raise LinkedInError(
        f"{what} failed ({response.status_code}): {detail}"
        + (f" [serviceErrorCode {code}]" if code else ""))


def _abs(repo_rel_path):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, repo_rel_path)


def check_image(path):
    """Refuse an asset LinkedIn documents as unsupported, before uploading.

    Cheap and local. The alternative is discovering it as a 415 or 413 after
    an initializeUpload has already been registered.
    """
    if not path.lower().endswith(ALLOWED_SUFFIXES):
        raise LinkedInError(
            f"{os.path.basename(path)}: LinkedIn's Images API supports "
            f"{', '.join(ALLOWED_SUFFIXES)}")
    from PIL import Image
    with Image.open(path) as im:
        pixels = im.width * im.height
    if pixels > MAX_PIXELS:
        raise LinkedInError(
            f"{os.path.basename(path)} is {pixels:,} pixels; the documented "
            f"limit is {MAX_PIXELS:,}")


def _await_available(image_urn, token, tries=IMAGE_TRIES, delay=IMAGE_DELAY):
    """Block until LinkedIn reports the image AVAILABLE.

    Statuses per the Images API: PROCESSING, PROCESSING_FAILED, AVAILABLE,
    WAITING_UPLOAD. There is deliberately no fall-through on an unknown
    status — publishing against an image whose state was never confirmed is
    how an invisible post gets created.
    """
    for attempt in range(tries):
        r = requests.get(f"{API}/images/{image_urn}",
                         headers=_headers(token, json_body=False), timeout=60)
        _check(r, "image status")
        status = (r.json() or {}).get("status")
        if status == "AVAILABLE":
            return
        if status == "PROCESSING_FAILED":
            raise LinkedInError(f"image processing failed for {image_urn}")
        if attempt < tries - 1:
            time.sleep(delay)
    raise LinkedInError(
        f"image {image_urn} never became AVAILABLE after {tries * delay}s — "
        f"refusing to publish a post whose image members would not see")


def upload_image(repo_rel_path, token, org_urn):
    """Upload a local image, returning its urn:li:image URN."""
    path = _abs(repo_rel_path)
    check_image(path)

    r = requests.post(f"{API}/images?action=initializeUpload",
                      headers=_headers(token),
                      data=json.dumps(
                          {"initializeUploadRequest": {"owner": org_urn}}),
                      timeout=120)
    _check(r, "image upload initialization")
    value = (r.json() or {}).get("value") or {}
    upload_url, image_urn = value.get("uploadUrl"), value.get("image")
    if not upload_url or not image_urn:
        raise LinkedInError(f"initializeUpload returned no upload target: "
                            f"{value}")

    with open(path, "rb") as f:
        # "Use a PUT method to upload the image. The upload call requires a
        # valid OAuth token in the 'Authorization' header. This is different
        # than the upload video call which doesn't accept an OAuth token."
        up = requests.put(upload_url,
                          headers={"Authorization": f"Bearer {token}"},
                          data=f, timeout=300)
    _check(up, "image upload")
    _await_available(image_urn, token)
    return image_urn


def post_image(repo_rel_path, commentary, alt_text=None):
    """Publish a single-image post to the Page. Returns {"id", "url"}."""
    token = os.environ["LINKEDIN_ACCESS_TOKEN"]
    org_urn = f"urn:li:organization:{os.environ['LINKEDIN_ORG_ID']}"
    image_urn = upload_image(repo_rel_path, token, org_urn)

    payload = {
        "author": org_urn,
        "commentary": commentary,
        "visibility": "PUBLIC",
        "distribution": {"feedDistribution": "MAIN_FEED",
                         "targetEntities": [],
                         "thirdPartyDistributionChannels": []},
        "content": {"media": {"id": image_urn,
                              "altText": alt_text or "PursuitAI"}},
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    r = requests.post(f"{API}/posts", headers=_headers(token),
                      data=json.dumps(payload), timeout=120)
    _check(r, "post creation")

    # "the ID in the x-restli-id response header" — NOT the body, which is
    # empty on 201. Reading it from the body returns None quietly, and the
    # post log would record a success with no id to find it by.
    post_id = r.headers.get("x-restli-id")
    if not post_id:
        raise LinkedInError(
            "post created but LinkedIn returned no x-restli-id header — "
            "refusing to log a post we cannot identify afterwards")
    print(f"[linkedin] posted {_permalink(post_id)}")
    return {"id": post_id, "url": _permalink(post_id)}


def _permalink(post_id):
    """Public URL for a post URN.

    Derived, not fetched: unlike Instagram's shortcode, LinkedIn's feed
    update URL is built from the URN itself, so there is no second call and
    nothing to fail after a successful publish.
    """
    return f"https://www.linkedin.com/feed/update/{post_id}/"


def token_days_left(now=None):
    """Days until LINKEDIN_TOKEN_EXPIRES_AT, or None if unset.

    LinkedIn access tokens last 60 days and programmatic refresh is limited
    to select partners, so this channel dies on a schedule unless a human
    re-authorizes. The expiry is carried as its own value rather than
    introspected, so the alarm works without spending a call — and so the
    absence of the value is visible rather than assumed healthy.
    """
    raw = os.environ.get("LINKEDIN_TOKEN_EXPIRES_AT")
    if not raw:
        return None
    return int((int(raw) - (now if now is not None else time.time())) // 86400)
