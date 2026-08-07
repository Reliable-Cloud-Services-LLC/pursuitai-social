"""OPERATIONS.md must not drift from the repo.

A stale operations doc is worse than none, because it is believed. This
file pins the facts in it that a reader would ACT on — cron times, topic
counts, the format list — so the doc cannot quietly rot into fiction.

It does not police prose. Only the numbers someone would rely on.
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import compliance  # noqa: E402
import run  # noqa: E402

DOC = open(os.path.join(ROOT, "docs", "OPERATIONS.md")).read()


def _wf(name):
    return open(os.path.join(ROOT, ".github", "workflows", name)).read()


def test_the_publishable_count_is_current():
    with open(os.path.join(ROOT, "content", "calendar.json")) as f:
        cal = json.load(f)
    pub = [t for t in cal["topics"] if compliance.is_publishable(t)
           and not compliance.check_rendered(t)]
    assert f"{len(pub)} of {len(cal['topics'])} topics publishable" in DOC, (
        f"doc does not say {len(pub)} of {len(cal['topics'])}")


def test_the_format_rotation_is_current():
    assert " → ".join(run.FORMATS) in DOC, (
        f"doc does not list the real rotation {run.FORMATS}")


def test_the_cron_times_are_current():
    """The doc tells an operator when to expect a post and when silence is
    a problem. Both wrong is worse than both absent.

    heartbeat.yml was missing from this loop, and its line went stale the
    moment its cron moved off :00 — the exact drift this file exists to
    stop. It was also the only time here written in ET, so it could not
    have been checked against a cron without converting first; the doc now
    states it in UTC like everything else.
    """
    for wf_name, label in (("daily.yml", "post"),
                           ("missed-run.yml", "alarm"),
                           ("heartbeat.yml", "heartbeat")):
        m = re.search(r'cron:\s*"(\d+)\s+(\d+)', _wf(wf_name))
        assert m, f"no cron in {wf_name}"
        minute, hour = m.group(1), m.group(2)
        stamp = f"{int(hour):02d}:{int(minute):02d}"
        assert stamp in DOC, f"{label} time {stamp} ({wf_name}) not in doc"


def test_the_alarm_margin_claimed_in_the_doc_is_real():
    """The doc justifies the alarm time with an observed delay. If someone
    tightens the cron gap without updating the reasoning, the number and
    its justification part ways."""
    post = re.search(r'cron:\s*"(\d+)\s+(\d+)', _wf("daily.yml"))
    alarm = re.search(r'cron:\s*"(\d+)\s+(\d+)', _wf("missed-run.yml"))
    gap = ((int(alarm.group(2)) * 60 + int(alarm.group(1)))
           - (int(post.group(2)) * 60 + int(post.group(1))))
    assert f"{gap} minutes after the cron" in DOC, (
        f"doc does not state the real {gap}-minute margin")


def test_every_linked_file_exists():
    for rel in re.findall(r"\]\((\.\./[^)#]+|[A-Z_]+\.md)\)", DOC):
        path = os.path.normpath(os.path.join(ROOT, "docs", rel))
        assert os.path.exists(path), f"OPERATIONS.md links to missing {rel}"


def test_the_documented_ffmpeg_cap_matches_the_workflow():
    """The doc tells an operator when to stop waiting on apt. If the
    workflow cap changes and the doc does not, the number they act on is
    fiction — and this doc's whole purpose is being trusted cold."""
    daily = _wf("daily.yml")
    m = re.search(r"Install ffmpeg\n\s+timeout-minutes:\s*(\d+)", daily)
    assert m, "the ffmpeg step lost its timeout"
    assert f"caps that step at **{m.group(1)} minutes**" in DOC, (
        f"doc does not state the real {m.group(1)}-minute cap")


def test_the_documented_supersampling_matches_the_renderer():
    """The doc explains WHY an ad takes 9-13 minutes. If the renderer's
    supersampling changes, the explanation stops explaining anything."""
    src = open(os.path.join(ROOT, "engine", "adspot.py")).read()
    m = re.search(r"^SS\s*=\s*(\d+)", src, re.M)
    assert m, "adspot.py no longer defines SS"
    assert f"{m.group(1)}× supersampling" in DOC, (
        f"doc does not cite the real {m.group(1)}x supersampling")
