"""Tests for `kuzushiji_atlas.ainu_source`, the bridge to the publishing project.

Everything here runs against a small synthetic checkout in `tmp_path`, so the tests say what the
contract is rather than what one source revision happens to contain. The cases that matter are the
ones where a plausible shortcut would attach a review to the wrong thing:

* the join is the みんなで翻刻 entry id, so two witnesses of one work are told apart instead of being
  matched by title or shelfmark;
* a correction's line is the source parser's own one-based count, which is not the atlas's
  `page.seq + 1` and not its markup segmentation;
* an `original` has to match exactly once in that line, because that is the rule that makes a changed
  upstream transcription fail the source's build rather than move the correction;
* a proposal that cannot be placed is reported as unmappable, never written.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from kuzushiji_atlas import ainu_source
from kuzushiji_atlas.ainu_source import AinuSource, AinuSourceError, Proposal

SOURCES = {
    "sources": [
        {
            "slug": "ezo-kiko",
            "title": "蝦夷紀行",
            "date": "1800",
            "kind": "prose",
            "witnesses": [
                {
                    "slug": "ryukoku",
                    "holder": "龍谷大学図書館",
                    "shelfmark": "12345",
                    "parts": [{"label": "第1冊", "entry": "aaaa1111bbbb2222cccc3333dddd4444"}],
                }
            ],
        },
        {
            "slug": "moshiogusa",
            "title": "蝦夷方言藻汐草",
            "date": "1792",
            "kind": "wordlist",
            "witnesses": [
                {
                    "slug": "ninjal",
                    "holder": "国立国語研究所",
                    "parts": [
                        {"label": "乾巻", "entry": "3daea514503efa7c8ec5ccc61c9be9d8"},
                        {"label": "坤巻", "entry": "62c6743982041882d0aefd6582ac6a84"},
                    ],
                },
                {
                    "slug": "leiden",
                    "holder": "Wereldmuseum Leiden",
                    "parts": [{"label": None, "entry": "83b29132c6d01a596a847de5ba3b0b0d"}],
                },
            ],
        },
    ]
}


@pytest.fixture
def source(tmp_path: Path) -> AinuSource:
    """A checkout with two works, three witnesses and one existing correction."""
    root = tmp_path / "ainu-records"
    (root / "data" / "editorial" / "corrections" / "ezo-kiko" / "ryukoku").mkdir(parents=True)
    (root / "data" / "sources.yaml").write_text(
        yaml.safe_dump(SOURCES, allow_unicode=True), encoding="utf-8"
    )
    (root / "data" / "editorial" / "corrections" / "ezo-kiko" / "ryukoku" / "p16.json").write_text(
        json.dumps([{
            "id": "ezo-kiko-ryukoku-deren", "line": 5, "original": "テシン",
            "corrected": "テレン", "note": "原画像のシをレに訂正。",
        }], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return AinuSource(root)


class Document:
    """The part of an atlas document the bridge reads."""

    def __init__(self, document_id: str, entry: str | None) -> None:
        self.id = document_id
        self.source_refs = {"ainu-work": "moshiogusa", "ainu-witness": "ninjal"}
        if entry:
            self.source_refs["honkoku-data"] = entry


def test_works_and_witnesses_are_read_with_their_parts(source: AinuSource) -> None:
    """A work has witnesses, and a witness has the platform entries that make up its parts."""
    works = {work.slug: work for work in source.works()}
    assert set(works) == {"ezo-kiko", "moshiogusa"}
    ninjal = next(w for w in works["moshiogusa"].witnesses if w.slug == "ninjal")
    assert [part.label for part in ninjal.parts] == ["乾巻", "坤巻"]
    assert ninjal.unit == "moshiogusa/ninjal"
    assert source.entries()["3daea514503efa7c8ec5ccc61c9be9d8"][2].label == "乾巻"


def test_a_document_maps_by_entry_id_not_by_title(source: AinuSource) -> None:
    """Two parts of one witness with different entries map to their own part."""
    first = source.map_document(Document("d1", "3daea514503efa7c8ec5ccc61c9be9d8"))
    second = source.map_document(Document("d2", "62c6743982041882d0aefd6582ac6a84"))
    assert first is not None and second is not None
    assert first.unit == second.unit == "moshiogusa/ninjal", "one witness"
    assert first.part.label == "乾巻" and second.part.label == "坤巻", "different parts"
    assert first.holder == "国立国語研究所"


def test_a_document_the_source_does_not_publish_maps_to_nothing(source: AinuSource) -> None:
    """An entry the curation does not hold is reported as unmapped, not attached by resemblance."""
    assert source.map_document(Document("d3", "ffff0000ffff0000ffff0000ffff0000")) is None
    assert source.map_document(Document("d4", None)) is None


def test_the_parser_line_count_skips_structural_markers() -> None:
    """Corrections are placed by the source's own one-based count of transcription lines.

    The 右丁 marker and the blank line are skipped, so the first transcription line below the marker is
    line 1 and not line 3 — which is why an atlas `page.seq + 1` cannot be used for placement.
    """
    text = "［右丁］\n\n最初の行\n二番目の行\n［左丁］\n三番目の行\n"
    lines = ainu_source.transcription_lines(text)
    assert lines == ["最初の行", "二番目の行", "三番目の行"]


def test_an_original_must_match_exactly_once() -> None:
    """Absent, present and ambiguous are three different refusals."""
    lines = ["テシンとテレン", "同じ字が同じ字"]
    assert ainu_source.place(lines, line=1, original="テシン") is None
    assert "does not contain" in (ainu_source.place(lines, line=1, original="ナイ") or "")
    assert "appears 2 times" in (ainu_source.place(lines, line=2, original="同じ") or "")
    assert "does not exist" in (ainu_source.place(lines, line=5, original="テシン") or "")
    assert "one-based" in (ainu_source.place(lines, line=0, original="テシン") or "")


def test_a_proposal_renders_the_sources_format_without_location_fields(source: AinuSource) -> None:
    """The path supplies unit and page, so a record must not carry them."""
    proposal = Proposal(
        unit="ezo-kiko/ryukoku", page=16, id="ezo-kiko-ryukoku-x", line=5,
        original="テシン", corrected="テレン", note="原画像を確認。",
    )
    record = proposal.record()
    assert "unit" not in record and "page" not in record
    assert record == {"id": "ezo-kiko-ryukoku-x", "line": 5, "original": "テシン",
                      "corrected": "テレン", "note": "原画像を確認。"}
    assert proposal.path() == Path("data/editorial/corrections/ezo-kiko/ryukoku/p16.json")


def test_the_loader_rejects_a_kind_it_cannot_carry(source: AinuSource) -> None:
    """`corrections.ts` accepts `transcription` only, so a proposal saying otherwise is refused."""
    proposal = Proposal(unit="ezo-kiko/ryukoku", page=16, id="x", line=5, original="あ",
                        corrected="い", note="n", kind="gloss")
    with pytest.raises(AinuSourceError, match="transcription"):
        proposal.record()


def test_an_unmappable_proposal_is_never_written(source: AinuSource) -> None:
    """A proposal carrying a reason is reported, not rendered."""
    proposal = Proposal(unit="ezo-kiko/ryukoku", page=16, id="x", unmappable="line 9 does not exist")
    with pytest.raises(AinuSourceError, match="line 9"):
        proposal.record()
    assert source.drafts([proposal]) == {}


def test_drafts_keep_the_records_already_there(source: AinuSource) -> None:
    """A submission adds to the editorial layer; it does not replace the file."""
    proposal = Proposal(unit="ezo-kiko/ryukoku", page=16, id="ezo-kiko-ryukoku-new", line=1,
                        original="あ", corrected="い", note="n")
    files = source.drafts([proposal])
    path = Path("data/editorial/corrections/ezo-kiko/ryukoku/p16.json")
    assert set(files) == {path}
    assert [record["id"] for record in files[path]] == ["ezo-kiko-ryukoku-deren",
                                                        "ezo-kiko-ryukoku-new"]


def test_validation_refuses_a_collision_and_an_unplaced_original(source: AinuSource) -> None:
    """The two rules the source's build enforces: unique ids, and one exact match in the stated line."""
    pages = {("ezo-kiko/ryukoku", 16): "［右丁］\n一行目\n二行目\n三行目\n四行目\nテシンとテレン\n"}
    collides = Proposal(unit="ezo-kiko/ryukoku", page=16, id="ezo-kiko-ryukoku-deren", line=5,
                        original="テシン", corrected="テレン", note="n")
    misstated = Proposal(unit="ezo-kiko/ryukoku", page=16, id="fresh", line=2,
                         original="テシン", corrected="テレン", note="n")
    problems = source.validate([collides, misstated], pages=pages)
    assert any("already has a correction" in problem for problem in problems)
    assert any("does not contain" in problem for problem in problems)


def test_validation_says_when_it_could_not_check_the_text(source: AinuSource) -> None:
    """Without the transcription the exact-match rule has not been applied, and it says so."""
    proposal = Proposal(unit="ezo-kiko/ryukoku", page=16, id="fresh", line=5, original="テシン",
                        corrected="テレン", note="n")
    problems = source.validate([proposal])
    assert problems == [f"{proposal.id}: not checked against the source text; pass the page transcription"]

    good = source.validate([proposal], pages={("ezo-kiko/ryukoku", 16): "a\nb\nc\nd\nテシン\n"})
    assert good == [], "line 5 of five transcription lines holds it exactly once"


def test_a_tree_without_the_index_is_refused(tmp_path: Path) -> None:
    """A path that is not a checkout is reported as such rather than read as empty."""
    (tmp_path / "elsewhere").mkdir()
    with pytest.raises(AinuSourceError, match="is not an ainu-records checkout"):
        AinuSource(tmp_path / "elsewhere")
