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


# --- the review image must be THIS run's render ----------------------------
#
# 2026-09-04: a spotlight was fixed, re-dispatched, and the reviewer saw the
# OLD broken render. Filenames are deterministic — a topic's capture is
# always <slug>_shot_x.png — so re-dispatching the same topic and format
# overwrites the object at the same URL, and Slack caches by URL.
#
# It looked like the fix had failed. The dangerous direction is the reverse:
# a stale GOOD image standing in for a broken new one, approved on the
# strength of a picture that is not what would publish.

def test_content_tag_reflects_bytes(tmp_path):
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    a.write_bytes(b"xxx")
    b.write_bytes(b"yyy")
    ta = media.content_tag("a.png", root=str(tmp_path))
    tb = media.content_tag("b.png", root=str(tmp_path))
    assert ta and tb and ta != tb
    b.write_bytes(b"xxx")
    assert media.content_tag("b.png", root=str(tmp_path)) == ta, \
        "identical bytes must produce an identical tag"


def test_a_missing_file_yields_no_tag(tmp_path):
    """Never invent a tag — a URL with a made-up version is worse than one
    without, because it looks deliberate."""
    assert media.content_tag("nope.png", root=str(tmp_path)) is None


def test_cache_bust_is_off_by_default(monkeypatch, tmp_path):
    """Instagram fetches by URL and its containers are long-lived, so the
    PUBLISH path keeps stable URLs. Only the review notification busts."""
    monkeypatch.setenv("MEDIA_BASE_URL", "https://cdn.test")
    assert "?v=" not in media.public_url("assets/cards/x_ig.jpg")


def test_cache_bust_appends_the_tag_when_the_file_exists(monkeypatch,
                                                         tmp_path):
    monkeypatch.setenv("MEDIA_BASE_URL", "https://cdn.test")
    root = tmp_path
    (root / "assets").mkdir()
    f = root / "assets" / "shot.png"
    f.write_bytes(b"render one")
    monkeypatch.setattr(media, "content_tag",
                        lambda rel, root=None: "deadbeef")
    url = media.public_url("assets/shot.png", cache_bust=True)
    assert url == "https://cdn.test/assets/shot.png?v=deadbeef"


def test_the_review_notification_asks_for_a_cache_busted_url(tmp_path,
                                                             monkeypatch):
    """The wiring, not just the helper. public_url defaults to NO bust, so a
    helper that works is worthless if the review path forgets to ask for it —
    which is the only place the staleness actually hurt anyone."""
    import json
    import shutil
    import sys
    sys.path.insert(0, os.path.join(ROOT, "engine"))
    import run
    import notify

    dst = tmp_path / "repo"
    dst.mkdir()
    for d in ("engine", "content", "assets", "logs"):
        src = os.path.join(ROOT, d)
        if os.path.exists(src):
            shutil.copytree(src, dst / d)
        else:
            (dst / d).mkdir()
    monkeypatch.setattr(run, "ROOT", str(dst))
    monkeypatch.setattr(run, "PENDING", str(dst / "content" / "pending.json"))
    (dst / "assets" / "cards").mkdir(parents=True, exist_ok=True)
    (dst / "assets" / "cards" / "t_x.png").write_bytes(b"render")
    with open(run.PENDING, "w") as f:
        json.dump({"topic": "t", "format": "card",
                   "media_x": "assets/cards/t_x.png",
                   "text_x": "x", "text_ig": "ig"}, f)

    seen = {}
    monkeypatch.setenv("MEDIA_BASE_URL", "https://cdn.test")
    monkeypatch.setattr(run.media, "public_url",
                        lambda rel, cache_bust=False: seen.update(
                            rel=rel, cache_bust=cache_bust) or "https://u")
    monkeypatch.setattr(notify, "_send_blocks", lambda *a, **k: True)
    run.notify_pending()
    assert seen.get("cache_bust") is True, (
        "the review image URL was not cache-busted — a reviewer can be shown "
        "the previous render of a post that has just been changed")
