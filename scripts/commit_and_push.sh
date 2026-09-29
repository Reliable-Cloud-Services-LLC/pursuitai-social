#!/usr/bin/env bash
# Commit the given paths and land them on the branch, surviving a
# concurrent push.
#
#   commit_and_push.sh "<message>" <path> [<path>...]
#
# WHY THIS IS SHARED RATHER THAN WRITTEN TWICE
#
# The publish job learned this on 2026-07-30: a bare `git push` loses the
# commit whenever develop moved while the job was running, and the job
# deliberately dirties its own tree, so the retry cannot assume a clean
# one. That fix was applied to publish and NOT to prepare, which kept a
# bare `git push`.
#
# Prepare survived anyway, because in normal operation it is the only
# writer at that moment. That stopped being true on 2026-09-29: a run that
# had been parked at the approval gate since the 24th was approved, and its
# publish job pushed the post log at the same moment the newly-unblocked
# run's prepare pushed pending.json. Prepare lost the race and the day's
# post was lost with it.
#
# That is not a freak collision — it is the RECOVERY PATH from a blocked
# queue, so it recurs every time a stale run is approved. Two copies of
# this logic is what let one of them miss a lesson the other had already
# learned, so there is now one copy.
set -euo pipefail

MESSAGE="$1"
shift

git config user.name "pursuitai-bot"
git config user.email "bot@pursuitai.net"

# `-- "$@"` so a path is never mistaken for a flag, and `|| true` because a
# path that does not exist this run (prepare writes no log; publish may
# delete pending.json) is normal, not an error.
git add -A -- "$@" || true

if git diff --cached --quiet; then
  echo "nothing to commit"
  exit 0
fi
git commit -m "$MESSAGE"

branch="${GITHUB_REF_NAME:-$(git rev-parse --abbrev-ref HEAD)}"
for attempt in 1 2 3; do
  if git push; then
    echo "pushed on attempt $attempt"
    exit 0
  fi
  echo "push rejected — rebasing onto origin/$branch"
  # Guarded: `set -e` would otherwise abort the script the instant the
  # remote is unreachable, skipping every ::error:: below it and failing
  # SILENTLY — which is the one outcome this helper exists to prevent.
  git fetch origin "$branch" || {
    echo "::error::could not fetch origin/$branch to rebase '$MESSAGE'."
    echo "::error::${PUSH_FAILURE_HINT:-Fix the branch by hand.}"
    exit 1
  }
  # --autostash because the publish job syncs the bucket INTO the working
  # tree before this runs. Any tracked file that sync touches leaves the
  # tree dirty, and a plain rebase then aborts with "cannot rebase: You
  # have unstaged changes" — which is how the 2026-07-30 run lost its push.
  # The retry must not depend on a clean tree, because a caller may
  # deliberately have dirtied it.
  git rebase --autostash "origin/$branch" || {
    echo "::error::rebase onto origin/$branch failed; '$MESSAGE' could not"
    echo "::error::be saved. ${PUSH_FAILURE_HINT:-Fix the branch by hand.}"
    exit 1
  }
done
echo "::error::could not push '$MESSAGE' after 3 attempts."
echo "::error::${PUSH_FAILURE_HINT:-Fix the branch by hand.}"
exit 1
