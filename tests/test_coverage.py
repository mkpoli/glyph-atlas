"""Tests for the coverage join across the transcription and line datasets.

Three small datasets are built in `tmp_path`: the transcriptions, Honkoku-Lines and the NDL
dataset. They share entries, spell their ids in different cases and number their pages the way their
upstream does, so the test fixes what the join counts.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from glyph_atlas import coverage, tables
from glyph_atlas.schema import Box, Document, Line, Page, PageText

#: Two entries the three datasets share, one only the line datasets know, one only the text.
ENTRY = "0A678AA21E602F6A3FFF3329B090920C"
OTHER = "1B678AA21E602F6A3FFF3329B090920D"
ONLY_LINES = "2C678AA21E602F6A3FFF3329B090920E"
ONLY_TEXT = "3D678AA21E602F6A3FFF3329B090920F"


def rows_of(path: Path) -> list[list[str]]:
    """The data rows of `coverage.tsv`: the `#` lines in front of the header are not rows."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return [line.split("\t") for line in lines if not line.startswith("#")]


def write_dataset(directory: Path, documents: list[Document], pages: list[Page], lines: list[Line]) -> Path:
    directory.mkdir(parents=True)
    tables.write(directory / "documents.parquet", documents, Document)
    tables.write(directory / "pages.parquet", pages, Page)
    if lines:
        tables.write(directory / "lines", lines, Line, shard=True)
    return directory


def text_dataset(root: Path) -> Path:
    """`work/honkoku-data`: three pages of one entry, two of another, each with a page text."""
    documents = [
        Document(id=f"hk:{ENTRY}", title="仮名文書"),
        Document(id=f"hk:{OTHER}", title="仮名文書 二"),
        Document(id=f"hk:{ONLY_TEXT}", title="仮名文書 三"),
    ]
    pages = [
        Page(id=f"hk:{ENTRY}:{number}", document_id=f"hk:{ENTRY}", seq=number, image="", width=0, height=0)
        for number in (1, 2, 3)
    ] + [
        Page(id=f"hk:{OTHER}:{number}", document_id=f"hk:{OTHER}", seq=number, image="", width=0, height=0)
        for number in (1, 2)
    ] + [
        Page(id=f"hk:{ONLY_TEXT}:1", document_id=f"hk:{ONLY_TEXT}", seq=1, image="", width=0, height=0)
    ]
    texts = [PageText(page_id=page.id, source="honkoku-data", revision="abc", text_raw="一\n") for page in pages]
    directory = write_dataset(root / "honkoku-data", documents, pages, [])
    tables.write(directory / "page_texts.parquet", texts, PageText)
    return directory


def lines_dataset(root: Path, name: str, prefix: str, seqs: dict[str, list[int]], zero_based: bool) -> Path:
    """A line dataset: `seqs` gives the page numbers of each entry that carry a line."""
    documents = []
    pages = []
    lines = []
    for entry, numbers in seqs.items():
        document_id = f"{prefix}{entry}"
        documents.append(Document(id=document_id, title="仮名文書"))
        for number in numbers:
            seq = number - 1 if zero_based else number
            page_id = f"{document_id}:{number}"
            pages.append(Page(id=page_id, document_id=document_id, seq=seq, image="", width=0, height=0))
            lines.append(
                Line(
                    id=f"{page_id}:1",
                    page_id=page_id,
                    seq=0,
                    box=Box(x=1, y=2, w=3, h=4),
                    text_raw="一",
                    text="一",
                )
            )
    return write_dataset(root / name, documents, pages, lines)


@pytest.fixture
def datasets(tmp_path: Path) -> list[Path]:
    """The three datasets: みんなで翻刻データ, Honkoku-Lines and the NDL dataset."""
    return [
        text_dataset(tmp_path),
        lines_dataset(tmp_path, "honkoku-lines", "hl:", {ENTRY: [1, 2], OTHER: [1], ONLY_LINES: [1]}, zero_based=True),
        lines_dataset(tmp_path, "ndl-minhon", "ndl-minhon:v2:demo:", {ENTRY: [2]}, zero_based=False),
    ]


