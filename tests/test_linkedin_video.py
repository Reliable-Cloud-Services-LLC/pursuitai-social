"""LinkedIn's Videos API — the `ad` format's route to the channel.

`ad` is 1 post in 4. Before this, every LinkedIn post went through
post_image, whose check_image accepts only jpg/jpeg/png/gif — so a quarter
of the calendar could not reach LinkedIn at all, and would have failed at
publish time with a legible error and no post.

The Videos API is a DIFFERENT API from Images, not a variant: multipart
upload with a per-part ETag, an explicit finalize step keyed on those ETags
in order, and a transcode that fails with a stated reason. Each of those is
a way to silently corrupt or lose a video, so each is tested.

The sharpest test here is test_the_uploaded_parts_reassemble_to_the_file:
a part sliced to the wrong length produces a video that uploads cleanly,
finalizes cleanly, and is broken.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import post_linkedin as li  # noqa: E402

OWNER = "urn:li:organization:123"
VIDEO_URN = "urn:li:video:C5505AQH-oV1qvnFtKA"


@pytest.fixture(autouse=True)
def no_sleeping(monkeypatch):
    monkeypatch.setattr(li.time, "sleep", lambda *_: None)


def make_mp4(tmp_path, size):
    p = tmp_path / "pipeline_ad.mp4"
    p.write_bytes(bytes(range(256)) * (size // 256) + b"\0" * (size % 256))
    return p


class FakeResponse:
    def __init__(self, payload=None, headers=None, status=200, ok=True):
        self._payload, self.headers = payload or {}, headers or {}
        self.status_code, self.ok, self.text = status, ok, ""

    def json(self):
        return self._payload


class FakeLinkedIn:
    """Records every call and answers like the documented API."""

    def __init__(self, parts, etag_style="unquoted", statuses=("AVAILABLE",),
                 upload_token="tok-1", fail_reason=None):
        self.parts, self.etag_style = parts, etag_style
        self.statuses, self.upload_token = list(statuses), upload_token
        self.fail_reason = fail_reason
        self.puts, self.posts, self.gets = [], [], []

    # -- the three POSTs --
    def post(self, url, headers=None, data=None, timeout=None):
        body = json.loads(data)
        self.posts.append((url, body, headers))
        if "initializeUpload" in url:
            return FakeResponse({"value": {
                "video": VIDEO_URN, "uploadToken": self.upload_token,
                "uploadInstructions": [
                    {"uploadUrl": f"https://up.test/{i}",
                     "firstByte": a, "lastByte": b}
                    for i, (a, b) in enumerate(self.parts)]}})
        if "finalizeUpload" in url:
            return FakeResponse({})
        return FakeResponse({}, headers={"x-restli-id": "urn:li:share:999"},
                            status=201)

    def put(self, url, headers=None, data=None, timeout=None):
        self.puts.append({"url": url, "body": data, "headers": headers})
        n = len(self.puts)
        raw = f"/ambry-video/signedId/AQ{n}.bin"
        tag = {"unquoted": raw, "quoted": f'"{"a" * 8}{n}"',
               "missing": None}[self.etag_style]
        return FakeResponse(headers={} if tag is None else {"etag": tag})

    def get(self, url, headers=None, params=None, timeout=None):
        self.gets.append(url)
        status = (self.statuses.pop(0) if len(self.statuses) > 1
                  else self.statuses[0])
        payload = {"status": status}
        if status == "PROCESSING_FAILED" and self.fail_reason:
            payload["processingFailureReason"] = self.fail_reason
        return FakeResponse(payload)


def run_upload(monkeypatch, tmp_path, fake, size=300_000):
    path = make_mp4(tmp_path, size)
    monkeypatch.setattr(li, "requests", fake)
    monkeypatch.setattr(li, "_abs", lambda rel: str(path))
    return li.upload_video("assets/video/pipeline_ad.mp4", "tok", OWNER), path


# ---------- the correctness property ----------

@pytest.mark.parametrize("parts,size", [
    ([(0, 299_999)], 300_000),                                  # single part
    ([(0, 4_194_303), (4_194_304, 5_000_000)], 5_000_001),      # two, uneven
    ([(0, 99), (100, 199), (200, 299_999)], 300_000),           # odd ranges
], ids=["one-part", "two-parts", "odd-ranges"])
def test_the_uploaded_parts_reassemble_to_the_file(monkeypatch, tmp_path,
                                                   parts, size):
    """THE test. A part sliced to the wrong length uploads cleanly,
    finalizes cleanly, and yields a corrupt video — there is no error to
    read. The docs invite exactly that: they say to `split -b 4194303`
    while describing parts as 0-4194303 INCLUSIVE, which is 4194304 bytes.
    Slicing from the instruction ranges is what makes this hold.
    """
    fake = FakeLinkedIn(parts)
    _, path = run_upload(monkeypatch, tmp_path, fake, size=size)
    assert b"".join(p["body"] for p in fake.puts) == path.read_bytes()
    assert len(fake.puts) == len(parts)


def test_each_part_is_the_length_its_range_declares(monkeypatch, tmp_path):
    parts = [(0, 4_194_303), (4_194_304, 5_000_000)]
    fake = FakeLinkedIn(parts)
    run_upload(monkeypatch, tmp_path, fake, size=5_000_001)
    for (first, last), sent in zip(parts, fake.puts):
        assert len(sent["body"]) == last - first + 1


# ---------- finalize ----------

def test_finalize_sends_the_part_ids_in_instruction_order(monkeypatch,
                                                          tmp_path):
    """"The order needs to be the same as the order of parts in the upload
    instructions." Out of order, finalize fails or the video is scrambled."""
    fake = FakeLinkedIn([(0, 99), (100, 199), (200, 299_999)])
    run_upload(monkeypatch, tmp_path, fake)
    body = next(b for u, b, _ in fake.posts if "finalizeUpload" in u)
    ids = body["finalizeUploadRequest"]["uploadedPartIds"]
    assert ids == ["/ambry-video/signedId/AQ1.bin",
                   "/ambry-video/signedId/AQ2.bin",
                   "/ambry-video/signedId/AQ3.bin"]


