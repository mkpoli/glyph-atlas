"""List the members of a remote zip over HTTP range requests. A command, not a test: it reaches the
network.

    uv run python scripts/check_remotezip.py https://data.lab.hi.u-tokyo.ac.jp/kuzushiji/2023-03-27/all.zip

The listing costs three requests: a HEAD for the size and the ETag, one range request for the last
64 KiB and one more for the central directory when it does not lie inside that tail. The listing is
left in `cache/zipindex/`, so a second run reuses it and costs one request; `--refresh` lists the
archive again. `--read NAME` prints one member.

Requests wait the project's minimum interval for the host: three seconds, one for CODH and Hugging
Face.
"""

from __future__ import annotations

import argparse
import sys
import time

import httpx

from kuzushiji_atlas.remotezip import USER_AGENT, RemoteZip

PAUSE = 3.0
SHORT_PAUSE = 1.0
SHORT_HOSTS = ("codh.rois.ac.jp", "huggingface.co")


def pause_for(url: str) -> float:
    host = (httpx.URL(url).host or "").lower()
    if host in SHORT_HOSTS or host.endswith(".huggingface.co"):
        return SHORT_PAUSE
    return PAUSE


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url", help="the remote zip")
    parser.add_argument("--read", metavar="NAME", help="print the bytes of one member after listing")
    parser.add_argument("--refresh", action="store_true", help="ignore a cached listing")
    options = parser.parse_args(argv)

    pause = pause_for(options.url)
    last = 0.0

    def throttle(request: httpx.Request) -> None:
        nonlocal last
        wait = pause - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
        last = time.monotonic()

    client = httpx.Client(
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        timeout=httpx.Timeout(30.0, read=120.0),
        event_hooks={"request": [throttle]},
    )
    with client, RemoteZip(options.url, client=client, use_cache=not options.refresh) as archive:
        print(f"url        {archive.url}")
        print(f"size       {archive.size} bytes")
        print(f"etag       {archive.etag}")
        print(f"entries    {len(archive.entries)}")
        print(f"directory  {archive.central_directory_size} bytes")
        print(f"requests   {archive.requests_made()}")
        if options.read:
            data = archive.read(options.read)
            print(f"read       {options.read}: {len(data)} bytes, {archive.requests_made()} requests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
