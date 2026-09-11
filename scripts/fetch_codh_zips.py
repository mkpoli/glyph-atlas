"""Download the per-book zips of the 日本古典籍くずし字データセット into `cache/codh/`.

The book list is `data/sources/codh-books.tsv`. Requests wait 1 second apart, a partial file is
resumed, and a file whose size equals the server's `Content-Length` is left alone, so the command
can be rerun after an interruption. This is a cache filler: `atlas import codh` downloads what it
needs through `kuzushiji_atlas.net` and does not require this script.
"""

from __future__ import annotations

import argparse
import csv
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
URL = "https://codh.rois.ac.jp/char-shape/dataset/v2/{bid}.zip"
USER_AGENT = "kuzushiji-atlas (+https://github.com/mkpoli/kuzushiji-atlas)"
PAUSE = 1.0


def books(limit: int | None = None, only: list[str] | None = None) -> list[str]:
    rows = [
        line.split("\t")
        for line in (ROOT / "data" / "sources" / "codh-books.tsv").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    bids = [row[0] for row in rows]
    if only:
        bids = [bid for bid in bids if bid in only]
    return bids[:limit] if limit else bids


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "cache" / "codh")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--books", default=None, help="comma-separated bids")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    wanted = books(args.limit, args.books.split(",") if args.books else None)
    total = 0
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=120, follow_redirects=True) as client:
        for index, bid in enumerate(wanted, 1):
            target = args.out / f"{bid}.zip"
            head = client.head(URL.format(bid=bid))
            size = int(head.headers.get("content-length", 0))
            if target.exists() and target.stat().st_size == size:
                print(f"[{index}/{len(wanted)}] {bid} cached ({size} bytes)", flush=True)
                total += size
                continue
            headers = {"Range": f"bytes={target.stat().st_size}-"} if target.exists() else {}
            time.sleep(PAUSE)
            with client.stream("GET", URL.format(bid=bid), headers=headers) as response:
                response.raise_for_status()
                mode = "ab" if response.status_code == 206 else "wb"
                with target.open(mode) as handle:
                    for chunk in response.iter_bytes(1 << 20):
                        handle.write(chunk)
            print(f"[{index}/{len(wanted)}] {bid} {target.stat().st_size} bytes", flush=True)
            total += target.stat().st_size
    print(f"{len(wanted)} books, {total} bytes under {args.out}")
    with (ROOT / "work" / "codh-downloads.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["bid", "bytes"])
        for bid in wanted:
            writer.writerow([bid, (args.out / f"{bid}.zip").stat().st_size])


if __name__ == "__main__":
    main()
