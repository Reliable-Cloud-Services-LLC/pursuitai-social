"""W5 — the topic verification schema.

Every claim-bearing topic must carry evidence for its status. The point is
not that everything is VERIFIED today — most of it is not — but that no
topic can sit in the calendar with an unrecorded provenance, and that
nothing but VERIFIED reaches an audience.
"""
import datetime
import json
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import compliance  # noqa: E402

VALID_STATUSES = {"VERIFIED", "UNVERIFIED", "MISMATCH", "UNVERIFIABLE"}
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@pytest.fixture(scope="module")
def cal():
    with open(os.path.join(ROOT, "content", "calendar.json")) as f:
        return json.load(f)


def test_every_topic_carries_a_verification_block(cal):
    for t in cal["topics"]:
        assert "verification" in t, f"{t['id']} has no verification block"


def test_status_is_from_the_enum(cal):
    for t in cal["topics"]:
        status = t["verification"].get("status")
        assert status in VALID_STATUSES, f"{t['id']} has status {status!r}"


def test_verified_topics_carry_evidence_and_a_date(cal):
    """A VERIFIED claim must point at something checkable."""
    for t in cal["topics"]:
        v = t["verification"]
        if v["status"] != "VERIFIED":
            continue
        assert v.get("source_type") in ("code", "external"), t["id"]
        if v["source_type"] == "code":
            assert v.get("source_ref"), f"{t['id']} missing source_ref"
            assert ":" in v["source_ref"], (
                f"{t['id']} source_ref must be <path>:<line range>")
        else:
            assert v.get("source_url", "").startswith("https://"), t["id"]
        assert ISO_DATE.match(v.get("verified_on", "")), t["id"]


def test_code_evidence_points_at_implementation_not_prose(cal):
    """A docstring, README, marketing page, or SEO article is not evidence —
    much of that prose was written from the same copy being verified, so
    citing it is circular."""
    banned = ("README", "CLAUDE.md", "platform_map", "docs/", "landing",
              "why-pursuitai", "resources/", ".md:")
    for t in cal["topics"]:
        v = t["verification"]
        if v.get("source_type") != "code":
            continue
        ref = v.get("source_ref", "")
        for bad in banned:
            assert bad not in ref, f"{t['id']} cites prose, not code: {ref}"


def test_external_evidence_uses_an_authoritative_source(cal):
    allowed = ("acquisition.gov", "sba.gov", "ecfr.gov", "federalregister.gov",
               "gao.gov", "sam.gov", "usaspending.gov", "fpds.gov",
               "govinfo.gov", "developers.facebook.com", "docs.x.com")
    for t in cal["topics"]:
        v = t["verification"]
        if v.get("source_type") != "external":
            continue
        url = v.get("source_url", "")
        assert any(d in url for d in allowed), (
            f"{t['id']} cites a non-authoritative source: {url}")


def test_non_verified_topics_explain_themselves(cal):
    """MISMATCH and UNVERIFIABLE must say why, or the status is useless to
    whoever picks this up next."""
    for t in cal["topics"]:
        v = t["verification"]
        if v["status"] in ("MISMATCH", "UNVERIFIABLE"):
            assert v.get("note"), f"{t['id']} is {v['status']} with no note"


def test_verified_on_is_not_in_the_future(cal):
    today = datetime.date.today()
    for t in cal["topics"]:
        stamp = t["verification"].get("verified_on")
        if stamp:
            assert datetime.date.fromisoformat(stamp) <= today, t["id"]


def test_competitor_pricing_figures_are_gone(cal):
    """Deltek does not publish list pricing, so no figure we print about a
    named competitor can be sourced."""
    blob = json.dumps(cal)
    for figure in ("$20K", "$20,000", "$30K", "$30,000"):
        assert figure not in blob, f"competitor pricing figure {figure} present"


def test_at_least_one_topic_is_publishable(cal):
    """If this fails the engine has nothing to post and the rotation stalls."""
    publishable = [t for t in cal["topics"] if compliance.is_publishable(t)]
    assert publishable, "no VERIFIED topics — the calendar cannot publish"


# VERIFIED topics whose captions still trip a caption rule, so they cannot
# publish yet. Each is one sentence away: an unverifiable competitive
# superlative ("No competitor has this") that needs an approved copy edit,
# not a code change. This list may only SHRINK — a new entry means we
# introduced a claim we cannot stand behind.
CAPTION_BLOCKED_PENDING_COPY_EDIT = {
    "fifty-percent-rule",   # "No competitor has this."
    "8a-copilot",           # "No other platform does this."
    "mpjv",                 # "No competitor models inherited JV eligibility."
    "pricing-plans",        # "the only built-in 50% rule check"
}


def caption_violations(cal, topic):
    import captions
    return sorted({v.rule
                   for caption in (captions.build_x(topic, cal["brand"],
                                                    fmt="card", fresh=False),
                                   captions.build_ig(topic, cal["brand"],
                                                     fresh=False))
                   for v in compliance.check_claims(topic, caption)})


def test_no_new_verified_topic_is_caption_blocked(cal):
    """A VERIFIED topic whose caption always violates is a deadlock:
    prepare() would pick it and publish() would refuse, forever."""
    for t in cal["topics"]:
        if not compliance.is_publishable(t):
            continue
        violations = caption_violations(cal, t)
        if t["id"] in CAPTION_BLOCKED_PENDING_COPY_EDIT:
            assert violations, (
                f"{t['id']} is no longer blocked — remove it from "
                f"CAPTION_BLOCKED_PENDING_COPY_EDIT")
        else:
            assert not violations, (
                f"{t['id']} is publishable but violates: {violations}")


def test_the_engine_has_something_clean_to_post(cal):
    clean = [t["id"] for t in cal["topics"]
             if compliance.is_publishable(t) and not caption_violations(cal, t)]
    assert clean, "nothing can publish — the rotation would stall"
