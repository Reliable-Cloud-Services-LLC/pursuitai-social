"""Workflow env values are code, and nothing else checks them.

Two bugs reached a live run through this gap: a hardcoded R2 region that
S3 rejects, and a MEDIA_BASE_URL still pointing at raw.githubusercontent
in the publish job after W8 moved media to object storage. Both were
invisible to the test suite and to a YAML structure check — the file
parsed fine and the job graph was correct.
"""
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOWS = os.path.join(ROOT, ".github", "workflows")


def workflow_files():
    return [os.path.join(WORKFLOWS, f) for f in sorted(os.listdir(WORKFLOWS))
            if f.endswith((".yml", ".yaml"))]


def test_media_base_url_always_comes_from_a_secret():
    """W8 moved media to object storage. A job still pointing at the repo
    would hand Instagram a URL where nothing lives — and Instagram reports
    that as an unfetchable media error hours later, saying nothing useful."""
    offenders = []
    for path in workflow_files():
        for i, line in enumerate(open(path), 1):
            if "MEDIA_BASE_URL:" not in line:
                continue
            if "secrets.MEDIA_BASE_URL" not in line:
                offenders.append(f"{os.path.basename(path)}:{i} {line.strip()}")
    assert not offenders, "MEDIA_BASE_URL must come from the secret:\n" + \
        "\n".join(offenders)


def test_no_workflow_serves_media_from_the_repo():
    """Rendered media is not committed any more."""
    offenders = [os.path.basename(p) for p in workflow_files()
                 if "raw.githubusercontent" in open(p).read()]
    assert not offenders, f"workflows still reference the repo as a media host: {offenders}"


def test_aws_region_is_not_hardcoded_to_an_r2_ism():
    """'auto' is an R2 convention; S3 rejects it outright."""
    offenders = []
    for path in workflow_files():
        for i, line in enumerate(open(path), 1):
            if re.search(r"AWS_DEFAULT_REGION:\s*auto\s*$", line):
                offenders.append(f"{os.path.basename(path)}:{i}")
    assert not offenders, f"region hardcoded to 'auto': {offenders}"


@pytest.mark.parametrize("secret", [
    "X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET",
    "IG_USER_ID", "IG_ACCESS_TOKEN", "MEDIA_BASE_URL", "NOTIFY_WEBHOOK_URL",
])
def test_publish_job_receives_every_credential_it_needs(secret):
    daily = open(os.path.join(WORKFLOWS, "daily.yml")).read()
    publish = daily.split("Publish to X + Instagram", 1)[1].split("run:", 1)[0]
    assert f"secrets.{secret}" in publish, \
        f"publish step is missing {secret}"


# ---------- voice deps stay out of the test job ----------

def test_voice_deps_are_not_in_the_main_requirements():
    """torch + the Kokoro model are ~1 GB. The test job installs
    requirements.txt and never synthesizes audio, so pulling them there
    would undo the CI work that got a run to 84 seconds."""
    main = open(os.path.join(ROOT, "requirements.txt")).read().lower()
    for pkg in ("torch", "kokoro"):
        assert pkg not in main, f"{pkg} belongs in requirements-voice.txt"


def test_only_the_prepare_job_installs_voice():
    daily = open(os.path.join(WORKFLOWS, "daily.yml")).read()
    prepare, publish = daily.split("  publish:", 1)
    assert "requirements-voice.txt" in prepare
    assert "requirements-voice.txt" not in publish, \
        "publish re-renders nothing; it has no use for a TTS engine"
    for name in ("test.yml", "analytics.yml", "heartbeat.yml"):
        assert "requirements-voice" not in open(
            os.path.join(WORKFLOWS, name)).read(), name


def test_torch_comes_from_the_cpu_index():
    """The default index resolves the CUDA build — ~2.5 GB of wheels for a
    GPU no runner has."""
    daily = open(os.path.join(WORKFLOWS, "daily.yml")).read()
    assert "download.pytorch.org/whl/cpu" in daily


def test_the_voice_model_is_cached():
    daily = open(os.path.join(WORKFLOWS, "daily.yml")).read()
    assert "actions/cache" in daily and "huggingface" in daily


# The workflow is read as text, like everything else here — a YAML parser
# would be a dependency the runtime never needs, carried solely for tests.
# The format input is written in flow style on one line so this stays a
# simple match rather than a hand-rolled block parser.
_FORMAT_OPTIONS = re.compile(r"^\s*options:\s*\[(.+)\]\s*$", re.M)


def _daily_text():
    with open(os.path.join(ROOT, ".github", "workflows", "daily.yml")) as f:
        return f.read()


def test_dispatch_can_select_every_format():
    """A format the rotation can produce but a manual run cannot select is
    a format nobody can preview until it comes round on its own — which is
    up to four runs and four burned topics away."""
    import sys
    sys.path.insert(0, os.path.join(ROOT, "engine"))
    import run
    m = _FORMAT_OPTIONS.search(_daily_text())
    assert m, "format input is not a one-line flow-style options list"
    options = [o.strip().strip('"\'') for o in m.group(1).split(",")]
    for fmt in set(run.FORMATS):
        assert fmt in options, f"{fmt} is not dispatchable"


def test_dispatch_format_defaults_to_the_rotation():
    """A sticky override would silently freeze the format for every later
    run, including the scheduled ones."""
    text = _daily_text()
    block = text[text.index("      format:"):text.index("permissions:")]
    assert re.search(r'^\s*default:\s*""\s*$', block, re.M), block
    assert re.search(r"^\s*required:\s*false\s*$", block, re.M), block


