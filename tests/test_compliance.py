"""W5 — L1 source grounding and the compliance reviewer.

Two independent guards:

  * Verification status on the topic. A claim that has not been traced to
    an implementation or an authoritative external source does not publish.
  * check_claims() on the rendered caption, enforcing the content rules —
    no implied federal endorsement, no win guarantees, no agency seals or
    logos, no customer names, no unsourced numerics.

Both block. Neither warns-and-continues: the audience is a small,
reputation-dense professional market where one wrong factual claim is not
recoverable.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import compliance  # noqa: E402


@pytest.fixture(scope="module")
def cal():
    with open(os.path.join(ROOT, "content", "calendar.json")) as f:
        return json.load(f)


def verified_topic(**over):
    base = {
        "id": "t", "feature": "F", "headline": "H", "body": "B",
        "hook_x": "x", "hook_ig": "ig", "stat": "s", "media": "card",
        "verification": {"status": "VERIFIED", "source_type": "code",
                         "source_ref": "app/x.py:1-2",
                         "verified_on": "2026-07-28"},
    }
    base.update(over)
    return base


def check(caption, topic=None):
    return compliance.check_claims(topic or verified_topic(), caption)


# ---------- implied federal endorsement ----------

@pytest.mark.parametrize("caption", [
    "PursuitAI is endorsed by the SBA.",
    "An official GSA partner for capture management.",
    "Government-approved capture software.",
    "In partnership with the Department of Homeland Security.",
    "SBA-approved bid scoring.",
])
def test_blocks_implied_federal_endorsement(caption):
    violations = check(caption)
    assert violations, f"should have blocked: {caption}"
    assert any(v.rule == "federal_endorsement" for v in violations)


@pytest.mark.parametrize("caption", [
    "Scores every SAM.gov opportunity across 5 dimensions.",
    "Pulls forecasts from DHS APFS and 20 more federal agencies.",
    "Checks your plan against FAR 52.219-14.",
])
def test_allows_naming_agencies_as_data_sources(caption):
    """Naming an agency is not claiming its endorsement — the whole
    product is built on federal data and must be able to say so."""
    assert not [v for v in check(caption) if v.rule == "federal_endorsement"]


# ---------- win guarantees ----------

@pytest.mark.parametrize("caption", [
    "Guaranteed to win more contracts.",
    "We guarantee your next award.",
    "You will win more bids with PursuitAI.",
    "Ensures you win the recompete.",
])
def test_blocks_win_guarantees(caption):
    assert any(v.rule == "win_guarantee" for v in check(caption))


@pytest.mark.parametrize("caption", [
    "Win more. Guess less.",
    "Know which agencies you can actually win.",
    "Helps you decide which bids are worth pursuing.",
])
def test_allows_aspirational_language_without_a_promise(caption):
    assert not [v for v in check(caption) if v.rule == "win_guarantee"]


# ---------- seals and logos ----------

@pytest.mark.parametrize("caption", [
    "Featuring the official DoD seal.",
    "Carries the GSA logo.",
    "The SBA emblem appears on every report.",
])
def test_blocks_agency_seal_or_logo_references(caption):
    assert any(v.rule == "agency_seal" for v in check(caption))


# ---------- customer names ----------

def test_blocks_configured_customer_names(monkeypatch):
    monkeypatch.setattr(compliance, "CUSTOMER_NAMES", ("Acme Federal",))
    assert any(v.rule == "customer_name"
               for v in check("Acme Federal doubled their win rate."))


def test_blocks_testimonial_shaped_company_references():
    v = check("Vector Dynamics LLC increased their pipeline 3x with PursuitAI.")
    assert any(x.rule == "customer_name" for x in v)


def test_allows_naming_competitors_generically():
    """Naming a competitor is not naming a customer."""
    assert not [v for v in check("The capability firms used to pay GovWin for.")
                if v.rule == "customer_name"]


# ---------- unverifiable competitive claims ----------

@pytest.mark.parametrize("caption", [
    "No competitor has this.",
    "No other platform does this.",
    "No competitor models inherited JV eligibility.",
    "The only built-in FAR 52.219-14 check.",
    "Nobody else offers this.",
    "Everyone else's tools only search outward.",
])
def test_blocks_unverifiable_competitive_claims(caption):
    """These cannot be checked against our code or any external source. One
    informed reader with a counterexample discredits the whole account."""
    assert any(v.rule == "competitive_claim" for v in check(caption))


@pytest.mark.parametrize("caption", [
    "Ranks the primes who actually subcontract in your NAICS.",
    "The capability firms used to pay a fortune for.",
    "Built for firms that get security-reviewed.",
])
def test_allows_positioning_without_a_superlative(caption):
    assert not [v for v in check(caption) if v.rule == "competitive_claim"]


# ---------- unsourced numerics ----------

def test_blocks_numerics_on_an_unverified_topic():
    topic = verified_topic(verification={"status": "UNVERIFIED"})
    v = check("Search 9,000+ vehicle holders across 14 GWACs.", topic)
    assert any(x.rule == "unsourced_numeric" for x in v)


def test_allows_numerics_on_a_verified_topic():
    v = check("Scores every opportunity across 5 dimensions.", verified_topic())
    assert not [x for x in v if x.rule == "unsourced_numeric"]


def test_regulatory_citations_are_not_treated_as_numerics():
    v = check("Checks FAR 52.219-14 across every option year.", verified_topic())
    assert not [x for x in v if x.rule == "unsourced_numeric"]


def test_would_have_caught_the_stale_sole_source_thresholds():
    """The real defect this rule exists for: dollar figures published from
    an UNVERIFIED topic. FAR 19.805-1 moved to $5.5M/$8.5M while the copy
    and the product both still said $4.5M/$7.0M."""
    topic = verified_topic(verification={"status": "UNVERIFIED"})
    v = check("Built-in FAR 19.805-1 threshold check "
              "($4.5M services / $7.0M manufacturing).", topic)
    assert any(x.rule == "unsourced_numeric" for x in v)


# ---------- publishability ----------

@pytest.mark.parametrize("status,expected", [
    ("VERIFIED", True),
    ("UNVERIFIED", False),
    ("MISMATCH", False),
    ("UNVERIFIABLE", False),
])
def test_only_verified_topics_are_publishable(status, expected):
    topic = verified_topic(verification={"status": status})
    assert compliance.is_publishable(topic) is expected


def test_topic_without_verification_is_not_publishable():
    assert compliance.is_publishable({"id": "t"}) is False


def test_mismatch_topics_are_never_publishable(cal):
    """A MISMATCH is a claim we know to be false. It must never ship."""
    for topic in cal["topics"]:
        if topic["verification"]["status"] == "MISMATCH":
            assert not compliance.is_publishable(topic)


# ---------- violations block, they do not warn ----------

def test_violation_has_a_readable_message():
    v = check("Guaranteed to win.")[0]
    assert v.rule and v.message and v.excerpt


def test_clean_caption_produces_no_violations(cal):
    topic = cal["topics"][0]
    assert compliance.check_claims(topic, "A clean sentence with no claims.") == []


# ---------- the card's own text ----------

def test_agency_approved_user_artifacts_are_allowed():
    """'your SBA-approved MPA' is accurate GovCon language — the agreement
    really was approved by SBA. Only a claim that WE are agency-approved is
    the violation."""
    for ok in ("Record your SBA-approved MPA once.",
               "Your SBA-approved joint venture can bid.",
               "an SBA-approved mentor-protégé agreement"):
        assert not [v for v in check(ok) if v.rule == "federal_endorsement"], ok


def test_agency_approving_us_is_still_blocked():
    for bad in ("SBA-approved bid scoring.", "GSA-certified capture software.",
                "Our platform is DHS-authorized."):
        assert any(v.rule == "federal_endorsement" for v in check(bad)), bad


def test_check_rendered_reads_the_card_fields():
    topic = verified_topic(body="No competitor has this.")
    rules = [v.rule for v in compliance.check_rendered(topic)]
    assert "competitive_claim" in rules


def test_check_rendered_names_the_offending_field():
    topic = verified_topic(headline="Guaranteed to win.")
    assert "headline:" in compliance.check_rendered(topic)[0].message


def test_no_publishable_topic_renders_a_violating_card(cal):
    """The gate saw captions only for the whole project. A claim in `body`
    is drawn onto every card and was never checked."""
    for t in cal["topics"]:
        if not compliance.is_publishable(t):
            continue
        violations = compliance.check_rendered(t)
        assert not violations, (
            f"{t['id']} card renders: {[str(v) for v in violations]}")
