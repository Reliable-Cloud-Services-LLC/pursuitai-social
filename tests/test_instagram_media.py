"""Instagram accepts JPEG only, and defaults a Reels cover to frame 0.

Two defects, one root cause: the Instagram integration was written against
an assumed contract rather than the published one, and IG credentials were
never set, so nothing ever exercised it. Nine green runs and a live X post
said nothing about any of this.

Meta's content-publishing reference, verbatim:

  "JPEG is the only image format supported. Extended JPEG formats such as
   MPO and JPS are not supported."

  cover_url (Reels only): "The path to an image to use as the cover image
   for the Reels tab. We will cURL the image using the URL that you specify
   so the image must be on a public server."

  thumb_offset: "Location, in milliseconds, of the video or reel frame to
   be used as the cover thumbnail image. The default value is 0, which is
   the first frame of the video or reel."

Every image we render is a PNG, and every animated spot opens on a bare
gradient — so as shipped, IG would have rejected every card and covered
every reel with a blank square.
"""
import json
import re
import os
import sys

import pytest
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import media  # noqa: E402
import post_ig  # noqa: E402


# ---------- JPEG only ----------

def test_as_jpeg_converts_a_png(tmp_path):
    src = tmp_path / "card.png"
    Image.new("RGB", (64, 64), (10, 10, 30)).save(src)
    out = media.as_jpeg(str(src))
    assert out.endswith(".jpg")
    with Image.open(out) as img:
        assert img.format == "JPEG"


def test_as_jpeg_flattens_alpha(tmp_path):
    """Cards are RGBA. JPEG has no alpha channel, so an unconverted save
    raises — and the failure would land at prepare time, mid-run."""
    src = tmp_path / "card.png"
    Image.new("RGBA", (64, 64), (10, 10, 30, 255)).save(src)
    out = media.as_jpeg(str(src))
    with Image.open(out) as img:
        assert img.mode == "RGB"


def test_as_jpeg_is_a_no_op_for_a_jpeg(tmp_path):
    src = tmp_path / "already.jpg"
    Image.new("RGB", (32, 32)).save(src, "JPEG")
    assert media.as_jpeg(str(src)) == str(src)


def test_as_jpeg_does_not_silently_fall_back(tmp_path):
    """A fallback to the PNG would hand Meta a format its own reference
    says is unsupported, and the container error arrives later saying
    nothing useful. Fail here instead."""
    with pytest.raises(Exception):
        media.as_jpeg(str(tmp_path / "missing.png"))


def test_the_poster_is_a_jpeg():
    """The poster is both the Slack review still and the Reels cover. One
    file for both, so they cannot drift — which means it must be JPEG."""
    assert media.poster_for("assets/video/x_ad.mp4").endswith(".jpg")


# ---------- the Reels cover ----------

def _container_payload(monkeypatch, **kwargs):
    captured = {}

    class Resp:
        ok = True          # _check() reads .ok, not raise_for_status
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"id": "container-1", "status_code": "FINISHED"}

    def fake_post(url, data=None, timeout=None):
        if url.endswith("/media"):
            captured.update(data)
        return Resp()

    monkeypatch.setenv("IG_USER_ID", "1")
    monkeypatch.setenv("IG_ACCESS_TOKEN", "t")
    monkeypatch.setenv("MEDIA_BASE_URL", "https://cdn.test")
    monkeypatch.setattr(post_ig.requests, "post", fake_post)
    monkeypatch.setattr(post_ig.requests, "get",
                        lambda *a, **k: Resp())
    post_ig.post_reel("assets/video/t_ad.mp4", "cap", **kwargs)
    return captured


def test_reel_sends_the_poster_as_the_cover(monkeypatch):
    payload = _container_payload(
        monkeypatch, cover_rel_path="assets/video/t_ad_poster.jpg")
    assert payload["cover_url"] == \
        "https://cdn.test/assets/video/t_ad_poster.jpg"


def test_reel_never_sends_both_cover_controls(monkeypatch):
    """cover_url takes precedence, so sending both is a silent
    contradiction in the request."""
    payload = _container_payload(
        monkeypatch, cover_rel_path="assets/video/t_ad_poster.jpg")
    assert "thumb_offset" not in payload


