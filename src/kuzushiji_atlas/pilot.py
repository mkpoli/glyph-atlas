"""Pilot packages: one directory per page, holding everything a reviewer needs.

A package is a page image, the page record, the lines with their boxes and text, and the units the
alignment pipeline proposed where it has run. The packages are the unit of exchange with reviewers:
they travel to a person, come back as adjudicated truth, and the same layout is what
`atlas eval alignment` compares a prediction against.

`data/pilot/items.tsv` names the items and the group of every page: the calibration group is
annotated first, under the protocol in `docs/implementation/pilot-protocol.md`, and the held-out
group is annotated without the machine output being shown.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from . import tables
from .schema import Line, Page, Unit

PILOT_ITEMS = Path(__file__).resolve().parents[2] / "data" / "pilot" / "items.tsv"
PACKAGE_FILES = ("page.json", "lines.parquet", "units.parquet", "image.jpg")


def selection(path: Path = PILOT_ITEMS) -> list[dict[str, str]]:
    """The rows of the pilot selection, without its comment header."""
    with path.open(encoding="utf-8", newline="") as handle:
        rows = [row for row in handle if not row.startswith("#")]
    return list(csv.DictReader(rows, delimiter="\t"))


def pages_for(group: str | None = None, items: list[str] | None = None, path: Path = PILOT_ITEMS) -> list[dict[str, str]]:
    """The selected pages, optionally one group of them or a list of items."""
    rows = selection(path)
    if group:
        rows = [row for row in rows if row["group"] == group]
    if items:
        wanted = set(items)
        rows = [row for row in rows if row["item_id"] in wanted]
    return rows


def fetch_page_images(
    directory: Path,
    *,
    group: str | None = None,
    items: list[str] | None = None,
    pages: list[str] | None = None,
    pause: float | None = None,
    per_item: int | None = None,
    selection_path: Path = PILOT_ITEMS,
) -> dict[str, int]:
    """Fetch the full-size page image of every selected page into the cache.

    The selection is the one `export` uses, so a page that travels in a package is in the cache
    before the package is built. `per_item` keeps the first n pages of each item, which is how the
    held-out group is bounded: a reviewer works through the calibration pages first, and a few pages
    an item are enough to measure on. Requests to one host wait the document pause apart, which is
    what makes this slow across ten holders; the fetched pages are counted apart from the failures.
    """
    from . import images, net

    dataset = tables.Dataset(directory)
    if dataset.tables["pages"] is None:
        raise ValueError(f"{directory} needs a pages table")
    selected = pages_for(group, items, selection_path)
    if per_item is not None:
        kept: list[dict[str, str]] = []
        seen: dict[str, int] = {}
        for row in selected:
            count = seen.get(row["item_id"], 0)
            if count < per_item:
                kept.append(row)
                seen[row["item_id"]] = count + 1
        selected = kept
    wanted = {row["page_id"] for row in selected}
    if pages:
        wanted &= set(pages)
    counts = {"pages": 0, "fetched": 0, "cached": 0, "failed": 0}
    for page in dataset.scan("pages"):
        for record in page:
            if record.id not in wanted:
                continue
            counts["pages"] += 1
            if images.path_for(record.image) is not None:
                counts["cached"] += 1
                continue
            try:
                images.fetch(record.image, pause=pause)
            except (images.ImageError, net.DownloadError):
                counts["failed"] += 1
                continue
            counts["fetched"] += 1
    return counts


def export(
    out: Path,
    directory: Path,
    *,
    group: str | None = None,
    items: list[str] | None = None,
    pages: list[str] | None = None,
    images: bool = True,
    selection_path: Path = PILOT_ITEMS,
) -> dict[str, int]:
    """Write one package per selected page and return the counts.

    `directory` is a dataset directory that holds the lines (and the units, when the alignment has
    run). `images` fetches the page image into the package through the image cache.
    """
    dataset = tables.Dataset(directory)
    if dataset.tables["pages"] is None or dataset.tables["lines"] is None:
        raise ValueError(f"{directory} needs pages and lines for a pilot package")
    wanted = {row["page_id"]: row for row in pages_for(group, items, selection_path)}
    if pages:
        wanted = {page: row for page, row in wanted.items() if page in set(pages)}
    page_records = {page.id: page for page in dataset.read("pages") if page.id in wanted}
    documents = (
        {document.id: document for document in dataset.read("documents")}
        if dataset.tables["documents"] is not None
        else {}
    )
    lines: dict[str, list[Line]] = {page: [] for page in wanted}
    for line in dataset.scan("lines"):
        for record in line:
            if record.page_id in lines:
                lines[record.page_id].append(record)
    units: dict[str, list[Unit]] = {page: [] for page in wanted}
    if dataset.tables["units"] is not None:
        for batch in dataset.scan("units"):
            for record in batch:
                if record.page_id in units:
                    units[record.page_id].append(record)

    counts = {"pages": 0, "lines": 0, "units": 0, "images": 0}
    for page_id, row in sorted(wanted.items()):
        page = page_records.get(page_id)
        if page is None:
            continue
        folder = out / page_id.replace(":", "_")
        folder.mkdir(parents=True, exist_ok=True)
        checksum = _fetch_image(page, folder / "image.jpg") if images else ""
        counts["images"] += int(bool(checksum))
        if checksum:
            page.sha256 = checksum
        document = documents.get(page.document_id)
        if document is not None:
            tables.write(folder / "documents.parquet", [document], type(document))
        tables.write(folder / "pages.parquet", [page], Page)
        (folder / "page.json").write_text(
            json.dumps(
                {"page": page.model_dump(mode="json"), "group": row["group"], "reason": row["reason"],
                 "item_id": row["item_id"], "image_index": row["image_index"]},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        tables.write(folder / "lines.parquet", lines[page_id], Line)
        tables.write(folder / "units.parquet", units[page_id], Unit)
        counts["pages"] += 1
        counts["lines"] += len(lines[page_id])
        counts["units"] += len(units[page_id])
    return counts


def _fetch_image(page: Page, target: Path) -> str:
    """Put the page image in the package and return its sha256, or an empty string.

    The page record is left as the upstream wrote it; the package states the checksum of the copy it
    carries, which is what lets the review service serve the local file instead of the IIIF URL.
    """
    import hashlib

    from . import images, net

    if not target.exists():
        try:
            record = images.fetch(page.image)
        except (images.ImageError, net.DownloadError):
            return ""
        source = images.path_for(record.url)
        if source is None:
            return ""
        target.write_bytes(source.read_bytes())
    digest = hashlib.sha256()
    with target.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def truth_pages(directory: Path) -> dict[str, int]:
    """Page id to unit count of an annotated package directory, for the evaluator."""
    counts: dict[str, int] = {}
    for folder in sorted(Path(directory).iterdir()):
        if not folder.is_dir():
            continue
        page_file = folder / "page.json"
        units_file = folder / "units.parquet"
        if not page_file.exists():
            continue
        page_id = json.loads(page_file.read_text(encoding="utf-8"))["page"]["id"]
        counts[page_id] = len(tables.read(units_file, Unit)) if units_file.exists() else 0
    return counts
