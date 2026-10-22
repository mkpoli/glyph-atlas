"""Download the site's review export, a page at a time, into one `atlas-character-reviews` file.

    python scripts/fetch_reviews.py work/form-reviews/reviews.json [--site https://glyphatlas.org]

`/atlas/reviews.json` answers up to 200 reviews a page, oldest first, with `next` naming where the
following page starts (`?after=`). The file written holds them all, as the tools that read the export
(`refine_feedback.py`, `atlas forms audit`, `atlas review consensus-relabel`) expect.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx

from glyph_atlas.review.cloudflare_import import merge_review_pages

USER_AGENT = "glyph-atlas (+https://github.com/mkpoli/glyph-atlas)"


def pages(client: httpx.Client, site: str):
    after = None
    while True:
        response = client.get(f"{site}/atlas/reviews.json", params={"after": after} if after else None)
        response.raise_for_status()
        page = response.json()
        yield page
        after = page.get("next")
        if not after:
            return


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("out", type=Path)
    parser.add_argument("--site", default="https://glyphatlas.org")
    args = parser.parse_args()
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=120) as client:
        export = merge_review_pages(list(pages(client, args.site.rstrip("/"))))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(export, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(export['reviews'])} reviews -> {args.out}")


if __name__ == "__main__":
    main()
