"""Ask the old and the new Worker the same questions and list every answer that differs.

    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/parity.py --old URL --new URL

The questions are the public GET routes, each asked about a sample drawn with a fixed seed from the
local copies: crops and corpus glyphs from `out/atlas.sqlite`, runs from `out/atlas-runs.sqlite`, and
pages, documents, characters and image keys from the `source-*.sqlite` copies. The old Worker is asked
by a crop's text id, the new one by its public id (`--new-ids text` asks both by the text id).

Before comparing, every text id in either answer becomes its public id through `out/mirror.sqlite`, the
crop number written in Crockford base 32 as `atlas3.ts` writes it. Headers that change between any two
answers (`VOLATILE`) are left out, and so are those the edge cache rewrites when only one answer is a
cache hit (`EDGE`). An image is compared by the SHA-256 of its bytes. A field listed in
`MOMENTS` (the time an answer was made) or `CHANGED` may differ; any other difference is unexplained.

Requests to both hosts together keep to `--rate` per second. The report goes to
`out/parity-<stamp>.txt`, and the run exits 1 on any unexplained difference.
"""

from __future__ import annotations

import argparse
import datetime
import fnmatch
import hashlib
import json
import os
import random
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(os.environ.get("ATLAS3_DIR", "work/atlas-v3"))
OUT = ROOT / "out"
DIGITS = "0123456789abcdefghjkmnpqrstvwxyz"
PAGE = 2**24
SEED = 20261020
# Headers whose value differs between two answers to one request, or that only describe the transfer.
VOLATILE = {"date", "report-to", "nel", "cf-ray", "server-timing", "age", "alt-svc", "server",
            "content-length", "transfer-encoding", "connection", "etag", "last-modified", "expires"}
# Headers Cloudflare's edge cache sets on a hit (it adds accept-ranges and rewrites cache-control to the
# zone's browser TTL); compared only when both answers came from the edge cache, or neither did.
EDGE = {"accept-ranges", "cache-control"}
# Body fields that hold the time an answer was made, as `route field` patterns (fnmatch).
MOMENTS = {"/api/ranking at"}
# Fields the new Worker answers differently on purpose, as `route field` patterns (fnmatch), each with
# its reason.
CHANGED: dict[str, str] = {}
SHOWN = 5  # examples listed per difference
MAX_DIFFS = 50  # differences kept per answer


def public_id(number: int) -> str:
    out = ""
    while True:
        out = DIGITS[number % 32] + out
        number //= 32
        if number == 0:
            return out


def code_point(text: str) -> str:
    return "-".join(f"U+{ord(c):04X}" for c in text)


@dataclass
class Case:
    route: str
    old: str
    new: str
    binary: bool = False


@dataclass
class Answer:
    status: int
    headers: dict[str, str]
    body: bytes


def connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def sample(db: sqlite3.Connection, table: str, column: str, n: int, rng: random.Random, where: str = "1") -> list:
    """`n` distinct values at seeded offsets; a table has only so many."""
    total = db.execute(f"SELECT count(*) FROM {table} WHERE {where}").fetchone()[0]
    offsets = sorted(rng.sample(range(total), min(n, total)))
    return [db.execute(f"SELECT {column} FROM {table} WHERE {where} LIMIT 1 OFFSET ?", (o,)).fetchone()[0]
            for o in offsets]