def test_build_joins_the_three_datasets_on_the_entry_id(datasets: list[Path], tmp_path: Path) -> None:
    out = tmp_path / "coverage.tsv"
    counts = coverage.build(datasets, out)
    text = out.read_text(encoding="utf-8")
    assert text.splitlines()[: len(coverage.NOTES)] == list(coverage.NOTES)
    assert rows_of(out) == [
        list(coverage.COLUMNS),
        f"{ENTRY}\t3\t2\t1\t1".split("\t"),
        f"{OTHER}\t2\t1\t0\t1".split("\t"),
        f"{ONLY_LINES}\t0\t1\t0\t0".split("\t"),
        f"{ONLY_TEXT}\t1\t0\t0\t1".split("\t"),
    ]
    assert counts == {
        "datasets": 3,
        "entries": 4,
        "documents_skipped": 0,
        "lines_without_pages": 0,
        "pages_with_text": 6,
        "pages_with_lines_honkoku_lines": 4,
        "pages_with_lines_ndl_minhon": 1,
        "entries_with_text_without_lines": 1,
        "pages_with_text_no_lines_shared": 2,
        "pages_with_text_no_lines": 3,
    }


def test_pages_are_compared_by_their_number_within_the_entry(datasets: list[Path], tmp_path: Path) -> None:
    """Honkoku-Lines numbers its pages from zero, so its page `0` is page 1 of the transcription.

    Counting the Honkoku-Lines sequence as it stands would leave pages 2 and 3 of the first entry
    uncovered instead of page 3 alone.
    """
    out = tmp_path / "coverage.tsv"
    coverage.build(datasets, out)
    rows = {row[0]: row[1:] for row in rows_of(out)[1:]}
    assert rows[ENTRY] == ["3", "2", "1", "1"]
    assert rows[OTHER] == ["2", "1", "0", "1"]


def test_the_entry_of_a_document_comes_from_source_refs(datasets: list[Path], tmp_path: Path) -> None:
    """A document that states the entry joins it, whatever its id says; a document that names no
    entry is skipped."""
    other = tmp_path / "other"
    other.mkdir()
    tables.write(
        other / "documents.parquet",
        [
            Document(id="ndl-minhon:v2:demo:999", title="仮名文書 二", source_refs={"honkoku-data": OTHER}),
            Document(id="codh:900000001", title="別の資料"),
        ],
        Document,
    )
    tables.write(
        other / "pages.parquet",
        [
            Page(
                id="ndl-minhon:v2:demo:999:2",
                document_id="ndl-minhon:v2:demo:999",
                seq=2,
                image="",
                width=0,
                height=0,
            )
        ],
        Page,
    )
    tables.write(
        other / "lines",
        [
            Line(
                id="ndl-minhon:v2:demo:999:2:1",
                page_id="ndl-minhon:v2:demo:999:2",
                seq=0,
                text_raw="一",
                text="一",
            )
        ],
        Line,
        shard=True,
    )
    counts = coverage.build([*datasets, other], tmp_path / "coverage.tsv")
    assert counts["documents_skipped"] == 1
    assert counts["entries"] == 4
    assert counts["pages_with_lines_ndl_minhon"] == 2
    assert counts["pages_with_text_no_lines"] == 2
    assert counts["pages_with_text_no_lines_shared"] == 1  # the second entry is covered now
    assert counts["entries_with_text_without_lines"] == 1  # the entry only the transcription has


def test_a_missing_dataset_is_skipped_with_a_warning(datasets: list[Path], tmp_path: Path) -> None:
    with pytest.warns(RuntimeWarning, match="no such dataset"):
        counts = coverage.build([datasets[0], tmp_path / "nowhere"], tmp_path / "coverage.tsv")
    assert counts["datasets"] == 1
    assert counts["pages_with_text"] == 6
    assert counts["pages_with_lines_honkoku_lines"] == 0
    assert counts["entries_with_text_without_lines"] == 3