def test_reel_without_a_poster_still_avoids_frame_zero(monkeypatch):
    """Meta's default is frame 0. Our spots open on a bare gradient, so the
    default is a blank square in the profile grid."""
    payload = _container_payload(monkeypatch)
    assert int(payload["thumb_offset"]) > 0
    assert "cover_url" not in payload


# ---------- the ratchet ----------

IG_IMAGE_SUFFIXES = (".jpg", ".jpeg")


def test_nothing_png_can_reach_instagram(tmp_path, monkeypatch):
    """The defect this file exists for, pinned end to end: whatever
    prepare() records as the Instagram media must be something Meta will
    accept — for every format the rotation can produce."""
    import run
    for name in ("assets/cards/t_ig.png", "assets/screens/t_ig.png",
                 "assets/cards/t_ig.jpg"):
        src = tmp_path / os.path.basename(name)
        if src.suffix == ".jpg":
            Image.new("RGB", (32, 32), (0, 0, 0)).save(src, "JPEG")
        else:
            Image.new("RGBA", (32, 32), (0, 0, 0, 255)).save(src, "PNG")
        out = media.as_jpeg(str(src))
        assert out.lower().endswith(IG_IMAGE_SUFFIXES), out


def test_the_reel_route_forwards_the_cover(monkeypatch):
    """The cover has to survive the hop from pending.json to the API call —
    a poster that is rendered, uploaded, and then dropped here is a poster
    that never reaches Instagram."""
    import run
    seen = {}
    monkeypatch.setitem(sys.modules, "post_ig", type(sys)("post_ig"))
    sys.modules["post_ig"].post_reel = (
        lambda path, cap, cover_rel_path=None: seen.update(
            path=path, cover=cover_rel_path))
    sys.modules["post_ig"].post_image = lambda *a, **k: None
    run._post_ig({"media_ig": "assets/video/t_ad.mp4", "text_ig": "c",
                  "cover_ig": "assets/video/t_ad_poster.jpg"})
    assert seen["cover"] == "assets/video/t_ad_poster.jpg"


def test_the_image_route_is_unaffected_by_the_cover_field(monkeypatch):
    """A card has no cover; the field is present and None."""
    import run
    seen = {}
    monkeypatch.setitem(sys.modules, "post_ig", type(sys)("post_ig"))
    sys.modules["post_ig"].post_image = (
        lambda path, cap: seen.update(path=path))
    sys.modules["post_ig"].post_reel = lambda *a, **k: None
    run._post_ig({"media_ig": "assets/cards/t_ig.jpg", "text_ig": "c",
                  "cover_ig": None})
    assert seen["path"] == "assets/cards/t_ig.jpg"


# ---------- the pre-flight validator must test what actually ships ----------

def test_validator_tests_a_jpeg_not_a_png():
    """scripts/validate_ig.py is the one check run before trusting the
    automation. It hardcoded a .png — the format Meta does not accept and
    the pipeline stopped sending. Worse than useless: the PNG is still in
    the bucket beside the JPEG, so the check would PASS and give false
    confidence about a path that never runs.
    """
    src = open(os.path.join(ROOT, "scripts", "validate_ig.py")).read()
    body = src[src.index("--container"):]
    assert "_ig.jpg" in body, "validator no longer tests the JPEG variant"
    assert "_ig.png" not in body, "validator still tests a PNG"


def test_validator_does_not_hardcode_a_topic_id():
    """A hardcoded topic breaks silently the day it stops being
    publishable — the validator would 404 on an asset that was never
    rendered, and read as a credentials problem."""
    src = open(os.path.join(ROOT, "scripts", "validate_ig.py")).read()
    assert "is_publishable" in src, (
        "the test asset should be derived from the calendar, not fixed")


