"""The dark-channel alarm.

Written after the outage it exists to catch. From 2026-09-24 to 2026-09-29
the engine posted nothing, and every monitor the project had said it was
fine:

  * missed_run.check      — a run WAS created each day. Healthy.
  * missed_run.preceding_gap — same question, backwards. No gap.
  * the weekly heartbeat  — two posts inside the trailing 7-day window. OK.
  * Slack                 — silent, because `prepare` never ran, so the
                            review that would have shown a human something
                            was due was itself suppressed.

The cause was a run parked at the approval gate holding daily.yml's
`daily-post` concurrency group: later runs queued behind it and were
cancelled before starting a job. Runs existed; posts did not.

So every test here is about the distinction between a run happening and a
post landing, and several are negative controls — they fail if the guard
they describe is removed.
"""
import datetime
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import notify           # noqa: E402
import stale_channel    # noqa: E402

REAL_LOG = os.path.join(ROOT, "logs", "posted.jsonl")

WED = datetime.date(2026, 9, 23)
THU = datetime.date(2026, 9, 24)
FRI = datetime.date(2026, 9, 25)
SAT = datetime.date(2026, 9, 26)
SUN = datetime.date(2026, 9, 27)
MON = datetime.date(2026, 9, 28)


def write_log(tmp_path, entries):
    p = tmp_path / "posted.jsonl"
    p.write_text("".join(json.dumps(e) + "\n" for e in entries))
    return str(p)


def posted(date, channel="x"):
    return {"date": date, "topic": "t", "format": "card",
            "channels": {channel: {"status": "posted", "id": "1"}}}


def failed(date):
    """A run that happened and posted nothing — the shape the alarm must
    refuse to count as life."""
    return {"date": date, "topic": "t", "format": "card",
            "channels": {"x": {"status": "error", "error": "boom"},
                         "ig": {"status": "error", "error": "boom"},
                         "linkedin": {"status": "skipped", "error": None}}}


class FakeGitHub:
    """Routes on URL, because check() hits two different endpoints."""

    def __init__(self, waiting=(), state="active", raises=False):
        self.waiting = list(waiting)
        self.state = state
        self.raises = raises
        self.seen = []

    def get(self, url, headers=None, params=None, timeout=None):
        self.seen.append((url, params))
        if self.raises:
            raise RuntimeError("github unreachable")
        payload = ({"workflow_runs": self.waiting} if url.endswith("/runs")
                   else {"state": self.state})
        outer = self

        class R:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return payload

        del outer
        return R()


def waiting_run(created="2026-09-24T17:56:02Z"):
    return {"created_at": created, "status": "waiting",
            "html_url": "https://github.com/o/r/actions/runs/1"}


# ---------- what counts as a post ----------

def test_a_run_that_posted_nothing_is_not_a_post(tmp_path):
    """The load-bearing rule. If a failed run counted, the alarm would
    report a healthy channel on exactly the days it was broken."""
    log = write_log(tmp_path, [posted("2026-09-23"), failed("2026-09-24"),
                               failed("2026-09-25")])
    assert stale_channel.last_confirmed_post(log) == WED


def test_one_posted_channel_is_enough(tmp_path):
    """A partial outage is not a dark channel — X posting while Instagram
    fails is a different alarm (notify.failure), and firing this one too
    would double-report a problem already being reported."""
    log = write_log(tmp_path, [{"date": "2026-09-25", "channels": {
        "x": {"status": "posted", "id": "1"},
        "ig": {"status": "error", "error": "boom"}}}])
    assert stale_channel.last_confirmed_post(log) == FRI


def test_a_truncated_final_line_does_not_blind_the_alarm(tmp_path):
    """The log is appended to by a job that can be interrupted. A
    half-written last line must not hide the fifty good ones above it."""
    p = tmp_path / "posted.jsonl"
    p.write_text(json.dumps(posted("2026-09-23")) + "\n" + '{"date": "2026-09')
    assert stale_channel.last_confirmed_post(str(p)) == WED


