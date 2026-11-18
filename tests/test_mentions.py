"""Company and executive mentions on award posts.

A company mention is a real little-text element, and only when exactly one
LinkedIn organization has a website domain equal to the SAM website.
People are suggested in the approval queue and mentioned only after a
person URN is confirmed. Anything else stays plain text, and a tagging
failure retries the post without mentions.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import awards  # noqa: E402
import captions  # noqa: E402
import mentions  # noqa: E402
import notify  # noqa: E402
import post_linkedin  # noqa: E402

UEI = "AKUM3NINC001"
ORG = "urn:li:organization:8675309"
STAMP = "2026-11-18T15:00:00+00:00"


@pytest.fixture(autouse=True)
def no_live_lookups(monkeypatch):
    monkeypatch.delenv("SAM_API_KEY", raising=False)
    monkeypatch.delenv("LINKEDIN_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("LINKEDIN_ORG_ID", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def org(org_id=8675309, name="Akumen, Inc.", website="https://www.akumen.com",
        vanity="akumen"):
    return {
        "id": org_id,
        "organization_urn": f"urn:li:organization:{org_id}",
        "localized_name": name,
        "vanity_name": vanity,
        "linkedin_website": website,
    }


def row(**over):
    base = {
        "is_new_award": True,
        "id": "page-akumen",
        "slug": "akumen-navy",
        "url": "https://pursuitai.net/awards/akumen-navy",
        "recipient_name": "Akumen, Inc.",
        "recipient_uei": UEI,
        "recipient_website": "https://www.akumen.com",
        "awarding_agency": "Department of the Navy",
        "piid": "N0002426F6230",
        "parent_award_piid": "47QTCA24D006J",
        "contract_vehicle": "GSA MAS",
        "is_task_order": True,
        "obligation": 915_980,
        "ceiling": 2_778_270,
        "action_date": "2026-07-27",
        "first_seen_at": "2026-11-13T15:04:00Z",
        "first_published_at": "2026-11-13T15:04:00Z",
        "naics_code": "541519",
        "naics_description": "Other Computer Related Services",
        "summary": "EPS support for Navy systems.",
        "generated_unique_award_id": "",
    }
    base.update(over)
    return base


def brand():
    with open(os.path.join(ROOT, "content", "calendar.json")) as handle:
        return json.load(handle)["brand"]


def lookup_one(vanity):
    if vanity == "akumen":
        return [org()]
    return []


LEADERSHIP = """
<html><body>
  <a href="https://www.linkedin.com/company/akumen">LinkedIn</a>
  <a href="https://www.linkedin.com/in/carl-garcia">Carl Garcia</a>
  <p>Chief Executive Officer</p>
  <h3>Kameron Turner</h3>
  <p>Vice President, Business Development</p>