def corpus(per_route: int, images: int, seed: int, new_ids: str) -> list[Case]:
    rng = random.Random(seed)
    atlas, runs, mirror = connect(OUT / "atlas.sqlite"), connect(OUT / "atlas-runs.sqlite"), connect(OUT / "mirror.sqlite")

    def text_of(number: int) -> str:
        found = mirror.execute("SELECT text FROM ids WHERE id = ?", (number,)).fetchone()
        if not found:
            raise SystemExit(f"crop {number} has no text id in the mirror")
        return found[0]

    def ids(number: int) -> tuple[str, str]:
        text = text_of(number)
        return text, (public_id(number) if new_ids == "public" else text)

    def char(number: int) -> str | None:
        found = atlas.execute("SELECT g.text FROM crops c JOIN graphemes g ON g.id = c.char WHERE c.id = ?",
                              (number,)).fetchone()
        return found[0] if found else None

    q = urllib.parse.quote
    cases: list[Case] = []

    def both(route: str, old: str, new: str | None = None) -> None:
        cases.append(Case(route, old, old if new is None else new))

    for path in ["/health", "/layers/summary", "/atlas/collection/status", "/atlas/dates/stats", "/atlas/similar/model",
                 "/api/ranking", "/atlas/ledger", "/atlas/reviews",
                 "/atlas/corpus/reviews?state=flagged", "/layers/graphemes?limit=50", "/layers/ligatures?limit=50",
                 "/api/favourites", "/api/favourites/crops", "/api/account", "/atlas/history",
                 "/atlas?purpose=browse&limit=24", "/atlas?purpose=review&state=pending&production=all&limit=24",
                 "/atlas/ngrams/2?limit=20", "/atlas/ngrams/3?limit=20", "/atlas/corpus/characters",
                 "/atlas/forms/families", "/atlas/forms/codes"]:
        both(path, path)

    crops = sample(atlas, "crops", "id", per_route, rng, "kind = 0")
    glyphs = sample(atlas, "crops", "id", per_route, rng, "kind = 1")
    for number in crops + glyphs:
        old, new = ids(number)
        for suffix in ["", "/line", "/versions", "/similar"]:
            both(f"/atlas/characters/{{id}}{suffix}", f"/atlas/characters/{q(old, safe='')}{suffix}",
                 f"/atlas/characters/{q(new, safe='')}{suffix}")
        both("/atlas/cite/crop/{id}", f"/atlas/cite/crop/{q(old, safe='')}", f"/atlas/cite/crop/{q(new, safe='')}")
        both("/atlas/claims?subject", f"/atlas/claims?subject={q(old, safe='')}", f"/atlas/claims?subject={q(new, safe='')}")
    for number in glyphs:
        old, new = ids(number)
        both("/atlas/corpus/character?id", f"/atlas/corpus/character?id={q(old, safe='')}",
             f"/atlas/corpus/character?id={q(new, safe='')}")

    letters = sorted({c for c in (char(n) for n in crops + glyphs) if c})[:per_route * 2]
    for text in rng.sample(letters, min(per_route, len(letters))):
        point = code_point(text)
        both("/layers/characters/{cp}", f"/layers/characters/{q(point, safe='')}?expand=none")
        both("/layers/occurrences?code_point", f"/layers/occurrences?code_point={q(point)}&limit=24")
        both("/layers/candidates?code_point", f"/layers/candidates?code_point={q(point)}&limit=24")
        both("/layers/chronology?code_point", f"/layers/chronology?code_point={q(point)}")
        both("/layers/decades?code_point", f"/layers/decades?code_point={q(point)}")
        both("/layers/structure?c", f"/layers/structure?c={q(text)}")
        both("/layers/suggest?q", f"/layers/suggest?q={q(text)}")
        both("/layers/search?q", f"/layers/search?q={q(text)}")
        both("/atlas?grapheme", f"/atlas?grapheme={q(point)}&limit=24")
        both("/atlas/history?label", f"/atlas/history?label={q(text)}")
        both("/atlas/cite/grapheme/{cp}", f"/atlas/cite/grapheme/{q(point, safe='')}")

    for pair in sample(runs, "runs", "first || ' ' || second", per_route, rng, "size = 2"):
        first, second = map(int, pair.split())
        text = (char(first) or "") + (char(second) or "")
        if len(text) < 2:
            continue
        both("/atlas/runs?text", f"/atlas/runs?text={q(text)}")
        both("/atlas/runs/related?text", f"/atlas/runs/related?text={q(text)}")

    documents = sample(connect(ROOT / "source-corpus_documents.sqlite"), "corpus_documents", "document", per_route, rng)
    documents += sample(atlas, "books", "key", per_route, rng)
    for document in documents:
        both("/atlas/ngrams/2?document", f"/atlas/ngrams/2?document={q(document)}&limit=20")
        both("/atlas?document", f"/atlas?document={q(document)}&limit=24")
    # Only the documents another site shows character by character have this listing.
    listed = ROOT / "source-document_characters.sqlite"
    if listed.exists():
        for document in sample(connect(listed), "(SELECT DISTINCT document FROM document_characters)", "document",
                               per_route, rng):
            both("/atlas/documents/{doc}/characters", f"/atlas/documents/{q(document, safe='')}/characters")
    else:
        print(f"no {listed.name}: /atlas/documents/{{doc}}/characters is not asked", flush=True)
    for page in sample(connect(ROOT / "source-pages.sqlite"), "pages", "id", per_route, rng):
        both("/atlas/pages/{id}", f"/atlas/pages/{q(page, safe='')}")
    for key in sample(connect(ROOT / "source-media.sqlite"), "media", "key", images, rng):
        cases.append(Case("/atlas/media/{key}.webp", f"/atlas/media/{key}.webp", f"/atlas/media/{key}.webp", True))
    return cases


