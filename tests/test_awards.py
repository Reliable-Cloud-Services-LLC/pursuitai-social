"""Public-award LinkedIn posts.

Selection: only is_new_award, largest obligated amount first, at most
three, and never an id already in the dedupe log.

The feed: a 404 (the feature flag is off) and a missing token queue
nothing. A rejected token, a 5xx, and a body without `items` fail.

Captions: every dollar figure is the obligation, the ceiling, or a
figure already written in the feed. A rewrite that invents one is
discarded. The link is the award page on pursuitai.net.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import awards  # noqa: E402
import captions  # noqa: E402
import cards  # noqa: E402
import compliance  # noqa: E402


def award(**over):
    """One feed row, using the keys public_awards.py actually returns."""
    base = {
        "is_new_award": True,
        "id": "award-1",
        "slug": "acme-va",
        "url": "https://pursuitai.net/awards/acme-va",
        "first_published_at": "2026-11-12T15:00:00Z",
        "published_at": "2026-11-12T15:00:00Z",
        "recipient_name": "Acme Federal Solutions LLC",
        "recipient_uei": "ABC123DEF456",
        "awarding_agency": "Department of Veterans Affairs",
        "awarding_sub_agency": "Veterans Health Administration",
        "awarding_office": "Network Contracting Office",
        "piid": "36C10B26C0001",
        "parent_award_piid": None,
        "award_type_code": "D",
        "award_category": "contract",
        "obligation": 4_200_000,
        "ceiling": 12_000_000,
        "action_date": "2026-11-01",
        "pop_start": "2026-11-01",
        "pop_end": "2027-10-31",
        "naics_code": "541512",
        "naics_description": "Computer Systems Design Services",
        "psc_code": "D399",
        "set_aside": None,
        "extent_competed": None,
        "summary": "Cloud hosting and service desk for regional medical centers.",
        "description_source": "usaspending",
        "generated_unique_award_id": "CONT_AWD_ACME",
        "official_urls": {
            "usaspending": "https://www.usaspending.gov/award/CONT_AWD_ACME"},
    }
    base.update(over)
    return base


def branded():
    with open(os.path.join(ROOT, "content", "calendar.json")) as handle:
        return json.load(handle)["brand"]


@pytest.fixture
def quiet_model(monkeypatch):
    """No caption test should call Claude, even if a key is exported."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


# ---------- selection ----------

def test_only_new_awards_and_largest_obligation_first():
    rows = [
        award(generated_unique_award_id="SMALL", slug="small",
              obligation=1_000_000, ceiling=1_000_000),
        award(generated_unique_award_id="OLD", slug="old",
              obligation=99_000_000, ceiling=99_000_000, is_new_award=False),
        award(generated_unique_award_id="MID", slug="mid",
              obligation=5_000_000, ceiling=5_000_000),
        award(generated_unique_award_id="BIG", slug="big",
              obligation=8_000_000, ceiling=8_000_000),
        award(generated_unique_award_id="MISSING", slug="missing",
              is_new_award=None, obligation=50_000_000, ceiling=50_000_000),
    ]
    chosen, deferred = awards.select_awards(rows)
    assert [row["generated_unique_award_id"] for row in chosen] == [
        "BIG", "MID", "SMALL"]
    assert deferred == []
    assert "OLD" not in {row["generated_unique_award_id"] for row in chosen}


def test_caps_at_three_and_keeps_the_rest_for_later():
    rows = [award(generated_unique_award_id=f"A{i}", slug=f"a{i}",
                  obligation=1_000_000 * (i + 1),
                  ceiling=1_000_000 * (i + 1)) for i in range(5)]
    chosen, deferred = awards.select_awards(rows)
    assert len(chosen) == 3
    assert [row["obligation"] for row in chosen] == [
        5_000_000, 4_000_000, 3_000_000]
    assert len(deferred) == 2
    assert awards.DAILY_CAP == 3


def test_already_posted_ids_are_not_selected_again():
    rows = [
        award(generated_unique_award_id="DONE", slug="done",
              obligation=20_000_000),
        award(generated_unique_award_id="NEXT", slug="next",
              obligation=3_000_000),
    ]
    chosen, deferred = awards.select_awards(rows, seen_ids={"DONE"})
    assert [row["generated_unique_award_id"] for row in chosen] == ["NEXT"]
    assert deferred == []


