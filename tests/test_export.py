"""Tests of the release build: filters, closure, derived columns and the release documents."""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from kuzushiji_atlas import export, tables
from kuzushiji_atlas.schema import (
    Box,
    Classification,
    Document,
    Group,
    Licence,
    Line,
    Page,
    PageText,
    Production,
    Register,
    ReviewState,
    Rights,
    Script,
    Unit,
    UnitKind,
)

# The fixture's arithmetic, in one place.
#   codh:A  images CC BY 4.0, text bespoke-free   3 units (2 machine, 1 transcriber), 2 pages
#   codh:B  images CC BY-NC 4.0, text CC BY 4.0   1 unit  (transcriber), 1 page
#   codh:C  no rights at all                      1 unit, and never in a release, because a
#                                                  document that states no terms is not admitted
TRANSCRIBER = {"units": 2, "lines": 2, "pages": 2, "documents": 2}
TRANSCRIBER_AND_MACHINE = {"units": 4, "lines": 4, "pages": 3, "documents": 2}

FREE = Rights(
    licence=Licence.CC_BY_4,
    holder="東京大学史料編纂所",
    attribution="東京大学史料編纂所「くずし字データセット」",
    evidence="https://lab.hi.u-tokyo.ac.jp/datasets/kuzushiji",
)
BESPOKE = Rights(
    licence=Licence.BESPOKE_FREE,
    holder="国立公文書館",
    attribution="国立公文書館デジタルアーカイブ",
    evidence="https://www.digital.archives.go.jp/secondary-use",
)
NC = Rights(
    licence=Licence.CC_BY_NC_4,
    holder="example",
    attribution="example holder",
    evidence="https://creativecommons.org/licenses/by-nc/4.0/",
)


def page(page_id: str, document_id: str, seq: int) -> Page:
    return Page(
        id=page_id,
        document_id=document_id,
        seq=seq,
        image=f"https://example.test/iiif/{page_id}.tif",
        width=1000,
        height=1400,
    )


def line(line_id: str, page_id: str, seq: int, text: str) -> Line:
    return Line(
        id=line_id,
        page_id=page_id,
        seq=seq,
        box=Box(x=10, y=10 + seq * 100, w=80, h=80),
        text_raw=text,
        text=text,
        meta={"split": "train"},
    )


def unit(
    unit_id: str,
    page_id: str,
    line_id: str,
    seq: int,
    *,
    text: str,
    reading: str,
    code_point: str,
    script: Script,
    review: ReviewState,
    method: str,
    document_id: str,
) -> Unit:
    return Unit(
        id=unit_id,
        document_id=document_id,
        page_id=page_id,
        line_id=line_id,
        seq=seq,
        box=Box(x=10, y=10 + seq * 100, w=40, h=40),
        text_source=text,
        reading=reading,
        unicode=code_point,
        classification=Classification.IDENTIFIED,
        script=script,
        method="import" if method == "import" else "detect-align",
        review=review,
        upstream={"source": "codh-char-shape", "ref": unit_id},
        meta={"split": "train"},
    )