def test_a_missing_log_is_not_a_crash(tmp_path):
    assert stale_channel.last_confirmed_post(str(tmp_path / "nope")) is None


# ---------- counting dark days ----------

def test_today_is_not_counted_as_dark():
    """Negative control for the commonest false positive. The alarm runs
    hours before the day's post is approved; counting today would make
    every ordinary morning read one day darker than it is."""
    assert stale_channel.dark_days(WED, THU) == []


def test_sunday_is_stepped_over_not_counted():
    """The cron is Mon-Sat. Counting Sunday would inflate every Monday
    reading by one and turn a normal weekend into an alarm."""
    assert SUN not in stale_channel.dark_days(SAT, MON)


def test_dark_days_accumulate_across_the_outage():
    assert stale_channel.dark_days(WED, SAT) == [THU, FRI]


def test_the_walk_is_bounded_when_nothing_ever_posted():
    """Without a bound an empty log would count every scheduled day since
    the epoch — and a fresh clone has an empty log."""
    days = stale_channel.dark_days(None, MON)
    assert len(days) <= stale_channel.MAX_LOOKBACK_DAYS
    assert days and min(days) >= MON - datetime.timedelta(
        days=stale_channel.MAX_LOOKBACK_DAYS)


# ---------- the verdict ----------

def test_one_dark_day_stays_silent(tmp_path):
    """The case missed_run already defends: a single unapproved day is the
    operator exercising the gate, not a failure."""
    log = write_log(tmp_path, [posted("2026-09-23")])
    stale, _ = stale_channel.check(log, today=FRI)
    assert stale is False


def test_two_dark_days_fire(tmp_path):
    log = write_log(tmp_path, [posted("2026-09-23")])
    stale, message = stale_channel.check(log, today=SAT)
    assert stale is True
    assert "2026-09-24" in message and "2026-09-25" in message


def test_the_real_outage_would_have_been_caught_on_the_saturday(tmp_path):
    """Against the REAL post log, sliced at the outage.

    The outage began 2026-09-24 and a human found it on the 29th; the alarm
    fires on the 26th. Real entries, real code path — but the slice matters:
    the first version of this test read the live log, which passed when
    written and FAILED IN CI four days later, because the 09-24 post was
    approved in the meantime and the log it asserts about is appended to
    every day. An assertion about a historical fact must not be read from a
    file whose future changes can falsify it.
    """
    frozen = tmp_path / "posted.jsonl"
    with open(REAL_LOG) as src, open(frozen, "w") as out:
        for line in src:
            if not line.strip():
                continue
            if json.loads(line).get("date", "") <= WED.isoformat():
                out.write(line)
    assert stale_channel.last_confirmed_post(str(frozen)) == WED, \
        "the slice should end on the last pre-outage post"

    stale, message = stale_channel.check(str(frozen), today=SAT)
    assert stale is True
    assert "2026-09-24" in message and "2026-09-25" in message


def test_the_threshold_never_fires_on_the_engines_real_history():
    """The empirical claim in STALE_AFTER_DAYS' comment, pinned.

    Over every confirmed post before the outage, no two consecutive
    scheduled days were ever dark — so this threshold has a zero
    false-positive rate on all of recorded operation. If a future change to
    what counts as a post breaks that, this fails rather than quietly
    turning the alarm into noise.
    """
    dates = [d for d in stale_channel.confirmed_dates(REAL_LOG) if d <= WED]
    assert len(dates) > 40, "log unexpectedly short — is the rule still right?"
    worst = max(len(stale_channel.dark_days(a, b))
                for a, b in zip(dates, dates[1:]))
    assert worst < stale_channel.STALE_AFTER_DAYS, (
        f"{worst} dark day(s) occurred in normal operation; a threshold of "
        f"{stale_channel.STALE_AFTER_DAYS} would have cried wolf")


def test_a_never_posting_channel_is_stale(tmp_path):
    log = write_log(tmp_path, [failed("2026-09-24"), failed("2026-09-25")])
    stale, message = stale_channel.check(log, today=SAT)
    assert stale is True
    assert "any confirmed post" in message