def _calls_which_ffmpeg(node):
    import ast
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call) or not sub.args:
            continue
        func = sub.func
        target = func.attr if isinstance(func, ast.Attribute) else getattr(
            func, "id", None)
        arg = sub.args[0]
        if (target == "which" and isinstance(arg, ast.Constant)
                and arg.value == "ffmpeg"):
            return True
    return False


def test_every_ffmpeg_test_is_selected_by_the_quarantined_job():
    """ffmpeg lives only in the `video` job, which selects on `-k video`.

    A render test that skips without ffmpeg AND is not matched by `-k video`
    skips in the main job and is never collected by the video job — so it
    passes everywhere by never running. That is how the poster render test
    first landed, and a green suite said nothing about it.
    """
    import ast
    here = os.path.dirname(__file__)
    offenders = []
    for name in sorted(os.listdir(here)):
        if not name.startswith("test_") or not name.endswith(".py"):
            continue
        source = open(os.path.join(here, name)).read()
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.FunctionDef):
                continue
            if not node.name.startswith("test_"):
                continue
            # The skip idiom is the signal. A test that needs ffmpeg and
            # does NOT guard fails loudly in CI, which is fine; the silent
            # one is the test that skips itself out of both jobs.
            # Matched on the AST, not the text, so this check does not
            # match its own description of what it looks for.
            if _calls_which_ffmpeg(node) and "video" not in node.name:
                offenders.append(f"{name}::{node.name}")
    assert not offenders, (
        "these tests need ffmpeg but `-k video` will not select them, so "
        f"they never run in CI: {offenders}")


def test_a_failed_channel_can_be_retried_alone():
    """A single-channel outage — Meta rejecting while X succeeds — left no
    way to remediate: re-running posted to BOTH, duplicating on the healthy
    channel. run.py already had --skip-x/--skip-ig; only the dispatch input
    was missing. (Live case: 2026-07-30, IG 400 from a blocked developer
    account while X posted fine.)"""
    text = _daily_text()
    block = text[text.index("      skip:"):text.index("      topic:")]
    for ch in ("x", "ig"):
        assert f'"{ch}"' in block, f"cannot skip {ch}"
    assert 'default: ""' in block, "skipping must not be the default"

    publish = text[text.index("--publish"):]
    assert "--skip-x" in text and "--skip-ig" in text, (
        "the input is not wired to the publish step")


def test_skip_is_wired_to_publish_not_prepare():
    """Skipping is a PUBLISH-time decision. Wiring it to prepare would
    change what gets rendered and reviewed, so the human would approve
    something other than what ships."""
    text = _daily_text()
    prepare_half = text[:text.index("  publish:")]
    assert "--skip-" not in prepare_half, (
        "skip leaked into the prepare job")


def test_the_dispatchable_formats_match_the_rotation():
    """Dropping a format from FORMATS while leaving it dispatchable would
    let a manual run produce something the rotation can no longer make —
    including, in the `video` case, a renderer with no audio path."""
    import sys
    sys.path.insert(0, os.path.join(ROOT, "engine"))
    import run
    m = _FORMAT_OPTIONS.search(_daily_text())
    options = {o.strip().strip('"\'') for o in m.group(1).split(",")}
    options.discard("")
    assert options == set(run.FORMATS), (
        f"dispatch offers {options}, rotation makes {set(run.FORMATS)}")


# Only real schedule entries — the prose in these files says "cron" a lot.
_CRON = re.compile(r'^\s*-\s*cron:\s*"([^"]+)"', re.M)


def crons(path):
    """(minute, hour) for every schedule entry in one workflow."""
    out = []
    for expr in _CRON.findall(open(path).read()):
        minute, hour = expr.split()[:2]
        assert minute.isdigit() and hour.isdigit(), (
            f"{os.path.basename(path)}: {expr!r} — a wildcard or step minute "
            f"lands on :00 by definition, which is the slot to avoid")
        out.append((int(minute), int(hour)))
    return out


@pytest.mark.parametrize("path", workflow_files(),
                         ids=lambda p: os.path.basename(p))
def test_every_cron_is_off_the_busy_slots(path):
    """GitHub: "High load times include the start of every hour" and the
    mitigation is "schedule your workflow to run at a different time of the
    hour". :00 and :30 are the two busiest. The 2026-07-30 post run queued
    118 minutes late on :30.

    This checked daily.yml alone, which is how heartbeat.yml and
    analytics.yml both stayed on :00 across two rounds of fixing exactly
    this problem. heartbeat.yml did not fire on 2026-08-03, its first
    scheduled Monday.
    """
    for minute, hour in crons(path):
        assert minute not in (0, 30), (
            f"{os.path.basename(path)} sits on the busy :{minute:02d} slot "
            f"({minute} {hour} …)")


def test_the_commit_step_cannot_lose_the_post_log_silently():
    """`git pull --rebase || true` swallowed a real failure: the media sync
    leaves assets/ dirty so rebase refuses, and if develop had also moved
    the push was rejected and the log vanished — which re-serves the topic
    later as a duplicate. It survived 2026-07-30 only because develop had
    not moved that hour."""
    text = _daily_text()
    # everything from the step name to EOF — it is the last step in the job
    step = text[text.index("Commit state + post log"):]
    assert "git pull --rebase || true" not in step, (
        "the swallowed-failure form is back")
    assert "::error::" in step, "a lost post log must fail loudly"
    assert "exit 1" in step, "a lost post log must fail the step"