@pytest.fixture
def dataset(tmp_path: Path) -> Path:
    """Three documents that exercise the three rights cases and both sides of the review filter.

    `codh:A` may be redistributed in text and in image; its units are machine proposals except one a
    transcriber labelled. `codh:B` may go out in text but not in image, which makes it the document
    that keeps its records and gets no crop. `codh:C` states no terms at all, so it is absent.
    """
    directory = tmp_path / "work" / "fixture"
    directory.mkdir(parents=True)
    documents = [
        Document(
            id="codh:A",
            title="machine book",
            holder="国文学研究資料館",
            production=Production.WOODBLOCK,
            genre=["monogatari"],
            text_register=Register.WABUN,
            dating=[{"literal": "文政3", "start": 1820, "end": 1820, "kind": "publication"}],
            image_rights=FREE,
            text_rights=BESPOKE,
        ),
        Document(
            id="codh:B",
            title="reviewed book",
            holder="例",
            production=Production.MANUSCRIPT,
            image_rights=NC,
            text_rights=FREE,
        ),
        Document(id="codh:C", title="no terms", image_rights=None, text_rights=None),
    ]
    pages = [
        page("codh:A:p1", "codh:A", 1),
        page("codh:A:p2", "codh:A", 2),
        page("codh:B:p1", "codh:B", 1),
        page("codh:C:p1", "codh:C", 1),
    ]
    lines = [
        line("codh:A:p1:L1", "codh:A:p1", 1, "か"),
        line("codh:A:p1:L2", "codh:A:p1", 2, "國"),
        line("codh:A:p2:L1", "codh:A:p2", 1, "あ"),
        line("codh:B:p1:L1", "codh:B:p1", 1, "い"),
        line("codh:C:p1:L1", "codh:C:p1", 1, "う"),
    ]
    units = [
        unit("codh:A:u1", "codh:A:p1", "codh:A:p1:L1", 1, text="か", reading="か",
             code_point="U+1B019", script=Script.HENTAIGANA, review=ReviewState.MACHINE,
             method="detect-align", document_id="codh:A"),
        unit("codh:A:u2", "codh:A:p1", "codh:A:p1:L2", 1, text="國", reading="くに",
             code_point="U+570B", script=Script.KANJI, review=ReviewState.MACHINE,
             method="detect-align", document_id="codh:A"),
        unit("codh:A:u3", "codh:A:p2", "codh:A:p2:L1", 1, text="あ", reading="あ",
             code_point="U+3042", script=Script.HIRAGANA, review=ReviewState.TRANSCRIBER,
             method="manual", document_id="codh:A"),
        unit("codh:B:u1", "codh:B:p1", "codh:B:p1:L1", 1, text="い", reading="い",
             code_point="U+3044", script=Script.HIRAGANA, review=ReviewState.TRANSCRIBER,
             method="manual", document_id="codh:B"),
        unit("codh:C:u1", "codh:C:p1", "codh:C:p1:L1", 1, text="う", reading="う",
             code_point="U+3046", script=Script.HIRAGANA, review=ReviewState.TRANSCRIBER,
             method="manual", document_id="codh:C"),
    ]
    # A transcriber-labelled unit that stands in a 連綿 group of its own.
    units[2].group_id = "codh:A:g1"
    groups = [Group(id="codh:A:g1", page_id="codh:A:p2", box=Box(x=10, y=10, w=40, h=40),
                    unit_ids=["codh:A:u3"])]
    page_texts = [PageText(page_id="codh:B:p1", source="honkoku-data", revision="abc", text_raw="い")]
    tables.write(directory / "documents.parquet", documents, Document)
    tables.write(directory / "pages.parquet", pages, Page)
    tables.write(directory / "lines.parquet", lines, Line)
    tables.write(directory / "units.parquet", units, Unit)
    tables.write(directory / "groups.parquet", groups, Group)
    tables.write(directory / "page_texts.parquet", page_texts, PageText)
    tables.Dataset(directory).merge([], directory, command="atlas import codh --all")
    return directory


def flat(text: str) -> str:
    """The text with its line breaks collapsed, so a figure can be looked for across a wrap."""
    return " ".join(text.split())


def units_of(directory: Path) -> list[dict]:
    path = directory / "units.parquet"
    if not path.exists():
        path = next((directory / "units").glob("*.parquet"))
    return pq.read_table(path).to_pylist()


def test_a_release_writes_the_merged_tables_filtered_by_review(dataset: Path, tmp_path: Path):
    out = tmp_path / "out" / "0.1"
    counts = export.release([dataset], out, review=[ReviewState.TRANSCRIBER.value])
    # Two of the four units are transcriber-labelled, one on each page of codh:A; codh:B is a
    # document whose text may go out, so it is in the release even though its images may not be.
    for name, value in TRANSCRIBER.items():
        assert counts[name] == value, name
    assert tables.Dataset(out).validate() == []
    rows = units_of(out)
    assert {row["id"] for row in rows} == {"codh:A:u3", "codh:B:u1"}
    assert {document.id for document in tables.read(out / "documents.parquet", Document)} == {
        "codh:A", "codh:B"
    }


def test_the_closure_only_keeps_lines_and_pages_a_kept_unit_needs(dataset: Path, tmp_path: Path):
    out = tmp_path / "out" / "0.1"
    export.release([dataset], out, review=[ReviewState.TRANSCRIBER.value])
    lines = tables.read(out / "lines.parquet", Line)
    pages = tables.read(out / "pages.parquet", Page)
    assert {line.id for line in lines} == {"codh:A:p2:L1", "codh:B:p1:L1"}
    assert {page.id for page in pages} == {"codh:A:p2", "codh:B:p1"}
    # codh:A:p1 holds only machine units, so neither the page nor its lines are in this release.
    assert "codh:A:p1" not in {page.id for page in pages}


