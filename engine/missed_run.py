"""Did the scheduled run actually fire today?

GitHub is explicit that it may not:

  "The schedule event can be delayed during periods of high loads of
   GitHub Actions workflow runs. High load times include the start of
   every hour."
  "If the load is sufficiently high enough, some queued jobs may be
   dropped."

A dropped run is the worst kind of failure this project has: nothing fails,
nothing is logged, no review arrives, and the day simply passes. The weekly
heartbeat is too coarse to catch it — a Tuesday that never happened is
invisible until the following Monday, if at all.

This asks GitHub directly whether a run of the daily workflow was CREATED
today, which is the precise question. Deliberately not "did a post happen":
a run that fired and was left unapproved is the operator exercising the
gate, not a failure, and alarming on it would train them to ignore alarms.
"""
import datetime
import os

import requests

API = "https://api.github.com"
WORKFLOW = "daily.yml"
# Mon=0 … Sun=6. The cron is "* * 1-6" — Mon-Sat.
SCHEDULED_WEEKDAYS = frozenset({0, 1, 2, 3, 4, 5})


def is_scheduled_day(today=None):
    today = today or datetime.date.today()
    return today.weekday() in SCHEDULED_WEEKDAYS


def runs_created_today(repo, token, today=None, session=requests):
    """Runs of the daily workflow created on `today` (UTC).

    Counts EVERY event type, not just `schedule`. A manual dispatch that
    posted the day's content means the day was not missed, and alarming
    anyway would be a false positive on exactly the day someone worked
    around a problem by hand.
    """
    today = today or datetime.date.today()
    r = session.get(
        f"{API}/repos/{repo}/actions/workflows/{WORKFLOW}/runs",
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json"},
        params={"created": f">={today.isoformat()}", "per_page": 50},
        timeout=30)
    r.raise_for_status()
    return [run for run in r.json().get("workflow_runs", [])
            if (run.get("created_at") or "").startswith(today.isoformat())]


def check(repo, token, today=None, session=requests):
    """(missed, message). `missed` True only when a run was DUE and none ran."""
    today = today or datetime.date.today()
    if not is_scheduled_day(today):
        return False, f"{today} is not a scheduled day — nothing due"
    runs = runs_created_today(repo, token, today, session)
    if runs:
        return False, f"{len(runs)} run(s) created on {today}"
    return True, (
        f"No run of {WORKFLOW} was created on {today}, but one was due. "
        f"GitHub drops scheduled runs under load — this is that, or the "
        f"workflow has been disabled. Dispatch it by hand to post today: "
        f"Actions → Daily social post → Run workflow.")


# How far back to look for a gap. A bound, not a policy: without one this
# would walk to the repo's first commit on a fresh clone, and re-reporting a
# months-old outage helps nobody.
MAX_GAP_DAYS = 14


def runs_by_date(repo, token, since, session=requests):
    """{ "YYYY-MM-DD": run_count } for the daily workflow, since `since`."""
    r = session.get(
        f"{API}/repos/{repo}/actions/workflows/{WORKFLOW}/runs",
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json"},
        params={"created": f">={since.isoformat()}", "per_page": 100},
        timeout=30)
    r.raise_for_status()
    counts = {}
    for run in r.json().get("workflow_runs", []):
        day = (run.get("created_at") or "")[:10]
        if day:
            counts[day] = counts.get(day, 0) + 1
    return counts


def preceding_gap(repo, token, today=None, max_days=MAX_GAP_DAYS,
                  session=requests):
    """(missed_days, message) — scheduled days immediately before `today` on
    which no run was created.

    WHY THIS EXISTS, and why it is not just check() with a wider window:

    check() runs on a SCHEDULE, so it shares the failure mode it was built
    to detect. On 2026-08-27 GitHub dropped every scheduled run for this
    repo — the daily post at 13:37 and the alarm at 17:07 both — so the
    alarm could not fire to report the post it was watching. The day passed
    silently and was noticed by a human, which is the exact outcome the
    alarm exists to prevent. A scheduled workflow cannot detect its own
    non-execution.

    So this runs from a workflow that DID fire, and looks BACKWARDS. It
    cannot catch a gap on the day itself — check() owns that — but it
    cannot be silenced by the same drop either.

    Scanning contiguously backwards from `today` and stopping at the first
    day that DID run makes it self-limiting and self-deduplicating: no state
    file, no "already alerted" bookkeeping. Once a run fires on day N, the
    scan on day N+1 stops at N immediately, so a gap is reported exactly
    once. A wider "any missing day in the last fortnight" scan would
    re-report the same outage every day until it aged out.

    Non-scheduled days (Sunday) are stepped over rather than ending the
    scan, so a Saturday drop is still reported on Monday.

    Like check(), this asks whether a RUN was created — not whether a post
    happened. A run left unapproved is the operator exercising the gate.
    """
    today = today or datetime.date.today()
    since = today - datetime.timedelta(days=max_days)
    counts = runs_by_date(repo, token, since, session)

    missed = []
    day = today - datetime.timedelta(days=1)
    while day >= since:
        if is_scheduled_day(day):
            if counts.get(day.isoformat()):
                break
            missed.append(day)
        day -= datetime.timedelta(days=1)

    if not missed:
        return [], "no gap — the previous scheduled day ran"
    # Chronological. The scan runs backwards, but returning it that way
    # would hand callers a list whose order disagrees with the message
    # built from it.
    missed.reverse()
    days = ", ".join(d.isoformat() for d in missed)
    return missed, (
        f"No run of {WORKFLOW} was created on {days} "
        f"({len(missed)} scheduled day(s) missed). The same-day alarm is "
        f"itself a scheduled workflow, so a GitHub drop takes it out too — "
        f"this is that gap, found by the next run that did fire.")