def test_validator_prefers_an_asset_that_actually_shipped(tmp_path, monkeypatch):
    """The bucket holds exactly what past prepare runs uploaded, and S3
    answers 403 for a missing key — so a derived-but-never-rendered path
    reads as a credentials failure to whoever is setting up credentials.
    posted.jsonl is the ledger of what really reached the bucket; the
    validator must walk it (newest first) before guessing."""
    src = open(os.path.join(ROOT, "scripts", "validate_ig.py")).read()
    body = src[src.index("--container"):]
    assert "posted.jsonl" in body, "validator no longer consults the ledger"
    assert "reversed" in body, "must prefer the NEWEST shipped asset"
    # the calendar-derived guess must remain, as the fresh-clone fallback
    assert "is_publishable" in body


def test_validator_does_not_bless_an_expiring_token():
    """A 0-days token passes every chain check and still cannot run a
    daily cron. The operator hit exactly this: every check green, a
    'can post autonomously' verdict, and a token that died the same day.
    The verdict must gate on expiry, and exit non-zero."""
    src = open(os.path.join(ROOT, "scripts", "validate_ig.py")).read()
    verdict = src[src.index("token_days_left is not None"):]
    assert "sys.exit(1)" in verdict.split("All checks passed")[0], (
        "an expiring token must fail the verdict, not just warn")


# ---------- Meta's error body must survive ----------

def test_meta_errors_are_not_reduced_to_a_bare_status():
    """The first live Reels failure produced only:

        HTTPError: 400 Client Error: Bad Request for url: .../media

    Meta had said exactly what was wrong in the JSON body — message,
    error_user_msg, fbtrace_id — and raise_for_status() threw all of it
    away. A 400 that cannot distinguish a bad aspect ratio from an expired
    token is unactionable.
    """
    import post_ig
    src = open(os.path.join(ROOT, "engine", "post_ig.py")).read()
    # the CALL, not the docstring that explains why it is gone
    assert ".raise_for_status(" not in src, (
        "raise_for_status discards Meta's explanation — use _check()")
    assert hasattr(post_ig, "InstagramError")


def test_check_surfaces_message_and_trace():
    import post_ig

    class Resp:
        ok = False
        status_code = 400
        text = ""

        def json(self):
            return {"error": {"message": "Invalid aspect ratio",
                              "error_user_msg": "Try 9:16",
                              "fbtrace_id": "AbCd123"}}

    with pytest.raises(post_ig.InstagramError) as exc:
        post_ig._check(Resp(), "reel container creation")
    text = str(exc.value)
    assert "reel container creation" in text
    assert "Invalid aspect ratio" in text
    assert "Try 9:16" in text
    assert "AbCd123" in text


def test_check_falls_back_to_body_when_json_is_unparseable():
    import post_ig

    class Resp:
        ok = False
        status_code = 502
        text = "<html>gateway timeout</html>"

        def json(self):
            raise ValueError("not json")

    with pytest.raises(post_ig.InstagramError) as exc:
        post_ig._check(Resp(), "publish")
    assert "gateway timeout" in str(exc.value)


def test_check_passes_a_good_response_through():
    import post_ig

    class Resp:
        ok = True

    assert post_ig._check(Resp(), "x") is not None


# ---------- the container must be FINISHED before publish ----------
#
# Meta builds a container asynchronously and rejects media_publish until it
# says FINISHED. post_reel polled for this from the start; post_image never
# did — it created the container and published in the next call. That is a
# race, and images usually won it, so it read as working for nine live
# posts. On 2026-08-03 the card lost:
#
#   publish failed (400): Cannot Publish — The media is not ready for
#   publishing, please wait for a moment — Media ID is not available
#
# X had already posted, so the day went out half-published.

class _Calls(list):
    """The URLs POSTed, plus how many times the status was polled.

    Both halves are needed. "Did media_publish happen?" alone cannot tell a
    route that waited from one that never asked — with the pre-fix image
    route the publish still succeeds, it just succeeds blind.
    """
    polls = 0