def test_the_review_filter_keeps_machine_units_when_asked(dataset: Path, tmp_path: Path):
    out = tmp_path / "out" / "0.1-machine"
    counts = export.release(
        [dataset],
        out,
        review=[ReviewState.TRANSCRIBER.value],
        include_machine=True,
    )
    for name, value in TRANSCRIBER_AND_MACHINE.items():
        assert counts[name] == value, name
    reviews = {str(row["review"]) for row in units_of(out)}
    assert reviews == {"machine", "transcriber"}


def test_a_release_with_nothing_admitted_fails_loudly(dataset: Path, tmp_path: Path):
    out = tmp_path / "out" / "empty"
    with pytest.raises(export.ExportError, match="no unit"):
        export.release([dataset], out, review=[ReviewState.ADJUDICATED.value])
    with pytest.raises(export.ExportError, match="no document"):
        export.release([dataset], out, review=[ReviewState.TRANSCRIBER.value], licence=Licence.UNKNOWN)


def test_derived_columns_follow_the_named_policy(dataset: Path, tmp_path: Path):
    out = tmp_path / "out" / "0.1-policy"
    export.release([dataset], out, review=[ReviewState.TRANSCRIBER.value], include_machine=True)
    rows = {row["id"]: row for row in units_of(out)}
    assert rows["codh:A:u1"]["modern_kana"] == "か"
    assert rows["codh:A:u1"]["unicode"] == "U+1B019"
    assert rows["codh:A:u1"]["shinji"] is None
    assert rows["codh:A:u2"]["shinji"] == "国"
    assert rows["codh:A:u2"]["unicode"] == "U+570B"
    assert rows["codh:A:u2"]["modern_kana"] is None
    # An ordinary hiragana already is the modern kana, so the policy adds nothing for it.
    assert rows["codh:A:u3"]["modern_kana"] is None
    assert rows["codh:A:u3"]["shinji"] is None
    assert set(rows) == {"codh:A:u1", "codh:A:u2", "codh:A:u3", "codh:B:u1"}


