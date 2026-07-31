"""The missed-run alarm.

GitHub, verbatim: "If the load is sufficiently high enough, some queued
jobs may be dropped." A dropped run fails nothing, logs nothing, sends no
review — the day just passes. It is the quietest failure this project has,
and the weekly heartbeat cannot see it.

Observed on 2026-07-30: the scheduled run queued 118 minutes late. On
2026-07-31 it had not fired 75 minutes past its window. Nothing was broken
either day, which is precisely the problem.
"""
import datetime
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import missed_run  # noqa: E402


class FakeSession:
    def __init__(self, runs, expect_created=None):
        self._runs = runs
        self.expect_created = expect_created
        self.params = None

    def get(self, url, headers=None, params=None, timeout=None):
        self.params = params
        session = self

        class R:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"workflow_runs": session._runs}

        return R()


MON = datetime.date(2026, 7, 27)
SAT = datetime.date(2026, 8, 1)
SUN = datetime.date(2026, 8, 2)


# ---------- which days are due ----------

@pytest.mark.parametrize("day,due", [(MON, True), (SAT, True), (SUN, False)])
def test_scheduled_days_match_the_cron(day, due):
    """daily.yml runs Mon-Sat. Alarming on Sunday would be a weekly false
    positive, which is how alarms get ignored."""
    assert missed_run.is_scheduled_day(day) is due


# ---------- the alarm ----------

def test_no_run_on_a_due_day_is_missed():
    missed, msg = missed_run.check("o/r", "t", MON, FakeSession([]))
    assert missed
    assert "due" in msg and "Run workflow" in msg, "must say how to recover"


def test_a_run_today_is_not_missed():
    session = FakeSession([{"created_at": "2026-07-27T15:28:06Z"}])
    missed, _ = missed_run.check("o/r", "t", MON, session)
    assert not missed


def test_sunday_never_alarms():
    missed, msg = missed_run.check("o/r", "t", SUN, FakeSession([]))
    assert not missed
    assert "not a scheduled day" in msg


def test_a_manual_dispatch_counts_as_the_day_running():
    """Someone working around a problem by hand has NOT missed the day.
    Alarming then would fire on exactly the day a human already
    intervened."""
    session = FakeSession([{"created_at": "2026-07-27T22:09:19Z",
                            "event": "workflow_dispatch"}])
    missed, _ = missed_run.check("o/r", "t", MON, session)
    assert not missed


def test_yesterdays_run_does_not_count_as_todays():
    """The API filter is a >= date range, so a stale run can come back in
    the payload. Filtering must be on the run's own date, not on trusting
    the query."""
    session = FakeSession([{"created_at": "2026-07-26T13:37:00Z"}])
    missed, _ = missed_run.check("o/r", "t", MON, session)
    assert missed, "a run from another day was counted as today's"


def test_the_query_is_scoped_to_today():
    session = FakeSession([])
    missed_run.check("o/r", "t", MON, session)
    assert session.params["created"] == ">=2026-07-27"


# ---------- a LATE run is not a MISSING run ----------

def test_the_alarm_runs_after_the_observed_delay():
    """2026-07-30 queued 118 minutes late. An alarm firing at, say, 14:30
    would have cried wolf that day — and an alarm that cries wolf is worse
    than none, because the next real one gets dismissed."""
    import re
    wf = open(os.path.join(ROOT, ".github", "workflows",
                           "missed-run.yml")).read()
    alarm_h, alarm_m = _cron_hm(wf)
    daily = open(os.path.join(ROOT, ".github", "workflows",
                              "daily.yml")).read()
    post_h, post_m = _cron_hm(daily)
    gap = (alarm_h * 60 + alarm_m) - (post_h * 60 + post_m)
    assert gap >= 120, f"only {gap} min after the post window — too tight"


def _cron_hm(workflow_text):
    import re
    m = re.search(r'cron:\s*"(\d+)\s+(\d+)', workflow_text)
    return int(m.group(2)), int(m.group(1))


def test_the_alarm_covers_the_same_days_as_the_post():
    import re
    for name in ("daily.yml", "missed-run.yml"):
        text = open(os.path.join(ROOT, ".github", "workflows", name)).read()
        m = re.search(r'cron:\s*"[\d]+\s+[\d]+\s+\*\s+\*\s+([\d\-]+)"', text)
        assert m and m.group(1) == "1-6", f"{name} days: {m and m.group(1)}"
