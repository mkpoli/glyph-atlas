"""Turn an `export_cloudflare.py` output into a publication `publish_cloudflare.sh` can upload and apply.

    python scripts/prepare_publication.py EXPORT OUTPUT --prefix ar: --prefix hk: \\
        [--live LIVE.jsonl] [--extra retire.sql ...] [--status]

1. The export's catalogue is copied, and every image row is checked against the pack it points into.
   An export stopped mid-write leaves rows past the end of its last pack; the crops using them are
   left out here and deleted from the export's own catalogue, so `export_cloudflare.py --resume`
   cuts them again. They are listed in OUTPUT/lost.json.
2. Only units whose id starts with one of `--prefix` are kept, and the copy is sealed.
3. The units the site already holds are read from D1 (or from `--live`, one JSON object per unit
   with id, revision, quiz, data, reviewed) and planned by `refresh_published_units.plan`: new crops
   are inserted, changed ones updated in place or replaced, a reviewed crop whose crop changed is
   held and listed in OUTPUT/held.json. Only the image rows and packs of those crops are published;
   the rest are already on the site.
4. The SQL parts hold, in order: image rows, new units, the refresh, each `--extra` file whole, and
   with `--status` the collection status row, and last the row the Worker keys its cached listings
   on (`units_refreshed_at`), so they change once the rest has. Each part stays under D1's upload size and every
   statement under its statement limit; `publication.json` lists the parts.

Nothing is uploaded or written to D1 here: reading the live units is the only request.
"""
from __future__ import annotations

import argparse
import importlib.util
import itertools
import json
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from cloudflare_schema import schema
from seal_cloudflare import seal

_spec = importlib.util.spec_from_file_location("refresh", ROOT / "scripts" / "refresh_published_units.py")
refresh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(refresh)

PART_BYTES = 45 * 1024 * 1024
STATEMENT_BYTES = 95 * 1024
MEDIA_KEY = re.compile(r'^INSERT OR REPLACE INTO "media"[^(]*\(\'([0-9a-f]+)\'')
UNIT_ID = re.compile(r'^INSERT OR IGNORE INTO "units"[^(]*\(\'((?:[^\']|\'\')*)\'')
ID_CHARS = "0123456789abcdefghijklmnopqrstuvwxyz"


def key_of(url) -> str:
    return str(url or "").rsplit("/", 1)[-1].removesuffix(".webp")


def snapshot(export: Path, output: Path) -> Path:
    target = output / "catalogue.sqlite"
    target.unlink(missing_ok=True)
    with sqlite3.connect(f"file:{export / 'catalogue.sqlite'}?mode=ro", uri=True, timeout=120) as src, \
            sqlite3.connect(target) as dst:
        src.backup(dst)
    # An export made before a migration is brought up to it, as a resume of the export would be, so
    # the seal copies rows of the shape the site holds.
    with sqlite3.connect(target) as db:
        schema(db)
    for pack in export.glob("pack-*.bin"):
        link = output / pack.name
        link.unlink(missing_ok=True)
        link.symlink_to(pack.resolve())
    return target


def drop_truncated(export: Path, catalogue: Path) -> dict:
    """Leave out the crops whose images lie past the end of their pack, here and in the export."""
    db = sqlite3.connect(catalogue)
    lost_media = set()
    for obj, in db.execute("SELECT DISTINCT object FROM media"):
        size = (export / obj).stat().st_size
        lost_media.update(k for k, in db.execute("SELECT key FROM media WHERE object=? AND offset+size>?", (obj, size)))
    lost_units = [i for i, data in db.execute("SELECT id, data FROM units")
                  if {key_of(json.loads(data).get(f)) for f in ("image", "context_image")} & lost_media]
    for target in (catalogue, export / "catalogue.sqlite"):
        with sqlite3.connect(target, timeout=120) as conn:
            conn.executemany("DELETE FROM units WHERE id=?", [(i,) for i in lost_units])
            conn.executemany("DELETE FROM media WHERE key=?", [(k,) for k in lost_media])
    return {"units": lost_units, "media": sorted(lost_media)}


