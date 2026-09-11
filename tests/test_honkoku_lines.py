"""Tests for the Honkoku-Lines importer, on a fixture cache built in `tmp_path`."""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from kuzushiji_atlas import koji, tables
from kuzushiji_atlas.importers import honkoku_lines
from kuzushiji_atlas.schema import Licence

ITEM_A = "AAAA0000000000000000000000000000"
ITEM_B = "BBBB0000000000000000000000000000"
ITEM_C = "CCCC0000000000000000000000000000"
MANIFEST_A = "https://example.test/a/manifest.json"

ITEM_COLUMNS = (
    "item_id",
    "project_id",
    "title",
    "iiif_host",
    "honkoku_url",
    "iiif_manifest_url",
    "n_pages",
    "n_lines",
    "n_chars_plain",
    "mean_align_distance",
    "image_license",
    "image_license_url",
    "holding_institution",
    "split",
)

ITEMS = (
    {
        "item_id": ITEM_A,
        "project_id": "demo",
        "title": "試し A",
        "iiif_host": "iiif.example.test",
        "honkoku_url": f"https://app.honkoku.org/transcription/{ITEM_A}/1",
        "iiif_manifest_url": MANIFEST_A,
        "n_pages": "2",
        "n_lines": "5",
        "n_chars_plain": "21",
        "mean_align_distance": "0.05",
        "image_license": "CC-BY-4.0",
        "image_license_url": "https://creativecommons.org/licenses/by/4.0/",
        "holding_institution": "Example Library",
        "split": "train",
    },
    {
        "item_id": ITEM_B,
        "project_id": "demo",
        "title": "試し B",
        "iiif_host": "example.test",
        "honkoku_url": f"https://app.honkoku.org/transcription/{ITEM_B}/1",
        "iiif_manifest_url": "https://example.test/b/manifest.json",
        "n_pages": "1",
        "n_lines": "4",
        "n_chars_plain": "12",
        "mean_align_distance": "0.10",
        "image_license": "(unspecified)",
        "image_license_url": "",
        "holding_institution": "",
        "split": "val",
    },
)

# The upstream fields of one line; the image URL is replaced for the items that have no Image API.
LINE = {
    "project_id": "demo",
    "iiif_host": "iiif.example.test",
    "ocr_text": "草木性譜",
    "edit_distance": 0.2,
    "length_ratio": 1.0,
    "det_score": 0.75,
    "image_on_hf": True,
}


def line_row(
    item_id: str,
    image_index: int,
    line_index: int,
    *,
    text: str = "草木性譜",
    plain_text: str | None = None,
    bbox: tuple[int, int, int, int] = (10, 20, 30, 40),
    image_url: str | None = None,
    licence: str = "CC-BY-4.0",
    licence_url: str = "https://creativecommons.org/licenses/by/4.0/",
    holder: str = "Example Library",
    split: str = "train",
) -> dict[str, Any]:
    """One `lines.jsonl.gz` row, with every field the importer reads."""
    plain = text if plain_text is None else plain_text
    x, y, w, h = bbox
    service = f"https://iiif.example.test/iiif/2/{item_id}/R{image_index:04d}"
    row = dict(LINE)
    row.update(
        {
            "image_id": f"{item_id}_{image_index:03d}_{line_index:03d}",
            "item_id": item_id,
            "image_index": image_index,
            "line_index": line_index,
            "iiif_image_url": image_url or f"{service}/full/full/0/default.jpg",
            "iiif_region_url": f"{service}/{x},{y},{w},{h}/full/0/default.jpg",
            "bbox": [x, y, w, h],
            "text": text,
            "plain_text": plain,
            "plain_len": len(plain),
            "image_license": licence,
            "image_license_url": licence_url,
            "holding_institution": holder,
            "split": split,
        }
    )
    return row


def fixture_rows(*, wrong_plain: bool = False) -> list[dict[str, Any]]:
    """Ten lines over three items: two with an `items.tsv` row, one without."""
    rows = [
        line_row(ITEM_A, 0, 0, text="草木《割書：《題：一》｜《題：二》》性譜", plain_text="草木題一二性譜"),
        line_row(ITEM_A, 0, 1, bbox=(50, 20, 30, 44)),
        line_row(ITEM_A, 0, 2, bbox=(90, 20, 30, 41)),
        line_row(ITEM_A, 1, 0, text="春はあけぼの", bbox=(10, 20, 30, 60)),
        line_row(ITEM_A, 1, 1, text="草木（くさき）性譜", plain_text="草木性譜", bbox=(50, 20, 30, 60)),
        line_row(
            ITEM_B,
            0,
            0,
            image_url="https://example.test/pages/plain-0001.jpg",
            licence="(unspecified)",
            licence_url="",
            holder="",
            split="val",
        ),
        line_row(
            ITEM_B,
            0,
            1,
            image_url="https://example.test/pages/plain-0001.jpg",
            licence="(unspecified)",
            licence_url="",
            holder="",
            split="val",
            bbox=(50, 20, 30, 40),
        ),
        line_row(
            ITEM_B,
            0,
            2,
            image_url="https://example.test/pages/plain-0001.jpg",
            licence="(unspecified)",
            licence_url="",
            holder="",
            split="val",
            bbox=(90, 20, 30, 40),
        ),
        line_row(
            ITEM_B,
            0,
            3,
            image_url="https://example.test/pages/plain-0001.jpg",
            licence="(unspecified)",
            licence_url="",
            holder="",
            split="val",
            bbox=(130, 20, 30, 40),
        ),
        line_row(
            ITEM_C,
            0,
            0,
            licence="PDM-1.0",
            licence_url="https://creativecommons.org/publicdomain/mark/1.0/",
            split="test",
        ),
    ]
    if wrong_plain:
        rows[-1]["plain_text"] = "草木性譜誤"
    return rows


