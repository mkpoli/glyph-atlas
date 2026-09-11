"""Import Honkoku-Lines v2.0 as documents, pages and lines.

Honkoku-Lines is built from みんなで翻刻: one row per line crop that the detector found on a page
image and the recogniser read, with the box on the full-size image, the page's transcription line in
みんなで翻刻's koji markup (`text`), the same line with the notation removed (`plain_text`), the
recogniser's string (`ocr_text`) and the match distance between the two.

The cached inputs are `lines.jsonl.gz` and `items.tsv` under `cache/honkoku-lines/`, pinned to the
Hugging Face revision in `data/sources/honkoku-lines.yaml`. Ids come from the upstream identity —
`hl:<item_id>` for a document, `hl:<item_id>:<image_index>` for a page, `hl:<image_id>` for a line —
so a second import writes the same tables.

Memory
------
`lines.jsonl.gz` holds 1,169,304 rows and is read one JSON object at a time. Line records are
written to Parquet every `BATCH_SIZE` rows under `<out>/.import-parts/lines/`, a working directory
that is removed once the import finishes; documents (4,140) and pages (79,086) are small enough to
hold as models. The parts are then handed to `tables.Dataset.merge`, which reads the lines table
into Arrow once to sort it and to collapse rows that share an id, and writes `<out>/lines/` as a
directory of shards. That last step is the peak: the full import of 2026-09-11 took 4 minutes and
2.6 GiB resident, of which the 1,169,304 line records in Arrow and the map of line ids to their
content are the largest parts.

`koji.parse(text).plain` is compared with the upstream `plain_text` on every row. Mismatches are
counted, returned as `koji_mismatches` beside the table counts, and the first 100 are written to
`koji-mismatches.tsv` in the output directory.
"""

from __future__ import annotations

import csv
import gzip
import json
import os
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .. import images, koji, rights, tables
from ..schema import Box, Document, Licence, Line, Page, Rights

SOURCE = "honkoku-lines"
"""Upstream id, as `data/sources/honkoku-lines.yaml` states it."""

REPO_ROOT = Path(__file__).resolve().parents[3]
CACHE_ENV = "GLYPH_ATLAS_CACHE"
LINES_FILE = "lines.jsonl.gz"
ITEMS_FILE = "items.tsv"
PARTS_DIR = ".import-parts"
MISMATCH_FILE = "koji-mismatches.tsv"
MISMATCH_LIMIT = 100
BATCH_SIZE = 100_000
COMMAND = "atlas import honkoku-lines"
MATCH_METHOD = "honkoku-lines-v2.0"

HONKOKU_REF = "honkoku-data"
MANIFEST_REF = "iiif-manifest"
TEXT_HOLDER = "橋本雄太"
TEXT_EVIDENCE = "https://huggingface.co/datasets/yuta1984/honkoku-lines"
TEXT_ATTRIBUTION = (
    "橋本雄太, Honkoku-Lines v2.0, doi:10.5281/zenodo.21801040; "
    "transcriptions from みんなで翻刻 (CC BY-SA 4.0)"
)

MISMATCH_COLUMNS = ("image_id", "line_index", "text", "plain_text", "parsed_plain")

#: The columns of `items.tsv` that are counts or measurements, and how they are read.
INTEGER_COLUMNS = ("n_pages", "n_lines", "n_chars_plain")
FLOAT_COLUMNS = ("mean_align_distance",)


def cache_dir() -> Path:
    """The directory holding the two inputs: `$GLYPH_ATLAS_CACHE/honkoku-lines`, or `cache/`."""
    override = os.environ.get(CACHE_ENV)
    root = Path(override) if override else REPO_ROOT / "cache"
    return root / SOURCE


def import_all(
    out: Path,
    *,
    items: list[str] | None = None,
    licences: list[str] | None = None,
    limit: int | None = None,
) -> dict[str, int]:
    """Import the cached Honkoku-Lines files into the dataset directory `out`.

    `items` keeps the given item ids, `licences` the given upstream image licence strings (matched
    without case), and `limit` stops after that many line records; the three are filters for pilots
    and are empty by default. The returned mapping holds the row count of every table written, in
    the shape `tables.Dataset.merge` returns it, plus `koji_mismatches`.
    """
    return import_from(cache_dir(), out, items=items, licences=licences, limit=limit)


