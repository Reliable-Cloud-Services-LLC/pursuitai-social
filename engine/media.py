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
