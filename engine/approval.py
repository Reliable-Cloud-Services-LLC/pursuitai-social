"""The human approval gate — content integrity half.

There are two gates on a publish and they defend different things:

  * GitHub's required-reviewer environment is the SECURITY boundary. A human
    named in repo settings must click approve before the publish job runs.
    Enforced by GitHub, so no env var, timeout, or code path here can
    bypass it.
  * This module is the CONTENT-INTEGRITY check. It answers a narrower
    question: is the post about to go out byte-for-byte the one that was
    reviewed?

That second question is not academic. The cron prepares daily. If Monday's
post is never approved, Tuesday's run overwrites pending.json — and a click
on Monday's Slack notification would then publish Tuesday's content, which
nobody read.

Closing that needs the hash to come from OUTSIDE the publish job. As first
wired it did not: daily.yml ran --approve and --publish as consecutive steps
of the same job, so write_approval hashed pending.json and verify_approval
re-hashed the same file seconds later. It could not fail, and it could not
see a swap that happened in the hours before the reviewer clicked. The
prepare job now passes forward the hash of what it actually sent to Slack,
and --approve refuses anything else.

Approvals expire after 24 hours, and are never granted automatically.
"""
import datetime
import hashlib
import json
import os

TTL_HOURS = 24

# A timestamp slightly ahead of us is ordinary clock skew between the
# machine that approved and the one publishing. Hours ahead is not, and
# would otherwise mint an approval that never expires.
FUTURE_TOLERANCE = datetime.timedelta(minutes=5)


def compute_hash(pending_path):
    """SHA-256 of pending.json's exact bytes.

    Deliberately the raw bytes rather than a re-serialised object: any
    change at all — a caption, a media path, the date — invalidates the
    approval, which is the property we want.
    """
    with open(pending_path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class ApprovalMismatch(RuntimeError):
    """The content on disk is not the content that was reviewed."""


def write_approval(pending_path, approved_path, actor=None, now=None,
                   expect=None):
    """Record an approval for the current pending.json.

    `expect` is the hash taken when the post was SENT for review. Without
    it this function blesses whatever pending.json happens to say, which is
    not the same question as "did a human approve this content" — see the
    module docstring. The verify side then compares approval-time to
    publish-time, a gap of seconds in the same job, so it cannot catch a
    swap that happened during the hours before the click.
    """
    if not os.path.exists(pending_path):
        raise FileNotFoundError("nothing to approve — pending.json is missing")
    if expect and compute_hash(pending_path) != expect:
        raise ApprovalMismatch(
            "pending.json is not what was sent for review — approving it "
            "would publish content nobody read. Expected sha256 "
            f"{expect[:12]}…, found {compute_hash(pending_path)[:12]}…. "
            "Re-run --prepare and review the new post.")
    now = now or datetime.datetime.now(datetime.timezone.utc)
    with open(pending_path) as f:
        pending = json.load(f)
    record = {
        "pending_sha256": compute_hash(pending_path),
        "approved_at": now.isoformat(),
        "approved_by": actor or os.environ.get("GITHUB_ACTOR") or "local",
        # Redundant with the hash, but makes the audit trail readable
        # without cross-referencing pending.json.
        "topic": pending.get("topic"),
        "format": pending.get("format"),
    }
    os.makedirs(os.path.dirname(approved_path), exist_ok=True)
    with open(approved_path, "w") as f:
        json.dump(record, f, indent=2)
    return record


def verify_approval(pending_path, approved_path, now=None):
    """Return (ok, reason). `reason` is written for a human reading a log."""
    if not os.path.exists(pending_path):
        return False, "no pending.json — run --prepare first"
    if not os.path.exists(approved_path):
        return False, ("not approved — content/approved.json is missing. "
                       "Review the post, then run --approve.")
    try:
        with open(approved_path) as f:
            record = json.load(f)
        stamp = datetime.datetime.fromisoformat(record["approved_at"])
    except (ValueError, KeyError, TypeError) as e:
        return False, f"approved.json is unreadable ({type(e).__name__}) — re-approve"

    if stamp.tzinfo is None:
        return False, ("approved.json has no timezone, so its age is "
                       "ambiguous — re-approve")

    if record.get("pending_sha256") != compute_hash(pending_path):
        return False, ("pending.json changed after it was approved — the post "
                       "about to go out is NOT the one that was reviewed. "
                       "Re-approve.")

    now = now or datetime.datetime.now(datetime.timezone.utc)
    age = now - stamp
    if age < -FUTURE_TOLERANCE:
        return False, "approved.json is dated in the future — refusing"
    if age > datetime.timedelta(hours=TTL_HOURS):
        hours = age.total_seconds() / 3600
        return False, (f"approval expired ({hours:.1f}h old, limit "
                       f"{TTL_HOURS}h) — re-approve")
    return True, "approved"


def clear_approval(approved_path):
    """Spend the approval so it cannot authorise a later post."""
    if os.path.exists(approved_path):
        os.remove(approved_path)
