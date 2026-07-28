"""W8 — media hosting moved off the repo.

Instagram's Graph API fetches media itself from a public URL, which is why
assets were committed to a public repo in the first place. That made the
repo grow with every run — 18 MB tracked, 33.7 MB of history — and every
checkout in every workflow slower.

MEDIA_BASE_URL was always the indirection point. This module is the one
place a public media URL is built, so the host can move without touching
the posting code.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import media  # noqa: E402


def test_joins_base_and_path(monkeypatch):
    monkeypatch.setenv("MEDIA_BASE_URL", "https://cdn.pursuitai.net")
    assert media.public_url("assets/cards/x.png") == \
        "https://cdn.pursuitai.net/assets/cards/x.png"


@pytest.mark.parametrize("base,path", [
    ("https://cdn.pursuitai.net/", "assets/cards/x.png"),
    ("https://cdn.pursuitai.net", "/assets/cards/x.png"),
    ("https://cdn.pursuitai.net/", "/assets/cards/x.png"),
])
def test_slashes_never_double_or_vanish(monkeypatch, base, path):
    monkeypatch.setenv("MEDIA_BASE_URL", base)
    url = media.public_url(path)
    assert url == "https://cdn.pursuitai.net/assets/cards/x.png", url
    assert "//assets" not in url


def test_windows_style_separators_are_normalised(monkeypatch):
    """os.path.relpath yields backslashes on Windows; a URL never has them."""
    monkeypatch.setenv("MEDIA_BASE_URL", "https://cdn.pursuitai.net")
    assert media.public_url("assets\\cards\\x.png") == \
        "https://cdn.pursuitai.net/assets/cards/x.png"


def test_missing_base_fails_with_an_actionable_message(monkeypatch):
    monkeypatch.delenv("MEDIA_BASE_URL", raising=False)
    with pytest.raises(media.MediaConfigError) as e:
        media.public_url("assets/cards/x.png")
    assert "MEDIA_BASE_URL" in str(e.value)


def test_non_https_base_is_rejected(monkeypatch):
    """Instagram fetches this URL itself and will not accept plain http."""
    monkeypatch.setenv("MEDIA_BASE_URL", "http://cdn.pursuitai.net")
    with pytest.raises(media.MediaConfigError):
        media.public_url("assets/cards/x.png")


def test_localhost_base_is_rejected(monkeypatch):
    """A local path is unreachable to Instagram's fetcher — failing here is
    far clearer than a Graph API error about an unfetchable media URL."""
    monkeypatch.setenv("MEDIA_BASE_URL", "https://localhost:8000")
    with pytest.raises(media.MediaConfigError):
        media.public_url("assets/cards/x.png")


def test_post_ig_uses_the_shared_builder(monkeypatch):
    """One implementation, so the host can move in one place."""
    import post_ig
    monkeypatch.setenv("MEDIA_BASE_URL", "https://cdn.pursuitai.net")
    assert post_ig._public_url("assets/cards/x.png") == \
        media.public_url("assets/cards/x.png")


def test_generated_assets_are_not_tracked_by_git():
    """The whole point of W8: rendered media must stop entering history."""
    import subprocess
    tracked = subprocess.run(
        ["git", "ls-files", "assets/cards", "assets/screenshots",
         "assets/video", "assets/preview"],
        cwd=ROOT, capture_output=True, text=True).stdout.split()
    assert not tracked, (
        f"{len(tracked)} generated asset(s) still tracked, e.g. {tracked[:3]}")