@pytest.mark.parametrize("style,expected", [
    ("unquoted", "/ambry-video/signedId/AQ1.bin"),
    ("quoted", "aaaaaaaa1"),
])
def test_both_documented_etag_forms_work(monkeypatch, tmp_path, style,
                                          expected):
    """The docs show the ETag two ways — a quoted hex digest in the schema
    table and an unquoted path in the live sample. Sending a quoted id where
    an unquoted one was meant fails finalize with nothing useful to read."""
    fake = FakeLinkedIn([(0, 299_999)], etag_style=style)
    run_upload(monkeypatch, tmp_path, fake)
    body = next(b for u, b, _ in fake.posts if "finalizeUpload" in u)
    assert body["finalizeUploadRequest"]["uploadedPartIds"] == [expected]


def test_a_part_with_no_etag_raises_rather_than_finalizing(monkeypatch,
                                                            tmp_path):
    """Negative control. Finalizing with a missing id would register an
    upload whose parts cannot be named — a failure with no error to read."""
    fake = FakeLinkedIn([(0, 299_999)], etag_style="missing")
    with pytest.raises(li.LinkedInError, match="no ETag"):
        run_upload(monkeypatch, tmp_path, fake)
    assert not any("finalizeUpload" in u for u, _, _ in fake.posts)


@pytest.mark.parametrize("token", ["tok-1", ""])
def test_the_upload_token_is_echoed_back(monkeypatch, tmp_path, token):
    """LinkedIn's own single-part sample returns an EMPTY uploadToken, so
    its absence is not an error — but it still has to be sent."""
    fake = FakeLinkedIn([(0, 299_999)], upload_token=token)
    run_upload(monkeypatch, tmp_path, fake)
    body = next(b for u, b, _ in fake.posts if "finalizeUpload" in u)
    assert body["finalizeUploadRequest"]["uploadToken"] == token


def test_initialize_declares_the_real_file_size(monkeypatch, tmp_path):
    """fileSizeBytes "determines how many parts the video upload will be
    broken into" — a wrong value yields instructions that do not cover the
    file."""
    fake = FakeLinkedIn([(0, 299_999)])
    _, path = run_upload(monkeypatch, tmp_path, fake)
    body = next(b for u, b, _ in fake.posts if "initializeUpload" in u)
    req = body["initializeUploadRequest"]
    assert req["fileSizeBytes"] == path.stat().st_size
    assert req["owner"] == OWNER


# ---------- waiting for AVAILABLE ----------

def test_it_waits_for_the_transcode(monkeypatch, tmp_path):
    fake = FakeLinkedIn([(0, 299_999)],
                        statuses=("PROCESSING", "PROCESSING", "AVAILABLE"))
    urn, _ = run_upload(monkeypatch, tmp_path, fake)
    assert urn == VIDEO_URN
    assert len(fake.gets) == 3


