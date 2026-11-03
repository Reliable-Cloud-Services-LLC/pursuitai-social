"""Out-of-band posts: a correction, or a one-off, outside the rotation.

The motivating case: an ad published with a mispronounced acronym, because
the pronunciation lexicon was silently inert in CI. Reposting it correctly
means running one specific topic again — and that must NOT move the
rotation, or the topic that was actually due next gets skipped and nobody
notices until it never appears.
"""
import json
import os
import shutil
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import run  # noqa: E402


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A throwaway copy of the project, so state is never touched."""
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


def _publishable_ids():
    """The topics prepare will actually accept.

    Deliberately run.publishable_topics and NOT compliance.is_publishable.
    prepare indexes the performance-weighted rotation CYCLE, which omits
    weak topics outright, so the looser check can hand back a topic prepare
    then refuses. That is not hypothetical: once enough metrics accumulated
    for weighting to engage, five topics dropped out and this helper started
    returning fit-scoring at index 0 — turning the suite red with no code
    change at all. Deduped because the cycle repeats strong topics.
    """
    with open(os.path.join(ROOT, "content", "calendar.json")) as f:
        cal = json.load(f)
    return list(dict.fromkeys(t["id"] for t in run.publishable_topics(cal)))


def test_forced_topic_is_the_one_prepared(repo):
    wanted = _publishable_ids()[3]
    run.prepare(force_format="card", force_topic=wanted)
    with open(run.PENDING) as f:
        assert json.load(f)["topic"] == wanted


def test_a_forced_topic_is_marked_out_of_band(repo):
    run.prepare(force_format="card", force_topic=_publishable_ids()[3])
    with open(run.PENDING) as f:
        assert json.load(f)["out_of_band"] is True


def test_the_rotations_own_turn_is_not_out_of_band(repo):
    run.prepare(force_format="card")
    with open(run.PENDING) as f:
        assert json.load(f)["out_of_band"] is False


def test_an_out_of_band_post_does_not_advance_the_rotation(repo, monkeypatch):
    """The defect this exists to prevent: reposting a correction would
    consume the topic that was actually due, which then never appears."""
    before = {"topic_index": 2, "run_count": 2}
    with open(run.STATE, "w") as f:
        json.dump(before, f)
    run.prepare(force_format="card", force_topic=_publishable_ids()[0])

    # Go through the real approval gate rather than stubbing it — the gate
    # is what decides whether publish() runs at all, and faking it here
    # would let a change to the gate pass this test unnoticed.
    run.approve()
    monkeypatch.setenv("X_API_KEY", "test")
    # Both channels stay in the map — the post log records every one of
    # them, so dropping ig here would fail for a reason unrelated to the
    # rotation. ig has no credential, so it lands as "skipped".
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
        after = json.load(f)
    assert after["topic_index"] == before["topic_index"]
    assert after["run_count"] == before["run_count"]


def test_an_unknown_topic_refuses_rather_than_falling_back(repo):
    """Silently posting the rotation's topic instead of the one asked for
    would be a correction that never corrects anything."""
    with pytest.raises(SystemExit):
        run.prepare(force_format="card", force_topic="no-such-topic")


def test_an_unpublishable_topic_cannot_be_forced(repo):
    """--topic bypasses the cursor, never compliance."""
    import compliance
    with open(os.path.join(ROOT, "content", "calendar.json")) as f:
        cal = json.load(f)
    blocked = [t["id"] for t in cal["topics"]
               if not compliance.is_publishable(t)]
    if not blocked:
        pytest.skip("every topic is publishable")
    with pytest.raises(SystemExit):
        run.prepare(force_format="card", force_topic=blocked[0])