# ---------- the diagnostic ----------

def test_a_waiting_run_is_named_as_the_cause(tmp_path):
    """The whole point of the diagnostic half: turn "the channel is dark"
    into a one-click fix."""
    log = write_log(tmp_path, [posted("2026-09-23")])
    gh = FakeGitHub(waiting=[waiting_run()])
    _, message = stale_channel.check(log, "o/r", "tok", today=SAT, session=gh)
    assert "awaiting approval since 2026-09-24" in message
    assert "concurrency" in message


def test_the_oldest_waiting_run_is_the_one_reported(tmp_path):
    """It is the oldest that holds the group; a newer one is queued behind
    it and naming that one would send the operator to the wrong run."""
    log = write_log(tmp_path, [posted("2026-09-23")])
    gh = FakeGitHub(waiting=[waiting_run("2026-09-26T10:00:00Z"),
                             waiting_run("2026-09-24T17:56:02Z")])
    _, message = stale_channel.check(log, "o/r", "tok", today=MON, session=gh)
    assert "since 2026-09-24" in message


def test_no_waiting_run_says_so_rather_than_implying_one(tmp_path):
    log = write_log(tmp_path, [posted("2026-09-23")])
    gh = FakeGitHub(waiting=[])
    _, message = stale_channel.check(log, "o/r", "tok", today=SAT, session=gh)
    assert "No run is waiting for approval" in message


def test_the_query_asks_github_only_for_waiting_runs(tmp_path):
    """Scoped server-side. Paging 100 completed runs to filter locally would
    miss a waiting run older than the page."""
    log = write_log(tmp_path, [posted("2026-09-23")])
    gh = FakeGitHub(waiting=[waiting_run()])
    stale_channel.check(log, "o/r", "tok", today=SAT, session=gh)
    runs_call = [p for url, p in gh.seen if url.endswith("/runs")]
    assert runs_call and runs_call[0].get("status") == "waiting"


# ---------- false-positive guards ----------

def test_a_deliberately_disabled_workflow_is_not_an_outage(tmp_path):
    """The README documents disabling the workflow as the kill switch. An
    alarm that screams through an intentional pause gets muted, and the
    mute costs the next real outage."""
    log = write_log(tmp_path, [posted("2026-09-23")])
    gh = FakeGitHub(state="disabled_manually")
    stale, message = stale_channel.check(log, "o/r", "tok", today=SAT,
                                         session=gh)
    assert stale is False
    assert "deliberate pause" in message


def test_github_disabling_the_schedule_for_inactivity_still_alarms(tmp_path):
    """Negative control on the guard above. `disabled_inactivity` is GitHub
    switching the cron off after 60 days — the definition of a silent
    failure, and the one thing a blanket "disabled means intentional" check
    would swallow."""
    log = write_log(tmp_path, [posted("2026-09-23")])
    gh = FakeGitHub(state="disabled_inactivity")
    stale, _ = stale_channel.check(log, "o/r", "tok", today=SAT, session=gh)
    assert stale is True


def test_an_unreachable_github_does_not_change_the_verdict(tmp_path):
    """The verdict comes from the local log on purpose, so the alarm cannot
    be silenced by the API it uses only for diagnosis."""
    log = write_log(tmp_path, [posted("2026-09-23")])
    stale, message = stale_channel.check(log, "o/r", "tok", today=SAT,
                                         session=FakeGitHub(raises=True))
    assert stale is True
    assert "2026-09-24" in message


# ---------- agreement with the heartbeat ----------

def test_the_heartbeat_and_the_alarm_share_one_definition_of_a_post(tmp_path):
    """Two monitors disagreeing about whether the channel is alive, during
    an outage, is the worst possible ambiguity. One definition, imported."""
    log = write_log(tmp_path, [posted("2026-09-22"), failed("2026-09-23")])
    _, last = notify.heartbeat_stats(log, today="2026-09-24")
    assert last == str(stale_channel.last_confirmed_post(log))


