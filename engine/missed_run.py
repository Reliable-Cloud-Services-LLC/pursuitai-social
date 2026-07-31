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
