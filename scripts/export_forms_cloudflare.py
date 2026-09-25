"""Publish a form clustering, with the decisions recorded so far, as D1 SQL and R2 packs for the hosted Forms tab.

Reads the current clustering (`work/forms/current`) and a decision log in the `forms.py` format,
and writes a publication `scripts/publish_cloudflare.sh` uploads: tile packs under `objects/`,
ordered SQL parts under `sql/`, and `publication.json` listing both.

1. Every clustered glyph whose pixels are on disk gets a 240-pixel tile, packed into immutable
   objects with a `media` row each, so the Worker serves it like any other crop.
2. `form_families`, `form_clusters` and `form_units` are replaced. Each glyph carries its tile, its
   rank in its cluster and its group for every "Split into k" (k = 2..8), since the Worker has no
   embeddings to split with.
3. The local decisions are added to `form_decisions`, which keeps every decision made on the site.
   All of them are then replayed onto the new rows in the order they were made, so a republished
   clustering keeps what people named online. From the reload until the replay the site refuses new
   decisions, which would otherwise name the glyphs of a half-loaded cluster.
4. `corpus_units.character` follows each decided glyph's form, and the per-character counts Quick
   review deals from are recounted (`CORPUS_REFRESH`, which every corpus publication also runs).

Apply the migrations first (`wrangler d1 migrations apply glyph-atlas --remote`).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from cloudflare_schema import CORPUS_REFRESH

PART_BYTES = 90 * 1024**2
PACK_BYTES = 32 * 1024**2
TILE_EDGE = 240
SPLITS = range(2, 9)
UNITS_PER_STATEMENT = 1000

# Every decision stored in D1, replayed in the order it was made onto freshly loaded rows: a glyph's
# cluster form is the latest cluster decision that listed it, its own decision the latest glyph or
# inherit decision, and its form the second when it has one. The Worker applies one decision the
# same way. The site takes no decision from the reload to here (`form_loading`).
REPLAY = """
DELETE FROM form_marks;
INSERT INTO form_marks SELECT j.value,d.at,d.seq,d.kind,d.form,d.id FROM form_decisions d,json_each(d.units) j;
UPDATE form_units SET cluster_form=(SELECT m.form FROM form_marks m WHERE m.id=form_units.id AND m.kind='cluster'
  ORDER BY m.at DESC,m.seq DESC LIMIT 1) WHERE id IN (SELECT id FROM form_marks WHERE kind='cluster');
UPDATE form_units SET (glyph_set,glyph_form,glyph_decision)=(SELECT m.kind='glyph',CASE WHEN m.kind='glyph' THEN m.form END,
  CASE WHEN m.kind='glyph' THEN m.decision END FROM form_marks m WHERE m.id=form_units.id AND m.kind<>'cluster'
  ORDER BY m.at DESC,m.seq DESC LIMIT 1) WHERE id IN (SELECT id FROM form_marks WHERE kind<>'cluster');
UPDATE form_units SET form=CASE WHEN glyph_set=1 THEN glyph_form ELSE cluster_form END;
DELETE FROM form_marks;
UPDATE form_clusters SET (form,decision)=(SELECT d.form,d.id FROM form_decisions d WHERE d.kind='cluster'
  AND d.cluster=form_clusters.id AND d.revision=(SELECT revision FROM form_families WHERE code_point=form_clusters.family)
  ORDER BY d.at DESC,d.seq DESC LIMIT 1);