class Limiter:
    def __init__(self, rate: float):
        self.gap, self.last = 1 / rate, 0.0

    def wait(self) -> None:
        delay = self.last + self.gap - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self.last = time.monotonic()


class Stay(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # a redirect is an answer to compare
        return None


OPENER = urllib.request.build_opener(Stay)


def fetch(base: str, path: str, limiter: Limiter, tries: int = 3) -> Answer:
    for attempt in range(tries):
        limiter.wait()
        request = urllib.request.Request(base.rstrip("/") + path, headers={"user-agent": "glyph-atlas-parity",
                                                                           "accept-encoding": "identity"})
        try:
            with OPENER.open(request, timeout=60) as response:
                answer = Answer(response.status, dict(response.headers.items()), response.read())
        except urllib.error.HTTPError as error:
            answer = Answer(error.code, dict(error.headers.items()), error.read())
        except OSError as error:  # a reset or a timeout is status 0, and is listed if it lasts
            answer = Answer(0, {}, str(error).encode())
        if answer.status not in (0, 429, 503) or attempt == tries - 1:
            return answer
        time.sleep(min(float(answer.headers.get("retry-after", "2") or 2), 10))
    raise AssertionError("unreachable")


class Ids:
    """Text ids to public ids, looked up in the mirror once each."""

    def __init__(self, mirror: sqlite3.Connection):
        self.mirror, self.known = mirror, {}

    def __call__(self, value: str) -> str:
        if value not in self.known:
            found = self.mirror.execute("SELECT id FROM ids WHERE text = ?", (value,)).fetchone()
            self.known[value] = public_id(found[0]) if found else None
        return self.known[value] or value

    def normal(self, value):
        if isinstance(value, dict):
            return {key: self.normal(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.normal(item) for item in value]
        if isinstance(value, str) and ":" in value:
            if "/" in value:
                return "/".join(urllib.parse.quote(self(urllib.parse.unquote(part)), safe=":")
                                if ":" in urllib.parse.unquote(part) else part for part in value.split("/"))
            return self(value)
        return value


def differences(old, new, path: str = "") -> list[tuple[str, object, object]]:
    if isinstance(old, dict) and isinstance(new, dict):
        found = []
        for key in list(old) + [k for k in new if k not in old]:
            where = f"{path}.{key}" if path else key
            if key not in new:
                found.append((where, old[key], "(absent)"))
            elif key not in old:
                found.append((where, "(absent)", new[key]))
            else:
                found += differences(old[key], new[key], where)
        return found
    if isinstance(old, list) and isinstance(new, list):
        found = [(f"{path}[]", f"{len(old)} items", f"{len(new)} items")] if len(old) != len(new) else []
        for a, b in zip(old, new):
            found += differences(a, b, f"{path}[]")
        return found
    return [] if old == new else [(path or "(body)", old, new)]


def compare(case: Case, old: Answer, new: Answer, ids: Ids) -> list[tuple[str, object, object]]:
    found: list[tuple[str, object, object]] = []
    if old.status != new.status:
        found.append(("(status)", old.status, new.status))
    heads = [{k.lower(): v for k, v in a.headers.items() if k.lower() not in VOLATILE} for a in (old, new)]
    hits = [h.pop("cf-cache-status", None) == "HIT" for h in heads]
    if hits[0] != hits[1]:
        heads = [{k: v for k, v in h.items() if k not in EDGE} for h in heads]
    for name in sorted(set(heads[0]) | set(heads[1])):
        a, b = (ids.normal(h.get(name, "(absent)")) for h in heads)
        if a != b:
            found.append((f"(header {name})", a, b))
    if case.binary or "json" not in heads[0].get("content-type", "") + heads[1].get("content-type", ""):
        a, b = (hashlib.sha256(x.body).hexdigest() for x in (old, new))
        if a != b:
            found.append(("(bytes sha256)", a, b))
        return found
    try:
        bodies = [ids.normal(json.loads(x.body)) for x in (old, new)]
    except ValueError:
        return found + ([("(body)", "not JSON", "not JSON")] if old.body != new.body else [])
    return found + differences(*bodies)


def explained(route: str, where: str) -> str | None:
    if any(fnmatch.fnmatchcase(f"{route} {where}", pattern) for pattern in MOMENTS):
        return "time of answering"
    for pattern, reason in CHANGED.items():
        if fnmatch.fnmatchcase(f"{route} {where}", pattern):
            return reason
    return None


def short(value) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= 120 else text[:117] + "..."


@dataclass
class Tally:
    cases: int = 0
    differing: int = 0
    statuses: dict[int, int] = field(default_factory=dict)
    diffs: dict[str, list[str]] = field(default_factory=dict)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--old", required=True, help="base URL of the Worker on the old layout")
    parser.add_argument("--new", required=True, help="base URL of the Worker on the new layout")
    parser.add_argument("--per-route", type=int, default=5, help="samples for each route that takes one")
    parser.add_argument("--images", type=int, default=10, help="image keys compared by hash")
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--rate", type=float, default=2.0, help="requests per second, both hosts together")
    parser.add_argument("--new-ids", choices=["public", "text"], default="public",
                        help="how the new Worker is asked for a crop")
    args = parser.parse_args()

    cases = corpus(args.per_route, args.images, args.seed, args.new_ids)
    print(f"{len(cases)} cases, {2 * len(cases)} requests, about {2 * len(cases) / args.rate / 60:.0f} min", flush=True)
    ids, limiter = Ids(connect(OUT / "mirror.sqlite")), Limiter(args.rate)
    routes: dict[str, Tally] = {}
    unexplained = explained_count = 0
    for number, case in enumerate(cases, 1):
        old, new = fetch(args.old, case.old, limiter), fetch(args.new, case.new, limiter)
        tally = routes.setdefault(case.route, Tally())
        tally.cases += 1
        tally.statuses[old.status] = tally.statuses.get(old.status, 0) + 1
        found = compare(case, old, new, ids)[:MAX_DIFFS]
        open_ = [d for d in found if not explained(case.route, d[0])]
        explained_count += len(found) - len(open_)
        if open_:
            tally.differing += 1
            unexplained += len(open_)
            for where, a, b in open_:
                tally.diffs.setdefault(where, []).append(f"{case.old} | old {short(a)} | new {short(b)}")
        if number % 20 == 0:
            print(f"{number}/{len(cases)}", flush=True)

    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%S")
    lines = [f"parity {stamp}: old {args.old}, new {args.new}, seed {args.seed}, new ids {args.new_ids}",
             (f"{len(cases)} cases on {len(routes)} routes; {sum(t.differing for t in routes.values())} differ "
              f"({unexplained} unexplained differences, {explained_count} explained)"), "",
              "route\tcases\tdiffer\told statuses"]
    lines += [f"{route}\t{t.cases}\t{t.differing}\t{' '.join(f'{k}x{v}' for k, v in sorted(t.statuses.items()))}"
              for route, t in routes.items()]
    for route, tally in routes.items():
        for where, examples in tally.diffs.items():
            lines += ["", f"{route} {where}: {len(examples)} answers"] + [f"  {e}" for e in examples[:SHOWN]]
    if CHANGED:
        lines += ["", "explained:"] + [f"  {p}: {r}" for p, r in CHANGED.items()]
    report = OUT / f"parity-{stamp}.txt"
    report.write_text("\n".join(lines) + "\n")
    print("\n".join(lines[:2]))
    print(f"report: {report}")
    sys.exit(1 if unexplained else 0)


if __name__ == "__main__":
    main()
