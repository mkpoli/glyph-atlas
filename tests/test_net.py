"""Tests for the polite downloader.

Every request goes to the `http_server` fixture. The clock and the sleeper are replaced for the whole
module, so a pause or a backoff is asserted from the fake clock instead of being waited out.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from conftest import Scripted
from conftest import TestServer as Server
from PIL import Image

from glyph_atlas import net

PAYLOAD = bytes(range(256)) * 8  # 2048 bytes
HTML = b"<!DOCTYPE html>\n<html><body>the file is not here</body></html>"
RESUMED = 700  # bytes of a partial download


class Clock:
    """A monotonic clock that moves only when the code under test sleeps."""

    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    """Stand in for `time.monotonic` and `time.sleep`, and forget the host pauses of other tests."""
    clock = Clock()
    monkeypatch.setattr(net, "CLOCK", clock)
    monkeypatch.setattr(net, "SLEEP", clock.sleep)
    net.reset_pauses()
    yield clock
    net.reset_pauses()


def headers_seen(server: Server) -> list[dict[str, str]]:
    """Record the headers of every request the fixture server answers."""
    seen: list[dict[str, str]] = []
    original = server.RequestHandlerClass

    class Recording(original):
        def _respond(self, body_only_status: int = 200) -> None:
            seen.append({key.lower(): value for key, value in self.headers.items()})
            super()._respond(body_only_status)

        def do_HEAD(self) -> None:
            seen.append({key.lower(): value for key, value in self.headers.items()})
            super().do_HEAD()

    server.RequestHandlerClass = Recording
    return seen


def jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 6), (10, 20, 30)).save(buffer, format="JPEG")
    return buffer.getvalue()


def test_host_pause_follows_the_readme() -> None:
    assert net.host_pause("https://codh.rois.ac.jp/char-shape/iiif/1/2.tif/info.json") == 1.0
    assert net.host_pause("codh.rois.ac.jp") == 1.0
    assert net.host_pause("https://huggingface.co/datasets/yuta1984/honkoku-lines") == 1.0
    assert net.host_pause("https://cdn-lfs.huggingface.co/x") == 1.0
    assert net.host_pause("https://dl.ndl.go.jp/api/iiif/1/manifest.json") == 3.0
    assert net.host_pause("https://example.org/file.bin") == 3.0


def test_download_writes_the_body(http_server: Server, tmp_path: Path, clock: Clock) -> None:
    http_server.put("data/file.bin", PAYLOAD)

    dest = net.download(http_server.url("data/file.bin"), tmp_path / "file.bin")

    assert dest.read_bytes() == PAYLOAD
    assert http_server.requests == ["GET /data/file.bin"]
    assert clock.slept == []  # the first request to a host waits for nothing


def test_requests_carry_the_project_user_agent(http_server: Server, tmp_path: Path) -> None:
    http_server.put("file.bin", PAYLOAD)
    seen = headers_seen(http_server)

    net.download(http_server.url("file.bin"), tmp_path / "file.bin")

    assert seen[0]["user-agent"] == net.USER_AGENT


def test_an_existing_destination_is_kept(http_server: Server, tmp_path: Path) -> None:
    http_server.put("file.bin", PAYLOAD)
    dest = tmp_path / "file.bin"
    dest.write_bytes(b"already here")

    net.download(http_server.url("file.bin"), dest)
    assert dest.read_bytes() == b"already here"
    assert http_server.requests == []

    net.download(http_server.url("file.bin"), dest, refresh=True)
    assert dest.read_bytes() == PAYLOAD
    assert http_server.requests == ["GET /file.bin"]


def test_pause_is_held_between_requests_to_one_host(
    http_server: Server, tmp_path: Path, clock: Clock
) -> None:
    http_server.put("a.bin", b"a")
    http_server.put("b.bin", b"b")

    net.download(http_server.url("a.bin"), tmp_path / "a.bin", pause=3.0)
    assert clock.slept == []

    net.download(http_server.url("b.bin"), tmp_path / "b.bin", pause=3.0)
    assert clock.slept == [pytest.approx(3.0)]


def test_no_pause_once_the_interval_has_passed(
    http_server: Server, tmp_path: Path, clock: Clock
) -> None:
    http_server.put("a.bin", b"a")
    http_server.put("b.bin", b"b")

    net.download(http_server.url("a.bin"), tmp_path / "a.bin", pause=3.0)
    clock.now += 5.0
    net.download(http_server.url("b.bin"), tmp_path / "b.bin", pause=3.0)

    assert clock.slept == []


def test_429_is_retried_after_retry_after(
    http_server: Server, tmp_path: Path, clock: Clock
) -> None:
    http_server.put("file.bin", PAYLOAD)
    http_server.script["/file.bin"] = [Scripted(429, headers={"Retry-After": "0"})]

    dest = net.download(http_server.url("file.bin"), tmp_path / "file.bin", pause=0)

    assert dest.read_bytes() == PAYLOAD
    assert http_server.requests == ["GET /file.bin", "GET /file.bin"]
    assert clock.slept == []


def test_503_waits_for_the_retry_after_header(
    http_server: Server, tmp_path: Path, clock: Clock
) -> None:
    http_server.put("file.bin", PAYLOAD)
    http_server.script["/file.bin"] = [Scripted(503, headers={"Retry-After": "2"})]

    net.download(http_server.url("file.bin"), tmp_path / "file.bin", pause=0)

    assert clock.slept == [pytest.approx(2.0)]


def test_a_5xx_without_retry_after_backs_off_exponentially(
    http_server: Server, tmp_path: Path, clock: Clock
) -> None:
    http_server.put("file.bin", PAYLOAD)
    http_server.script["/file.bin"] = [Scripted(500), Scripted(502), Scripted(503)]

    net.download(http_server.url("file.bin"), tmp_path / "file.bin", pause=0)

    assert clock.slept == [pytest.approx(1.0), pytest.approx(2.0), pytest.approx(4.0)]


def test_gives_up_after_the_retry_budget(
    http_server: Server, tmp_path: Path, clock: Clock
) -> None:
    http_server.put("file.bin", PAYLOAD)
    http_server.script["/file.bin"] = [Scripted(429, headers={"Retry-After": "0"}) for _ in range(5)]
    dest = tmp_path / "file.bin"

    with pytest.raises(net.DownloadError) as failure:
        net.download(http_server.url("file.bin"), dest, retries=5, pause=0)

    assert "gave up after 5 attempts" in str(failure.value)
    assert len(http_server.requests) == 5
    assert not dest.exists()


def test_a_partial_download_resumes_with_range(
    http_server: Server, tmp_path: Path
) -> None:
    http_server.put("big.bin", PAYLOAD)
    dest = tmp_path / "big.bin"
    part = tmp_path / "big.bin.part"
    part.write_bytes(PAYLOAD[:RESUMED])
    seen = headers_seen(http_server)

    net.download(http_server.url("big.bin"), dest, pause=0)

    assert dest.read_bytes() == PAYLOAD
    assert http_server.requests == ["GET /big.bin"]
    assert seen[0]["range"] == f"bytes={RESUMED}-"
    assert not part.exists()


def test_a_server_that_ignores_range_is_written_from_the_start(
    http_server: Server, tmp_path: Path
) -> None:
    http_server.put("big.bin", PAYLOAD)
    http_server.ignore_ranges = True
    dest = tmp_path / "big.bin"
    (tmp_path / "big.bin.part").write_bytes(PAYLOAD[:RESUMED])
    seen = headers_seen(http_server)

    net.download(http_server.url("big.bin"), dest, pause=0)

    assert seen[0]["range"] == f"bytes={RESUMED}-"
    assert dest.read_bytes() == PAYLOAD  # not the partial file with the whole body appended


def test_redirects_are_followed(http_server: Server, tmp_path: Path) -> None:
    http_server.put("real.bin", PAYLOAD)
    http_server.script["/moved.bin"] = [Scripted(302, headers={"Location": http_server.url("real.bin")})]

    dest = net.download(http_server.url("moved.bin"), tmp_path / "moved.bin", pause=0)

    assert dest.read_bytes() == PAYLOAD
    assert http_server.requests == ["GET /moved.bin", "GET /real.bin"]


def test_a_missing_file_names_the_url(http_server: Server, tmp_path: Path) -> None:
    url = http_server.url("absent.bin")

    with pytest.raises(net.DownloadError) as failure:
        net.download(url, tmp_path / "absent.bin", retries=2, pause=0)

    assert url in str(failure.value)
    assert "404" in str(failure.value)
    assert http_server.requests == ["GET /absent.bin"]


@pytest.mark.parametrize("kind", ["json", "image", "zip", "text"])
def test_an_html_body_is_a_failure(http_server: Server, tmp_path: Path, kind: str) -> None:
    http_server.script["/page"] = [Scripted(200, HTML, {"Content-Type": "text/html"})]
    url = http_server.url("page")

    with pytest.raises(net.DownloadError) as failure:
        net.download(url, tmp_path / "page", expected=kind, pause=0)

    assert url in str(failure.value)
    assert "HTML" in str(failure.value)
    assert not (tmp_path / "page").exists()
    assert not (tmp_path / "page.part").exists()  # a body of the wrong kind is no base for a resume


def test_an_html_body_behind_an_image_content_type_is_a_failure(
    http_server: Server, tmp_path: Path
) -> None:
    http_server.script["/page.jpg"] = [Scripted(200, HTML, {"Content-Type": "image/jpeg"})]

    with pytest.raises(net.DownloadError, match="HTML"):
        net.download(http_server.url("page.jpg"), tmp_path / "page.jpg", expected="image", pause=0)


def test_json_and_image_bodies_pass(http_server: Server, tmp_path: Path) -> None:
    http_server.put("info.json", b'{"width": 8, "height": 6}')
    http_server.put("page.jpg", jpeg())

    assert net.download(http_server.url("info.json"), tmp_path / "info.json", expected="json").exists()
    assert net.download(http_server.url("page.jpg"), tmp_path / "page.jpg", expected="image").exists()


def test_a_json_that_does_not_parse_is_a_failure(http_server: Server, tmp_path: Path) -> None:
    http_server.put("info.json", b'{"width": ')

    with pytest.raises(net.DownloadError, match="does not parse"):
        net.download(http_server.url("info.json"), tmp_path / "info.json", expected="json", pause=0)


def test_a_listed_host_gets_the_browser_user_agent_once(
    http_server: Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(net, "BROWSER_UA_HOSTS", frozenset({"127.0.0.1"}))
    http_server.put("file.bin", PAYLOAD)
    http_server.script["/file.bin"] = [Scripted(403)]
    seen = headers_seen(http_server)

    dest = net.download(http_server.url("file.bin"), tmp_path / "file.bin", pause=0)

    assert dest.read_bytes() == PAYLOAD
    assert [item["user-agent"] for item in seen] == [net.USER_AGENT, net.BROWSER_USER_AGENT]
    assert http_server.requests == ["GET /file.bin", "GET /file.bin"]


def test_a_refusal_from_another_host_is_final(http_server: Server, tmp_path: Path) -> None:
    http_server.script["/file.bin"] = [Scripted(403)]

    with pytest.raises(net.DownloadError, match="403"):
        net.download(http_server.url("file.bin"), tmp_path / "file.bin", retries=2, pause=0)

    assert http_server.requests == ["GET /file.bin"]


def test_meta_reports_the_response(http_server: Server, tmp_path: Path) -> None:
    http_server.put("file.bin", PAYLOAD)
    meta: dict = {}

    net.download(http_server.url("file.bin"), tmp_path / "file.bin", meta=meta, pause=0)

    assert meta["status"] == 200
    assert meta["content_type"] == "application/octet-stream"
    assert meta["content_length"] == len(PAYLOAD)
    assert meta["etag"] == f'"{len(PAYLOAD)}"'
    assert meta["last_modified"] is None


def test_arguments_are_checked(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="retries"):
        net.download("https://example.org/x", tmp_path / "x", retries=0)
    with pytest.raises(ValueError, match="expected"):
        net.download("https://example.org/x", tmp_path / "x", expected="audio")
    with pytest.raises(net.DownloadError, match="http"):
        net.download("example.org/x.bin", tmp_path / "x")


def test_a_429_slows_the_host_down_for_the_rest_of_the_process(http_server):
    """A host that answers 429 keeps a longer interval instead of being asked at the same rate."""
    from glyph_atlas import net

    net.reset_pauses()
    try:
        body = b"payload"
        http_server.put("data.json", body)
        url = http_server.url("data.json")
        http_server.script["/data.json"] = [net_test_scripted(429)]

        assert net.host_pause(url) == net.DEFAULT_PAUSE
        dest = tmp_path_file(http_server.root, "out.json")
        assert net.download(url, dest, pause=0.0) == dest
        assert net.host_pause(url) == net.SLOW_PAUSE
        assert dest.read_bytes() == body
    finally:
        net.reset_pauses()


def net_test_scripted(status: int):
    """A canned response for the fixture's scripted sequence."""
    return Scripted(status=status, body=b"", headers={"Retry-After": "0"})


def tmp_path_file(root, name):
    from pathlib import Path

    return Path(root) / name
