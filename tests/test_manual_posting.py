"""The hand-posted channels.

LinkedIn has no API access and Instagram has none yet, so both are posted
by hand from artifacts scripts/preview.py generates. That makes the script
operational tooling, not a convenience — a broken ratio name or a caption
that silently exceeds a channel limit costs a real post.

The README documents these commands. It is the only place an operator
looks, so a drift between it and the code is a defect, not untidiness.
"""
import importlib.util
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import brand  # noqa: E402


def _preview():
    spec = importlib.util.spec_from_file_location(
        "preview", os.path.join(ROOT, "scripts", "preview.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def preview():
    return _preview()


@pytest.fixture(scope="module")
def readme():
    with open(os.path.join(ROOT, "README.md")) as f:
        return f.read()


# ---------- the channel specs are complete ----------

def test_every_channel_declares_both_artifact_kinds(preview):
    for channel, spec in preview.MANUAL.items():
        assert spec["ratios"], f"{channel} has no card ratios"
        assert spec["video_ratios"], f"{channel} has no video ratios"


def test_every_named_ratio_resolves(preview):
    """A typo here renders nothing and fails mid-run, after the voiceover
    has already been synthesised."""
    for channel, spec in preview.MANUAL.items():
        for key in ("ratios", "video_ratios"):
            for name in spec[key]:
                assert name in brand.SIZES, f"{channel}.{key}: {name!r}"


def test_instagram_leads_with_the_vertical_spot(preview):
    """Reels is the format with reach, and the operator attaches the first
    one listed."""
    assert preview.MANUAL["instagram"]["video_ratios"][0] == "video"
    assert brand.size("video") == (1080, 1920)


# ---------- the README matches the code ----------

def test_readme_documents_every_manual_channel(preview, readme):
    for channel in preview.MANUAL:
        assert f"--{channel}" in readme, f"{channel} is undocumented"


def test_readme_documents_the_ad_format(readme):
    assert "--format ad" in readme


def test_readme_lists_the_real_ratios(preview, readme):
    """The 'What gets written' table tells an operator which file to
    attach. A stale row sends the wrong aspect ratio to a live post."""
    table = readme[readme.index("### What gets written"):]
    table = table[:table.index("###", 10)]
    for channel, spec in preview.MANUAL.items():
        for name in set(spec["ratios"]) | set(spec["video_ratios"]):
            w, h = brand.size(name)
            assert f"`{name}` {w}×{h}" in table, (
                f"{channel}: {name} {w}x{h} missing from the README table")


def test_readme_does_not_promise_a_flag_that_does_not_exist(preview, readme):
    """The reverse drift: documentation for a flag that was renamed or
    removed sends an operator down a dead end."""
    import re
    block = readme[readme.index("## Posting by hand"):]
    block = block[:block.index("\n## ", 10)]
    flags = set(re.findall(r"(?<![\w-])--([a-z][a-z-]*)", block))
    known = {"linkedin", "instagram", "format", "topic", "posted", "channel"}
    assert flags <= known, f"README documents unknown flags: {flags - known}"
