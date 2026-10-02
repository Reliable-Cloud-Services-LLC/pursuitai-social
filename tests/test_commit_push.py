"""The shared commit-and-push helper.

Three jobs write to develop — prepare (pending.json), publish (post log +
rotation state) and analytics (metrics). Each had its own copy of the push
logic, at three different levels of robustness:

  publish    rebase + retry + loud failure   (learned 2026-07-30)
  prepare    bare `git push`                 (lost the post on 2026-09-29)
  analytics  `git pull --rebase || true`     (the form publish replaced)

Prepare's bare push survived for two months because in normal operation it
is the only writer at that instant. It stopped being the only writer the
moment a queue blocked since 2026-09-24 was unblocked: the approved run's
publish pushed the post log while the newly-released run's prepare pushed
pending.json. That is the RECOVERY PATH from a blocked queue, so it recurs
every time a stale run is approved.

These tests drive the real script against real git repositories, rather
than grepping the workflow for reassuring words. A source scan would pass
against a script that does not work.
"""
import os
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "commit_and_push.sh")
WORKFLOWS = os.path.join(ROOT, ".github", "workflows")


def git(cwd, *args, check=True):
    return subprocess.run(["git", *args], cwd=cwd, check=check,
                          capture_output=True, text=True)


def run_script(cwd, message, *paths, env=None):
    e = {**os.environ, "GITHUB_REF_NAME": "main", **(env or {})}
    return subprocess.run(["bash", SCRIPT, message, *paths],
                          cwd=cwd, capture_output=True, text=True, env=e)


@pytest.fixture
def repos(tmp_path):
    """A bare remote and two clones — the two concurrent jobs."""
    remote = tmp_path / "remote.git"
    git(str(tmp_path), "init", "--bare", "-b", "main", str(remote))
    clones = []
    for name in ("a", "b"):
        path = tmp_path / name
        git(str(tmp_path), "clone", str(remote), str(path))
        git(str(path), "config", "user.email", "t@t")
        git(str(path), "config", "user.name", "t")
        clones.append(str(path))
    seed = clones[0]
    os.makedirs(os.path.join(seed, "content"), exist_ok=True)
    open(os.path.join(seed, "content", "pending.json"), "w").write("{}")
    git(seed, "add", "-A")
    git(seed, "commit", "-m", "seed")
    git(seed, "push", "-u", "origin", "main")
    git(clones[1], "pull")
    return clones


def test_a_plain_commit_lands(repos):
    a, _ = repos
    open(os.path.join(a, "content", "pending.json"), "w").write('{"x":1}')
    r = run_script(a, "prepare", "content/pending.json")
    assert r.returncode == 0, r.stderr
    assert "pushed on attempt 1" in r.stdout


def test_a_commit_survives_a_concurrent_push(repos):
    """THE regression. Another job pushed between our checkout and our push
    — the exact shape of the 2026-09-29 failure."""
    a, b = repos
    open(os.path.join(b, "other.txt"), "w").write("from the other job")
    git(b, "add", "-A")
    git(b, "commit", "-m", "post log")
    git(b, "push")

    open(os.path.join(a, "content", "pending.json"), "w").write('{"x":1}')
    r = run_script(a, "prepare 2026-09-29", "content/pending.json")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "rebasing onto" in r.stdout

    git(a, "fetch", "origin")
    log = git(a, "log", "--oneline", "origin/main").stdout
    assert "prepare 2026-09-29" in log, "our commit was lost"
    assert "post log" in log, "we clobbered the other job's commit"


def test_a_dirty_tree_does_not_block_the_rebase(repos):
    """The publish job syncs the bucket into its own working tree before
    committing, so the retry must not require a clean one. A plain rebase
    aborts with 'cannot rebase: You have unstaged changes' — which is how
    the 2026-07-30 run lost its push."""
    a, b = repos
    open(os.path.join(b, "other.txt"), "w").write("concurrent")
    git(b, "add", "-A")
    git(b, "commit", "-m", "concurrent")
    git(b, "push")

    # a tracked file left modified and deliberately NOT part of the commit
    tracked = os.path.join(a, "content", "pending.json")
    open(tracked, "w").write('{"committed": true}')
    git(a, "add", "content/pending.json")
    git(a, "commit", "-m", "staged separately")
    open(os.path.join(a, "dirty.txt"), "w").write("untracked churn")
    open(tracked, "w").write('{"dirtied": "after"}')

    os.makedirs(os.path.join(a, "logs"), exist_ok=True)
    open(os.path.join(a, "logs", "metrics.jsonl"), "w").write("{}\n")
    r = run_script(a, "post log", "content", "logs")
    assert r.returncode == 0, r.stdout + r.stderr