def test_duplicate_id_in_one_page_is_one_post():
    row = award()
    chosen, _ = awards.select_awards([row, dict(row)])
    assert len(chosen) == 1


def test_string_false_and_missing_flag_are_not_new():
    not_new = award(generated_unique_award_id="S", slug="s",
                    is_new_award="false", obligation=9_000_000)
    missing = award(generated_unique_award_id="X", slug="x",
                    obligation=8_000_000)
    del missing["is_new_award"]
    flagged = award(generated_unique_award_id="M", slug="m",
                    is_new_award="true", obligation=2_000_000)
    chosen, _ = awards.select_awards([not_new, missing, flagged])
    assert [row["generated_unique_award_id"] for row in chosen] == ["M"]


def test_rank_is_the_greater_of_obligation_and_ceiling():
    """Same key the server uses for value_desc: greatest(obligation, ceiling)."""
    big_ceiling = award(generated_unique_award_id="CEIL", slug="ceil",
                        obligation=1_500_000, ceiling=80_000_000)
    big_obligation = award(generated_unique_award_id="OBL", slug="obl",
                           obligation=6_000_000, ceiling=6_000_000)
    chosen, _ = awards.select_awards([big_ceiling, big_obligation])
    assert chosen[0]["generated_unique_award_id"] == "CEIL"
    assert awards.rank_value(chosen[0]) == 80_000_000


def test_either_figure_alone_is_enough_to_rank():
    only_ceiling = award(generated_unique_award_id="C", slug="c",
                         obligation=None, ceiling=4_000_000)
    smaller = award(generated_unique_award_id="S", slug="s",
                    obligation=2_000_000, ceiling=None)
    chosen, _ = awards.select_awards([only_ceiling, smaller])
    assert [row["generated_unique_award_id"] for row in chosen] == ["C", "S"]


def test_reads_feed_keys_and_ignores_the_earlier_names():
    raw = award(
        recipient="WRONG NAME",
        agency="WRONG AGENCY",
        naics="000000",
    )
    chosen, _ = awards.select_awards([raw])
    assert chosen[0]["recipient_name"] == "Acme Federal Solutions LLC"
    assert chosen[0]["awarding_agency"] == "Department of Veterans Affairs"
    assert chosen[0]["naics_code"] == "541512"
    assert chosen[0]["official_urls"]["usaspending"].startswith(
        "https://www.usaspending.gov/")


def test_a_row_that_only_has_the_old_field_names_is_not_selected():
    legacy = {
        "is_new_award": True,
        "slug": "legacy",
        "url": "https://pursuitai.net/awards/legacy",
        "recipient": "Legacy LLC",
        "agency": "Department of Energy",
        "naics": "541512",
        "obligation": 9_000_000,
        "generated_unique_award_id": "LEGACY",
    }
    chosen, deferred = awards.select_awards([legacy, award()])
    assert [row["generated_unique_award_id"] for row in chosen] == [
        "CONT_AWD_ACME"]
    assert deferred == []


def test_sole_source_is_not_filtered():
    row = award(extent_competed="NOT AVAILABLE FOR COMPETITION",
                set_aside="NO SET ASIDE USED",
                generated_unique_award_id="SOLE", slug="sole")
    chosen, _ = awards.select_awards([row])
    assert [item["generated_unique_award_id"] for item in chosen] == ["SOLE"]


def test_slug_can_come_from_the_page_url():
    raw = award(slug="", url="https://pursuitai.net/awards/from-url")
    chosen, _ = awards.select_awards([raw])
    assert chosen[0]["slug"] == "from-url"


def test_a_row_missing_recipient_or_value_is_not_queued():
    nameless = award(recipient_name="", generated_unique_award_id="NONAME",
                     slug="noname")
    valueless = award(obligation=None, ceiling=None,
                      generated_unique_award_id="NOVAL", slug="noval")
    chosen, deferred = awards.select_awards([nameless, valueless, award()])
    assert [row["generated_unique_award_id"] for row in chosen] == [
        "CONT_AWD_ACME"]
    assert deferred == []