def keep_prefixes(catalogue: Path, prefixes: list[str]) -> None:
    with sqlite3.connect(catalogue) as db:
        if prefixes:
            clause = " AND ".join("id NOT LIKE ?" for _ in prefixes)
            db.execute(f"DELETE FROM units WHERE {clause}", [p + "%" for p in prefixes])
        db.execute("DELETE FROM metadata WHERE key='catalogue'")


def d1(sql: str) -> list[dict]:
    result = subprocess.run(["bunx", "wrangler", "d1", "execute", "glyph-atlas", "--remote", "--json", "--command", sql],
                            cwd=ROOT / "apps" / "cloudflare", capture_output=True, text=True, check=True)
    return json.loads(result.stdout)[0]["results"]


def read_live(prefixes: list[str]) -> list[dict]:
    """The units the site holds under `prefixes`, read in id ranges small enough for one response."""
    rows = []
    for prefix in prefixes:
        expected = d1(f"SELECT count(*) AS n FROM units WHERE id LIKE '{prefix}%'")[0]["n"]
        got = []
        bounds = [prefix] + [prefix + c for c in ID_CHARS[1:]] + [prefix + "~"]
        for low, high in itertools.pairwise(bounds):
            got += d1("SELECT u.id, u.revision, u.quiz, u.data, EXISTS(SELECT 1 FROM events e WHERE e.target=u.id) AS reviewed "
                      f"FROM units u WHERE u.id >= '{low}' AND u.id < '{high}'")
        if len(got) != expected:
            raise SystemExit(f"read {len(got)} live {prefix} units, D1 holds {expected}")
        rows += got
    return rows


def plan_refresh(atlas: Path, live: dict[str, dict]) -> tuple[list[str], dict, list[str], set[str]]:
    db = sqlite3.connect(atlas)
    db.row_factory = sqlite3.Row
    statements, counts, held, touched = [], {}, [], set()
    for row in db.execute("SELECT * FROM units"):
        new = dict(row)
        current = live.get(new["id"])
        if current is None:
            continue
        try:
            action, statement = refresh.plan(new, current)
        except refresh.Collision as error:
            raise SystemExit(str(error)) from None
        counts[action] = counts.get(action, 0) + 1
        if action == "hold":
            held.append(new["id"])
        if statement:
            statements.append(statement + "\n")
            touched.add(new["id"])
    return statements, counts, held, touched


def statements(text: str) -> list[str]:
    """The statements of an SQL file, one per line or several lines ending in `;`."""
    found, current = [], []
    for line in text.splitlines(keepends=True):
        if not line.strip() and not current:
            continue
        current.append(line)
        if line.rstrip().endswith(";"):
            found.append("".join(current).rstrip("\n") + "\n")
            current = []
    if "".join(current).strip():
        raise SystemExit("an SQL file ends inside a statement")
    return found


def status_row() -> str:
    from glyph_atlas.review import collection
    value = json.dumps(collection.status((ROOT / "work").resolve()), ensure_ascii=False, separators=(",", ":"))
    return ("INSERT INTO metadata(key, value) VALUES('collection', " + refresh.quote(value)
            + ") ON CONFLICT(key) DO UPDATE SET value=excluded.value;\n")


