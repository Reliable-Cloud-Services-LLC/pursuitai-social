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


def test_a_reel_that_never_finishes_is_not_published():
    """The poll loop had no else-branch: after 40 tries it fell through and
    published a container that never reached FINISHED — shipping something
    whose transcode was never confirmed."""
    src = open(os.path.join(ROOT, "engine", "post_ig.py")).read()
    loop = src[src.index("for _ in range(40)"):src.index("def _publish")]
    assert "else:" in loop, "no terminal branch — an unfinished reel publishes"
    assert "never reached FINISHED" in loop


def test_validator_can_exercise_the_reel_path():
    """The reel path reached production untested because --container only
    ever built an IMAGE container."""
    src = open(os.path.join(ROOT, "scripts", "validate_ig.py")).read()
    assert '"--reel"' in src
    assert "REELS" in src
    assert "fbtrace" in src, "the point is printing what Meta said"