def import_from(
    cache: Path,
    out: Path,
    *,
    items: list[str] | None = None,
    licences: list[str] | None = None,
    limit: int | None = None,
) -> dict[str, int]:
    """Import `lines.jsonl.gz` and `items.tsv` of `cache` into the dataset directory `out`.

    Tests call this with a fixture cache; `import_all` calls it with `cache_dir()`.
    """
    cache, out = Path(cache), Path(out)
    known_items = read_items(cache / ITEMS_FILE)
    wanted_items = set(items) if items else None
    wanted_licences = {value.casefold() for value in licences} if licences else None

    out.mkdir(parents=True, exist_ok=True)
    _drop(out, "lines")
    parts = out / PARTS_DIR
    shutil.rmtree(parts, ignore_errors=True)
    (parts / "lines").mkdir(parents=True)

    documents: dict[str, Document] = {}
    pages: dict[str, Page] = {}
    batch: list[Line] = []
    samples: list[dict[str, Any]] = []
    mismatches = 0
    part = 0
    for index, row in enumerate(_lines_of(cache / LINES_FILE, wanted_items, wanted_licences)):
        if limit is not None and index >= limit:
            break
        item_id = row["item_id"]
        if item_id not in documents:
            documents[item_id] = document_of(item_id, known_items.get(item_id), row)
        page_id = f"hl:{item_id}:{row['image_index']}"
        if page_id not in pages:
            pages[page_id] = page_of(item_id, row)
        batch.append(line_of(row))
        parsed = koji.parse(row["text"]).plain
        if parsed != row["plain_text"]:
            mismatches += 1
            if len(samples) < MISMATCH_LIMIT:
                samples.append(
                    {
                        "image_id": row["image_id"],
                        "line_index": row["line_index"],
                        "text": row["text"],
                        "plain_text": row["plain_text"],
                        "parsed_plain": parsed,
                    }
                )
        if len(batch) >= BATCH_SIZE:
            part = _write_part(parts, part, batch)
    if batch:
        part = _write_part(parts, part, batch)
    if not part:
        _write_part(parts, part, [])  # a lines table with no rows still needs its columns

    tables.write_table(out / "documents.parquet", documents.values(), Document)
    tables.write_table(out / "pages.parquet", pages.values(), Page)
    counts = tables.Dataset(out).merge([tables.Dataset(parts)], out, command=COMMAND)
    shutil.rmtree(parts, ignore_errors=True)
    write_mismatches(out / MISMATCH_FILE, samples)
    counts["koji_mismatches"] = mismatches
    return counts


def read_items(path: Path) -> dict[str, dict[str, str]]:
    """`items.tsv` keyed by item id, one dictionary per row."""
    with path.open(encoding="utf-8", newline="") as handle:
        return {row["item_id"]: row for row in csv.DictReader(handle, delimiter="\t")}


def document_of(item_id: str, item: dict[str, str] | None, row: dict[str, Any] | None = None) -> Document:
    """The document of one item, from its `items.tsv` row or from a line when the item has no row.

    An item that `items.tsv` does not list takes its title, holder and rights from the line that
    named it, and `meta.items_row` is false.
    """
    if item is None:
        if row is None:
            raise ValueError(f"{item_id}: no items.tsv row and no line to build a document from")
        licence, url, holder = row["image_license"], row["image_license_url"], row["holding_institution"]
        title = item_id
        refs = {HONKOKU_REF: item_id}
        meta: dict[str, Any] = {
            "items_row": False,
            "split": row["split"],
            "project_id": row["project_id"],
            "iiif_host": row["iiif_host"],
            "image_license": licence,
            "image_license_url": url,
        }
    else:
        licence, url, holder = item["image_license"], item["image_license_url"], item["holding_institution"]
        title = item["title"].strip() or item_id
        refs = {HONKOKU_REF: entry_id(item["honkoku_url"], item_id)}
        if item["iiif_manifest_url"].strip():
            refs[MANIFEST_REF] = item["iiif_manifest_url"].strip()
        meta = {
            "items_row": True,
            "split": item["split"],
            "project_id": item["project_id"],
            "iiif_host": item["iiif_host"],
            "honkoku_url": item["honkoku_url"],
            "image_license": licence,
            "image_license_url": url,
        }
        for column in INTEGER_COLUMNS:
            meta[column] = _integer(item[column])
        for column in FLOAT_COLUMNS:
            meta[column] = _number(item[column])
    resolved = rights.resolve(licence=licence, url=url, holder=holder)
    return Document(
        id=f"hl:{item_id}",
        title=title,
        source_refs=refs,
        holder=resolved.holder,
        image_rights=resolved,
        text_rights=text_rights(),
        meta=meta,
    )


