"""Link each occurrence of ainu-records' character pages to the atlas unit that shows the same ink.

ainu-records publishes its own crops of the Ainu sources and no longer reviews them; its
character page links an occurrence to the atlas, where it is reviewed. An occurrence is linked
only to a unit the hosted atlas publishes, on the same page, whose box overlaps it by at least
`--min-iou`, one unit per occurrence. Both sides measure boxes in the pixels of the same witness
image; a page whose image has a different size on the two sides is not linked at all. A matched
pair is linked only when the two agree on the character: where the atlas labels that ink as another
character, a reader following the link would open a crop named for its neighbour. Those pairs are
returned as disagreements, the evidence for correcting the atlas's pairing, and `--report` writes
them out. Anything less certain is left without a link rather than sent to the wrong crop.

    .venv/bin/python scripts/export_ainu_atlas_links.py CATALOGUE ATLAS_PAGES AINU_RECORDS [--write] [--report FILE]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import unicodedata
from collections import defaultdict
from pathlib import Path

import yaml

ATLAS = "https://atlas.mkpo.li"


def entries(records: Path) -> dict[str, str]:
    """`<source>/<witness>[-<part>]` → the みんなで翻刻 entry id, as ainu-records names its units."""
    found: dict[str, str] = {}
    for source in yaml.safe_load((records / "data/sources.yaml").read_text(encoding="utf-8"))["sources"]:
        for witness in source.get("witnesses") or []:
            parts = [p for p in witness.get("parts") or [] if p.get("entry")]
            if witness.get("entry"):
                parts = [witness]
            for index, part in enumerate(parts, start=1):
                suffix = f"-{index}" if len(parts) > 1 else ""
                found[f"{source['slug']}/{witness['slug']}{suffix}"] = part["entry"]
    return found


def normal(text: str | None) -> str:
    return unicodedata.normalize("NFC", text or "").strip()


def iou(a, b) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    w = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    h = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    inter = w * h
    union = aw * ah + bw * bh - inter
    return inter / union if union else 0.0


def links(catalogue: Path, atlas_pages: Path, records: Path, min_iou: float) -> dict[str, dict]:
    from glyph_atlas import tables
    from glyph_atlas.schema import Page

    sizes = {page.id: (page.width, page.height) for page in tables.read(atlas_pages, Page)}
    db = sqlite3.connect(catalogue)
    units_by_page: dict[str, list] = defaultdict(list)
    labels: dict[str, str] = {}
    for (data,) in db.execute("SELECT data FROM units WHERE origin='local' AND id LIKE 'hk:%'"):
        d = json.loads(data)
        labels[d["id"]] = normal(d.get("label"))
        box = d.get("box")
        if box and d.get("page_id"):
            units_by_page[d["page_id"]].append((d["id"], (box["x"], box["y"], box["w"], box["h"])))
    by_key = entries(records)
    out: dict[str, dict] = {}
    for folder in sorted((records / "data/characters").iterdir()):
        samples_path = folder / "samples.json"
        if not samples_path.is_file():
            continue
        unit = json.loads(samples_path.read_text(encoding="utf-8"))
        entry = by_key.get(unit["key"])
        # ainu-records' reading of an occurrence: its published review, else the proposed label.
        reviews_path = folder / "reviews.json"
        reviewed = ({e["id"]: e.get("label") for e in json.loads(reviews_path.read_text(encoding="utf-8")).get("edits", [])}
                    if reviews_path.is_file() else {})
        linked: dict[str, str] = {}
        disagree: list[dict] = []
        if entry:
            pages = {p["n"]: p for p in unit["pages"]}
            by_id = {sample["id"]: sample for sample in unit["samples"]}
            by_page: dict[int, list] = defaultdict(list)
            for sample in unit["samples"]:
                by_page[sample["page"]].append(sample)
            for n, samples in by_page.items():
                page_id = f"hk:{entry}:{n - 1}"
                atlas = units_by_page.get(page_id, [])
                if not atlas or sizes.get(page_id) != (pages[n]["width"], pages[n]["height"]):
                    continue
                pairs = sorted(((iou(s["box"], box), s["id"], uid) for s in samples for uid, box in atlas),
                               reverse=True)
                taken_s, taken_u = set(), set()
                for score, sid, uid in pairs:
                    if score < min_iou:
                        break
                    if sid in taken_s or uid in taken_u:
                        continue
                    taken_s.add(sid)
                    taken_u.add(uid)
                    ours = normal(reviewed.get(sid) or by_id[sid]["proposed"])
                    if ours == labels.get(uid):
                        linked[sid] = uid
                    else:
                        disagree.append({"sample": sid, "unit": uid, "records": ours, "atlas": labels.get(uid),
                                         "box": by_id[sid]["box"], "iou": round(score, 3)})
        out[unit["key"]] = {"entry": entry, "samples": len(unit["samples"]), "links": linked, "disagree": disagree}
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("catalogue", type=Path)
    parser.add_argument("atlas_pages", type=Path)
    parser.add_argument("records", type=Path)
    parser.add_argument("--min-iou", type=float, default=0.5)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--report", type=Path, help="write the pairs whose labels disagree here")
    args = parser.parse_args()
    result = links(args.catalogue, args.atlas_pages, args.records, args.min_iou)
    for key, value in result.items():
        print(f"{key:28} entry={value['entry'] or '-':34} samples={value['samples']:6} "
              f"linked={len(value['links']):6} disagree={len(value['disagree']):6}")
        if args.write:
            # A copy with no counterpart on the atlas gets no file, and the page shows it without links.
            target = args.records / "data/characters" / key.replace("/", "--") / "atlas.json"
            if value["links"]:
                target.write_text(json.dumps({"version": 1, "atlas": ATLAS, "links": dict(sorted(value["links"].items()))},
                                             ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
            else:
                target.unlink(missing_ok=True)
    if args.report:
        args.report.write_text(json.dumps({key: value["disagree"] for key, value in result.items() if value["disagree"]},
                                          ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