def _fake_graph(monkeypatch, statuses):
    """Drive a post through a scripted sequence of container statuses."""
    posted = _Calls()
    seq = list(statuses)

    class Resp:
        ok = True
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._payload

    def fake_post(url, data=None, timeout=None):
        posted.append(url)
        return Resp({"id": "container-1" if url.endswith("/media")
                     else "media-1"})

    def fake_get(url, params=None, timeout=None):
        if "status_code" in (params or {}).get("fields", ""):
            posted.polls += 1
            nxt = seq.pop(0)
            return Resp(nxt if isinstance(nxt, dict) else {"status_code": nxt})
        return Resp({"permalink": "https://instagram.com/p/abc"})

    monkeypatch.setenv("IG_USER_ID", "1")
    monkeypatch.setenv("IG_ACCESS_TOKEN", "t")
    monkeypatch.setenv("MEDIA_BASE_URL", "https://cdn.test")
    monkeypatch.setattr(post_ig.requests, "post", fake_post)
    monkeypatch.setattr(post_ig.requests, "get", fake_get)
    monkeypatch.setattr(post_ig.time, "sleep", lambda s: None)
    return posted


def test_an_image_waits_for_a_container_that_is_not_ready_yet(monkeypatch):
    """The regression. IN_PROGRESS then FINISHED must publish HAVING
    WAITED — not fire media_publish at a container Meta has not built.

    The poll count is the assertion that bites: publishing succeeds either
    way against a fake, so a test that only checks the result passes just
    as happily against the code that caused the outage.
    """
    posted = _fake_graph(monkeypatch, ["IN_PROGRESS", "FINISHED"])
    result = post_ig.post_image("assets/cards/teaming_ig.jpg", "cap")
    assert result["id"] == "media-1"
    assert any(u.endswith("/media_publish") for u in posted)
    assert posted.polls == 2, \
        f"published after {posted.polls} status check(s) — it must wait"


def test_an_image_that_never_becomes_ready_is_not_published(monkeypatch):
    """Publishing a container whose status was never confirmed is how
    something unverified ships. The image route used to do exactly that,
    unconditionally, because it never asked."""
    posted = _fake_graph(monkeypatch,
                         ["IN_PROGRESS"] * post_ig.IMAGE_TRIES)
    with pytest.raises(post_ig.InstagramError, match="never reached FINISHED"):
        post_ig.post_image("assets/cards/teaming_ig.jpg", "cap")
    assert not any(u.endswith("/media_publish") for u in posted), \
        "published a container that never reported FINISHED"


def test_an_image_container_error_says_what_meta_said(monkeypatch):
    posted = _fake_graph(monkeypatch, [
        {"status_code": "ERROR", "status_error_message": "image is not JPEG"}])
    with pytest.raises(post_ig.InstagramError, match="image is not JPEG"):
        post_ig.post_image("assets/cards/teaming_ig.jpg", "cap")
    assert not any(u.endswith("/media_publish") for u in posted)


def test_a_reel_that_never_finishes_is_not_published(monkeypatch):
    """Was a source-text check for an `else:` in the poll loop. Now that
    both routes share one helper, ask the behaviour instead — the reel path
    must keep the guarantee it already had."""
    posted = _fake_graph(monkeypatch, ["IN_PROGRESS"] * post_ig.REEL_TRIES)
    with pytest.raises(post_ig.InstagramError, match="never reached FINISHED"):
        post_ig.post_reel("assets/video/t_ad.mp4", "cap")
    assert not any(u.endswith("/media_publish") for u in posted)


def test_a_reel_still_waits_for_its_transcode(monkeypatch):
    posted = _fake_graph(monkeypatch, ["IN_PROGRESS", "IN_PROGRESS",
                                       "FINISHED"])
    post_ig.post_reel("assets/video/t_ad.mp4", "cap")
    assert any(u.endswith("/media_publish") for u in posted)
    assert posted.polls == 3


def test_both_routes_wait_through_the_same_helper():
    """Two copies of this loop is how the image route ended up without one.
    A second copy would drift the same way."""
    src = open(os.path.join(ROOT, "engine", "post_ig.py")).read()
    assert src.count("def _await_ready") == 1
    assert src.count("_await_ready(") == 3, \
        "expected one definition and one call from each of the two routes"