# ---------- feed errors ----------

class _Response:
    def __init__(self, status, payload=None, broken=False):
        self.status_code = status
        self._payload = payload
        self._broken = broken

    def json(self):
        if self._broken:
            raise ValueError("not json")
        return self._payload


def _get(status, payload=None, broken=False, sink=None):
    def get(url, headers=None, params=None, timeout=None):
        if sink is not None:
            sink.update(url=url, headers=headers, params=params)
        return _Response(status, payload, broken)
    return get


def test_request_uses_the_documented_query_and_bearer_token():
    seen = {}
    page = awards.fetch_feed(
        since="cursor-1", token="sekret",
        get=_get(200, {"items": [], "next_since": "cursor-2"}, sink=seen))
    assert seen["url"] == "https://api.pursuitai.net/api/v1/public-awards/feed"
    assert seen["params"]["order"] == "recent"
    assert seen["params"]["limit"] == "100"
    assert seen["params"]["since"] == "cursor-1"
    assert seen["headers"]["Authorization"] == "Bearer sekret"
    assert "sekret" not in seen["url"]
    assert page["next_since"] == "cursor-2"
    assert page["unavailable"] is False


def test_base_url_accepts_origin_api_root_or_full_path():
    assert awards.feed_endpoint("https://api.pursuitai.net") == (
        "https://api.pursuitai.net/api/v1/public-awards/feed")
    assert awards.feed_endpoint("https://api.pursuitai.net/api/v1") == (
        "https://api.pursuitai.net/api/v1/public-awards/feed")
    assert awards.feed_endpoint(
        "https://example.test/api/v1/public-awards/feed") == (
        "https://example.test/api/v1/public-awards/feed")


def test_404_is_an_empty_day_not_an_error():
    page = awards.fetch_feed(token="t", get=_get(404, {"detail": "Not Found"}))
    assert page["unavailable"] is True
    assert page["items"] == []
    assert page["status"] == 404


def test_missing_token_does_not_call_the_network(monkeypatch):
    monkeypatch.delenv("PUBLIC_AWARDS_FEED_TOKEN", raising=False)

    def boom(*args, **kwargs):
        raise AssertionError("the feed was called without a token")

    page = awards.fetch_feed(get=boom, token=None)
    assert page["unavailable"] is True
    assert page["reason"] == "not_configured"


def test_401_is_a_bad_token_not_an_empty_day():
    with pytest.raises(awards.FeedError, match="401"):
        awards.fetch_feed(token="t", get=_get(401, {"detail": "Unauthorized"}))


def test_422_is_a_bad_cursor_not_an_empty_day():
    with pytest.raises(awards.FeedError, match="since"):
        awards.fetch_feed(token="t", since="not-a-date",
                          get=_get(422, {"detail": "bad since"}))


@pytest.mark.parametrize("status", [403, 500, 503])
def test_other_errors_raise_and_are_not_treated_as_empty(status):
    with pytest.raises(awards.FeedError):
        awards.fetch_feed(token="t", get=_get(status, {"detail": "no"}))


def test_limit_is_clamped_to_the_server_max():
    seen = {}
    awards.fetch_feed(token="t", limit=500,
                      get=_get(200, {"items": [], "next_since": None,
                                     "order": "recent"}, sink=seen))
    assert seen["params"]["limit"] == "100"
    assert seen["params"]["order"] == "recent"


def test_non_json_and_a_missing_items_list_raise():
    with pytest.raises(awards.FeedError):
        awards.fetch_feed(token="t", get=_get(200, broken=True))
    with pytest.raises(awards.FeedError):
        awards.fetch_feed(token="t", get=_get(200, {"next_since": "x"}))


def test_a_transport_error_raises():
    def get(*args, **kwargs):
        raise OSError("timed out")

    with pytest.raises(awards.FeedError):
        awards.fetch_feed(token="t", get=get)


def test_prepare_on_404_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(awards, "fetch_feed", lambda **kwargs: {
        "items": [], "next_since": None, "unavailable": True,
        "status": 404, "reason": "not_found"})
    result = awards.prepare(root=str(tmp_path))
    assert result["posts"] == []
    assert not os.path.exists(os.path.join(tmp_path, "content",
                                           "awards_pending.json"))
    assert awards.load_cursor(str(tmp_path)) is None