def test_a_failed_transcode_reports_linkedins_reason(monkeypatch, tmp_path):
    """The image path has no equivalent of processingFailureReason. Losing
    it would turn a stated cause into "processing failed"."""
    fake = FakeLinkedIn([(0, 299_999)], statuses=("PROCESSING_FAILED",),
                        fail_reason="UNSUPPORTED_AUDIO_CODEC")
    with pytest.raises(li.LinkedInError, match="UNSUPPORTED_AUDIO_CODEC"):
        run_upload(monkeypatch, tmp_path, fake)


def test_the_status_url_encodes_the_urn(monkeypatch, tmp_path):
    """The documented GET form is urn%3Ali%3Avideo%3A..."""
    fake = FakeLinkedIn([(0, 299_999)])
    run_upload(monkeypatch, tmp_path, fake)
    assert "urn%3Ali%3Avideo%3A" in fake.gets[0]


def test_it_never_returns_while_still_processing(monkeypatch, tmp_path):
    """Negative control on the whole point. The Posts API accepts a video
    that is still processing, and the result is a post members cannot see."""
    fake = FakeLinkedIn([(0, 299_999)], statuses=("PROCESSING",))
    monkeypatch.setattr(li, "VIDEO_TRIES", 3)
    with pytest.raises(li.LinkedInError, match="never became AVAILABLE"):
        run_upload(monkeypatch, tmp_path, fake)


# ---------- local refusals ----------

def test_a_non_mp4_is_refused(tmp_path):
    p = tmp_path / "card.png"
    p.write_bytes(b"x" * 200_000)
    with pytest.raises(li.LinkedInError, match="Videos API supports"):
        li.check_video(str(p))


@pytest.mark.parametrize("size", [1024, li.MAX_VIDEO_BYTES + 1])
def test_a_video_outside_the_documented_range_is_refused(tmp_path, size):
    """75kb-500MB. Refusing locally beats discovering it after a multipart
    upload has already been spent."""
    p = make_mp4(tmp_path, size) if size < 10_000_000 else tmp_path / "big.mp4"
    if size > 10_000_000:
        p.write_bytes(b"")
        os.truncate(p, size)
    with pytest.raises(li.LinkedInError, match="documented range"):
        li.check_video(str(p))


def test_a_normal_ad_passes(tmp_path):
    assert li.check_video(str(make_mp4(tmp_path, 2_000_000))) == 2_000_000


# ---------- dispatch ----------

@pytest.mark.parametrize("rel,expected", [
    ("assets/video/pipeline_ad.mp4", True),
    ("assets/video/PIPELINE_AD.MP4", True),
    ("assets/cards/pipeline_x.png", False),
    ("assets/cards/pipeline_x.jpg", False),
    (None, False),
])
def test_is_video(rel, expected):
    assert li.is_video(rel) is expected


def test_post_media_routes_a_video_to_the_videos_api(monkeypatch):
    seen = {}
    monkeypatch.setattr(li, "post_video",
                        lambda rel, c, title=None: seen.update(
                            kind="video", rel=rel, title=title) or {})
    monkeypatch.setattr(li, "post_image",
                        lambda rel, c, alt_text=None: seen.update(
                            kind="image") or {})
    li.post_media("assets/video/pipeline_ad.mp4", "copy", alt_text="pipeline")
    assert seen["kind"] == "video"
    assert seen["title"] == "pipeline", \
        "video content carries `title`; alt_text must not be dropped"


def test_post_media_routes_an_image_to_the_images_api(monkeypatch):
    seen = {}
    monkeypatch.setattr(li, "post_video",
                        lambda *a, **k: seen.update(kind="video") or {})
    monkeypatch.setattr(li, "post_image",
                        lambda rel, c, alt_text=None: seen.update(
                            kind="image", alt=alt_text) or {})
    li.post_media("assets/cards/x_x.png", "copy", alt_text="x card")
    assert seen["kind"] == "image" and seen["alt"] == "x card"


def test_the_engine_posts_through_the_dispatcher():
    """Negative control on the regression this fixes: run.py called
    post_image directly, so the `ad` format — 1 post in 4 — hit
    check_image's jpg/png allow-list and could never reach LinkedIn."""
    src = open(os.path.join(ROOT, "engine", "run.py")).read()
    block = src[src.index("def _post_linkedin("):]
    block = block[:block.index("\nPOSTERS")]
    # Comment lines only EXPLAIN the old call; the explanation necessarily
    # names it. Assert against live code, not prose.
    live = "\n".join(l for l in block.splitlines()
                      if not l.strip().startswith("#"))
    assert "post_media" in live
    assert "post_image" not in live
