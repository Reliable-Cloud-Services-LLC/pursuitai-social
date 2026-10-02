"""The member (Share on LinkedIn) posting chain.

Why this chain exists at all: posting as the PursuitAI *Page* needs
`w_organization_social`, which is only inside the vetted Community
Management API. LinkedIn's Open Permissions table — "the only permissions
that are available to all developers without special approval" — contains
exactly three entries, and the only write among them is `w_member_social`,
from the self-serve Share on LinkedIn product. Being a Page ADMINISTRATOR
does not substitute: the Posts API returns 403 unless the scope is granted
AND the member holds the page role.

So the self-serve route authors as a person. That is a product decision
recorded in docs/LINKEDIN_ACCESS.md, not an implementation detail.
"""
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import validate_linkedin as vl  # noqa: E402


def test_person_urn_is_built_from_the_subject_claim():
    assert vl.person_urn_from({"sub": "AbC123"}) == "urn:li:person:AbC123"


@pytest.mark.parametrize("payload", [{}, {"sub": ""}, {"name": "x"}, None])
def test_a_missing_subject_raises_rather_than_building_a_broken_urn(payload):
    """Negative control. Returning None would build "urn:li:person:None",
    which LinkedIn rejects three calls later as an opaque 400 — long after
    the actual cause (a token minted without the `profile` scope) has
    scrolled off."""
    with pytest.raises(ValueError) as e:
        vl.person_urn_from(payload)
    assert "profile" in str(e.value), "the error must name the missing scope"


def test_the_member_scope_is_the_self_serve_one():
    """w_organization_social is NOT self-serve. If this constant ever drifts
    to it, the validator would pass only for an app that already has the
    access we are routing around."""
    assert vl.MEMBER_SCOPE == "w_member_social"


def test_member_mode_is_reachable_from_the_command_line():
    """The entry-point class of bug this repo has already hit once: a
    function defined below the __main__ guard is a NameError the moment it
    is called. Running the real script is the only check that catches it."""
    out = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "validate_linkedin.py"),
         "--member"],
        capture_output=True, text=True,
        env={**os.environ, "LINKEDIN_ACCESS_TOKEN": ""})
    assert "LINKEDIN_ACCESS_TOKEN not set" in out.stdout, out.stdout + out.stderr
    assert "NameError" not in out.stderr


def test_member_mode_never_publishes():
    """The validator's contract across every mode: it proves the chain
    without putting anything on a feed. A member post has no unpublished
    equivalent (the Posts API accepts only lifecycleState PUBLISHED at
    creation), so the probe stops at the image upload — an uploaded image
    attached to no post appears nowhere."""
    source = open(os.path.join(ROOT, "scripts", "validate_linkedin.py")).read()
    member_half = source[source.index("def check_member("):]
    assert "post_image" not in member_half, \
        "the member validator must never call the publishing path"