def test_dry_run_prints_three_and_writes_nothing(tmp_path, monkeypatch, capsys,
                                                 quiet_model):
    rows = [award(generated_unique_award_id=f"A{i}", slug=f"slug-{i}",
                  url=f"https://pursuitai.net/awards/slug-{i}",
                  obligation=1_000_000 * (i + 1),
                  ceiling=1_000_000 * (i + 1),
                  recipient_name=f"Firm {i} LLC") for i in range(4)]
    monkeypatch.setattr(awards, "fetch_feed", lambda **kwargs: {
        "items": rows, "next_since": "later", "unavailable": False,
        "status": 200, "reason": None})
    result = awards.prepare(root=str(tmp_path), dry_run=True)
    out = capsys.readouterr().out
    assert "dry-run: nothing queued, nothing posted" in out
    assert "Firm 3 LLC" in out and "Firm 0 LLC" not in out
    assert len(result["posts"]) == 3
    assert os.listdir(tmp_path) == []


# ---------- captions ----------

def test_template_is_grounded_and_links_to_the_award_page(quiet_model):
    built = captions.build_award_linkedin(award(), branded(), fresh=False)
    text = built["text"]
    assert built["varied"] is False
    assert "Acme Federal Solutions LLC" in text
    assert "Department of Veterans Affairs" in text
    assert "$4.2 million" in text
    assert "ceiling" in text.casefold()
    assert "$12 million" in text
    assert "https://pursuitai.net/awards/acme-va" in text
    assert "utm_source=linkedin" in text
    assert "utm_campaign=award" in text
    assert captions.ungrounded_money(text, award()) == []
    assert captions.ungrounded_numbers(text, award()) == []
    assert compliance.check_claims(captions._award_topic(award()), text) == []
    assert "orangeslices" not in text.casefold()
    assert "usaspending.gov" not in text


def test_the_post_link_is_the_feed_url():
    row = award(slug="from-slug",
                url="https://pursuitai.net/awards/from-feed")
    text = captions.build_award_linkedin(row, branded(), fresh=False)["text"]
    assert "https://pursuitai.net/awards/from-feed" in text
    assert "https://pursuitai.net/awards/from-slug" not in text


def test_set_aside_and_competition_are_quoted_when_the_feed_has_them(quiet_model):
    row = award(set_aside="8(a) Competed",
                extent_competed="FULL AND OPEN AFTER EXCLUSION")
    text = captions.build_award_linkedin(row, branded(), fresh=False)["text"]
    assert "Set-aside: 8(a) Competed." in text
    assert "Competition: FULL AND OPEN AFTER EXCLUSION." in text
    assert captions.ungrounded_numbers(text, row) == []
    assert captions.ungrounded_money(text, row) == []


def test_set_aside_and_competition_are_omitted_when_absent(quiet_model):
    text = captions.build_award_linkedin(award(), branded(), fresh=False)["text"]
    assert "Set-aside:" not in text
    assert "Competition:" not in text


def test_an_invented_dollar_figure_is_not_grounded():
    row = award()
    invented = ("Acme Federal Solutions LLC was awarded work by "
                "Department of Veterans Affairs. The obligated amount is "
                "$9 million.")
    assert captions.ungrounded_money(invented, row)


def test_a_number_that_is_only_in_the_summary_is_allowed():
    row = award(summary="Supports 12 regional medical centers.")
    text = ("Acme Federal Solutions LLC was awarded work by "
            "Department of Veterans Affairs. Supports 12 regional "
            "medical centers.")
    assert captions.ungrounded_numbers(text, row) == []


def test_a_rewrite_that_invents_a_figure_falls_back(monkeypatch, quiet_model):
    def fake(topic, platform, brand):
        return (topic["hook_ig"].replace("$4.2 million", "$9 million")
                + "\n\nPlus a $9 million bonus.")

    monkeypatch.setattr(captions, "_claude_variant", fake)
    built = captions.build_award_linkedin(award(), branded(), fresh=True)
    assert "$9 million" not in built["text"]
    assert "$4.2 million" in built["text"]
    assert built["varied"] is False


