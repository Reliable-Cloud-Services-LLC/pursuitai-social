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

def test_the_alarm_runs_well_after_the_observed_delay():
    """Two consecutive scheduled runs queued almost identically late:
    118 min (2026-07-30) and 123 min (2026-07-31). So ~2h is TYPICAL here,
    not the worst case, and the margin must clear typical by a wide band —
    an alarm that cries wolf is worse than none, because the next real one
    gets dismissed."""
    import re
    wf = open(os.path.join(ROOT, ".github", "workflows",
                           "missed-run.yml")).read()
    alarm_h, alarm_m = _cron_hm(wf)
    daily = open(os.path.join(ROOT, ".github", "workflows",
                              "daily.yml")).read()
    post_h, post_m = _cron_hm(daily)
    gap = (alarm_h * 60 + alarm_m) - (post_h * 60 + post_m)
    assert gap >= 180, (
        f"only {gap} min after the post window. Observed delay is ~123 "
        f"min TYPICAL, so this leaves too little headroom.")


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


# --- the gap check ---------------------------------------------------------
#
# 2026-08-27: GitHub dropped EVERY scheduled run for this repo — the daily
# post at 13:37 and this alarm at 17:07. So the alarm could not report the
# post it was watching, and the day passed silently until a human noticed,
# which is the exact outcome the alarm exists to prevent.
#
# check() cannot cover that. It runs on a schedule, so it shares the failure
# mode it detects. preceding_gap() runs from a workflow that DID fire and
# looks backwards instead.

def _runs(*dates):
    return [{"created_at": f"{d}T14:00:00Z"} for d in dates]


def test_the_previous_scheduled_day_having_no_run_is_a_gap():
    """The 2026-08-27 case, from the next morning."""
    missed, msg = missed_run.preceding_gap(
        "r", "t", today=datetime.date(2026, 8, 28),
        session=FakeSession(_runs("2026-08-25", "2026-08-26")))
    assert [d.isoformat() for d in missed] == ["2026-08-27"]
    assert "2026-08-27" in msg


def test_no_gap_when_the_previous_scheduled_day_ran():
    missed, msg = missed_run.preceding_gap(
        "r", "t", today=datetime.date(2026, 8, 27),
        session=FakeSession(_runs("2026-08-26")))
    assert missed == [] and "no gap" in msg


def test_a_saturday_drop_is_still_reported_on_monday():
    """Sunday is not scheduled, so it must be stepped OVER rather than end
    the scan — otherwise every Saturday outage is invisible."""
    missed, _ = missed_run.preceding_gap(
        "r", "t", today=datetime.date(2026, 8, 31),   # Monday
        session=FakeSession(_runs("2026-08-28")))     # Fri ran, Sat did not
    assert [d.isoformat() for d in missed] == ["2026-08-29"]


def test_a_multi_day_outage_is_reported_whole():
    missed, msg = missed_run.preceding_gap(
        "r", "t", today=datetime.date(2026, 8, 28),
        session=FakeSession(_runs("2026-08-24")))
    assert [d.isoformat() for d in missed] == ["2026-08-25", "2026-08-26",
                                               "2026-08-27"]
    assert "3 scheduled day(s)" in msg


def test_the_scan_stops_at_the_first_day_that_ran():
    """What makes this self-deduplicating, with no state file: once a run
    fires on day N, the scan on N+1 stops at N. An older gap BEHIND a
    successful day is deliberately not re-reported — it was already
    reported when it was the leading edge."""
    missed, _ = missed_run.preceding_gap(
        "r", "t", today=datetime.date(2026, 8, 28),
        session=FakeSession(_runs("2026-08-26")))   # 25 missing, 26 ran
    assert [d.isoformat() for d in missed] == ["2026-08-27"]


def test_a_manual_dispatch_closes_the_gap():
    """Same rule as check(): any event type counts. Alarming on the day
    somebody worked around a problem by hand is a false positive."""
    missed, _ = missed_run.preceding_gap(
        "r", "t", today=datetime.date(2026, 8, 28),
        session=FakeSession(_runs("2026-08-27")))
    assert missed == []


def test_the_scan_is_bounded():
    """Without a bound this walks to the first commit on a fresh clone."""
    missed, _ = missed_run.preceding_gap(
        "r", "t", today=datetime.date(2026, 8, 28), max_days=3,
        session=FakeSession([]))
    assert len(missed) <= 3


def test_the_query_window_covers_the_whole_lookback():
    """Negative control on the API call itself: a `created` filter scoped to
    today would return no history, and every day would look like a gap."""
    s = FakeSession(_runs("2026-08-26"))
    missed_run.preceding_gap("r", "t", today=datetime.date(2026, 8, 28),
                             max_days=14, session=s)
    assert s.params["created"] == ">=2026-08-14"
