"""Has a post actually REACHED a channel lately?

Both existing alarms ask whether a RUN was CREATED, and both say so
deliberately — missed_run's docstring defends it: "a run that fired and was
left unapproved is the operator exercising the gate, not a failure, and
alarming on it would train them to ignore alarms."

That reasoning holds for ONE unapproved day. It is blind to the state that
took the channel down for six days in September 2026:

    daily.yml declares `concurrency: {group: daily-post,
    cancel-in-progress: false}`. A run parked at the social-publish
    approval gate still OCCUPIES that group. GitHub allows one running run
    plus one queued, so every later day's run sat queued behind the
    unapproved 2026-09-24 run, and each new day displaced the previous
    queued one. Sep 25, 26 and 28 were cancelled before starting a single
    job; Sep 29 sat pending with zero jobs.

Every one of those days had a run CREATED, so check() and preceding_gap()
both read healthy. `prepare` never executed, so no review was ever sent —
the operator's evidence that something was due was itself suppressed. The
weekly heartbeat ran throughout and reported "OK", because two posts landed
inside its trailing 7-day window before the outage began.

So this asks the question none of them ask: when did a post last actually
reach a channel? That question cannot be satisfied by a run that never got
as far as rendering, which is exactly the property the other three lack.

It is a symptom check, not a cause check, and deliberately so — it catches
any cause of a dark channel, including ones nobody has thought of yet. When
it fires it also reports whether a run is sitting at the approval gate,
because that is the one cause the operator can clear in a single click.
"""
import datetime
import json
import os

import requests

import missed_run

# The scheduled days, the API base and the workflow name all come from
# missed_run rather than being restated. Two alarms that disagree about
# which days are due would contradict each other in the middle of an
# outage, which is the worst possible moment to be ambiguous.
API = missed_run.API
WORKFLOW = missed_run.WORKFLOW

# Alarm on the SECOND consecutive scheduled day with no confirmed post.
#
# Set from observed history, not taste. Over the engine's first two months
# — 49 confirmed posts, 2026-07-28 to 2026-09-23 — the gaps between
# consecutive posts were: 47 of zero dark scheduled days, one of a single
# dark day, and NONE of two or more. So this threshold has a zero
# false-positive rate against every day the engine has ever run, while
# still firing on day two of an outage rather than day six.
#
# One dark day stays silent on purpose: that is the single case missed_run
# already defends as the operator exercising the gate. Two consecutive dark
# days has never been normal operation here.
STALE_AFTER_DAYS = 2

# Bound on the backwards walk, mirroring missed_run.MAX_GAP_DAYS. Without
# one, a fresh clone with an empty log would count every scheduled day since
# the epoch.
MAX_LOOKBACK_DAYS = 30


def is_confirmed(entry):
    """True when a channel actually reported `posted` for this entry.

    The same rule notify.heartbeat_stats uses — and it now literally is the
    same code, because the two must never disagree about whether the
    channel is alive. A run that logged nothing but failures or skips is
    not a sign of life; it is the failure, written down.
    """
    channels = entry.get("channels") or {}
    return any((c or {}).get("status") == "posted"
               for c in channels.values() if isinstance(c, dict))


def confirmed_dates(log_path):
    """Sorted dates on which a post reached at least one channel.

    Malformed lines are skipped rather than raised on: the log is appended
    to by a job that may be interrupted, and a half-written final line must
    not blind the alarm to the fifty good ones above it.
    """
    if not os.path.exists(log_path):
        return []
    out = []
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                when = datetime.date.fromisoformat(entry["date"])
            except (ValueError, KeyError, TypeError):
                continue
            if is_confirmed(entry):
                out.append(when)
    return sorted(out)


def last_confirmed_post(log_path):
    """Date of the most recent confirmed post, or None."""
    dates = confirmed_dates(log_path)
    return dates[-1] if dates else None