UPDATE form_families SET assigned=(SELECT count(*) FROM form_units WHERE family=code_point AND form IS NOT NULL);
DELETE FROM form_loading;
"""


def _quote(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, int | float):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def _values(values) -> str:
    return ",".join(_quote(v) for v in values)


class Parts:
    def __init__(self, out: Path):
        self.out, self.names, self.handle, self.size = out, [], None, 0
        (out / "sql").mkdir(parents=True, exist_ok=False)

    def write(self, statement: str) -> None:
        line = statement + "\n"
        if self.handle is None or self.size + len(line.encode()) > PART_BYTES:
            if self.handle:
                self.handle.close()
            self.names.append(f"sql/{len(self.names) + 1:03}.sql")
            self.handle, self.size = (self.out / self.names[-1]).open("w"), 0
        self.handle.write(line)
        self.size += len(line.encode())

    def close(self) -> list[str]:
        if self.handle:
            self.handle.close()
        return self.names


class Tiles:
    """Rendered tiles packed into content-addressed objects, named the way `seal_cloudflare.py` names them."""

    def __init__(self, out: Path):
        self.objects = out / "objects"
        self.objects.mkdir(parents=True, exist_ok=False)
        self.manifest, self.rows, self.pending, self.handle = [], [], [], None

    def add(self, key: str, path: Path) -> None:
        if self.handle is None:
            self.handle = (self.objects / "open.bin").open("wb")
        body = path.read_bytes()
        self.pending.append((key, self.handle.tell(), len(body)))
        self.handle.write(body)
        if self.handle.tell() >= PACK_BYTES:
            self._seal()

    def _seal(self) -> None:
        self.handle.close()
        opened = self.objects / "open.bin"
        with opened.open("rb") as handle:
            sha = hashlib.file_digest(handle, "sha256").hexdigest()
        opened.rename(self.objects / f"{sha}.bin")
        self.manifest.append({"key": f"packs/{sha}.bin", "file": f"objects/{sha}.bin", "sha256": sha,
                              "bytes": (self.objects / f"{sha}.bin").stat().st_size})
        self.rows += [(key, f"packs/{sha}.bin", offset, size, "image/webp") for key, offset, size in self.pending]
        self.pending, self.handle = [], None

    def close(self) -> None:
        if self.handle:
            self._seal()


def tiles(corpus_root: Path, out: Path, workers: int) -> tuple[dict[str, str], Tiles, Counter]:
    """A tile URL for every clustered glyph whose pixels are on disk, the tiles packed under `out`."""
    from glyph_atlas import forms
    from glyph_atlas.review.forms import located
    from glyph_atlas.review.media import MediaCache

    media = MediaCache(corpus_root=corpus_root)
    found = located(corpus_root)
    counts = Counter(tile_unheld=len(forms.clusters()["units"]) - len(found))
    keys: dict[str, str] = {}
    # File by file, so each scan is decoded once.
    for identity, (path, box) in sorted(found.items(), key=lambda item: str(item[1][0])):
        keys[identity] = media.local(path, list(box) if box else None, edge=TILE_EDGE).rsplit("/", 1)[-1].removesuffix(".webp")

    def render(key):
        try:
            return key, media.materialize(key)
        except (OSError, ValueError):
            return key, None

    packed, rendered = Tiles(out), {}
    wanted = list(dict.fromkeys(keys.values()))
    with ThreadPoolExecutor(workers) as pool:
        for n, (key, path) in enumerate(pool.map(render, wanted), 1):
            rendered[key] = path
            if path is not None:
                packed.add(key, path)
            if n % 20000 == 0:
                print(json.dumps({"tiles": n, "of": len(wanted)}), flush=True)
    packed.close()
    urls = {}
    for identity, key in keys.items():
        if rendered[key] is None:
            counts["tile_failed"] += 1
        else:
            urls[identity] = f"/atlas/media/{key}.webp"
    counts["tiles"] = len(packed.rows)
    return urls, packed, counts


def export(corpus_root: Path, out: Path, workers: int = 8) -> dict:
    from glyph_atlas import forms
    from glyph_atlas.review.forms import _form_entry, shape_runs

    data = forms.clusters()
    if data["revision"] is None:
        raise SystemExit("No clustering: run `atlas forms cluster` first.")
    out.mkdir(parents=True, exist_ok=False)
    urls, packed, counts = tiles(corpus_root, out, workers)

    parts = Parts(out)
    for row in packed.rows:
        parts.write(f"INSERT OR IGNORE INTO media VALUES({_values(row)});")
    # A run that stopped midway leaves the site refusing decisions; this one reloads everything anyway.
    parts.write("DELETE FROM form_loading;")
    for event in forms._events():
        units = event["units"]
        parts.write("INSERT OR IGNORE INTO form_decisions(id,at,actor,kind,family,form,cluster,revision,units,note) "
                    f"VALUES({_values((event['id'], event['at'], event.get('actor', 'local'), event['kind'], event['family'], event.get('form'), event.get('cluster'), event['revision'], json.dumps(units[:UNITS_PER_STATEMENT]), event.get('note', '')))});")
        # D1 refuses a statement over 100 KB, so a large cluster's glyphs follow in parts. Each part
        # extends only the list it follows, which leaves a decision already in D1 as it is.
        for start in range(UNITS_PER_STATEMENT, len(units), UNITS_PER_STATEMENT):
            chunk = json.dumps(units[start:start + UNITS_PER_STATEMENT])
            parts.write(f"UPDATE form_decisions SET units=substr(units,1,length(units)-1)||','||substr({_quote(chunk)},2) "
                        f"WHERE id={_quote(event['id'])} AND json_array_length(units)={start};")
        counts["decisions"] += 1
    parts.write("INSERT INTO form_loading VALUES(datetime('now'));")
    for table in ("form_units", "form_clusters", "form_families"):
        parts.write(f"DELETE FROM {table};")
    for code_point, family in sorted(data["families"].items()):
        clusters = family["clusters"]
        near = data["neighbours"].get(code_point, {})
        shape = shape_runs(near, clusters) if near.get("order") else [c["id"] for c in clusters]
        for size_position, cluster in enumerate(clusters):
            members = data["members"][cluster["id"]]
            groups = {k: {identity: g for g, ids in enumerate(forms.split(cluster["id"], k)) for identity in ids}
                      for k in SPLITS}
            for identity in members:
                _family, _cluster, similarity, rank = data["units"][identity]
                split = "".join(str(groups[k][identity]) for k in SPLITS)
                parts.write("INSERT INTO form_units(id,family,cluster,rank,similarity,image,split) VALUES("
                            f"{_values((identity, code_point, cluster['id'], rank, similarity, urls.get(identity), split))});")
                counts["units"] += 1
            representatives = [{"id": identity, "image": urls.get(identity)} for identity in members[:12]]
            parts.write("INSERT INTO form_clusters(id,family,label,count,coherence,shape_position,size_position,"
                        f"representatives) VALUES({_values((cluster['id'], code_point, cluster['label'], cluster['count'], cluster['coherence'], shape.index(cluster['id']), size_position, json.dumps(representatives, ensure_ascii=False)))});")
            counts["clusters"] += 1
        forms_of = json.dumps([_form_entry(char) for char in forms.family_members(code_point)], ensure_ascii=False)
        parts.write(f"INSERT INTO form_families VALUES({_values((code_point, family['char'], family['label'], family['count'], len(clusters), forms_of, 0, data['revision']))});")
        counts["families"] += 1
    parts.write(REPLAY.strip())
    parts.write(CORPUS_REFRESH)
    names = parts.close()
    (out / "publication.json").write_text(json.dumps({"revision": data["revision"], "counts": dict(counts),
                                                      "objects": packed.manifest, "sql": names}, indent=1) + "\n")
    return {"revision": data["revision"], **counts, "objects": len(packed.manifest), "sql_parts": len(names)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("--corpus-root", type=Path, default=Path("work"))
    parser.add_argument("--workers", type=int, default=8, help="threads rendering tiles")
    args = parser.parse_args()
    os.environ.setdefault("ATLAS_FORM_CLUSTERS", str((args.corpus_root / "forms/current").resolve()))
    print(json.dumps(export(args.corpus_root, args.out, args.workers)))