def test_ids_are_matched_without_case(tmp_path: Path) -> None:
    """The transcription spells the entry in lower case and Honkoku-Lines in upper case."""
    lines = lines_dataset(tmp_path, "honkoku-lines", "hl:", {ENTRY.upper(): [1]}, zero_based=True)
    text = tmp_path / "honkoku-data"
    text.mkdir()
    tables.write(text / "documents.parquet", [Document(id=f"hk:{ENTRY.lower()}", title="仮名文書")], Document)
    tables.write(
        text / "pages.parquet",
        [Page(id=f"hk:{ENTRY.lower()}:1", document_id=f"hk:{ENTRY.lower()}", seq=1, image="", width=0, height=0)],
        Page,
    )
    tables.write(
        text / "page_texts.parquet",
        [PageText(page_id=f"hk:{ENTRY.lower()}:1", source="honkoku-data", revision="abc", text_raw="一\n")],
        PageText,
    )
    counts = coverage.build([lines, text], tmp_path / "coverage.tsv")
    assert counts["entries"] == 1
    assert counts["pages_with_lines_honkoku_lines"] == 1
    assert counts["pages_with_text_no_lines"] == 0
    row = rows_of(tmp_path / "coverage.tsv")[1]
    assert row[0] == ENTRY.lower()


def test_a_blank_transcription_is_not_text(tmp_path: Path) -> None:
    """A page whose text file is empty has nothing for a line dataset to cover."""
    directory = tmp_path / "honkoku-data"
    directory.mkdir()
    tables.write(directory / "documents.parquet", [Document(id=f"hk:{ENTRY}", title="仮名文書")], Document)
    tables.write(
        directory / "pages.parquet",
        [
            Page(id=f"hk:{ENTRY}:{number}", document_id=f"hk:{ENTRY}", seq=number, image="", width=0, height=0)
            for number in (1, 2)
        ],
        Page,
    )
    tables.write(
        directory / "page_texts.parquet",
        [
            PageText(page_id=f"hk:{ENTRY}:1", source="honkoku-data", revision="abc", text_raw="一\n"),
            PageText(page_id=f"hk:{ENTRY}:2", source="honkoku-data", revision="abc", text_raw="\n"),
        ],
        PageText,
    )
    counts = coverage.build([directory], tmp_path / "coverage.tsv")
    assert counts["pages_with_text"] == 1
    assert counts["pages_with_text_no_lines"] == 1
    assert rows_of(tmp_path / "coverage.tsv")[1] == f"{ENTRY}\t1\t0\t0\t1".split("\t")
    assert counts["entries_with_text_without_lines"] == 1


def test_a_repository_path_in_source_refs_names_the_entry(tmp_path: Path) -> None:
    """`work/ndl-minhon` states the entry as the path it has in the みんなで翻刻データ repository."""
    lines = tmp_path / "ndl-minhon"
    lines.mkdir()
    document = Document(id="ndl-minhon:v2:demo:999", title="仮名文書", source_refs={"honkoku-data": f"demo/{ENTRY}"})
    tables.write(lines / "documents.parquet", [document], Document)
    tables.write(
        lines / "pages.parquet",
        [Page(id="ndl-minhon:v2:demo:999:001", document_id=document.id, seq=1, image="", width=0, height=0)],
        Page,
    )
    tables.write(
        lines / "lines",
        [
            Line(
                id="ndl-minhon:v2:demo:999:001:1",
                page_id="ndl-minhon:v2:demo:999:001",
                seq=0,
                text_raw="一",
                text="一",
            )
        ],
        Line,
        shard=True,
    )
    text = tmp_path / "honkoku-data"
    text.mkdir()
    tables.write(text / "documents.parquet", [Document(id=f"hk:{ENTRY}", title="仮名文書")], Document)
    tables.write(
        text / "pages.parquet",
        [Page(id=f"hk:{ENTRY}:1", document_id=f"hk:{ENTRY}", seq=1, image="", width=0, height=0)],
        Page,
    )
    tables.write(
        text / "page_texts.parquet",
        [PageText(page_id=f"hk:{ENTRY}:1", source="honkoku-data", revision="abc", text_raw="一\n")],
        PageText,
    )
    counts = coverage.build([lines, text], tmp_path / "coverage.tsv")
    assert counts["entries"] == 1
    assert counts["pages_with_lines_ndl_minhon"] == 1
    assert counts["pages_with_text_no_lines"] == 0
