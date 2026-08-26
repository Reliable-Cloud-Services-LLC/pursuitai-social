"""Which site section a `screenshot` post uses.

The defect this file exists for: run.py took the first section whose files
existed, from a fixed ("hero", "features", "pricing", "why"). `hero` is the
site root, so it always captures, so it always won. Every screenshot post
ever published — nine of them between 2026-07-18 and 2026-08-26, across
nine different topics — shipped the identical hero image, while features,
pricing and why were re-captured on every run and never once used.

It passed every gate, because compliance, freshness and card-overflow all
answer whether a post is PERMITTED. None of them notice that the picture
has not changed since July.
"""
import json
import os
import shutil
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import run  # noqa: E402
import screenshots  # noqa: E402


def _seed(sdir, names):
    """Create the pair of files a captured section leaves behind.

    Real (tiny) PNGs, not empty placeholders: prepare converts the
    Instagram variant to JPEG, because Meta accepts no other image format,
    and that opens the file. A zero-byte stand-in passes an os.path.exists
    check and then fails several steps later somewhere unrelated.
    """
    from PIL import Image
    os.makedirs(sdir, exist_ok=True)
    for name in names:
        for kind, size in (("x", (16, 9)), ("ig", (8, 10))):
            Image.new("RGB", size, (20, 12, 46)).save(
                os.path.join(sdir, f"{name}_{kind}.png"))


def _section_of(path):
    return os.path.basename(path)[:-len("_x.png")]


def _walk(sdir, count):
    """The sections `count` consecutive screenshot posts would use."""
    return [_section_of(run.pick_section(i, sdir)[0]) for i in range(count)]


def test_every_section_is_reachable(tmp_path):
    """The test that would have caught this. Six captured sections, six
    consecutive posts, six distinct images — not one image six times."""
    sdir = str(tmp_path / "shots")
    _seed(sdir, run.SECTION_ORDER)
    used = _walk(sdir, len(run.SECTION_ORDER))
    assert set(used) == set(run.SECTION_ORDER)
    assert len(set(used)) == len(used), f"repeated within one cycle: {used}"


def test_consecutive_screenshot_posts_differ(tmp_path):
    """Back-to-back posts sharing an image is the visible symptom."""
    sdir = str(tmp_path / "shots")
    _seed(sdir, run.SECTION_ORDER)
    used = _walk(sdir, len(run.SECTION_ORDER) * 2)
    repeats = [(a, b) for a, b in zip(used, used[1:]) if a == b]
    assert not repeats, f"consecutive repeat: {repeats}"


def test_the_previous_strategy_fails_this(tmp_path):
    """Negative control: pin the OLD behaviour as wrong, so the tests above
    are known to discriminate rather than merely pass. Without this, a
    regression to first-existing could look green if the new tests were
    subtly toothless."""
    sdir = str(tmp_path / "shots")
    _seed(sdir, run.SECTION_ORDER)

    def old_pick(_cursor, d):
        for c in ("hero", "features", "pricing", "why"):
            xp = os.path.join(d, f"{c}_x.png")
            if os.path.exists(xp):
                return (xp, None)
        return (None, None)

    used = [_section_of(old_pick(i, sdir)[0])
            for i in range(len(run.SECTION_ORDER))]
    assert used == ["hero"] * len(run.SECTION_ORDER)
    assert len(set(used)) == 1  # exactly the bug: one image, forever


def test_a_failed_capture_is_stepped_over(tmp_path):
    """capture_all is best-effort per section. A section that failed must
    cost that section, not the post."""
    sdir = str(tmp_path / "shots")
    _seed(sdir, [s for s in run.SECTION_ORDER if s != run.SECTION_ORDER[0]])
    x, ig = run.pick_section(0, sdir)          # cursor points at the missing one
    assert x and ig
    assert _section_of(x) == run.SECTION_ORDER[1]


def test_no_captures_at_all_yields_nothing(tmp_path):
    """prepare falls back to a card on (None, None). Returning a path to a
    file that does not exist would fail later and less legibly."""
    assert run.pick_section(0, str(tmp_path / "empty")) == (None, None)


def test_cursor_wraps(tmp_path):
    """The cursor only ever increases; it indexes a ring."""
    sdir = str(tmp_path / "shots")
    _seed(sdir, run.SECTION_ORDER)
    n = len(run.SECTION_ORDER)
    assert _walk(sdir, n) == _walk(sdir, n * 3)[n * 2:]


def test_every_captured_section_is_published_or_withheld_on_purpose():
    """Drift guard across two files. A section added to screenshots.py that
    appears in neither list is captured on every run and used by nothing —
    the original defect in miniature, and silent. Withholding one is fine;
    withholding one by accident is not, so it has to be written down."""
    captured = {name for name, *_ in screenshots.SECTIONS}
    accounted = set(run.SECTION_ORDER) | set(run.SECTIONS_WITHHELD)
    assert captured == accounted, (
        f"captured but unaccounted for: {captured - accounted}; "
        f"listed but never captured: {accounted - captured}")


def test_withheld_sections_are_never_published():
    """The two lists must not overlap, or a section documented as unfit
    ships anyway."""
    assert not set(run.SECTION_ORDER) & set(run.SECTIONS_WITHHELD)


def test_every_withheld_section_says_why():
    """A bare exclusion list rots into superstition — nobody remembers
    whether the reason still holds, so nothing is ever promoted back."""
    for name, reason in run.SECTIONS_WITHHELD.items():
        assert len(reason) > 20, f"{name} withheld without a real reason"


# --- the cursor advances on publish, like topic_index -----------------------

@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A throwaway copy of the project, so real state is never touched."""
    dst = tmp_path / "repo"
    dst.mkdir()
    for d in ("engine", "content", "assets", "logs"):
        src = os.path.join(ROOT, d)
        if os.path.exists(src):
            shutil.copytree(src, dst / d)
        else:
            (dst / d).mkdir()
    monkeypatch.setattr(run, "ROOT", str(dst))
    for name in ("STATE", "PENDING", "APPROVED", "LOG", "METRICS"):
        rel = os.path.relpath(getattr(run, name), ROOT)
        monkeypatch.setattr(run, name, str(dst / rel))
    return dst


def _publish_one(repo, monkeypatch, fmt):
    _seed(str(repo / "assets" / "screenshots"), run.SECTION_ORDER)
    monkeypatch.setattr(screenshots, "capture_all", lambda *a, **k: [])
    with open(run.STATE, "w") as f:
        json.dump({"topic_index": 0, "run_count": 0, "shot_index": 3}, f)
    run.prepare(force_format=fmt)
    with open(run.PENDING) as f:
        assert json.load(f)["format"] == fmt, f"prepare fell back off {fmt}"
    run.approve()
    monkeypatch.setenv("X_API_KEY", "test")
    monkeypatch.delenv("IG_USER_ID", raising=False)
    monkeypatch.setattr(run, "POSTERS", {
        "x": ("X_API_KEY", lambda pending: "fake-id"),
        "ig": ("IG_USER_ID", lambda pending: "unreachable"),
    })
    try:
        run.publish()
    except SystemExit:
        pass
    with open(run.STATE) as f:
        return json.load(f)


def test_a_published_screenshot_advances_the_cursor(repo, monkeypatch):
    assert _publish_one(repo, monkeypatch, "screenshot")["shot_index"] == 4


def test_a_card_leaves_the_cursor_alone(repo, monkeypatch):
    """Otherwise three of every four posts would skip sections, and the
    rotation would sample the list rather than walk it."""
    assert _publish_one(repo, monkeypatch, "card")["shot_index"] == 3