def test_the_policy_name_and_version_are_written_into_the_manifest(dataset: Path, tmp_path: Path):
    out = tmp_path / "out" / "0.1-manifest"
    export.release([dataset], out, review=[ReviewState.TRANSCRIBER.value])
    manifest = json.loads((out / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["normalisation"]["policy"] == "export-v1"
    assert manifest["normalisation"]["version"] == 1
    assert manifest["normalisation"]["columns"] == ["modern_kana", "shinji"]
    assert manifest["release"]["filters"]["review"] == ["transcriber"]
    assert manifest["inputs"][0]["tables"]["units"] == 5


def test_every_release_document_is_written(dataset: Path, tmp_path: Path):
    out = tmp_path / "out" / "0.2"
    export.release([dataset], out, review=[ReviewState.TRANSCRIBER.value], include_machine=True,
                   command="atlas export work/fixture --out out/0.2")
    for name in ("COUNTS.md", "ATTRIBUTION.md", "datasheet.md", "CHECKSUMS.txt", "MANIFEST.json",
                 "README.md", "zenodo.json"):
        assert (out / name).is_file(), name
    datasheet = (out / "datasheet.md").read_text(encoding="utf-8")
    assert "{{" not in datasheet
    assert "4 units in 4 lines, 3 pages and 2 documents" in flat(datasheet)
    checksums = (out / "CHECKSUMS.txt").read_text(encoding="utf-8")
    assert "documents.parquet" in checksums and "datasheet.md" in checksums
    assert "MANIFEST.json" not in checksums


def test_the_datasheet_numbers_equal_counts_md(dataset: Path, tmp_path: Path):
    out = tmp_path / "out" / "0.3"
    export.release([dataset], out, review=[ReviewState.TRANSCRIBER.value], include_machine=True)
    counts = (out / "COUNTS.md").read_text(encoding="utf-8")
    datasheet = (out / "datasheet.md").read_text(encoding="utf-8")
    # `COUNTS.md` counts the tables it holds; the datasheet is the prose that quotes them, and the
    # same numbers have to appear in both.
    for figure in ("| units | 4 |", "| lines | 4 |", "| pages | 3 |", "| documents | 2 |",
                   "| detect-align | 4 |", "| codh-char-shape | 4 |"):
        assert figure in flat(counts), figure
    for figure in ("4 units in 4 lines, 3 pages and 2 documents", "detect-align 4",
                   "codh-char-shape 4"):
        assert figure in flat(datasheet), figure


def test_an_unfilled_placeholder_fails_the_export(dataset: Path, tmp_path: Path):
    template = tmp_path / "template.md"
    template.write_text("# Datasheet\n\nunits {{counts.units}} and {{counts.nothing_here}}\n",
                        encoding="utf-8")
    out = tmp_path / "out" / "0.4"
    export.release([dataset], out, review=[ReviewState.TRANSCRIBER.value], include_machine=True)
    from kuzushiji_atlas import reconcile

    figures = export.figures_of(
        out,
        policy=export.policy("export-v1"),
        filters={"licence": "CC-BY-SA-4.0", "review": ["reviewed"], "crops": False, "limit": None},
        command="atlas export",
        version="0.4",
        inputs=export.input_manifests([dataset]),
    )
    with pytest.raises(export.ExportError, match="counts.nothing_here"):
        export.datasheet(figures, template=template)
    # The shipped template fills completely.
    assert "{{" not in export.datasheet(figures)
    assert reconcile is not None


def test_a_document_with_ineligible_images_keeps_its_records_without_crops(dataset: Path, tmp_path: Path):
    out = tmp_path / "out" / "0.5"
    counts = export.release(
        [dataset],
        out,
        review=[ReviewState.TRANSCRIBER.value],
        include_machine=True,
        crops=True,
    )
    assert counts["units"] == 4
    assert counts["crops"] == 0
    # codh:B is in the release with its coordinates and no crop, because its text may go out and its
    # images may not; no page image is cached, so nothing is materialised either way.
    documents = {document.id for document in tables.read(out / "documents.parquet", Document)}
    assert documents == {"codh:A", "codh:B"}
    rows = units_of(out)
    assert all(row["box"] is not None for row in rows)
    assert export.images_ok(next(d for d in tables.read(out / "documents.parquet", Document)
                                 if d.id == "codh:B"), Licence.CC_BY_SA_4) is False


def test_a_document_whose_text_is_ineligible_is_absent_altogether(dataset: Path, tmp_path: Path):
    out = tmp_path / "out" / "0.6"
    export.release([dataset], out, review=[ReviewState.TRANSCRIBER.value])
    kept = {document.id for document in tables.read(out / "documents.parquet", Document)}
    assert kept == {"codh:A", "codh:B"}
    assert "codh:C" not in kept
    # No record of the excluded document survives: not a unit, not a page, not a line.
    assert all(not row["id"].startswith("codh:C") for row in units_of(out))
    assert all(page.document_id != "codh:C" for page in tables.read(out / "pages.parquet", Page))


def test_limit_caps_the_units_for_a_scratch_build(dataset: Path, tmp_path: Path):
    out = tmp_path / "out" / "0.8"
    counts = export.release(
        [dataset], out, review=[ReviewState.TRANSCRIBER.value], include_machine=True, limit=2
    )
    assert counts["units"] == 2
    assert tables.Dataset(out).validate() == []


def test_groups_survive_only_when_every_unit_of_them_does(dataset: Path, tmp_path: Path):
    out = tmp_path / "out" / "0.9"
    export.release([dataset], out, review=[ReviewState.TRANSCRIBER.value])
    groups = tables.read(out / "groups.parquet", Group)
    # The group holds one transcriber unit, which the default filter keeps, so it survives.
    assert [group.id for group in groups] == ["codh:A:g1"]
    out2 = tmp_path / "out" / "0.10"
    export.release([dataset], out2, review=[ReviewState.TRANSCRIBER.value], include_machine=True)
    groups = tables.read(out2 / "groups.parquet", Group)
    assert [group.id for group in groups] == ["codh:A:g1"]
    assert groups[0].unit_ids == ["codh:A:u3"]


def test_a_page_text_follows_its_page(dataset: Path, tmp_path: Path):
    out = tmp_path / "out" / "0.11"
    export.release([dataset], out, review=[ReviewState.TRANSCRIBER.value])
    texts = tables.read(out / "page_texts.parquet", PageText)
    assert [row.page_id for row in texts] == ["codh:B:p1"]


def test_the_policy_none_adds_no_columns(dataset: Path, tmp_path: Path):
    out = tmp_path / "out" / "0.12"
    export.release([dataset], out, review=[ReviewState.TRANSCRIBER.value], normalisation="none")
    row = units_of(out)[0]
    assert "modern_kana" not in row and "shinji" not in row


def test_an_unknown_policy_fails(dataset: Path, tmp_path: Path):
    with pytest.raises(export.ExportError, match="unknown normalisation policy"):
        export.release([dataset], tmp_path / "out" / "0.13", normalisation="export-v9")


def test_a_release_needs_an_input(dataset: Path, tmp_path: Path):
    with pytest.raises(export.ExportError, match="at least one input"):
        export.release([], tmp_path / "out" / "0.14")
    with pytest.raises(export.ExportError, match="holds no dataset tables"):
        export.release([tmp_path / "missing"], tmp_path / "out" / "0.15")


def test_a_standalone_crop_unit_keeps_its_crop_column(dataset: Path, tmp_path: Path):
    # A HI Lab style unit has no page and no box: it belongs to its document and travels with its
    # archive path, so the closure must not ask for a page it never had.
    directory = tmp_path / "work" / "crops-only"
    tables.write(
        directory / "documents.parquet",
        [Document(id="hi:kuzushiji-2023", title="HI Lab", image_rights=FREE, text_rights=FREE)],
        Document,
    )
    tables.write(
        directory / "units.parquet",
        [
            Unit(
                id="hi:34000001",
                document_id="hi:kuzushiji-2023",
                crop="all.zip!all/characters/U+4E00/34000001.jpg",
                unicode="U+4E00",
                reading="一",
                script=Script.KANJI,
                review=ReviewState.TRANSCRIBER,
                kind=UnitKind.CHAR,
                upstream={"source": "hi-lab-kuzushiji", "ref": "34000001"},
            )
        ],
        Unit,
    )
    out = tmp_path / "out" / "0.16"
    counts = export.release([directory], out, review=[ReviewState.TRANSCRIBER.value])
    assert counts["units"] == 1
    assert tables.Dataset(out).validate() == []
    row = units_of(out)[0]
    assert row["page_id"] is None and row["crop"].endswith("34000001.jpg")


def test_a_dataset_without_units_releases_its_lines(tmp_path):
    """A platform transcription has lines and text but no units until an alignment runs.

    Such a release carries every line of a kept document; there are no units to say which line is
    used, and refusing the build would mean the atlas could not publish the transcription at all.
    """
    from kuzushiji_atlas import export as export_module
    from kuzushiji_atlas import koji, tables
    from kuzushiji_atlas.schema import Document, Line, Page, PageText, ReviewState, Rights

    document = Document(
        id="hk:d1", title="t",
        image_rights=Rights(licence="CC-BY-SA-4.0", attribution="a"),
        text_rights=Rights(licence="CC-BY-SA-4.0", attribution="a"),
    )
    page = Page(id="hk:d1:0", document_id="hk:d1", seq=0, image="file:p.jpg", width=100, height=100)
    line = Line(id="hk:d1:0:L0", page_id="hk:d1:0", seq=0, box=None,
                text_raw="あ", text=koji.plain("あ"), match_method="ainu-records-2026-09")
    source = tmp_path / "ainu"
    tables.write(source / "documents.parquet", [document], Document)
    tables.write(source / "pages.parquet", [page], Page)
    tables.write(source / "lines.parquet", [line], Line)
    tables.write(source / "page_texts.parquet",
                 [PageText(page_id="hk:d1:0", source="ainu-records", text_raw="あ")], PageText)

    out = tmp_path / "release"
    counts = export_module.release([source], out, review=[ReviewState.TRANSCRIBER.value])
    assert counts["lines"] == 1 and counts["pages"] == 1 and counts["documents"] == 1
    assert not (out / "units.parquet").exists(), "a dataset without units releases none"
    kept = tables.read(out / "lines.parquet", Line)
    assert [row.id for row in kept] == ["hk:d1:0:L0"]
