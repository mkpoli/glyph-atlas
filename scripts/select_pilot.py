"""Choose the pilot items for `data/pilot/items.tsv`.

The candidates are Honkoku-Lines items whose image licence is PDM 1.0 or CC BY 4.0, which is what a
release build can redistribute. A candidate needs at least `--pages` pages so that the calibration
group (the first two pages) and the held-out group are separate pages of the same item. Within each
stratum (licence, line density band, IIIF host) the items are ordered by `sha1(item_id)`, so the
choice does not change between runs.

The item's IIIF manifest supplies the production type where its metadata states one, and the pages
of the item come from the line records, which give the `image_index` of every page the upstream
covers. Items that share a manifest URL or a printing-block statement with another item go to the
same group; the group of an item is the one its first page falls in.

The manifest of every candidate is cached under `cache/manifests/`; requests to one host wait three
seconds apart, as `docs/implementation/README.md` says.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import pickle
import re
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

import httpx

ROOT = Path(__file__).resolve().parents[1]
ITEMS = ROOT / "cache" / "honkoku-lines" / "items.tsv"
LINES = ROOT / "cache" / "honkoku-lines" / "lines.jsonl.gz"
MANIFESTS = ROOT / "cache" / "manifests"
USER_AGENT = "kuzushiji-atlas (+https://github.com/mkpoli/kuzushiji-atlas)"
PAUSE = 3.0
LICENCES = {"PDM-1.0", "CC-BY-4.0"}
BANDS = [(0, 12, "sparse"), (12, 22, "medium"), (22, 10_000, "dense")]
PRODUCTION = [
    (re.compile(r"活字|活版"), "movable-type"),
    (re.compile(r"写本|自筆|稿本|模写|筆写"), "manuscript"),
    (re.compile(r"版本|刊本|整版|板本|刊"), "woodblock"),
]
STOP = {"の", "に", "は", "を", "と", "て", "し", "た", "、", "。"}


def band_of(density: float) -> str:
    for low, high, name in BANDS:
        if low <= density < high:
            return name
    return "dense"


def items() -> list[dict[str, str]]:
    with ITEMS.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def manifest(client: httpx.Client, url: str, pauses: dict[str, float]) -> dict | None:
    if not url:
        return None
    target = MANIFESTS / f"{hashlib.sha256(url.encode()).hexdigest()}.json"
    if target.exists():
        try:
            return json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
    host = urlparse(url).netloc
    wait = PAUSE - (time.monotonic() - pauses.get(host, 0.0))
    if wait > 0:
        time.sleep(wait)
    pauses[host] = time.monotonic()
    try:
        response = client.get(url)
    except httpx.HTTPError:
        return None
    if response.status_code != 200 or not response.text.lstrip().startswith("{"):
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(response.text, encoding="utf-8")
    return json.loads(response.text)


def manifest_fields(document: dict | None) -> dict[str, str]:
    if not document:
        return {}
    fields = {}
    for entry in document.get("metadata", []) or []:
        label = entry.get("label")
        value = entry.get("value")
        if isinstance(label, list):
            label = label[0] if label else ""
        if isinstance(value, list):
            value = " ".join(str(item) for item in value)
        fields[str(label)] = str(value)
    return fields


def production_of(fields: dict[str, str], title: str) -> str:
    statement = " ".join(f"{key} {value}" for key, value in fields.items())
    for pattern, name in PRODUCTION:
        if pattern.search(statement):
            return name
    for pattern, name in PRODUCTION:
        if pattern.search(title):
            return name
    return "unknown"


def page_index() -> dict[str, dict[int, tuple[int, str]]]:
    """Page index of every item: `{item_id: {image_index: (line count, image url)}}`.

    The lines file is 100 MB gzipped and 1.17M rows, so it is read once and the index is kept beside
    it under `cache/honkoku-lines/page-index.pkl`; delete that file to rebuild it.
    """
    cache = ROOT / "cache" / "honkoku-lines" / "page-index.pkl"
    if cache.exists():
        with cache.open("rb") as handle:
            return pickle.load(handle)
    import gzip

    found: dict[str, dict[int, list]] = defaultdict(dict)
    with gzip.open(LINES, "rt", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            entry = found[row["item_id"]].setdefault(
                int(row["image_index"]), [0, row.get("iiif_image_url", "")]
            )
            entry[0] += 1
    index = {item: {page: tuple(value) for page, value in pages.items()} for item, pages in found.items()}
    with cache.open("wb") as handle:
        pickle.dump(index, handle)
    return index


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "pilot" / "items.tsv")
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--pages", type=int, default=12, help="fewest pages an item may have")
    parser.add_argument("--candidates", type=int, default=60, help="manifests to read before choosing")
    args = parser.parse_args()

    rows = [
        row
        for row in items()
        if row["image_license"] in LICENCES and int(row["n_pages"] or 0) >= args.pages
    ]
    for row in rows:
        density = int(row["n_lines"] or 0) / max(1, int(row["n_pages"]))
        row["band"] = band_of(density)
        row["density"] = f"{density:.1f}"
    by_stratum: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_stratum[(row["image_license"], row["band"])].append(row)
    for stratum in by_stratum.values():
        stratum.sort(key=lambda row: hashlib.sha1(row["item_id"].encode()).hexdigest())

    ordered: list[dict[str, str]] = []
    index = 0
    while len(ordered) < args.candidates:
        added = False
        for key in sorted(by_stratum):
            stratum = by_stratum[key]
            if index < len(stratum):
                ordered.append(stratum[index])
                added = True
                if len(ordered) == args.candidates:
                    break
        if not added:
            break
        index += 1

    index_of = page_index()
    selected: list[tuple[dict[str, str], list[tuple[int, int, str]]]] = []
    taken: set[str] = set()
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True) as client:
        pauses: dict[str, float] = {}
        # One pass over the candidates to read their manifests, then a second pass in reading order
        # to fill the groups: a stratum is taken before it repeats, and a host is taken before it
        # repeats, until the round is full or the candidates run out.
        for row in ordered:
            fields = manifest_fields(manifest(client, row["iiif_manifest_url"], pauses))
            row["production"] = production_of(fields, row["title"])
            row["manifest_holder"] = fields.get("DCTERMS.relation", "")
            row["pages"] = [
                (index, count, url)
                for index, (count, url) in sorted(index_of.get(row["item_id"], {}).items())
            ]
            if len(row["pages"]) < args.pages:
                row["production"] = row["production"]
        usable = [row for row in ordered if len(row["pages"]) >= args.pages]
        # The first round fills every stratum once, the second round every host not yet used, and
        # the remaining rounds take what is left, so the ten items span the strata before any
        # stratum repeats.
        for round_kind in ("stratum", "host", "any"):
            for row in usable:
                if len(selected) >= args.count:
                    break
                key = (row["image_license"], row["band"], row["production"])
                if round_kind == "stratum" and key in taken and len({(r["image_license"], r["band"], r["production"]) for r, _ in selected}) < 6:
                    continue
                if round_kind == "host" and any(r["iiif_host"] == row["iiif_host"] for r, _ in selected) and len({r["iiif_host"] for r, _ in selected}) < 6:
                    continue
                if row["item_id"] in {r["item_id"] for r, _ in selected}:
                    continue
                selected.append((row, row["pages"]))
                taken.add(key)
                print(
                    f"{len(selected):2d} {row['item_id'][:8]} {row['image_license']:<10} {row['band']:<6} "
                    f"{row['density']:>5} {row['production']:<12} {len(row['pages']):3d} pages "
                    f"{row['iiif_host'][:28]:<28} {row['title'][:24]}",
                    flush=True,
                )
            if len(selected) >= args.count:
                break

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as handle:
        handle.write("# Pilot items: Honkoku-Lines items on PDM 1.0 or CC BY 4.0 images.\n")
        handle.write("# group: calibration (the first two pages) or heldout (the rest).\n")
        handle.write("# page_id is the page record id the importer writes, hl:<item_id>:<image_index>.\n")
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(
            ["item_id", "title", "holder", "licence", "production", "band", "lines_per_page",
             "n_pages", "group", "page_id", "image_index", "n_lines", "reason"]
        )
        for row, pages in selected:
            for position, (image_index, n_lines, _url) in enumerate(pages):
                group = "calibration" if position < 2 else "heldout"
                writer.writerow([
                    row["item_id"], row["title"], row["holding_institution"], row["image_license"],
                    row["production"], row["band"], row["density"], len(pages), group,
                    f"hl:{row['item_id']}:{image_index}", image_index, n_lines,
                    f"{row['band']} lines, {row['production']}, {row['holding_institution']}",
                ])
    print(f"{len(selected)} items, {sum(len(p) for _, p in selected)} pages -> {args.out}")


if __name__ == "__main__":
    main()