def write_parts(sealed: Path, groups: list[list[str]]) -> list[str]:
    directory = sealed / "sql"
    directory.mkdir(exist_ok=True)
    for old in directory.glob("*.sql"):
        old.unlink()
    parts, part, size = [], [], 0

    def flush():
        nonlocal part, size
        if part:
            name = f"sql/part-{len(parts) + 1:02d}.sql"
            (sealed / name).write_text("".join(part), encoding="utf-8")
            parts.append(name)
            part, size = [], 0

    for group in groups:
        for statement in group:
            length = len(statement.encode())
            if length > STATEMENT_BYTES:
                raise SystemExit(f"a statement of {length} bytes is over D1's limit: {statement[:120]}")
            if size + length > PART_BYTES:
                flush()
            part.append(statement)
            size += length
    flush()
    return parts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("export", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--prefix", action="append", default=[], help="unit id prefix to publish, e.g. ar:")
    parser.add_argument("--live", type=Path, help="live units as JSON lines, instead of reading D1")
    parser.add_argument("--extra", type=Path, action="append", default=[], help="SQL file applied after the refresh")
    parser.add_argument("--status", action="store_true", help="also write the collection status row")
    args = parser.parse_args()
    if not args.prefix:
        raise SystemExit("name the unit prefixes to publish with --prefix")
    out = args.output
    out.mkdir(parents=True, exist_ok=True)

    catalogue = snapshot(args.export, out)
    lost = drop_truncated(args.export, catalogue)
    (out / "lost.json").write_text(json.dumps(lost, indent=1))
    keep_prefixes(catalogue, args.prefix)

    corpus = out / "empty-corpus"
    corpus.mkdir(exist_ok=True)
    with sqlite3.connect(corpus / "corpus.sqlite") as db:
        schema(db)
    sealed = out / "sealed"
    # The seal is rebuilt on every run; the export and its packs are the source.
    shutil.rmtree(sealed, ignore_errors=True)
    seal(out, corpus, sealed)

    live_rows = ([json.loads(line) for line in args.live.read_text().splitlines() if line.strip()] if args.live
                 else read_live(args.prefix))
    live = {row["id"]: row for row in live_rows}
    (out / "live.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in live_rows))
    updates, counts, held, touched = plan_refresh(sealed / "atlas.sqlite", live)
    (out / "held.json").write_text(json.dumps(held, indent=1))

    # Only the images of crops this publication inserts or updates, and only the packs that hold them:
    # every other image row and pack is already on the site from an earlier publication.
    atlas = sqlite3.connect(sealed / "atlas.sqlite")
    fresh = {i for i, in atlas.execute("SELECT id FROM units")} - live.keys()
    wanted = set()
    for i, data in atlas.execute("SELECT id, data FROM units"):
        if i in fresh or i in touched:
            wanted.update(key_of(json.loads(data).get(f)) for f in ("image", "context_image"))
    wanted.discard("")
    objects = {obj for key, obj in atlas.execute("SELECT key, object FROM media") if key in wanted}
    media, units = [], []
    for line in (sealed / "catalogue.sql").read_text(encoding="utf-8").splitlines(keepends=True):
        if line.startswith('INSERT OR REPLACE INTO "media"'):
            if MEDIA_KEY.match(line).group(1) in wanted:
                media.append(line.replace('INSERT OR REPLACE INTO "media"', 'INSERT OR IGNORE INTO "media"', 1))
        elif line.startswith('INSERT OR IGNORE INTO "units"') and \
                UNIT_ID.match(line).group(1).replace("''", "'") in fresh:
            units.append(line)
    extras = [statements(path.read_text(encoding="utf-8")) for path in args.extra]
    # The version row goes last, so the Worker's cached listings change only once every row has.
    groups = [media, units, updates, *extras] + ([[status_row()]] if args.status else []) + [[refresh.VERSION_BUMP]]
    parts = write_parts(sealed, groups)

    manifest = json.loads((sealed / "publication.json").read_text())
    manifest["objects"] = [o for o in manifest["objects"] if o["key"] in objects]
    manifest["sql"] = parts
    (sealed / "publication.json").write_text(json.dumps(manifest, ensure_ascii=False))
    print(json.dumps({"lost": len(lost["units"]), "new": len(units), "media": len(media), "refresh": counts,
                      "held": len(held), "extra": [str(p) for p in args.extra], "status": args.status,
                      "objects": len(manifest["objects"]), "parts": parts, "publication": str(sealed)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
