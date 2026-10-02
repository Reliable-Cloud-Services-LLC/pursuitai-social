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
from urllib.parse import quote

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

# --- Videos API ------------------------------------------------------------
# A different API from Images, not a variant of it: multipart upload with a
# per-part ETag, an explicit finalize step, and a transcode that can fail
# with a reason. The `ad` format is 1 post in 4, so without this LinkedIn
# would silently cover three formats out of four.
VIDEO_SUFFIXES = (".mp4",)

# "Length: Three seconds to 30 minutes. File size: Between 75kb and 500MB.
# File format: MP4."
#
# NB the schema table separately says "Maximum allowed Videos size is 5GB"
# for fileSizeBytes, which contradicts the 500MB specification above it.
# The tighter documented bound is used: our ads are a few MB, so the choice
# costs nothing, and refusing locally beats a transcode failure after a
# multipart upload has already been spent.
MIN_VIDEO_BYTES = 75 * 1024
MAX_VIDEO_BYTES = 500 * 1024 * 1024

# Transcode is slower than an image resize and the cost of giving up early
# is the same as it is there: publishing against media members cannot see.
VIDEO_TRIES, VIDEO_DELAY = 60, 5     # 5min


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
        # URN encoded. We send X-Restli-Protocol-Version: 2.0.0, which
        # requires path keys be encoded — an unencoded urn:li:image:... is
        # read as path variables and rejected with "Syntax exception in path
        # variables" (400). The Images doc's GET sample shows the URN raw,
        # but that sample also omits the protocol header; the Videos doc,
        # which keeps it, shows the encoded form. Observed live 2026-10-02.
        r = requests.get(f"{API}/images/{quote(image_urn, safe='')}",
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


def upload_image(repo_rel_path, token, owner_urn):
    """Upload a local image, returning its urn:li:image URN.

    `owner_urn` is a person OR an organization — the Images API documents
    both, and which one we send is the whole difference between posting to
    the Page and posting to a profile.
    """
    path = _abs(repo_rel_path)
    check_image(path)

    r = requests.post(f"{API}/images?action=initializeUpload",
                      headers=_headers(token),
                      data=json.dumps(
                          {"initializeUploadRequest": {"owner": owner_urn}}),
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


# Resolved once per process: the publish job posts one thing, but
# post_media -> post_image/post_video would otherwise ask twice.
_PERSON_URN = None


def person_urn(token):
    """This token's own urn:li:person, asked of the API.

    Derived rather than stored as a secret: a stored person URN is one more
    value to keep in sync with the token, and the failure mode of getting it
    wrong is posting as somebody else.
    """
    global _PERSON_URN
    if _PERSON_URN:
        return _PERSON_URN
    r = requests.get("https://api.linkedin.com/v2/userinfo",
                     headers={"Authorization": f"Bearer {token}"}, timeout=30)
    _check(r, "member identity lookup")
    sub = (r.json() or {}).get("sub")
    if not sub:
        raise LinkedInError(
            "userinfo returned no `sub`, so the author URN cannot be "
            "resolved — the token lacks the `profile` scope. Re-mint it with "
            "`profile` alongside w_member_social.")
    _PERSON_URN = f"urn:li:person:{sub}"
    return _PERSON_URN


def author_urn(token):
    """Who the post is authored by: the Page if we can, else the member.

    LINKEDIN_ORG_ID is the switch, and it is absent on purpose today.
    Posting as the Page needs `w_organization_social`, which only the vetted
    Community Management product grants — that request was denied and is
    under appeal. The self-serve route grants `w_member_social`, which
    authors as a person.

    So this reads as a capability check rather than a mode flag: set
    LINKEDIN_ORG_ID when the Page access lands and posting moves to the Page
    with no code change. Until then it is a profile, and that is announced
    at publish time rather than inferred from silence — posting to the wrong
    author is not something to discover from the feed.
    """
    org = os.environ.get("LINKEDIN_ORG_ID")
    if org:
        return f"urn:li:organization:{org}"
    urn = person_urn(token)
    print(f"[linkedin] LINKEDIN_ORG_ID unset — authoring as {urn} "
          f"(a member profile, NOT the Pursuit AI Page)")
    return urn


def post_image(repo_rel_path, commentary, alt_text=None):
    """Publish a single-image post to the Page. Returns {"id", "url"}."""
    token = os.environ["LINKEDIN_ACCESS_TOKEN"]
    # One URN for both the asset owner and the post author. They MUST match:
    # "For images with member URN owners, the caller needs to match the
    # image owner" — a mismatch is a 403 at publish, after the upload.
    author = author_urn(token)
    image_urn = upload_image(repo_rel_path, token, author)

    payload = {
        "author": author,
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


# The token's real state, not an assumption about it.
INTROSPECT_URL = "https://www.linkedin.com/oauth/v2/introspectToken"

# What the posting path cannot work without. Checked against the token's
# ACTUAL scopes rather than against what we believe we ticked in the portal.
REQUIRED_SCOPE = "w_organization_social"


def introspect(token=None, client_id=None, client_secret=None):
    """LinkedIn's own view of a token: {active, status, expires_at, scope}.

    Requires the app's client credentials. Returns None when they are absent,
    so every caller degrades to the LINKEDIN_TOKEN_EXPIRES_AT fallback rather
    than failing.

    Worth the extra secret because it answers two questions a stored expiry
    timestamp cannot:

      * REVOCATION. A revoked token has a future expires_at and is dead. A
        timestamp reports it healthy right up until the post fails.
      * SCOPE. The token carries whatever the approving member consented to,
        which is not necessarily what you meant to tick. Finding out at
        publish time is finding out too late.
    """
    cid = client_id or os.environ.get("LINKEDIN_CLIENT_ID")
    secret = client_secret or os.environ.get("LINKEDIN_CLIENT_SECRET")
    tok = token or os.environ.get("LINKEDIN_ACCESS_TOKEN")
    if not (cid and secret and tok):
        return None
    r = requests.post(INTROSPECT_URL,
                      data={"client_id": cid, "client_secret": secret,
                            "token": tok},
                      headers={"Content-Type":
                               "application/x-www-form-urlencoded"},
                      timeout=30)
    # 400 = invalid client id or token, 401 = invalid client secret. Both are
    # real answers about the credentials, so they are raised rather than
    # swallowed into "unknown".
    _check(r, "token introspection")
    return r.json() or {}


def token_state(now=None):
    """(days_left, status, scopes) from introspection, else the fallback.

    status is LinkedIn's own: active / expired / revoked — or "unknown" when
    only the stored timestamp is available, because a timestamp genuinely
    cannot tell you whether a token was revoked.
    """
    try:
        data = introspect()
    except Exception as e:            # never let a check break a publish
        print(f"[linkedin] introspection unavailable ({e})")
        data = None
    if data:
        expires = data.get("expires_at")
        days = (int((expires - (now if now is not None else time.time()))
                    // 86400) if expires else None)
        status = data.get("status") or (
            "active" if data.get("active") else "inactive")
        scopes = [s.strip() for s in (data.get("scope") or "").split(",")
                  if s.strip()]
        return days, status, scopes
    return token_days_left(now), "unknown", []


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


def check_video(path):
    """Refuse a video LinkedIn documents as unsupported. Returns its size.

    DURATION IS NOT CHECKED HERE, deliberately. LinkedIn requires 3s-30min,
    but measuring needs ffprobe and the publish job has no ffmpeg — only
    prepare installs it. Plumbing the measurement through pending.json would
    be real work to guard a case that cannot arise: adspot.SCENES totals
    ~14.2s before narration scaling, so our ads are an order of magnitude
    clear of the floor and three orders clear of the ceiling. If that ever
    changes, LinkedIn reports it as PROCESSING_FAILED and
    _await_video_available surfaces its processingFailureReason verbatim.
    """
    if not path.lower().endswith(VIDEO_SUFFIXES):
        raise LinkedInError(
            f"{os.path.basename(path)}: LinkedIn's Videos API supports "
            f"{', '.join(VIDEO_SUFFIXES)}")
    size = os.path.getsize(path)
    if not MIN_VIDEO_BYTES <= size <= MAX_VIDEO_BYTES:
        raise LinkedInError(
            f"{os.path.basename(path)} is {size:,} bytes; the documented "
            f"range is {MIN_VIDEO_BYTES:,}-{MAX_VIDEO_BYTES:,}")
    return size


def _part_id(response, part_no, total):
    """The upload-part id, read from the ETag response header.

    "Callers should get the IDs as ETags from the response headers when they
    upload the videos."

    The docs show the value two ways — a quoted hex digest in the schema
    table ("e4383924336106965d6cd2a111beaceb") and an unquoted path in the
    live sample (/ambry-video/signedId/AQ...bin). Strip surrounding quotes
    when present and pass anything else through, so both forms work; sending
    a quoted id where an unquoted one was meant fails finalize with nothing
    useful to read.
    """
    etag = response.headers.get("etag") or response.headers.get("ETag")
    if not etag:
        raise LinkedInError(
            f"video part {part_no}/{total} uploaded but returned no ETag — "
            f"refusing to finalize an upload whose parts cannot be named")
    return etag.strip('"')


def upload_video(repo_rel_path, token, owner_urn):
    """Upload a local MP4, returning its urn:li:video URN.

    Three steps, all required: initialize to register the upload and receive
    per-part byte ranges, PUT each range and keep its ETag, then finalize
    with those ids in instruction order.
    """
    path = _abs(repo_rel_path)
    size = check_video(path)

    r = requests.post(f"{API}/videos?action=initializeUpload",
                      headers=_headers(token),
                      data=json.dumps({"initializeUploadRequest": {
                          "owner": owner_urn,
                          "fileSizeBytes": size,
                          "uploadCaptions": False,
                          "uploadThumbnail": False}}),
                      timeout=120)
    _check(r, "video upload initialization")
    value = (r.json() or {}).get("value") or {}
    video_urn = value.get("video")
    instructions = value.get("uploadInstructions") or []
    # Present and empty-string for a single-part upload in LinkedIn's own
    # sample, so its absence is not an error — but it must be echoed back.
    upload_token = value.get("uploadToken", "")
    if not video_urn or not instructions:
        raise LinkedInError(
            f"initializeUpload returned no upload target: {value}")

    part_ids = []
    total = len(instructions)
    with open(path, "rb") as f:
        for n, part in enumerate(instructions, 1):
            first, last = part.get("firstByte"), part.get("lastByte")
            url = part.get("uploadUrl")
            if url is None or first is None or last is None:
                raise LinkedInError(f"upload instruction {n}/{total} is "
                                    f"incomplete: {part}")
            # Sliced from the RANGES LinkedIn sent, not a hardcoded chunk
            # size. The docs say to "split -b 4194303" but then describe
            # parts as 0-4194303 inclusive, which is 4194304 bytes — the two
            # disagree by one, and a part sliced to the wrong length fails
            # finalize. The instructions are authoritative; the prose is not.
            f.seek(first)
            chunk = f.read(last - first + 1)
            up = requests.put(url,
                              headers={"Authorization": f"Bearer {token}",
                                       "Content-Type":
                                           "application/octet-stream"},
                              data=chunk, timeout=300)
            _check(up, f"video part {n}/{total} upload")
            part_ids.append(_part_id(up, n, total))

    fin = requests.post(f"{API}/videos?action=finalizeUpload",
                        headers=_headers(token),
                        data=json.dumps({"finalizeUploadRequest": {
                            "video": video_urn,
                            "uploadToken": upload_token,
                            # "The order needs to be the same as the order of
                            # parts in the upload instructions."
                            "uploadedPartIds": part_ids}}),
                        timeout=120)
    _check(fin, "video upload finalization")
    _await_video_available(video_urn, token)
    return video_urn


def _await_video_available(video_urn, token, tries=VIDEO_TRIES,
                           delay=VIDEO_DELAY):
    """Block until LinkedIn reports the video AVAILABLE.

    Same contract as _await_available for images, and the same reason: the
    Posts API will happily accept a video that is still processing, and the
    result is a post members cannot see.

    One thing the image path has no equivalent of: PROCESSING_FAILED carries
    a `processingFailureReason`, so a failure here can say WHY instead of
    just that it happened.
    """
    for attempt in range(tries):
        # URN encoded, per the documented GET form
        # (urn%3Ali%3Avideo%3A...). Images are fetched unencoded and work,
        # so that path is left alone rather than changed on a hunch.
        r = requests.get(f"{API}/videos/{quote(video_urn, safe='')}",
                         headers=_headers(token, json_body=False), timeout=60)
        _check(r, "video status")
        data = r.json() or {}
        status = data.get("status")
        if status == "AVAILABLE":
            return
        if status == "PROCESSING_FAILED":
            raise LinkedInError(
                f"video processing failed for {video_urn}: "
                f"{data.get('processingFailureReason') or 'no reason given'}")
        if attempt < tries - 1:
            time.sleep(delay)
    raise LinkedInError(
        f"video {video_urn} never became AVAILABLE after "
        f"{tries * delay}s — refusing to publish a post whose video members "
        f"would not see")


def upload_media(repo_rel_path, token, owner_urn):
    """Upload image or video, returning its URN.

    The upload half of post_media, exposed separately so the validator can
    prove the chain WITHOUT publishing — and so it proves the same code the
    publish job runs, rather than a parallel implementation that can drift.
    """
    if is_video(repo_rel_path):
        return upload_video(repo_rel_path, token, owner_urn)
    return upload_image(repo_rel_path, token, owner_urn)


def is_video(repo_rel_path):
    return (repo_rel_path or "").lower().endswith(VIDEO_SUFFIXES)


def post_media(repo_rel_path, commentary, alt_text=None):
    """Publish a single-media post, image or video, to the Page.

    The ONE entry point callers use, so a new format cannot reach LinkedIn
    through a path that only understands images — which is exactly what
    happened before this existed: `ad` is 1 post in 4 and post_image's
    check_image rejected its .mp4, so a quarter of the calendar could not
    reach the channel at all.
    """
    if is_video(repo_rel_path):
        return post_video(repo_rel_path, commentary, title=alt_text)
    return post_image(repo_rel_path, commentary, alt_text=alt_text)


def post_video(repo_rel_path, commentary, title=None):
    """Publish a single-video post to the Page. Returns {"id", "url"}."""
    token = os.environ["LINKEDIN_ACCESS_TOKEN"]
    # Same rule as images: "For videos with member URN owners, the caller
    # needs to match the video owner."
    author = author_urn(token)
    video_urn = upload_video(repo_rel_path, token, author)

    payload = {
        "author": author,
        "commentary": commentary,
        "visibility": "PUBLIC",
        "distribution": {"feedDistribution": "MAIN_FEED",
                         "targetEntities": [],
                         "thirdPartyDistributionChannels": []},
        # Video content carries `title`, where an image carries `altText` —
        # the Posts API documents them as different fields on the same
        # media object, not as synonyms.
        "content": {"media": {"id": video_urn,
                              "title": title or "PursuitAI"}},
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    r = requests.post(f"{API}/posts", headers=_headers(token),
                      data=json.dumps(payload), timeout=120)
    _check(r, "post creation")
    post_id = r.headers.get("x-restli-id")
    if not post_id:
        raise LinkedInError(
            "post created but LinkedIn returned no x-restli-id header — "
            "refusing to log a post we cannot identify afterwards")
    print(f"[linkedin] posted {_permalink(post_id)}")
    return {"id": post_id, "url": _permalink(post_id)}
