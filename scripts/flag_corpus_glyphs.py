"""Send the corpus glyphs `atlas repair relabel` left for review to the site's Flagged view.

A relabel keeps the label of a box whose realigned character disagrees with a form a person decided
(`box_relabel.relabel`, status `review`); a republication lists those glyphs in a `review-ids.json`
(id, label, realigned, form). This sends them to the Worker's `/api/admin/corpus-flags`, at most 96 a
request, as an admin. The Worker writes each glyph's `units` row as a round would, flagged with a
wrong-character issue and the finding beside it, and leaves a glyph that already has a row as it is,
so running the list again writes nothing. Each request's answer is printed as a JSON line.

    python scripts/flag_corpus_glyphs.py REVIEW-IDS.json --dry-run
    GLYPH_ATLAS_COOKIE='…' python scripts/flag_corpus_glyphs.py REVIEW-IDS.json --site https://glyphatlas.org

The cookie is an admin's signed-in session, as the browser sends it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

#: The Worker's limit on glyphs a request (`FLAG_MAX`).
BATCH = 96
FINDING = "box-relabel"


def glyph(entry: dict) -> dict:
    """What the Worker needs of one listed glyph. The form a person decided is the character the site shows
    for it, so the finding holds while the site still shows that character."""
    form = entry.get("form")
    if not form or form.startswith("U+"):
        raise ValueError(f"{entry['id']}: the decided form is not one character ({form!r}), so the shown character is unknown")
    return {"id": entry["id"], "character": form, "realigned": entry.get("realigned"), "transcribed": entry.get("label")}


def batches(entries: list[dict], size: int = BATCH) -> list[dict]:
    glyphs = [glyph(entry) for entry in entries]
    ids = [g["id"] for g in glyphs]
    if len(set(ids)) != len(ids):
        raise ValueError("a glyph is listed twice")
    return [{"finding": FINDING, "glyphs": glyphs[start:start + size]} for start in range(0, len(glyphs), size)]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect would carry the session cookie to wherever it points, so none is followed."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, f"refused a redirect to {newurl}", headers, fp)


OPENER = urllib.request.build_opener(NoRedirect)


def send(site: str, cookie: str, batch: dict) -> dict:
    if not site.startswith("https://"):
        raise ValueError(f"{site}: the session cookie is only sent over https")
    request = urllib.request.Request(f"{site}/api/admin/corpus-flags", data=json.dumps(batch, ensure_ascii=False).encode(),
                                     headers={"content-type": "application/json", "origin": site, "cookie": cookie}, method="POST")
    try:
        with OPENER.open(request, timeout=120) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        sys.exit(f"{error.code} from {site}: {error.read().decode(errors='replace')[:500]}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("review_ids", type=Path)
    parser.add_argument("--site", default="https://glyphatlas.org")
    parser.add_argument("--dry-run", action="store_true", help="print the requests without sending them")
    args = parser.parse_args(argv)
    planned = batches(json.loads(args.review_ids.read_text()))
    if args.dry_run:
        for batch in planned:
            print(json.dumps(batch, ensure_ascii=False))
        return
    cookie = os.environ.get("GLYPH_ATLAS_COOKIE")
    if not cookie:
        sys.exit("GLYPH_ATLAS_COOKIE holds no session")
    for batch in planned:
        print(json.dumps(send(args.site.rstrip("/"), cookie, batch), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