def test_a_rewrite_that_drops_the_recipient_falls_back(monkeypatch, quiet_model):
    def fake(topic, platform, brand):
        return ("A contractor was awarded work by Department of Veterans "
                "Affairs. The obligated amount is $4.2 million. "
                "The contract ceiling is $12 million.")

    monkeypatch.setattr(captions, "_claude_variant", fake)
    built = captions.build_award_linkedin(award(), branded(), fresh=True)
    assert "Acme Federal Solutions LLC" in built["text"]


def test_summary_link_to_another_outlet_is_not_copied(quiet_model):
    row = award(summary=(
        "Cloud hosting. Read the writeup at "
        "https://orangeslices.ai/awards/acme-wins-big"))
    built = captions.build_award_linkedin(row, branded(), fresh=False)
    assert "orangeslices" not in built["text"].casefold()
    assert "Cloud hosting" in built["text"]


def test_ceiling_only_copy_does_not_call_the_ceiling_an_obligation(quiet_model):
    row = award(obligation=None, ceiling=4_000_000)
    built = captions.build_award_linkedin(row, branded(), fresh=False)
    text = built["text"].casefold()
    assert "ceiling" in text
    assert "obligated amount is" not in text
    assert "$4 million" in built["text"]


def test_award_card_renders_square(tmp_path, quiet_model):
    row = awards.normalize_item(award())
    topic = awards.card_topic(row)
    out = str(tmp_path / "card.png")
    img = cards.render_award_card(topic, branded(), out)
    assert img.size == (1080, 1080)
    assert os.path.getsize(out) > 10_000
    assert compliance.check_rendered(topic) == []


# ---------- queue, dedupe log, cursor ----------

def _page(rows, nxt="next-cursor"):
    return {"items": rows, "next_since": nxt, "unavailable": False,
            "status": 200, "reason": None}


def test_prepare_queues_a_batch_and_holds_the_cursor_when_more_remain(
        tmp_path, monkeypatch, quiet_model):
    rows = [award(generated_unique_award_id=f"A{i}", slug=f"slug-{i}",
                  url=f"https://pursuitai.net/awards/slug-{i}",
                  obligation=1_000_000 * (i + 1),
                  ceiling=1_000_000 * (i + 1),
                  recipient_name=f"Firm {i} LLC") for i in range(4)]
    monkeypatch.setattr(awards, "fetch_feed", lambda **kwargs: _page(rows))
    pending = awards.prepare(root=str(tmp_path))
    assert len(pending["posts"]) == 3
    assert pending["cursor_advance_to"] is None
    assert os.path.exists(os.path.join(tmp_path, "content",
                                       "awards_pending.json"))
    assert awards.load_cursor(str(tmp_path)) is None
    # The largest three, not the first three in feed order.
    assert pending["posts"][0]["generated_unique_award_id"] == "A3"


def test_publish_records_ids_and_advances_only_when_the_page_is_done(
        tmp_path, monkeypatch, quiet_model):
    root = str(tmp_path)
    rows = [award(generated_unique_award_id="ONLY", slug="only",
                  obligation=2_000_000)]
    monkeypatch.setattr(awards, "fetch_feed", lambda **kwargs: _page(rows))
    awards.prepare(root=root)
    saved = json.load(open(os.path.join(root, "content", "awards_pending.json")))
    assert saved["cursor_advance_to"] == "next-cursor"

    calls = []

    def poster(path, text, alt=None):
        calls.append((path, text, alt))
        return {"id": "urn:li:share:9", "url": "https://www.linkedin.com/feed/update/urn:li:share:9/"}

    assert awards.publish(root, poster=poster) == 1  # not approved
    assert calls == []

    assert awards.publish(root, poster=poster, force=True) == 0
    assert calls and calls[0][0].endswith("award_only.png")
    assert "ONLY" in awards.posted_ids(root)
    assert awards.load_cursor(root) == "next-cursor"
    assert not os.path.exists(os.path.join(root, "content", "awards_pending.json"))


