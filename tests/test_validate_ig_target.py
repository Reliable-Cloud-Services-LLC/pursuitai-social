"""Which video the reel check actually tests.

The 2026-08-17 reel failure went ten days unanswered, and one reason was
that --reel silently tests the NEWEST posted video. By the time anyone ran
it, that was a later, healthy file — so the check passed and said nothing
about the artifact that failed. A green result answered "does the reel path
work" while the open question was "was that specific file bad".

--file makes the target explicit.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import validate_ig  # noqa: E402


def _log(tmp_path, *entries):
    p = tmp_path / "posted.jsonl"
    p.write_text("".join(json.dumps(e) + "\n" for e in entries))
    return str(p)


def test_an_explicit_file_wins_over_the_log(tmp_path):
    log = _log(tmp_path, {"media_ig": "assets/video/newer_ad.mp4"})
    assert validate_ig.resolve_reel_target(
        "assets/video/pricing-plans_ad.mp4", log
    ) == "assets/video/pricing-plans_ad.mp4"


def test_an_explicit_file_is_not_required_to_exist_locally(tmp_path):
    """assets/ is gitignored and a CI checkout has none of it, so a local
    existence check would make the flag unusable in the one place it has
    production credentials. The bucket is the only existence that counts."""
    assert validate_ig.resolve_reel_target(
        "assets/video/never-on-this-disk.mp4", str(tmp_path / "missing.jsonl")
    ) == "assets/video/never-on-this-disk.mp4"


def test_a_leading_slash_is_tolerated(tmp_path):
    """The URL is built by joining onto the bucket base; a leading slash
    would double it."""
    assert validate_ig.resolve_reel_target(
        "/assets/video/x_ad.mp4", str(tmp_path / "none")
    ) == "assets/video/x_ad.mp4"


def test_a_non_video_is_refused_before_meta_sees_it(tmp_path):
    """Otherwise Meta rejects it for a reason unrelated to the question,
    and the run reads like a reel-path failure."""
    with pytest.raises(validate_ig.NoReelTarget) as e:
        validate_ig.resolve_reel_target("assets/cards/t_ig.jpg",
                                        str(tmp_path / "none"))
    assert "not a video" in str(e.value)


def test_without_a_file_it_takes_the_newest_posted_video(tmp_path):
    log = _log(tmp_path,
               {"media_ig": "assets/video/older_ad.mp4"},
               {"media_ig": "assets/cards/t_ig.jpg"},
               {"media_ig": "assets/video/newest_ad.mp4"})
    assert validate_ig.resolve_reel_target(None, log) == \
        "assets/video/newest_ad.mp4"


def test_the_default_skips_non_video_posts(tmp_path):
    """Most posts are images; the scan must walk past them rather than
    stopping at the newest entry."""
    log = _log(tmp_path,
               {"media_ig": "assets/video/only_ad.mp4"},
               {"media_ig": "assets/screenshots/a_ig.jpg"},
               {"media_ig": "assets/screenshots/b_ig.jpg"})
    assert validate_ig.resolve_reel_target(None, log) == \
        "assets/video/only_ad.mp4"


def test_a_log_with_no_video_says_so(tmp_path):
    log = _log(tmp_path, {"media_ig": "assets/screenshots/a_ig.jpg"})
    with pytest.raises(validate_ig.NoReelTarget) as e:
        validate_ig.resolve_reel_target(None, log)
    assert "--file" in str(e.value), "the error should name the way out"


def test_a_missing_log_says_so(tmp_path):
    with pytest.raises(validate_ig.NoReelTarget) as e:
        validate_ig.resolve_reel_target(None, str(tmp_path / "absent.jsonl"))
    assert "no post log" in str(e.value)


def test_file_without_reel_exits_rather_than_no_opping(monkeypatch, capsys):
    """A silent no-op would look like the named file was tested when the
    newest one was — or nothing was.

    Executed, not grepped for. The guard sits ahead of the token read and
    every network call, so main() reaches it and exits without credentials.
    A source-matching version of this test would pass on the string sitting
    in a comment, which is how this repo has been fooled before.
    """
    monkeypatch.setattr(sys, "argv",
                        ["validate_ig.py", "--file", "assets/video/x.mp4"])
    monkeypatch.delenv("IG_ACCESS_TOKEN", raising=False)
    with pytest.raises(SystemExit) as e:
        validate_ig.main()
    assert e.value.code == 1
    assert "--reel" in capsys.readouterr().out
