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