def dark_days(last_post, today, max_days=MAX_LOOKBACK_DAYS):
    """Scheduled days after `last_post` and strictly BEFORE `today`.

    Today is excluded deliberately. This alarm runs hours before the day's
    post has been approved — counting today would make every ordinary
    morning look one day darker than it is, and the threshold would have to
    be inflated to compensate, costing a day of detection on real outages.

    With no post on record at all, the walk still has to terminate, so it
    starts at the lookback bound instead of at the beginning of time.
    """
    start = (last_post + datetime.timedelta(days=1) if last_post
             else today - datetime.timedelta(days=max_days))
    floor = today - datetime.timedelta(days=max_days)
    day = max(start, floor)
    out = []
    while day < today:
        if missed_run.is_scheduled_day(day):
            out.append(day)
        day += datetime.timedelta(days=1)
    return out


def _get(url, token, session, **params):
    r = session.get(url,
                    headers={"Authorization": f"Bearer {token}",
                             "Accept": "application/vnd.github+json"},
                    params=params or None, timeout=30)
    r.raise_for_status()
    return r.json()


def workflow_state(repo, token, session=requests):
    """GitHub's own state for daily.yml: active / disabled_manually / ….

    A deliberately paused schedule is not an outage. The README documents
    disabling the workflow in the Actions tab as the fast kill switch, and
    an alarm that screams every morning through an intentional pause is one
    the operator learns to mute — which would cost them the next real
    outage. `disabled_inactivity` is NOT treated as deliberate: that is
    GitHub switching the schedule off after 60 days, which is precisely a
    silent failure.
    """
    data = _get(f"{API}/repos/{repo}/actions/workflows/{WORKFLOW}",
                token, session)
    return data.get("state")


def awaiting_approval(repo, token, session=requests):
    """Runs of daily.yml parked at the approval gate, oldest first.

    This is the diagnostic half. A waiting run holds the `daily-post`
    concurrency group, so it does not merely fail to post itself — it stops
    every later run from starting. Naming it turns "the channel is dark"
    into "approve or reject this run and the queue drains", which is a
    one-click fix the operator otherwise has to rediscover.
    """
    data = _get(f"{API}/repos/{repo}/actions/workflows/{WORKFLOW}/runs",
                token, session, status="waiting", per_page=20)
    return sorted(data.get("workflow_runs", []),
                  key=lambda run: run.get("created_at") or "")


def check(log_path, repo=None, token=None, today=None,
          threshold=STALE_AFTER_DAYS, session=requests):
    """(stale, message) — has the channel been dark too long?

    `repo`/`token` are optional: without them the staleness verdict is
    unchanged and only the approval-gate diagnostic is missing. The verdict
    comes from the post log, which is local, so this cannot be silenced by
    the GitHub API being unreachable.
    """
    today = today or datetime.date.today()
    last = last_confirmed_post(log_path)
    dark = dark_days(last, today)
    since = f"the last confirmed post on {last}" if last else \
        f"any confirmed post in {MAX_LOOKBACK_DAYS} days"

    if len(dark) < threshold:
        return False, (f"{len(dark)} dark scheduled day(s) since {since} — "
                       f"under the threshold of {threshold}")

    if repo and token:
        try:
            state = workflow_state(repo, token, session)
        except Exception as e:
            state = None
            print(f"[stale-channel] could not read workflow state "
                  f"({type(e).__name__}: {e})")
        if state == "disabled_manually":
            return False, (f"{len(dark)} dark scheduled day(s) since {since}, "
                           f"but {WORKFLOW} is disabled — a deliberate pause "
                           f"is not an outage")

    days = ", ".join(d.isoformat() for d in dark)
    message = (f"No post has reached a channel since {since}. "
               f"{len(dark)} scheduled day(s) dark: {days}.")

    if repo and token:
        try:
            blocked = awaiting_approval(repo, token, session)
        except Exception as e:
            blocked = []
            print(f"[stale-channel] could not read waiting runs "
                  f"({type(e).__name__}: {e})")
        if blocked:
            run = blocked[0]
            message += (
                f"\nA run has been awaiting approval since "
                f"{(run.get('created_at') or '?')[:10]}: {run.get('html_url')}"
                f"\nThat run holds the `daily-post` concurrency group, so "
                f"every later run queues behind it and is cancelled unstarted "
                f"— which is why no review arrived on the days since. "
                f"Approve or reject it and the queue drains.")
        else:
            message += ("\nNo run is waiting for approval, so this is not the "
                        "blocked-queue case — check whether prepare or publish "
                        "is failing.")
    return True, message
