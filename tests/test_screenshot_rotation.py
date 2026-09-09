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


# --- a topic we know the right section for skips the rotation ---------------
#
# 2026-09-09: a post about PRICING shipped the features section. pricing-plans
# tags the whole #pricing SECTION (6.13 tall/wide), which the aspect guard
# rejects — correctly, since scaling it to 16:9 makes an illegible sliver — so
# it fell through to the cursor, which happened to be on `features`. The
# fallback worked exactly as designed and still produced the wrong image,
# while a clean `pricing` capture sat unused.

def test_a_named_section_wins_over_the_rotation(repo, monkeypatch):
    """The rotation is the right default for a topic we have nothing better
    for, and the wrong one when we know which section the topic is about."""
    import json
    sdir = str(repo / "assets" / "screenshots")
    _seed(sdir, run.SECTION_ORDER)
    monkeypatch.setattr(screenshots, "capture_all", lambda *a, **k: [])
    monkeypatch.setattr(screenshots, "capture_topic",
                        lambda *a, **k: (None, None))
    with open(run.STATE, "w") as f:
        # cursor sits on `features` — the exact state that shipped the bug
        json.dump({"topic_index": 0, "run_count": 0, "shot_index": 1}, f)
    run.prepare(force_format="screenshot", force_topic="pricing-plans")
    with open(run.PENDING) as f:
        media = json.load(f)["media_x"]
    assert "pricing" in media, f"pricing-plans got {media}"
    assert "features" not in media


def test_a_topic_with_no_named_section_still_rotates(repo, monkeypatch):
    """The map is an override for the few, not a replacement for the many."""
    import json
    sdir = str(repo / "assets" / "screenshots")
    _seed(sdir, run.SECTION_ORDER)
    monkeypatch.setattr(screenshots, "capture_all", lambda *a, **k: [])
    monkeypatch.setattr(screenshots, "capture_topic",
                        lambda *a, **k: (None, None))
    with open(run.STATE, "w") as f:
        json.dump({"topic_index": 0, "run_count": 0, "shot_index": 1}, f)
    # Derived, not hardcoded: prepare indexes the performance-weighted
    # rotation cycle, which omits weak topics — naming a topic literally
    # here fails the day it drops out (fit-scoring did exactly that).
    with open(os.path.join(ROOT, "content", "calendar.json")) as f:
        cal = json.load(f)
    unmapped = next(t["id"] for t in run.publishable_topics(cal)
                    if t["id"] not in run.SECTION_FOR_TOPIC)
    run.prepare(force_format="screenshot", force_topic=unmapped)
    with open(run.PENDING) as f:
        assert "features" in json.load(f)["media_x"]


def test_section_files_returns_nothing_when_the_capture_is_missing(tmp_path):
    """A named section whose capture failed must fall through to the
    rotation, not hand back a path to a file that is not there."""
    assert run.section_files("pricing", str(tmp_path)) == (None, None)


def test_every_named_section_is_one_we_actually_capture():
    """Drift guard: a map entry naming a section screenshots.py does not
    produce is silently a no-op — it falls through to the rotation and
    reintroduces the exact bug this map exists to fix."""
    captured = {name for name, *_ in screenshots.SECTIONS}
    unknown = set(run.SECTION_FOR_TOPIC.values()) - captured
    assert not unknown, f"named sections never captured: {unknown}"


def test_every_named_topic_is_a_real_topic():
    import json
    with open(os.path.join(ROOT, "content", "calendar.json")) as f:
        ids = {t["id"] for t in json.load(f)["topics"]}
    unknown = set(run.SECTION_FOR_TOPIC) - ids
    assert not unknown, f"map names topics that do not exist: {unknown}"