def build_cache(tmp_path: Path, *, duplicate: str | None = "same", wrong_plain: bool = False) -> Path:
    """Write `lines.jsonl.gz` and `items.tsv` into a fixture cache directory and return it."""
    cache = tmp_path / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    with (cache / "items.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=ITEM_COLUMNS, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(ITEMS)
    rows = fixture_rows(wrong_plain=wrong_plain)
    if duplicate == "same":
        rows.append(dict(rows[0]))
    elif duplicate == "different":
        changed = dict(rows[0], text="草木性譜改", plain_text="草木性譜改")
        rows.append(changed)
    with gzip.open(cache / "lines.jsonl.gz", "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return cache


def imported(tmp_path: Path, **kwargs: Any) -> tuple[dict[str, int], tables.Dataset, Path]:
    """Import the fixture cache and return the counts, the dataset and its directory."""
    cache = build_cache(tmp_path, **kwargs)
    out = tmp_path / "work" / "honkoku-lines"
    counts = honkoku_lines.import_from(cache, out)
    return counts, tables.Dataset(out), out


def documents(dataset: tables.Dataset) -> dict[str, Any]:
    return {document.id: document for document in dataset.read("documents")}


def test_counts_cover_three_documents_four_pages_and_ten_lines(tmp_path):
    counts, dataset, out = imported(tmp_path)
    assert counts == {"documents": 3, "pages": 4, "lines": 10, "koji_mismatches": 0}
    assert (out / "MANIFEST.json").is_file()
    manifest = json.loads((out / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["command"] == honkoku_lines.COMMAND
    assert manifest["tables"] == {"documents": 3, "pages": 4, "lines": 10}
    assert dataset.validate() == []


def test_document_takes_its_item_row(tmp_path):
    _, dataset, _ = imported(tmp_path)
    document = documents(dataset)[f"hl:{ITEM_A}"]
    assert document.title == "試し A"
    assert document.holder == "Example Library"
    assert document.source_refs == {"honkoku-data": ITEM_A, "iiif-manifest": MANIFEST_A}
    assert document.image_rights.licence is Licence.CC_BY_4
    assert document.image_rights.evidence == "https://creativecommons.org/licenses/by/4.0/"
    assert document.text_rights.licence is Licence.CC_BY_SA_4
    assert "Honkoku-Lines" in document.text_rights.attribution
    assert document.meta["split"] == "train"
    assert document.meta["items_row"] is True
    assert document.meta["n_lines"] == 5
    assert document.meta["mean_align_distance"] == 0.05
    assert document.meta["image_license"] == "CC-BY-4.0"


def test_unspecified_licence_resolves_to_unknown(tmp_path):
    _, dataset, _ = imported(tmp_path)
    document = documents(dataset)[f"hl:{ITEM_B}"]
    assert document.image_rights.licence is Licence.UNKNOWN
    assert "(unspecified)" in document.image_rights.attribution
    assert document.text_rights.licence is Licence.CC_BY_SA_4


def test_item_absent_from_items_tsv_takes_the_line(tmp_path):
    _, dataset, _ = imported(tmp_path)
    document = documents(dataset)[f"hl:{ITEM_C}"]
    assert document.title == ITEM_C
    assert document.source_refs == {"honkoku-data": ITEM_C}
    assert document.meta["items_row"] is False
    assert document.meta["split"] == "test"
    assert document.image_rights.licence is Licence.PDM
    assert document.holder == "Example Library"


def test_page_carries_the_image_service_and_no_size(tmp_path):
    _, dataset, _ = imported(tmp_path)
    pages = {page.id: page for page in dataset.read("pages")}
    assert set(pages) == {f"hl:{ITEM_A}:0", f"hl:{ITEM_A}:1", f"hl:{ITEM_B}:0", f"hl:{ITEM_C}:0"}
    first = pages[f"hl:{ITEM_A}:0"]
    assert first.document_id == f"hl:{ITEM_A}"
    assert first.seq == 0
    assert first.image == f"https://iiif.example.test/iiif/2/{ITEM_A}/R0000"
    assert (first.width, first.height) == (0, 0)
    assert pages[f"hl:{ITEM_B}:0"].image == "https://example.test/pages/plain-0001.jpg"


def test_line_fields_come_from_the_upstream_row(tmp_path):
    _, dataset, _ = imported(tmp_path)
    lines = {line.id: line for line in dataset.read("lines")}
    line = lines[f"hl:{ITEM_A}_000_000"]
    assert line.page_id == f"hl:{ITEM_A}:0"
    assert line.seq == 0
    assert line.box.model_dump() == {"x": 10, "y": 20, "w": 30, "h": 40}
    assert line.text_raw == "草木《割書：《題：一》｜《題：二》》性譜"
    assert line.text == "草木題一二性譜"
    assert line.text == koji.parse(line.text_raw).plain
    assert line.match_method == "honkoku-lines-v2.0"
    assert line.match_confidence == pytest.approx(0.8)
    assert line.meta == {
        "split": "train",
        "det_score": 0.75,
        "edit_distance": 0.2,
        "length_ratio": 1.0,
        "ocr_text": "草木性譜",
        "image_on_hf": True,
        "iiif_region_url": f"https://iiif.example.test/iiif/2/{ITEM_A}/R0000/10,20,30,40/full/0/default.jpg",
    }


def test_duplicate_image_id_with_identical_rows_is_kept_once(tmp_path):
    counts, dataset, _ = imported(tmp_path, duplicate="same")
    ids = [line.id for line in dataset.read("lines")]
    assert counts["lines"] == 10
    assert len(ids) == len(set(ids)) == 10


def test_duplicate_image_id_with_different_rows_fails(tmp_path):
    cache = build_cache(tmp_path, duplicate="different")
    out = tmp_path / "work" / "honkoku-lines"
    with pytest.raises(tables.DuplicateIdError) as failure:
        honkoku_lines.import_from(cache, out)
    assert f"hl:{ITEM_A}_000_000" in str(failure.value)
    assert "草木性譜改" in str(failure.value)


def test_koji_mismatches_are_counted_and_written(tmp_path):
    counts, dataset, out = imported(tmp_path, wrong_plain=True)
    assert counts["koji_mismatches"] == 1
    with (out / "koji-mismatches.tsv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    assert len(rows) == 1
    assert rows[0]["image_id"] == f"{ITEM_C}_000_000"
    assert rows[0]["line_index"] == "0"
    assert rows[0]["plain_text"] == "草木性譜誤"
    assert rows[0]["parsed_plain"] == "草木性譜"
    assert {line.id for line in dataset.read("lines")} == {f"hl:{row['image_id']}" for row in fixture_rows()}


def test_no_mismatches_leaves_a_header_only_file(tmp_path):
    _, _, out = imported(tmp_path)
    text = (out / "koji-mismatches.tsv").read_text(encoding="utf-8")
    assert text == "\t".join(honkoku_lines.MISMATCH_COLUMNS) + "\n"


def test_items_filter_keeps_one_document(tmp_path):
    cache = build_cache(tmp_path)
    counts = honkoku_lines.import_from(cache, tmp_path / "filtered", items=[ITEM_A])
    assert counts == {"documents": 1, "pages": 2, "lines": 5, "koji_mismatches": 0}


def test_licence_filter_matches_without_case(tmp_path):
    cache = build_cache(tmp_path)
    out = tmp_path / "filtered"
    counts = honkoku_lines.import_from(cache, out, licences=["pdm-1.0"])
    assert counts == {"documents": 1, "pages": 1, "lines": 1, "koji_mismatches": 0}
    assert [document.id for document in tables.Dataset(out).read("documents")] == [f"hl:{ITEM_C}"]


def test_limit_stops_after_that_many_lines(tmp_path):
    cache = build_cache(tmp_path)
    counts = honkoku_lines.import_from(cache, tmp_path / "limited", limit=3)
    assert counts == {"documents": 1, "pages": 1, "lines": 3, "koji_mismatches": 0}


def test_rerun_writes_the_same_tables(tmp_path):
    counts, _, out = imported(tmp_path)
    before = _digests(out)
    cache = tmp_path / "cache"
    assert honkoku_lines.import_from(cache, out) == counts
    assert _digests(out) == before


def _digests(directory: Path) -> dict[str, str]:
    """The sha256 of every table file, keyed by its path inside the dataset directory."""
    return {
        str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*.parquet"))
    }


def test_filters_leave_nothing_of_the_previous_import(tmp_path):
    cache = build_cache(tmp_path)
    out = tmp_path / "work" / "honkoku-lines"
    honkoku_lines.import_from(cache, out)
    counts = honkoku_lines.import_from(cache, out, items=[ITEM_B])
    assert counts["lines"] == 4
    assert {document.id for document in tables.Dataset(out).read("documents")} == {f"hl:{ITEM_B}"}
    assert {line.id for line in tables.Dataset(out).read("lines")} == {
        f"hl:{ITEM_B}_000_00{index}" for index in range(4)
    }
