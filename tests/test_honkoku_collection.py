"""Tests for the honkoku-collection user-project importer."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
import yaml

from glyph_atlas import tables
from glyph_atlas.importers import honkoku_collection, honkoku_data, honkoku_queue
from glyph_atlas.schema import Document, Licence, Line, Page, PageText, Rights

ENTRY = "ABCDEF00000000000000000000000000"
OTHER = "FEDCBA00000000000000000000000000"
PENDING = "11111100000000000000000000000000"
MANIFEST = "https://example.test/manifest.json"


def selection(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.write_text(yaml.safe_dump({"projects": rows}, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def collection_root(tmp_path: Path) -> Path:
    root = tmp_path / "collection"
    root.mkdir()
    with sqlite3.connect(root / honkoku_queue.QUEUE_FILE) as db:
        db.executescript(honkoku_queue.QUEUE_SCHEMA)
    return root


def insert_book(root: Path, entry: str, project: str, *, state: str = "done") -> None:
    dataset = f"books/{entry}"
    with sqlite3.connect(root / honkoku_queue.QUEUE_FILE) as db:
        db.execute(
            "INSERT INTO books(entry_id, project_id, collection_id, label, state, dataset) VALUES(?,?,?,?,?,?)",
            (entry, project, "collection", entry, state, dataset),
        )


def write_book(
    root: Path,
    entry: str,
    project: str,
    *,
    texts: list[str],
    images: list[str] | None = None,
    image_rights: Rights | None = None,
    manifest: str = "",
    state: str = "done",
) -> None:
    insert_book(root, entry, project, state=state)
    directory = root / "books" / entry
    directory.mkdir(parents=True)
    document = Document(
        id=f"hk:{entry}",
        title=f"Book {entry[:4]}",
        source_refs={"honkoku-data": entry, "iiif-manifest": manifest},
        holder="Example Library",
        image_rights=image_rights or Rights(licence=Licence.CC_BY_SA_4, holder="Example Library", attribution="CC"),
        text_rights=Rights(licence=Licence.CC_BY_SA_4, attribution="Text"),
        meta={"kept": "yes"},
    )
    pages = []
    page_texts = []
    for index, text in enumerate(texts, start=1):
        page_id = f"hk:{entry}:{index}"
        pages.append(
            Page(
                id=page_id,
                document_id=document.id,
                seq=index - 1,
                canvas=f"https://example.test/canvas/{entry}/{index}",
                image=(images or [f"https://example.test/image/{entry}/{index}"] * len(texts))[index - 1],
                width=1000,
                height=800,
            )
        )
        page_texts.append(PageText(page_id=page_id, source="honkoku-api", revision="r1", text_raw=text))
    tables.write(directory / "documents.parquet", [document], Document)
    tables.write(directory / "pages.parquet", pages, Page)
    tables.write(directory / "page_texts.parquet", page_texts, PageText)


def write_honkoku_lines(root: Path, entry: str, *, image_index: int) -> Path:
    directory = root / "honkoku-lines"
    directory.mkdir()
    document = Document(id=f"hl:{entry.casefold()}", title="Line source", source_refs={"honkoku-data": entry.casefold()})
    page = Page(
        id=f"hl:{entry.casefold()}:{image_index}",
        document_id=document.id,
        seq=image_index,
        image="https://example.test/line.jpg",
        width=100,
        height=100,
    )
    tables.write(directory / "documents.parquet", [document], Document)
    tables.write(directory / "pages.parquet", [page], Page)
    return directory


def test_filters_selection_done_books_and_honkoku_lines_coverage(tmp_path: Path) -> None:
    root = collection_root(tmp_path)
    write_book(root, ENTRY, "demo", texts=["covered\n", "kept\n"])
    write_book(root, OTHER, "other", texts=["ignored\n"])
    write_book(root, PENDING, "demo", texts=["pending\n"], state="pending")
    source = selection(
        tmp_path / "projects.yaml",
        [{"id": "demo", "practice": True, "guidelines": "own"}, {"id": "empty", "practice": False, "guidelines": "platform"}],
    )
    lines = write_honkoku_lines(tmp_path, ENTRY.casefold(), image_index=0)

    out = tmp_path / "out"
    counts = honkoku_collection.import_all(out, collection=root, selection=source, honkoku_lines=lines)

    assert counts["projects"] == 2
    assert counts["projects_with_books"] == 1
    assert counts["books"] == 1
    assert counts["documents"] == 1
    assert counts["pages"] == 1
    assert counts["page_texts"] == 1
    assert counts["lines"] == 1
    assert counts["pages_covered_by_honkoku_lines"] == 1
    documents = tables.read(out / "documents.parquet", Document)
    assert [document.id for document in documents] == [f"hk:{ENTRY}"]
    assert documents[0].source_refs["honkoku-project"] == "demo"
    assert documents[0].meta["honkoku_project"] == {
        "id": "demo",
        "type": "user",
        "practice": True,
        "guidelines": "own",
    }
    assert [page.seq for page in tables.read(out / "pages.parquet", Page)] == [1]
    assert tables.Dataset(out).validate() == []


def test_counts_pages_without_image_and_text(tmp_path: Path) -> None:
    root = collection_root(tmp_path)
    write_book(root, ENTRY, "demo", texts=["has text", "   ", "also text"], images=["", "image", "image"])
    source = selection(tmp_path / "projects.yaml", [{"id": "demo", "practice": False, "guidelines": "own"}])

    counts = honkoku_collection.import_all(tmp_path / "out", collection=root, selection=source)

    assert counts["pages_without_image"] == 1
    assert counts["pages_without_text"] == 1
    assert counts["pages"] == 1


def test_manifest_rights_fallback_reads_only_the_cache(
    tmp_path: Path, monkeypatch: Any
) -> None:
    cache = tmp_path / "cache"
    monkeypatch.setenv(honkoku_data.ENV_CACHE, str(cache))
    path = honkoku_data.manifest_path(MANIFEST)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "@context": "http://iiif.io/api/presentation/2/context.json",
                "@id": MANIFEST,
                "@type": "sc:Manifest",
                "license": "https://creativecommons.org/licenses/by/4.0/",
                "attribution": "Manifest Library",
            }
        ),
        encoding="utf-8",
    )
    root = collection_root(tmp_path)
    write_book(
        root,
        ENTRY,
        "demo",
        texts=["text"],
        image_rights=Rights(licence=Licence.UNKNOWN, holder="Example Library", attribution="unknown"),
        manifest=MANIFEST,
    )
    source = selection(tmp_path / "projects.yaml", [{"id": "demo", "practice": False, "guidelines": "own"}])

    counts = honkoku_collection.import_all(tmp_path / "out", collection=root, selection=source)
    document = tables.read(tmp_path / "out" / "documents.parquet", Document)[0]

    assert counts["manifest_rights_applied"] == 1
    assert document.image_rights is not None
    assert document.image_rights.licence is Licence.CC_BY_4
    assert document.image_rights.holder == "Example Library"


def test_query_marks_become_koji_notes_but_page_text_stays_verbatim(tmp_path: Path) -> None:
    root = collection_root(tmp_path)
    write_book(root, ENTRY, "marked", texts=["漢？\n　かな？  \n"])
    source = selection(tmp_path / "projects.yaml", [{"id": "marked", "practice": True, "guidelines": "own"}])

    out = tmp_path / "out"
    counts = honkoku_collection.import_all(out, collection=root, selection=source)
    lines = tables.read(out / "lines.parquet", Line)
    text = tables.read(out / "page_texts.parquet", PageText)[0]

    assert counts["lines"] == 2
    assert text.text_raw == "漢？\n　かな？  \n"
    assert [line.text_raw for line in lines] == ["漢【？】", "　かな【？】"]
    assert [line.text for line in lines] == ["漢", "かな"]
    assert lines[0].meta["transcriber_text"] == "漢？"
    assert lines[1].meta["transcriber_text"] == "　かな？"
    assert lines[1].meta["transcription_index"] == 0
    assert lines[1].meta["line_position"] == 1
    assert lines[0].meta["source"] == "honkoku-collection"
    assert tables.Dataset(out).validate() == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("漢【？】字", "漢【？】字"),  # already a note
        ("【作者？】字", "【作者？】字"),  # inside a note
        ("漢（かん？）字", "漢（かん？）字"),  # inside a reading
        ("え？と？", "え【？】と【？】"),
        ("後に「？」を", "後に「【？】」を"),
        ("字?", "字【?】"),
    ],
)
def test_only_query_marks_of_the_document_text_become_notes(raw: str, expected: str) -> None:
    converted = honkoku_collection.query_marks_as_notes(raw)
    assert converted == expected
    assert "？" not in honkoku_collection.plain(converted).replace("】", "")
    assert "】" not in honkoku_collection.plain(converted)


def test_a_zero_width_image_request_becomes_the_service_base() -> None:
    url = "https://www.digital-archives.pref.fukui.lg.jp/iiif/2/1144994/full/0,/0/default.jpg"
    assert honkoku_collection.fetchable_image(url) == "https://www.digital-archives.pref.fukui.lg.jp/iiif/2/1144994"
    kept = "https://dl.ndl.go.jp/api/iiif/9892494/R0000001/full/5166,/0/default.jpg"
    assert honkoku_collection.fetchable_image(kept) == kept