def test_nothing_to_commit_is_a_success_not_a_failure(repos):
    """prepare legitimately has nothing to add when the post is unchanged.
    Exiting non-zero there would fail a healthy run."""
    a, _ = repos
    r = run_script(a, "prepare", "content/pending.json")
    assert r.returncode == 0
    assert "nothing to commit" in r.stdout


def test_a_missing_path_is_not_a_failure(repos):
    """publish deletes pending.json and analytics writes only metrics, so a
    named path that does not exist this run is normal."""
    a, _ = repos
    open(os.path.join(a, "content", "pending.json"), "w").write('{"x":2}')
    r = run_script(a, "post log", "content", "logs")
    assert r.returncode == 0, r.stdout + r.stderr


def test_an_unlandable_push_fails_loudly(repos):
    """Negative control on the whole point: silence is what cost the
    2026-07-30 post log. A push that cannot land must fail the step."""
    a, _ = repos
    open(os.path.join(a, "content", "pending.json"), "w").write('{"x":3}')
    git(a, "remote", "set-url", "origin", str(tmp := "/nonexistent/remote.git"))
    r = run_script(a, "prepare", "content/pending.json",
                   env={"PUSH_FAILURE_HINT": "the day is lost"})
    assert r.returncode != 0
    assert "::error::" in r.stdout
    assert "the day is lost" in r.stdout
    del tmp


# ---------- every writer uses it ----------

def workflow_text():
    out = {}
    for f in sorted(os.listdir(WORKFLOWS)):
        if f.endswith((".yml", ".yaml")):
            out[f] = open(os.path.join(WORKFLOWS, f)).read()
    return out


def test_no_workflow_pushes_without_the_helper():
    """The invariant that failed: one writer missing the lesson another had
    already learned. `git push` appearing anywhere but the helper means a
    fourth copy of this logic has started drifting."""
    offenders = [f for f, text in workflow_text().items()
                 if any(line.strip().startswith("git push")
                        for line in text.splitlines())]
    assert not offenders, \
        f"{offenders} push directly — use scripts/commit_and_push.sh"


def test_the_swallowing_form_is_not_reintroduced():
    for f, text in workflow_text().items():
        # Comment lines only DESCRIBE the old form — the explanation of why
        # it was removed necessarily quotes it.
        live = "\n".join(l for l in text.splitlines()
                          if not l.strip().startswith("#"))
        assert "git pull --rebase || true" not in live, \
            f"{f}: the swallowed-failure form is back"
    script = open(SCRIPT).read()
    for swallow in ("git push || true", "git push || echo",
                    "git commit -m \"$MESSAGE\" || "):
        assert swallow not in script, f"{swallow!r} swallows a real failure"


@pytest.mark.parametrize("workflow,message", [
    ("daily.yml", "prepare"),
    ("daily.yml", "post log"),
    ("analytics.yml", "metrics"),
])
def test_each_writer_routes_through_the_helper(workflow, message):
    text = workflow_text()[workflow]
    assert f'commit_and_push.sh "{message}' in text


def test_every_caller_supplies_a_failure_hint():
    """The generic error says a push failed; the hint says what it COST.
    'the rotation will duplicate this topic' is what makes the operator act
    tonight rather than tomorrow."""
    for f, text in workflow_text().items():
        calls = sum(1 for line in text.splitlines()
                    if line.strip().startswith("run:")
                    and "commit_and_push.sh" in line)
        assert text.count("PUSH_FAILURE_HINT") == calls, \
            f"{f}: {calls} call(s) but not all have a PUSH_FAILURE_HINT"


def test_the_helper_is_executable():
    assert os.access(SCRIPT, os.X_OK), "workflows invoke it directly"
