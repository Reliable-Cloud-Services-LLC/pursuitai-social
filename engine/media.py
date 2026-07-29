"""Public media URLs — the one place the media host is resolved.

Instagram's Graph API does not accept an upload; it fetches the file
itself from a public URL. That is the only reason rendered media was ever
committed to a public repo, and it made the repo grow with every run.

MEDIA_BASE_URL was always the indirection point, so moving to object
storage is a config change rather than a refactor. Everything that needs a
public URL comes through here.

The validation exists because the failure mode is otherwise miserable to
diagnose: Instagram reports an unfetchable media URL long after the run,
with no indication that the base was simply wrong.
"""
import os
import subprocess

# Instagram's fetcher is a remote server: it cannot reach a loopback or
# private address, and it will not follow a plain-http URL.
_UNREACHABLE_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "::1",
                      "host.docker.internal")


class MediaConfigError(RuntimeError):
    """The media host is misconfigured. Fail here, not at the platform."""


def base_url():
    base = (os.environ.get("MEDIA_BASE_URL") or "").strip()
    if not base:
        raise MediaConfigError(
            "MEDIA_BASE_URL is not set — Instagram fetches media by public "
            "URL and cannot be handed a local path. Point it at the media "
            "bucket (see SECRETS.md).")
    if not base.startswith("https://"):
        raise MediaConfigError(
            f"MEDIA_BASE_URL must be https, got {base!r} — Instagram will "
            "not fetch plain http.")
    host = base[len("https://"):].split("/", 1)[0].split(":", 1)[0].lower()
    if host in _UNREACHABLE_HOSTS:
        raise MediaConfigError(
            f"MEDIA_BASE_URL points at {host!r}, which Instagram's fetcher "
            "cannot reach. Use the public bucket URL.")
    return base.rstrip("/")


def public_url(repo_rel_path):
    """Repo-relative path -> the public URL a platform can fetch."""
    path = str(repo_rel_path).replace("\\", "/").lstrip("/")
    return f"{base_url()}/{path}"


def write_poster(video_path, at=None):
    """Write a still beside a rendered clip, for the Slack review.

    Slack image blocks need an actual image. Handing one an .mp4 URL gets
    the block rejected, which kills the whole review notification — and a
    review that never arrives is a silent stall, exactly what the fail-loud
    and approval work was built to prevent.

    `at` is the timestamp to grab, and the renderer should pass one: the
    reviewer is deciding whether THIS post goes out, so the still has to
    show the topic. The closing CTA is identical on every spot, so a frame
    from the end is a picture of nothing. Default is 55% of the clip, which
    lands in the body rather than the sign-off.

    Returns the poster path, or None if it could not be made — a degraded
    review (text, no image) is fine; a rejected block is not.
    """
    poster = os.path.splitext(video_path)[0] + "_poster.jpg"
    if at is None:
        at = (duration_seconds(video_path) or 8.0) * 0.55
    # JPEG, not PNG: this same still is the Instagram Reels cover, and
    # Meta's content-publishing reference states "JPEG is the only image
    # format supported." q:v 2 is visually indistinguishable here and a
    # third of the bytes. Slack renders JPEG in an image block equally well,
    # so one file serves both and the two cannot drift apart.
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", video_path,
         "-ss", f"{max(0.0, at):.2f}", "-frames:v", "1", "-q:v", "2", poster],
        check=False, capture_output=True)
    return poster if os.path.exists(poster) else None


def poster_for(rel_path):
    """The poster path for a media path, or the path itself if it is already
    an image. Callers that need something an image block will accept."""
    if not rel_path:
        return None
    if rel_path.lower().endswith((".mp4", ".mov")):
        return os.path.splitext(rel_path)[0] + "_poster.jpg"
    return rel_path


def duration_seconds(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True, check=True).stdout.strip()
        return float(out)
    except Exception:
        return None


# Meta's content-publishing reference: "JPEG is the only image format
# supported. Extended JPEG formats such as MPO and JPS are not supported."
# Every image we render is a PNG, so anything bound for Instagram — the
# card, the screenshot, the Reels cover — needs converting first.
IG_JPEG_QUALITY = 92


def as_jpeg(path):
    """A JPEG sibling of an image, for Instagram. Returns the JPEG path.

    A no-op for a file that is already JPEG. Raises rather than falling back
    to the PNG: a silent fallback would hand Meta a format its own reference
    says it does not support, and the resulting container error arrives
    later and says nothing useful.
    """
    if path.lower().endswith((".jpg", ".jpeg")):
        return path
    from PIL import Image
    out = os.path.splitext(path)[0] + ".jpg"
    with Image.open(path) as img:
        # Cards are RGBA; JPEG has no alpha channel. The designs are drawn
        # on an opaque background, so flattening onto black changes nothing
        # visible and avoids a save error.
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGB")
        img.save(out, "JPEG", quality=IG_JPEG_QUALITY, optimize=True)
    return out