</body></html>
"""


def test_mention_element_matches_the_posts_api_examples():
    """Posts API: @[Devtestco](urn:li:organization:2414183) and a person URN."""
    assert captions.mention_element(
        "Devtestco", "urn:li:organization:2414183"
    ) == "@[Devtestco](urn:li:organization:2414183)"
    assert captions.mention_element(
        "Eddy", "urn:li:person:1234"
    ) == "@[Eddy](urn:li:person:1234)"
    assert captions.mention_element("Foo]Bar", "urn:li:organization:1") == ""
    assert captions.mention_element("Akumen", "urn:li:company:1") == ""
    assert captions.mention_element("", "urn:li:organization:1") == ""


def test_escape_keeps_mention_syntax_and_escapes_the_rest():
    raw = (
        "@[Akumen, Inc.](urn:li:organization:8675309) | "
        "@[Foo (Bar)](urn:li:person:ab_cd)\n\n"
        "See (the award).\n"
        "https://pursuitai.net/awards/x?utm_source=linkedin\n\n"
        "#GovCon"
    )
    escaped = post_linkedin.escape_commentary_with_mentions(raw)
    assert "@[Akumen, Inc.](urn:li:organization:8675309)" in escaped
    assert r"@[Foo \(Bar\)](urn:li:person:ab\_cd)" in escaped
    assert r"\|" in escaped
    assert r"\(the award\)" in escaped
    assert r"utm\_source=linkedin" in escaped
    assert "#GovCon" in escaped
    # The mention markers themselves are not escaped, or LinkedIn will
    # not treat them as mentions.
    assert r"\@[" not in escaped
    assert r"\](urn" not in escaped


def test_post_image_sends_the_mention_unescaped(monkeypatch):
    sent = {}
    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", "t")
    monkeypatch.setattr(post_linkedin, "author_urn",
                        lambda tok: "urn:li:organization:1")
    monkeypatch.setattr(post_linkedin, "upload_image",
                        lambda p, t, a: "urn:li:image:1")

    class _Resp:
        ok = True
        status_code = 201
        headers = {"x-restli-id": "urn:li:share:1"}

        def json(self):
            return {}

    def _fake_post(url, headers=None, data=None, timeout=None):
        sent["payload"] = json.loads(data)
        return _Resp()

    monkeypatch.setattr(post_linkedin.requests, "post", _fake_post)
    commentary = (
        "@[Devtestco](urn:li:organization:2414183)\n\n"
        "See (the note).\nhttps://pursuitai.net/a?utm_source=linkedin"
    )
    post_linkedin.post_image("x.png", commentary)
    body = sent["payload"]["commentary"]
    assert "@[Devtestco](urn:li:organization:2414183)" in body
    assert r"\(the note\)" in body
    assert r"utm\_source=linkedin" in body


def test_vanity_candidates_drop_the_legal_suffix_and_generic_tokens():
    names = mentions.vanity_candidates("Akumen, Inc.")
    assert "akumen" in names
    assert "inc" not in names
    assert "akumen-inc" in names
    generic = mentions.vanity_candidates("Federal Solutions LLC")
    assert "solutions" not in generic
    assert "federal" not in generic
    assert "federal-solutions" in generic


def test_domains_match_www_and_a_subdomain_and_reject_a_lookalike():
    assert mentions.registrable_domain("https://www.akumen.com/about") == "akumen.com"
    assert mentions.registrable_domain("akumen.com") == "akumen.com"
    assert mentions.registrable_domain("https://careers.akumen.com") == "akumen.com"
    assert mentions.registrable_domain("https://notakumen.com") == "notakumen.com"
    assert mentions.registrable_domain("https://www.company.co.uk") == "company.co.uk"


def test_one_website_match_is_a_mention():
    decision = mentions.match_organization(
        row(), lookup=lookup_one, pages=[], website="https://www.akumen.com",
        now=STAMP)
    assert decision["status"] == "matched"
    assert decision["organization_urn"] == ORG
    assert decision["localized_name"] == "Akumen, Inc."
    assert decision["sam_domain"] == "akumen.com"
    assert decision["source"] == "verified"
    text = captions.with_award_mentions("Akumen, Inc. won.", decision, [], row())
    assert text.startswith("@[Akumen, Inc.](urn:li:organization:8675309)\n\n")
    assert "Akumen, Inc. won." in text


def test_no_match_when_the_website_differs():
    def lookup(vanity):
        return [org(website="https://other.example")]

    decision = mentions.match_organization(
        row(), lookup=lookup, pages=[], website="https://akumen.com")
    assert decision["status"] == "plain"
    assert decision["reason"] == "no_match"
    assert "akumen" in decision["checked"]
    text = captions.with_award_mentions("Akumen, Inc. won.", decision, [], row())
    assert "@[" not in text
    assert text == "Akumen, Inc. won."


def test_subdomain_on_the_linkedin_page_still_matches():
    def lookup(vanity):
        if vanity == "akumen":
            return [org(website="http://careers.akumen.com")]
        return []

    decision = mentions.match_organization(
        row(), lookup=lookup, pages=[], website="https://www.akumen.com")
    assert decision["status"] == "matched"
    assert decision["sam_domain"] == "akumen.com"


def test_ambiguous_when_two_organizations_share_the_domain():
    def lookup(vanity):
        if vanity == "akumen":
            return [org(org_id=1, name="Akumen, Inc.", vanity="akumen")]
        if vanity == "akumen-inc":
            return [org(org_id=2, name="Akumen Federal", vanity="akumen-inc",
                        website="https://akumen.com")]
        return []

    decision = mentions.match_organization(
        row(), lookup=lookup, pages=[], website="https://akumen.com")
    assert decision["status"] == "plain"
    assert decision["reason"] == "ambiguous"
    urns = {item["organization_urn"] for item in decision["matches"]}
    assert urns == {"urn:li:organization:1", "urn:li:organization:2"}
    assert "@[" not in captions.with_award_mentions("body", decision, [], row())


def test_two_vanities_for_the_same_organization_are_one_match():
    same = org()

    def lookup(vanity):
        if vanity in ("akumen", "akumen-inc"):
            return [same]
        return []

    decision = mentions.match_organization(
        row(), lookup=lookup, pages=[], website="https://akumen.com")
    assert decision["status"] == "matched"
    assert decision["organization_urn"] == ORG


def test_no_website_does_not_look_up_and_does_not_tag():
    def lookup(vanity):
        raise AssertionError(f"looked up {vanity} without a website")

    decision = mentions.match_organization(
        {"recipient_name": "Akumen, Inc.", "recipient_uei": UEI},
        lookup=lookup, pages=[])
    assert decision["reason"] == "no_website"
    assert captions.with_award_mentions("plain", decision, [], row()) == "plain"


def test_a_lookup_failure_is_not_stored_as_no_match(tmp_path):
    def lookup(vanity):
        raise mentions.LookupFailed("HTTP 500", fatal=True)

    award = row()
    result = mentions.decorate(
        award, "Akumen, Inc. won.", str(tmp_path), lookup=lookup, pages=[],
        now=STAMP)
    assert result["company"]["reason"] == "lookup_failed"
    assert "@[" not in result["text"]
    store = mentions.load(str(tmp_path))
    assert store["organizations"] == {}


def test_a_verified_mapping_is_reused_without_another_lookup(tmp_path):
    calls = []

    def lookup(vanity):
        calls.append(vanity)
        return lookup_one(vanity)

    award = row()
    root = str(tmp_path)
    first = mentions.decorate(
        award, "Akumen, Inc. won.", root, lookup=lookup, pages=[], now=STAMP)
    assert first["company"]["status"] == "matched"
    assert calls
    stored = mentions.load(root)["organizations"][UEI]
    assert stored["source"] == "verified"
    assert stored["organization_urn"] == ORG
    assert stored["verified_at"] == STAMP
    calls.clear()

    def explode(vanity):
        raise AssertionError(vanity)

    second = mentions.decorate(
        award, "Akumen, Inc. won.", root, lookup=explode, pages=[], now=STAMP)
    assert second["company"]["reused"] is True
    assert second["text"].startswith("@[Akumen, Inc.](urn:li:organization:8675309)")
    assert "reused stored mapping" in second["review"]
    assert "akumen.com" in second["review"]


def test_block_and_override_win_and_an_override_is_not_replaced(tmp_path):
    root = str(tmp_path)
    mentions.block_organization(root, UEI, note="holding company", now=STAMP)
    blocked = mentions.decorate(
        row(), "Akumen, Inc. won.", root, lookup=lookup_one, pages=[])
    assert blocked["company"]["status"] == "blocked"
    assert "@[" not in blocked["text"]
    assert "blocked" in blocked["review"]
    assert "holding company" in blocked["review"]

    mentions.unblock_organization(root, UEI)
    mentions.set_override(
        root, UEI, urn="urn:li:organization:5", name="Akumen, Inc.",
        note="owner checked the Page", now=STAMP)

    def explode(vanity):
        raise AssertionError(vanity)

    kept = mentions.decorate(
        row(), "Akumen, Inc. won.", root, lookup=explode, pages=[])
    assert "urn:li:organization:5" in kept["text"]
    assert kept["company"]["source"] == "override"
    assert "Owner override" in kept["review"]
    assert mentions.load(root)["organizations"][UEI]["organization_urn"] == (
        "urn:li:organization:5")


def test_company_url_override_uses_the_api_name(tmp_path):
    def lookup(vanity):
        assert vanity == "akumen"
        return [org(name="Akumen, Inc.", vanity="akumen")]

    mentions.set_override_from_vanity(
        str(tmp_path), UEI, "https://www.linkedin.com/company/akumen/",
        lookup=lookup, now=STAMP)
    stored = mentions.load(str(tmp_path))["organizations"][UEI]
    assert stored["source"] == "override"
    assert stored["localized_name"] == "Akumen, Inc."
    assert stored["organization_urn"] == ORG


def test_feed_website_is_used_without_the_sam_key():
    award = row(recipient_website="https://www.akumen.com/about")
    url, source = mentions.website_for(award)
    assert source == "feed"
    assert mentions.registrable_domain(url) == "akumen.com"


def test_sam_payload_website_is_optional(monkeypatch):
    payload = {"entityData": [{"entityRegistration": {
        "ueiSAM": UEI, "entityURL": "https://akumen.com"}}]}
    assert mentions.entity_url_from_sam(payload) == "https://akumen.com"

    def sam_get(url, params=None, headers=None, timeout=None):
        assert params["ueiSAM"] == UEI
        assert "api_key" in params

        class Response:
            status_code = 200

            def json(self):
                return payload

        return Response()

    monkeypatch.setenv("SAM_API_KEY", "not-a-real-key")
    url, source = mentions.website_for(
        {"recipient_uei": UEI, "recipient_name": "Akumen, Inc."},
        sam_get=sam_get)
    assert source == "sam"
    assert url == "https://akumen.com"


def test_website_link_is_checked_before_the_name(tmp_path):
    seen = []

    def lookup(vanity):
        seen.append(vanity)
        if vanity == "akumen":
            return [org()]
        return []

    pages = [{"url": "https://akumen.com/", "html": LEADERSHIP}]
    decision = mentions.match_organization(
        row(recipient_name="Akumen, Inc."), lookup=lookup, pages=pages,
        website="https://akumen.com")
    assert seen[0] == "akumen"
    assert decision["status"] == "matched"


def test_executives_are_suggested_and_not_tagged_until_confirmed(tmp_path):
    pages = [{"url": "https://akumen.com/leadership", "html": LEADERSHIP}]
    found = mentions.suggest_executives(pages, "Akumen, Inc.")
    by_role = {person["role"]: person for person in found}
    assert by_role["executive"]["name"] == "Carl Garcia"
    assert by_role["executive"]["profile_url"].endswith("/in/carl-garcia")
    assert by_role["growth"]["name"] == "Kameron Turner"
    assert all(person["confirmed"] is False for person in found)

    result = mentions.decorate(
        row(), "Akumen, Inc. won.", str(tmp_path), lookup=lookup_one,
        pages=pages, now=STAMP)
    assert "Carl Garcia" not in result["text"]
    assert "Kameron Turner" not in result["text"]
    assert "@[Akumen, Inc.]" in result["text"]
    assert "Suggested, not tagged until you confirm:" in result["review"]
    assert "Carl Garcia" in result["review"]
    assert "confirm-person" in result["review"]
    assert UEI in result["review"]


def test_two_ceos_are_not_a_suggestion():
    html = """
    <p>Carl Garcia</p><p>CEO</p>
    <p>Dana Stone</p><p>Chief Executive Officer</p>
    <p>Kameron Turner</p><p>Director of Business Development</p>
    """
    found = mentions.suggest_executives(
        [{"url": "https://akumen.com/team", "html": html}], "Akumen, Inc.")
    assert [person["name"] for person in found] == ["Kameron Turner"]
    assert found[0]["role"] == "growth"


def test_confirmed_person_urn_is_mentioned_and_a_url_without_a_urn_is_plain(tmp_path):
    root = str(tmp_path)
    mentions.confirm_person(
        root, UEI, name="Carl Garcia", title="CEO",
        urn="urn:li:person:carl01", now=STAMP)
    mentions.confirm_person(
        root, UEI, name="Kameron Turner", title="Business Development",
        profile="https://www.linkedin.com/in/kameron-turner",
        person_lookup=lambda url: "", now=STAMP)
    result = mentions.decorate(
        row(), "Akumen, Inc. won.", root, lookup=lookup_one, pages=[],
        person_lookup=lambda url: "", now=STAMP)
    assert "@[Carl Garcia](urn:li:person:carl01)" in result["text"]
    assert "Kameron Turner" in result["text"]
    assert "urn:li:person:kameron" not in result["text"]
    assert "plain name (person URN unavailable)" in result["review"]
    stored = mentions.load(root)["people"][UEI]
    assert {person["name"] for person in stored} == {
        "Carl Garcia", "Kameron Turner"}
    assert all(person["confirmed"] for person in stored)


def test_at_most_three_mentions_and_an_ungrounded_name_is_dropped():
    company = {
        "status": "matched",
        "localized_name": "Akumen, Inc.",
        "organization_urn": ORG,
    }
    people = [
        {"name": "Carl Garcia", "role": "executive", "confirmed": True,
         "person_urn": "urn:li:person:carl01"},
        {"name": "Kameron Turner", "role": "growth", "confirmed": True,
         "person_urn": "urn:li:person:kam02"},
        {"name": "Pat Extra", "role": "other", "confirmed": True,
         "person_urn": "urn:li:person:pat03"},
    ]
    text = captions.with_award_mentions("Akumen, Inc. won.", company, people, row())
    assert text.count("@[") == 3
    assert "Pat Extra" not in text
    assert text.startswith(
        "@[Akumen, Inc.](urn:li:organization:8675309) | "
        "@[Carl Garcia](urn:li:person:carl01) | "
        "@[Kameron Turner](urn:li:person:kam02)\n\n")

    poisoned = dict(company, localized_name="Acme $9 million")
    plain = captions.with_award_mentions("Acme won.", poisoned, [], row())
    assert "@[" not in plain
    assert plain == "Acme won."


def test_caption_rules_survive_the_mention_line():
    """Order-value lead, task-order wording, and the release lag stay put."""
    built = captions.build_award_linkedin(row(), brand(), fresh=False)
    company = {
        "status": "matched",
        "localized_name": "Akumen, Inc.",
        "organization_urn": ORG,
        "source": "verified",
        "sam_domain": "akumen.com",
        "linkedin_website": "https://www.akumen.com",
        "vanity_name": "akumen",
    }
    people = [
        {"name": "Carl Garcia", "role": "executive", "confirmed": True,
         "person_urn": "urn:li:person:carl01"},
        {"name": "Kameron Turner", "role": "growth", "confirmed": True,
         "person_urn": "urn:li:person:kam02"},
    ]
    text = captions.with_award_mentions(built["text"], company, people, row())
    lead, body = text.split("\n\n", 1)
    assert lead == (
        "@[Akumen, Inc.](urn:li:organization:8675309) | "
        "@[Carl Garcia](urn:li:person:carl01) | "
        "@[Kameron Turner](urn:li:person:kam02)")
    assert "task order" in body.casefold()
    assert "recently released" in body.casefold()
    assert "under GSA MAS" in body
    assert "was awarded a contract" not in body.casefold()
    assert "new contract" not in body.casefold()
    assert body.index("$2,778,270") < body.index("$915,980")
    assert captions.ungrounded_money(body, row()) == []
    assert captions.ungrounded_numbers(body, row()) == []
    assert "https://pursuitai.net/awards/akumen-navy" in body


def test_approval_queue_shows_the_proposed_tag(monkeypatch):
    sent = {}

    def fake_post(url, json=None, timeout=None):
        sent["json"] = json

        class Response:
            status_code = 200

        return Response()

    monkeypatch.setenv("NOTIFY_WEBHOOK_URL", "https://hooks.example/secret")
    monkeypatch.setattr(notify.requests, "post", fake_post)
    pending = {"posts": [{
        "recipient_name": "Akumen, Inc.",
        "alt": "card",
        "text_linkedin": (
            "@[Akumen, Inc.](urn:li:organization:8675309)\n\n"
            "Akumen, Inc. won."),
        "mention_review": (
            "Company tag: Akumen, Inc. (urn:li:organization:8675309)\n"
            "Verified: SAM domain akumen.com matches LinkedIn website "
            "https://www.akumen.com (vanity akumen, source verified)."),
    }]}
    assert notify.awards_review(
        pending, review_url="https://github.com/example/run") is True
    blob = json.dumps(sent["json"])
    assert "urn:li:organization:8675309" in blob
    assert "akumen.com" in blob
    assert "Verified:" in blob
    assert "hooks.example" not in blob


def test_publish_retries_without_mentions_when_tagging_fails(
        tmp_path, monkeypatch):
    root = str(tmp_path)
    mentions.set_override(
        root, UEI, urn=ORG, name="Akumen, Inc.", now=STAMP)
    monkeypatch.setattr(
        awards, "fetch_feed",
        lambda **kwargs: {
            "items": [row()], "next_since": "next-cursor",
            "unavailable": False, "status": 200, "reason": None})
    pending = awards.prepare(root=root)
    assert "@[Akumen, Inc.](urn:li:organization:8675309)" in (
        pending["posts"][0]["text_linkedin"])
    calls = []

    def poster(path, text, alt=None):
        calls.append(text)
        if "@[" in (text or ""):
            raise RuntimeError("post creation failed (400): bad mention")
        return {"id": "urn:li:share:1",
                "url": "https://www.linkedin.com/feed/update/urn:li:share:1/"}

    assert awards.approve(root=root) == 0
    assert awards.publish(root, poster=poster) == 0
    assert "@[" in calls[0]
    assert "@[" not in calls[1]
    assert "Akumen, Inc." in calls[1]
    assert "task order" in calls[1].casefold()


def test_an_upload_failure_is_not_retried_as_a_mention_problem(
        tmp_path, monkeypatch):
    root = str(tmp_path)
    mentions.set_override(root, UEI, urn=ORG, name="Akumen, Inc.", now=STAMP)
    monkeypatch.setattr(
        awards, "fetch_feed",
        lambda **kwargs: {
            "items": [row()], "next_since": None, "unavailable": False,
            "status": 200, "reason": None})
    awards.prepare(root=root)
    calls = []

    def poster(path, text, alt=None):
        calls.append(text)
        raise RuntimeError("image processing failed")

    assert awards.approve(root=root) == 0
    assert awards.publish(root, poster=poster) == 1
    assert len(calls) == 1


def test_confirm_during_approval_is_what_publish_sends(tmp_path, monkeypatch):
    """A confirmation committed before publish is on the caption."""
    root = str(tmp_path)
    mentions.set_override(root, UEI, urn=ORG, name="Akumen, Inc.", now=STAMP)
    monkeypatch.setattr(
        awards, "fetch_feed",
        lambda **kwargs: {
            "items": [row()], "next_since": None, "unavailable": False,
            "status": 200, "reason": None})
    pending = awards.prepare(root=root)
    assert "Carl Garcia" not in pending["posts"][0]["text_linkedin"]
    mentions.confirm_person(
        root, UEI, name="Carl Garcia", title="CEO",
        urn="urn:li:person:carl01", now=STAMP)
    sent = []

    def poster(path, text, alt=None):
        sent.append(text)
        return {"id": "urn:li:share:2", "url": "https://www.linkedin.com/x"}

    assert awards.approve(root=root) == 0
    assert awards.publish(root, poster=poster) == 0
    assert "@[Carl Garcia](urn:li:person:carl01)" in sent[0]
    assert "@[Akumen, Inc.](urn:li:organization:8675309)" in sent[0]


def test_docs_and_workflow_name_the_optional_key_and_the_store():
    workflow = open(os.path.join(ROOT, ".github", "workflows", "awards.yml")).read()
    secrets = open(os.path.join(ROOT, "SECRETS.md")).read()
    ops = open(os.path.join(ROOT, "docs", "OPERATIONS.md")).read()
    assert "content/linkedin_mentions.json" in workflow
    assert "secrets.SAM_API_KEY" in workflow
    assert "SAM_API_KEY" in secrets
    assert "optional" in secrets.lower()
    assert "recipient_website" in secrets
    assert "confirm-person" in ops
    assert "linkedin_mentions.json" in ops