def test_an_image_does_not_wait_as_long_as_a_transcode():
    """A card is one fetch; a reel is a transcode. Giving the image route
    the reel's ten minutes would hold the publish job open through an
    outage that a minute would have settled."""
    assert post_ig.IMAGE_TRIES * post_ig.IMAGE_DELAY < \
        post_ig.REEL_TRIES * post_ig.REEL_DELAY


def test_validator_can_exercise_the_reel_path():
    """The reel path reached production untested because --container only
    ever built an IMAGE container."""
    src = open(os.path.join(ROOT, "scripts", "validate_ig.py")).read()
    assert '"--reel"' in src
    assert "REELS" in src
    assert "fbtrace" in src, "the point is printing what Meta said"


def test_reel_mode_runs_without_container_mode(monkeypatch, tmp_path, capsys):
    """--reel alone crashed with UnboundLocalError: `base` was bound inside
    the --container branch, so the mode whose entire purpose was printing
    Meta's error could not reach a Meta call to ask.

    This EXECUTES the path with faked HTTP rather than inspecting the
    source. A first attempt at this test walked the AST for an assignment
    to `base` — and passed with the bug restored, because the assignment it
    found was the one inside the --container branch. Running the code is
    the only thing that proves the code runs.
    """
    import importlib.util
    import sys as _sys

    spec = importlib.util.spec_from_file_location(
        "validate_ig", os.path.join(ROOT, "scripts", "validate_ig.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    monkeypatch.setenv("IG_USER_ID", "1784100000000")
    monkeypatch.setenv("IG_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("MEDIA_BASE_URL", "https://cdn.test")
    monkeypatch.setattr(_sys, "argv", ["validate_ig.py", "--reel"])

    # MUST be logs/posted.jsonl — the path the script actually reads. An
    # earlier version of this fixture wrote it to the wrong place, so the
    # script fail()ed out before reaching the line under test and the
    # test passed against the BUG. The `except SystemExit` below is what
    # hid it.
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "posted.jsonl").write_text(
        json.dumps({"date": "2026-07-30", "topic": "t",
                    "media_ig": "assets/video/t.mp4"}) + "\n")
    monkeypatch.setattr(mod, "ROOT", str(tmp_path))

    # URL-aware fake: debug_token wants data as a DICT, the quota endpoint
    # wants it as a LIST. A single shape cannot satisfy both, which is why
    # the first attempt at this fake kept tripping on the next endpoint.
    class Resp:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200
            self.ok = True

        def json(self):
            return self._payload

    def fake_get(url, *a, **k):
        if "debug_token" in url:
            return Resp({"data": {"scopes": ["instagram_basic",
                                             "instagram_content_publish"],
                                  "type": "SYSTEM_USER"}})
        if "content_publishing_limit" in url:
            return Resp({"data": [{"quota_usage": 0}]})
        return Resp({"id": "1", "name": "t", "username": "t",
                     "followers_count": 0, "media_count": 0,
                     "status_code": "FINISHED"})

    monkeypatch.setattr(mod.requests, "get", fake_get)
    monkeypatch.setattr(mod.requests, "post",
                        lambda *a, **k: Resp({"id": "container-1"}))
    monkeypatch.setattr(mod.requests, "head", lambda *a, **k: Resp({}))

    try:
        mod.main()
    except UnboundLocalError as exc:            # the bug
        pytest.fail(f"--reel cannot run standalone: {exc}")
    except SystemExit as exc:
        # A clean exit is fine, but only AFTER the reel block ran. Exiting
        # from an earlier fail() would mean this test never touched the
        # line it exists to protect — which is how it once passed against
        # the bug.
        assert "[6]" in capsys.readouterr().out, (
            f"exited before the reel block ran ({exc}) — the test proves "
            f"nothing")


def test_the_happy_path_only_requests_the_documented_field():
    """status_code is the only DOCUMENTED container field. Asking for an
    unknown one can 400 the request, so the poll that runs on every
    success must not — a diagnostic nicety must never become an outage."""
    import post_ig

    asked = []

    class R:
        status_code = 200
        ok = True

        def json(self):
            return {"status_code": "FINISHED"}

    def spy(url, params=None, **k):
        asked.append(params["fields"])
        return R()

    original = post_ig.requests.get
    post_ig.requests.get = spy
    try:
        post_ig._await_ready("C1", "tok", 1, 0, "reel")
    finally:
        post_ig.requests.get = original
    assert asked == ["status_code"], f"hot path asked for {asked}"


def test_a_failed_detail_lookup_still_raises_a_usable_error(monkeypatch):
    """The detail call is best-effort. If it breaks, the caller must still
    learn the container errored — not get a different exception."""
    import post_ig

    class R:
        status_code = 200
        ok = True

        def json(self):
            return {"status_code": "ERROR", "id": "C1"}

    def get(url, params=None, **k):
        if params["fields"] == "status":
            raise RuntimeError("unknown field")
        return R()

    monkeypatch.setattr(post_ig.requests, "get", get)
    with pytest.raises(post_ig.InstagramError) as exc:
        post_ig._await_ready("C1", "tok", 1, 0, "reel")
    assert "processing failed" in str(exc.value)


def test_the_container_poll_requests_the_fields_the_error_path_reads():
    """The ERROR branch reached for status_error_message while the request
    asked only for status_code — so the field was always absent and a real
    failure (2026-08-17) printed a bare dict that could not distinguish a
    bad file from a transient transcode.

    Pins the request against the read: whatever the error path consumes
    must be asked for.
    """
    src = open(os.path.join(ROOT, "engine", "post_ig.py")).read()
    poll = src[src.index("def _await_ready"):src.index("def post_image")] \
        if "def post_image" in src[src.index("def _await_ready"):] \
        else src[src.index("def _await_ready"):]
    requested = re.search(r'"fields":\s*"([^"]+)"', poll)
    assert requested, "the poll no longer requests any fields"
    asked = set(requested.group(1).split(","))
    read = set(re.findall(r's\.get\("([a-z_]+)"\)', poll))
    read.discard("status_code")          # always requested
    missing = {f for f in read if f not in asked}
    assert not missing, (
        f"the error path reads fields the request never asks for: {missing}")


def test_a_reel_error_surfaces_something_other_than_the_raw_dict(monkeypatch):
    """A bare dict is not a diagnosis. When Meta supplies detail it must
    reach the log."""
    import post_ig

    class R:
        status_code = 200
        ok = True

        def json(self):
            return {"status_code": "ERROR", "id": "C1",
                    "status": "Error: media could not be processed"}

    monkeypatch.setattr(post_ig.requests, "get", lambda *a, **k: R())
    with pytest.raises(post_ig.InstagramError) as exc:
        post_ig._await_ready("C1", "tok", 1, 0, "reel")
    assert "media could not be processed" in str(exc.value)


# --- retrying a transient container failure --------------------------------
#
# 2026-08-17: a reel failed with `2207085`, outside Meta's documented
# 2207001-2207057 range, so no file-shaped complaint fired and nothing said
# whether the file or the transcode was at fault. The 2026-08-24 ad then
# published from the same renderer, and a fresh container for that file
# transcodes clean. One-off, not a defect — so re-create rather than lose
# the day's post.

class _Seq:
    """Drives post_ig through a scripted sequence of container verdicts."""

    def __init__(self, verdicts):
        self.verdicts = list(verdicts)
        self.creates = 0
        self.publishes = 0

    def post(self, url, data=None, timeout=None):
        outer = self

        class R:
            ok = True
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                if url.endswith("/media"):
                    outer.creates += 1
                    return {"id": f"c{outer.creates}"}
                outer.publishes += 1
                return {"id": "published-1"}

        return R()

    def get(self, url, params=None, timeout=None, headers=None):
        outer = self

        class R:
            ok = True
            status_code = 200

            def json(self):
                if (params or {}).get("fields") == "permalink":
                    return {"permalink": "https://instagram.test/p/x"}
                # one verdict per poll; the last repeats
                v = (outer.verdicts.pop(0) if len(outer.verdicts) > 1
                     else outer.verdicts[0])
                return {"status_code": v, "id": "c"}

        return R()


def _run(monkeypatch, verdicts):
    seq = _Seq(verdicts)
    monkeypatch.setenv("IG_USER_ID", "1")
    monkeypatch.setenv("IG_ACCESS_TOKEN", "t")
    monkeypatch.setenv("MEDIA_BASE_URL", "https://cdn.test")
    monkeypatch.setattr(post_ig.requests, "post", seq.post)
    monkeypatch.setattr(post_ig.requests, "get", seq.get)
    monkeypatch.setattr(post_ig.time, "sleep", lambda s: None)
    return seq


def test_an_error_container_is_recreated_and_can_still_publish(monkeypatch):
    seq = _run(monkeypatch, ["ERROR", "FINISHED"])
    post_ig.post_reel("assets/video/t_ad.mp4", "cap")
    assert seq.creates == 2, "the container was not re-created"
    assert seq.publishes == 1


def test_a_clean_run_creates_exactly_one_container(monkeypatch):
    """Negative control: the retry must not fire when nothing failed, or
    every post silently costs two containers."""
    seq = _run(monkeypatch, ["FINISHED"])
    post_ig.post_reel("assets/video/t_ad.mp4", "cap")
    assert seq.creates == 1 and seq.publishes == 1


def test_retries_are_bounded(monkeypatch):
    """Each poll runs up to ten minutes and the publish job has thirty,
    which also has to cover X."""
    seq = _run(monkeypatch, ["ERROR"])
    with pytest.raises(post_ig.InstagramError):
        post_ig.post_reel("assets/video/t_ad.mp4", "cap")
    assert seq.creates == post_ig.REEL_ATTEMPTS
    assert seq.publishes == 0, "published despite never succeeding"


def test_a_container_that_never_finishes_is_not_retried(monkeypatch):
    """A stalled container is a DIFFERENT failure with a different cost: it
    burns the full poll before telling us anything, and two of those do not
    fit the job budget. This is why the ERROR verdict has its own exception
    type rather than being matched on message text."""
    monkeypatch.setattr(post_ig, "REEL_TRIES", 2)
    monkeypatch.setattr(post_ig, "REEL_DELAY", 0)
    seq = _run(monkeypatch, ["IN_PROGRESS"])
    with pytest.raises(post_ig.InstagramError):
        post_ig.post_reel("assets/video/t_ad.mp4", "cap")
    assert seq.creates == 1, "a stalled container was retried"


def test_a_rejected_creation_is_not_retried(monkeypatch):
    """A 4xx on creation is about credentials, permissions or the payload.
    Re-sending an identical request cannot fix any of them, and hammering
    Meta with it is how an account gets attention it does not want."""
    calls = {"n": 0}

    class Bad:
        ok = False
        status_code = 400

        def json(self):
            return {"error": {"message": "Invalid OAuth access token"}}
        text = "bad"

    def bad_post(url, data=None, timeout=None):
        calls["n"] += 1
        return Bad()

    monkeypatch.setenv("IG_USER_ID", "1")
    monkeypatch.setenv("IG_ACCESS_TOKEN", "t")
    monkeypatch.setenv("MEDIA_BASE_URL", "https://cdn.test")
    monkeypatch.setattr(post_ig.requests, "post", bad_post)
    monkeypatch.setattr(post_ig.time, "sleep", lambda s: None)
    with pytest.raises(post_ig.InstagramError):
        post_ig.post_reel("assets/video/t_ad.mp4", "cap")
    assert calls["n"] == 1


def test_the_error_verdict_has_its_own_type(monkeypatch):
    """The retry catches ContainerProcessingError specifically. If ERROR
    ever went back to a bare InstagramError, the retry would silently stop
    firing — and the tests above would still pass on the happy path."""
    assert issubclass(post_ig.ContainerProcessingError, post_ig.InstagramError)
    monkeypatch.setattr(post_ig, "REEL_ATTEMPTS", 1)
    seq = _run(monkeypatch, ["ERROR"])
    with pytest.raises(post_ig.ContainerProcessingError):
        post_ig.post_reel("assets/video/t_ad.mp4", "cap")