def text_rights() -> Rights:
    """CC BY-SA 4.0 for the transcription and the metadata, credited to Honkoku-Lines."""
    resolved = rights.resolve(licence=Licence.CC_BY_SA_4.value, url=TEXT_EVIDENCE)
    return resolved.model_copy(update={"holder": TEXT_HOLDER, "attribution": TEXT_ATTRIBUTION})


def page_of(item_id: str, row: dict[str, Any]) -> Page:
    """The page a line sits on, keyed by the upstream image index; size arrives with `images info`."""
    url = row["iiif_image_url"]
    return Page(
        id=f"hl:{item_id}:{row['image_index']}",
        document_id=f"hl:{item_id}",
        seq=int(row["image_index"]),
        image=images.service_of(url) or url,
        width=0,
        height=0,
    )


def line_of(row: dict[str, Any]) -> Line:
    """One line record: the box, the two texts, the match of box to text, and the upstream scores."""
    x, y, w, h = (int(value) for value in row["bbox"])
    distance = float(row["edit_distance"])
    return Line(
        id=f"hl:{row['image_id']}",
        page_id=f"hl:{row['item_id']}:{row['image_index']}",
        seq=int(row["line_index"]),
        box=Box(x=x, y=y, w=w, h=h),
        text_raw=row["text"],
        text=row["plain_text"],
        match_method=MATCH_METHOD,
        match_confidence=1.0 - distance,
        meta={
            "split": row["split"],
            "det_score": float(row["det_score"]),
            "edit_distance": distance,
            "length_ratio": float(row["length_ratio"]),
            "ocr_text": row["ocr_text"],
            "image_on_hf": bool(row["image_on_hf"]),
            "iiif_region_url": row["iiif_region_url"],
        },
    )


def write_mismatches(path: Path, rows: list[dict[str, Any]]) -> int:
    """Write the mismatching rows as TSV, header first, and return how many were written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MISMATCH_COLUMNS, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def entry_id(honkoku_url: str, item_id: str) -> str:
    """The みんなで翻刻 entry id in a transcription URL, or the item id when it carries none."""
    segments = urlsplit(honkoku_url).path.strip("/").split("/")
    if "transcription" in segments:
        found = segments[segments.index("transcription") + 1 :]
        if found and found[0]:
            return found[0]
    return item_id


def _lines_of(path: Path, items: set[str] | None, licences: set[str] | None) -> Iterator[dict[str, Any]]:
    """The rows of `lines.jsonl.gz` that pass the filters, in file order."""
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for text in handle:
            row = json.loads(text)
            if items is not None and row["item_id"] not in items:
                continue
            if licences is not None and row["image_license"].casefold() not in licences:
                continue
            yield row


def _write_part(parts: Path, index: int, batch: list[Line]) -> int:
    """Write one batch as a part of the lines table and return the next part number."""
    tables.write_table(parts / "lines" / f"part-{index:05d}.parquet", batch, Line)
    batch.clear()
    return index + 1


def _drop(directory: Path, name: str) -> None:
    """Remove one table of this importer's own output, so a rerun with other filters leaves nothing."""
    (directory / f"{name}.parquet").unlink(missing_ok=True)
    shards = directory / name
    if shards.is_dir():
        shutil.rmtree(shards)


def _integer(value: str) -> int | None:
    """A whole number of an `items.tsv` column, or None when the column is empty or malformed."""
    text = value.strip()
    return int(text) if text.isdigit() else None


def _number(value: str) -> float | None:
    """A decimal number of an `items.tsv` column, or None when the column is empty or malformed."""
    try:
        return float(value.strip())
    except ValueError:
        return None