def test_the_heartbeat_reports_stale_despite_a_healthy_window_count(tmp_path,
                                                                    monkeypatch):
    """The exact regression that hid this outage: on 2026-09-28 the weekly
    heartbeat said OK because two posts sat inside its trailing 7-day
    window — while the channel had been dark for five days. A trailing
    count cannot distinguish "posting" from "stopped last week"."""
    sent = {}
    monkeypatch.setattr(notify, "_send",
                        lambda text: sent.setdefault("text", text) or True)
    log = write_log(tmp_path, [posted("2026-09-22"), posted("2026-09-23")])
    notify.heartbeat(log, today="2026-09-28")
    assert "STALE" in sent["text"]
    assert "2 confirmed post(s)" in sent["text"], \
        "the window count is still worth reporting — it is the verdict that was wrong"


def test_the_heartbeat_still_says_ok_when_the_channel_is_posting(tmp_path,
                                                                 monkeypatch):
    """Negative control for the test above: if STALE were unconditional the
    heartbeat would be useless in the other direction."""
    sent = {}
    monkeypatch.setattr(notify, "_send",
                        lambda text: sent.setdefault("text", text) or True)
    log = write_log(tmp_path, [posted("2026-09-25")])
    notify.heartbeat(log, today="2026-09-26")
    assert "OK" in sent["text"] and "STALE" not in sent["text"]


@pytest.mark.parametrize("entries,expect", [
    ([], "NO POSTS"),
    ([posted("2026-09-25")], "OK"),
])
def test_heartbeat_verdicts(tmp_path, monkeypatch, entries, expect):
    sent = {}
    monkeypatch.setattr(notify, "_send",
                        lambda text: sent.setdefault("text", text) or True)
    notify.heartbeat(write_log(tmp_path, entries), today="2026-09-26")
    assert expect in sent["text"]


# ---------- where the alarm is wired ----------

WORKFLOWS = os.path.join(ROOT, ".github", "workflows")


def _text(name):
    return open(os.path.join(WORKFLOWS, name)).read()


def _step_blocks(name):
    """Workflow steps as raw text.

    Deliberately NOT a YAML parse. test_workflow_config.py reads these files
    as text for the same reason: a parser is a dependency, the repo keeps
    those minimal, and the first version of this file added PyYAML to a
    local venv and nowhere else — green locally, ModuleNotFoundError in CI.
    """
    marker = "      - name:"
    parts = _text(name).split(marker)
    return [marker + p for p in parts[1:]]


def _step_with(name, needle):
    for block in _step_blocks(name):
        if needle in block:
            return block
    return None


def test_the_alarm_is_wired_into_the_missed_run_workflow():
    assert _step_with("missed-run.yml", "stale_channel"), \
        "the module exists but nothing calls it — a monitor that never runs"


def test_the_alarm_does_not_live_in_the_daily_workflow():
    """Negative control on the placement, and the reason the module exists.

    daily.yml's `daily-post` concurrency group was what the outage held.
    A check living inside daily.yml would have been queued behind the very
    failure it was meant to report — which is exactly what happened to the
    review notification.
    """
    assert "stale_channel" not in _text("daily.yml")


def test_the_alarm_runs_even_when_the_same_day_check_failed():
    """The step before it exits 1 on a missed day. Without `if: always()`
    a dropped run would silence the dark-channel report — two failures,
    one of them swallowed by the other's exit code."""
    step = _step_with("missed-run.yml", "stale_channel")
    assert "if: always()" in step


def test_the_workflow_can_read_runs_for_the_diagnostic():
    """The approval-gate diagnostic needs `actions: read`. Without it the
    alarm still fires with the right verdict, but drops the one line that
    tells the operator what to click."""
    text = _text("missed-run.yml")
    perms = text[text.index("permissions:"):]
    perms = perms[:perms.index("\njobs:")]
    assert "actions: read" in perms