def test_a_posted_id_is_not_queued_on_the_next_run(tmp_path, monkeypatch,
                                                   quiet_model):
    root = str(tmp_path)
    log = os.path.join(root, "logs")
    os.makedirs(log)
    with open(os.path.join(log, "award_posted.jsonl"), "w") as handle:
        handle.write(json.dumps({
            "generated_unique_award_id": "DONE", "slug": "done"}) + "\n")
    rows = [
        award(generated_unique_award_id="DONE", slug="done",
              obligation=9_000_000),
        award(generated_unique_award_id="NEXT", slug="next",
              url="https://pursuitai.net/awards/next",
              obligation=2_000_000, recipient_name="Next Firm LLC"),
    ]
    monkeypatch.setattr(awards, "fetch_feed", lambda **kwargs: _page(rows))
    awards.prepare(root=root)
    pending = json.load(open(os.path.join(root, "content", "awards_pending.json")))
    assert [post["generated_unique_award_id"] for post in pending["posts"]] == [
        "NEXT"]


def test_publish_does_not_repeat_an_id_already_in_the_log(
        tmp_path, monkeypatch, quiet_model):
    root = str(tmp_path)
    monkeypatch.setattr(awards, "fetch_feed", lambda **kwargs: _page([award()]))
    awards.prepare(root=root)
    log = os.path.join(root, "logs")
    os.makedirs(log, exist_ok=True)
    with open(os.path.join(log, "award_posted.jsonl"), "a") as handle:
        handle.write(json.dumps({
            "generated_unique_award_id": "CONT_AWD_ACME"}) + "\n")

    def poster(*args, **kwargs):
        raise AssertionError("already-logged award was sent again")

    assert awards.publish(root, poster=poster, force=True) == 0


def test_an_already_seen_page_advances_the_cursor(tmp_path, monkeypatch,
                                                   quiet_model):
    root = str(tmp_path)
    log = os.path.join(root, "logs")
    os.makedirs(log)
    with open(os.path.join(log, "award_posted.jsonl"), "w") as handle:
        handle.write(json.dumps({
            "generated_unique_award_id": "CONT_AWD_ACME"}) + "\n")
    monkeypatch.setattr(awards, "fetch_feed", lambda **kwargs: _page([award()]))
    result = awards.prepare(root=root)
    assert result["posts"] == []
    assert awards.load_cursor(root) == "next-cursor"
    assert not os.path.exists(os.path.join(root, "content", "awards_pending.json"))


def test_force_is_refused_in_ci(tmp_path, monkeypatch, quiet_model):
    root = str(tmp_path)
    monkeypatch.setattr(awards, "fetch_feed", lambda **kwargs: _page([award()]))
    awards.prepare(root=root)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    called = []
    code = awards.publish(root, poster=lambda *a, **k: called.append(1),
                          force=True)
    assert code == 1
    assert called == []


# ---------- workflow and docs ----------

def test_awards_workflow_is_its_own_queue():
    text = open(os.path.join(ROOT, ".github", "workflows", "awards.yml")).read()
    assert "content/awards_pending.json" in text
    assert "content/pending.json" not in text
    assert "environment: social-publish" in text
    assert "engine/awards.py --dry-run" in text
    assert "engine/awards.py --prepare" in text
    assert "engine/awards.py --publish" in text
    assert "engine/awards.py --approve" in text
    assert "commit_and_push.sh" in text
    assert "group: award-posts" in text
    assert "secrets.PUBLIC_AWARDS_FEED_TOKEN" in text
    assert "secrets.LINKEDIN_ACCESS_TOKEN" in text
    assert "secrets.LINKEDIN_ORG_ID" in text
    assert 'cron: "47 12 * * 1-5"' in text


def test_docs_name_the_new_secret_and_the_schedule():
    secrets = open(os.path.join(ROOT, "SECRETS.md")).read()
    ops = open(os.path.join(ROOT, "docs", "OPERATIONS.md")).read()
    readme = open(os.path.join(ROOT, "README.md")).read()
    assert "PUBLIC_AWARDS_FEED_TOKEN" in secrets
    assert "PUBLIC_AWARDS_FEED_BASE_URL" in secrets
    assert "https://api.pursuitai.net" in secrets
    assert "12:47" in ops
    assert "awards.yml" in ops
    assert "PUBLIC_AWARDS_FEED_TOKEN" in ops
    assert "awards.yml" in readme
