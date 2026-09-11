"""Downloads that are polite to the host and reused from disk afterwards.

Every request carries the project User-Agent. A host whose front end filters on that header gets the
same request once more with a browser User-Agent, which is why the fallback is kept behind a short
host list: a second request against a server that answers the first one is wasted traffic.

Requests to one host are spaced by `host_pause`, held across calls in this process and measured
against a monotonic clock (`CLOCK`, or the `clock` argument), so a test can assert the pause without
sleeping. A 429 or 503 is retried after `Retry-After`, a transport error or a 5xx after an
exponential backoff, and at most `retries` attempts are made.

An interrupted download leaves `<dest>.part`. The next call resumes it with a `Range` request when
the server answers 206; a server that ignores `Range` and sends the whole body again is detected by
the status code, and the file is written from the start.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

USER_AGENT = "kuzushiji-atlas (+https://github.com/mkpoli/kuzushiji-atlas)"
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

DEFAULT_PAUSE = 3.0
FAST_PAUSE = 1.0
# Hosts the README gives a shorter pause: CODH and Hugging Face. A subdomain is included.
FAST_HOSTS = ("codh.rois.ac.jp", "huggingface.co")

# Hosts whose front end is an application rather than a file server, and which have refused the
# project User-Agent: 国立国会図書館デジタルコレクション (dl.ndl.go.jp), 国書データベース
# (kokusho.nijl.ac.jp) and 史料編纂所 SHIPS (wwwap.hi.u-tokyo.ac.jp). A refusal (403, 406 or 451) on
# one of them is answered with the browser User-Agent once. The list stays short because the fallback
# costs a request, and a host leaves it when the project User-Agent is served.
BROWSER_UA_HOSTS = frozenset(
    {
        "dl.ndl.go.jp",
        "www.dl.ndl.go.jp",
        "kokusho.nijl.ac.jp",
        "wwwap.hi.u-tokyo.ac.jp",
    }
)

REFUSAL_STATUS = frozenset({403, 406, 451})
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
KINDS = frozenset({"json", "image", "zip", "text"})
PART_SUFFIX = ".part"
CHUNK = 1 << 20
SNIFF = 1024
BACKOFF = 1.0
MAX_BACKOFF = 60.0
MAX_RETRY_AFTER = 300.0

#: Monotonic clock and sleeper used by `download`; tests replace them.
CLOCK: Callable[[], float] = time.monotonic
SLEEP: Callable[[float], None] = time.sleep

# Time of the last request per host, with the clock that measured it, so that a clock injected by one
# caller cannot be compared against a time taken by another.
_LAST_REQUEST: dict[str, tuple[float, object]] = {}

IMAGE_SIGNATURES = (
    b"\xff\xd8\xff",  # JPEG
    b"\x89PNG\r\n\x1a\n",  # PNG
    b"GIF87a",
    b"GIF89a",
    b"II*\x00",  # TIFF, little endian
    b"MM\x00*",  # TIFF, big endian
    b"BM",  # BMP
    b"\x00\x00\x00\x0cjP  ",  # JPEG 2000
)
ZIP_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
HTML_RE = re.compile(rb"^\s*(?:<!doctype\s+html|<html)", re.IGNORECASE)


class DownloadError(RuntimeError):
    """A download that did not finish."""


class _Retry(Exception):
    """A failed attempt that is worth repeating."""

    def __init__(self, delay: float, reason: str) -> None:
        super().__init__(reason)
        self.delay = delay
        self.reason = reason


def host_pause(url: str) -> float:
    """The least interval in seconds between two requests to the host of `url`.

    A host name may be given instead of a URL. The default is 3 s; `codh.rois.ac.jp` and any
    `*.huggingface.co` host get 1 s.
    """
    host = _host_of(url)
    for entry in FAST_HOSTS:
        if host == entry or host.endswith(f".{entry}"):
            return FAST_PAUSE
    return DEFAULT_PAUSE


def reset_pauses() -> None:
    """Forget which host was asked last. Tests call this between cases."""
    _LAST_REQUEST.clear()


def download(
    url: str,
    dest: Path,
    *,
    pause: float | None = None,
    retries: int = 5,
    expected: str | None = None,
    client: httpx.Client | None = None,
    refresh: bool = False,
    clock: Callable[[], float] | None = None,
    sleeper: Callable[[float], None] | None = None,
    timeout: float = 30.0,
    meta: dict[str, Any] | None = None,
) -> Path:
    """Fetch `url` into `dest` and return `dest`.

    `pause` is the least interval between two requests to the same host, held across calls in this
    process; the default comes from `host_pause`. `retries` counts attempts, not retries after the
    first, and the last failure is raised as `DownloadError`. `expected` names the content kind
    ("json", "image", "zip" or "text"): a response that does not look like that kind, an HTML body in
    particular, fails and names the URL. A `dest` that already exists is returned untouched unless
    `refresh` is set. An interrupted download leaves `<dest>.part` and the next call resumes it when
    the server answers a `Range` request with 206. `clock` and `sleeper` stand in for `time.monotonic`
    and `time.sleep`, and the module-level `CLOCK` and `SLEEP` do the same for every caller. `meta`,
    when given, takes the status line and headers of the request that succeeded.
    """
    dest = Path(dest)
    if dest.exists() and not refresh:
        return dest
    if retries < 1:
        raise ValueError(f"retries counts attempts and must be at least 1, not {retries!r}")
    if expected is not None and expected not in KINDS:
        raise ValueError(f"expected is one of {sorted(KINDS)}, not {expected!r}")
    if urlsplit(url).scheme not in ("http", "https"):
        raise DownloadError(f"{url}: not an http(s) URL")

    interval = host_pause(url) if pause is None else float(pause)
    now = clock if clock is not None else CLOCK
    sleep = sleeper if sleeper is not None else SLEEP
    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=timeout, follow_redirects=True)

    part = dest.with_name(dest.name + PART_SUFFIX)
    dest.parent.mkdir(parents=True, exist_ok=True)
    browser_agent = False
    attempt = 0
    failure = "no attempt was made"
    try:
        while True:
            attempt += 1
            _wait_for_turn(url, interval, now, sleep)
            start = part.stat().st_size if part.exists() else 0
            headers = {"Accept": "*/*"}
            if start:
                headers["Range"] = f"bytes={start}-"
            agents = (BROWSER_USER_AGENT,) if browser_agent else _user_agents(url)
            try:
                response, agent = _get(
                    client, url, headers=headers, agents=agents, interval=interval, clock=now, sleeper=sleep
                )
            except httpx.HTTPError as exc:
                failure = f"{exc.__class__.__name__}: {exc}"
                delay = _backoff(attempt)
            else:
                browser_agent = browser_agent or agent == BROWSER_USER_AGENT
                try:
                    headers_seen = _receive(
                        response, url=url, part=part, start=start, attempt=attempt, expected=expected
                    )
                except _Retry as retry:
                    failure = retry.reason
                    delay = retry.delay
                else:
                    os.replace(part, dest)
                    if meta is not None:
                        meta.update(headers_seen)
                    return dest
                finally:
                    response.close()
            if attempt >= retries:
                raise DownloadError(f"{url}: gave up after {attempt} attempts ({failure})")
            if delay > 0:
                sleep(delay)
    finally:
        if own_client:
            client.close()


def _get(
    client: httpx.Client,
    url: str,
    *,
    headers: dict[str, str],
    agents: tuple[str, ...],
    interval: float,
    clock: Callable[[], float],
    sleeper: Callable[[float], None],
) -> tuple[httpx.Response, str]:
    """Send one GET, asking again with the next User-Agent while the host refuses the previous one."""
    for index, agent in enumerate(agents):
        sent = dict(headers)
        sent["User-Agent"] = agent
        response = client.send(
            client.build_request("GET", url, headers=sent), stream=True, follow_redirects=True
        )
        if index + 1 < len(agents) and response.status_code in REFUSAL_STATUS:
            _drain(response)
            response.close()
            _wait_for_turn(url, interval, clock, sleeper)
            continue
        return response, agent
    raise AssertionError("agents is never empty")


def _drain(response: httpx.Response, limit: int = 64 * 1024) -> None:
    """Read a small body of a response that is not being kept, so the connection closes cleanly."""
    read = 0
    for chunk in response.iter_bytes(CHUNK):
        read += len(chunk)
        if read >= limit:
            break


def _receive(
    response: httpx.Response,
    *,
    url: str,
    part: Path,
    start: int,
    attempt: int,
    expected: str | None,
) -> dict[str, Any]:
    """Write the body of `response` to `part` and return what the response said about it."""
    status = response.status_code
    if status in RETRY_STATUS:
        _drain(response)
        raise _Retry(_retry_delay(response, attempt), f"HTTP {status}")
    if status == 416:
        _drain(response)
        part.unlink(missing_ok=True)
        raise _Retry(_backoff(attempt), "HTTP 416, the partial file is longer than the resource")
    if 300 <= status < 400:
        location = response.headers.get("location", "?")
        raise DownloadError(f"{url}: HTTP {status} to {location} was not followed")
    if status >= 400:
        _drain(response)
        raise DownloadError(f"{url}: HTTP {status} {response.reason_phrase}")

    # A 206 appends to the partial file; anything else is the whole body, whatever was asked for.
    resumed = start > 0 and status == 206
    head = b""
    with part.open("ab" if resumed else "wb") as handle:
        for chunk in response.iter_bytes(CHUNK):
            if not head:
                head = chunk[:SNIFF]
            handle.write(chunk)
    size = part.stat().st_size
    total = _content_range_total(response.headers.get("content-range"))
    if status == 206 and total is not None and size != total:
        raise _Retry(0.0, f"incomplete response, {size} of {total} bytes")

    if expected is not None:
        try:
            _check_content(
                expected,
                part=part,
                head=head,
                content_type=response.headers.get("content-type", ""),
                url=url,
                resumed=resumed,
            )
        except DownloadError:
            part.unlink(missing_ok=True)  # a body of the wrong kind is no base for a resume
            raise
    return {
        "status": status,
        "etag": response.headers.get("etag"),
        "last_modified": response.headers.get("last-modified"),
        "content_type": response.headers.get("content-type"),
        "content_length": size,
        "url": str(response.url),
    }


def _check_content(
    kind: str, *, part: Path, head: bytes, content_type: str, url: str, resumed: bool
) -> None:
    """Fail when the body is not of the kind the caller asked for.

    `head` holds the first bytes of this response, which is the middle of the file after a resume, so
    only the content type is checked then.
    """
    media = content_type.split(";", 1)[0].strip().lower()
    if media == "text/html" or (not resumed and HTML_RE.match(head)):
        raise DownloadError(f"{url}: expected {kind}, the server sent an HTML page")
    if kind == "json":
        # A JSON document is small and is the one kind that can be checked as a whole; a resume makes
        # the first bytes of the response useless for the check.
        try:
            json.loads(part.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise DownloadError(f"{url}: expected json, the body does not parse ({exc})") from exc
    elif resumed:
        return
    elif kind == "image" and not _looks_like_image(head) and not media.startswith("image/"):
        raise DownloadError(f"{url}: expected an image, got {media or 'no content type'}")
    elif kind == "zip" and not head.startswith(ZIP_SIGNATURES) and "zip" not in media:
        raise DownloadError(f"{url}: expected a zip, got {media or 'no content type'}")


def _looks_like_image(head: bytes) -> bool:
    if head.startswith(IMAGE_SIGNATURES):
        return True
    return head[:4] == b"RIFF" and head[8:12] == b"WEBP"


def _wait_for_turn(
    url: str, pause: float, clock: Callable[[], float], sleeper: Callable[[float], None]
) -> None:
    """Hold the per-host interval, counting from the previous request measured by the same clock."""
    host = _host_of(url)
    now = clock()
    last = _LAST_REQUEST.get(host)
    if last is not None and last[1] is clock:
        remaining = pause - (now - last[0])
        if remaining > 0:
            sleeper(remaining)
            now = clock()
    _LAST_REQUEST[host] = (now, clock)


def _user_agents(url: str) -> tuple[str, ...]:
    """The project User-Agent, and the browser one where a refusal has been seen."""
    host = _host_of(url)
    for entry in BROWSER_UA_HOSTS:
        if host == entry or host.endswith(f".{entry}"):
            return (USER_AGENT, BROWSER_USER_AGENT)
    return (USER_AGENT,)


def _host_of(value: str) -> str:
    if "//" in value:
        return (urlsplit(value).hostname or "").lower()
    return value.split("/", 1)[0].split(":", 1)[0].lower()  # a bare host name


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    seconds = _parse_retry_after(response.headers.get("retry-after"))
    return _backoff(attempt) if seconds is None else min(seconds, MAX_RETRY_AFTER)


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value.strip()))
    except ValueError:
        pass
    with suppress(TypeError, ValueError):
        when = parsedate_to_datetime(value)
        if when is not None:
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
            return max(0.0, (when - datetime.now(UTC)).total_seconds())
    return None


def _backoff(attempt: int) -> float:
    return min(BACKOFF * 2 ** (attempt - 1), MAX_BACKOFF)


def _content_range_total(value: str | None) -> int | None:
    if not value:
        return None
    total = value.rpartition("/")[2].strip()
    return int(total) if total.isdigit() else None
