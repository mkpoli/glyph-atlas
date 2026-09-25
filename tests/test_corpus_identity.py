"""Normalized source classes do not establish the written character."""

import json
from uuid import uuid4

import pytest
from fastapi import HTTPException

from glyph_atlas import tables, visual_families
from glyph_atlas.corpus import CorpusAPI, build_chars, details
from glyph_atlas.corpus.identity import identity_fields
from glyph_atlas.review.corpus_reviews import CorpusEdit, CorpusReviews
from glyph_atlas.schema import Box, Document, Page, Unit


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_VISUAL_FAMILIES_DIR", str(tmp_path / "visual"))
    root = tmp_path / "work"
    out = root / "codh-full"
    out.mkdir(parents=True)
    tables.write(out / "documents.parquet", [Document(
        id="codh:book", title="Book", production="printed/woodblock",
        image_rights={"licence": "CC-BY-4.0", "holder": "Library", "attribution": "Library"},
    )], Document)
    tables.write(out / "pages.parquet", [Page(
        id="codh:book:page", document_id="codh:book", seq=1, width=1000, height=1000,
        image="https://example.org/iiif/page",
    )], Page)
    tables.write(out / "units.parquet", [Unit(
        id=f"codh:unit:{i}", document_id="codh:book", page_id="codh:book:page",
        text_source=char, reading=char, unicode=f"U+{ord(char):04X}",
        kind="char", method="import", box=Box(x=10+i*20, y=10, w=16, h=20),
    ) for i, char in enumerate(("仮", "仮", "假"))], Unit)
    index = tmp_path / "index"
    build_chars(root, index)
    api = CorpusAPI(root, index)
    api._atlas_reviews = CorpusReviews(api)
    return api


def get(api, char="假", scope="grapheme", **kwargs):
    from urllib.parse import urlencode
    status, _, body = api.handle_get("/api/corpus/glyphs?" + urlencode({"char": char, "scope": scope, **kwargs}))
    assert status == 200
    return json.loads(body)


def test_family_search_unions_bins_without_asserting_exact_identity(api):
    family = get(api)
    assert family["total"] == family["family_total"] == family["unassigned_count"] == 3
    assert family["assigned_count"] == 0
    assert {row["source_code_point"] for row in family["items"]} == {"U+4EEE", "U+5047"}
    assert all(row["written_character"] is None for row in family["items"])
    assert all(row["identity_basis"] == "normalized_transcription" for row in family["items"])
    assert all(row["production"] == "printed/woodblock" for row in family["items"])
    assert get(api, "仮")["total"] == 3
    assert get(api, scope="character")["total"] == 0
    assert get(api, scope="character")["unassigned_count"] == 3
    first, second = get(api, limit=2), get(api, limit=2, offset=2)
    assert len({row["unit_id"] for row in first["items"] + second["items"]}) == 3


def test_visual_assignment_preserves_source_and_human_can_override(api, monkeypatch):
    def assignment(identity, *args, **kwargs):
        return {"written_character": "假", "visual_group": {"id": "family-1"}} if identity == "codh:unit:0" else None
    monkeypatch.setattr(visual_families, "assignment_for", assignment)
    found = get(api, scope="character")
    assert found["total"] == 1
    row = found["items"][0]
    assert (row["written_character"], row["source_label"], row["source_code_point"]) == ("假", "仮", "U+4EEE")
    assert row["identity_basis"] == "visual_model"
    assert get(api, visual_group="family-1")["total"] == 1
    assert get(api, visual_group="family-1")["family_total"] == 3
    assert get(api, visual_group="family-1")["unassigned_count"] == 2
    assert get(api, visual_group="unassigned")["total"] == 2
    source = details.detail(api, "codh:unit:0")
    saved = api._atlas_reviews.record(CorpusEdit(
        id=uuid4(), identity=source["id"], client_id="test", revision=0,
        source_revision=source["source_revision"], verdict="wrong", issue="character", character="仮",
    ))
    assert saved["identity_basis"] == "human_review"
    assert saved["written_character"] == saved["source_label"] == "仮"
    assert get(api, scope="character")["total"] == 0
    assert get(api, "仮", "character")["total"] == 1
    api._atlas_reviews.record(CorpusEdit(
        id=uuid4(), identity=source["id"], client_id="test", revision=saved["revision"],
        source_revision=source["source_revision"], verdict="wrong", issue="crop",
    ))
    assert api._atlas_reviews.detail(source["id"])["written_character"] == "仮"
    assert get(api, "仮", "character")["total"] == 1


def test_unassigned_match_cannot_promote_normalized_label(api):
    source = details.detail(api, "codh:unit:0")
    assert source["written_character"] is None
    with pytest.raises(HTTPException) as failure:
        api._atlas_reviews.record(CorpusEdit(
            id=uuid4(), identity=source["id"], client_id="test", revision=0,
            source_revision=source["source_revision"], verdict="match",
        ))
    assert failure.value.status_code == 422


def test_visual_assignment_is_invalid_after_source_crop_changes(api):
    identity = "codh:unit:0"
    source = details.detail(api, identity)
    directory = visual_families.directory()
    directory.mkdir(parents=True)
    (directory / "assignments.json").write_text(json.dumps({
        "version": 1, "assignments": {identity: {
            "written_character": "假", "source_label": "仮",
            "source_signature": source["source_signature"],
        }},
    }))
    assert get(api, scope="character")["total"] == 1
    assert details.detail(api, identity)["written_character"] == "假"
    out = api.root / "codh-full"
    units = list(tables.Dataset(out).read("units"))
    units[0] = units[0].model_copy(update={"box": Box(x=11, y=10, w=16, h=20)})
    tables.write(out / "units.parquet", units, Unit)
    assert get(api, scope="character")["total"] == 0
    assert details.detail(api, identity)["written_character"] is None


@pytest.mark.parametrize("modern,old", [("仮", "假"), ("国", "國"), ("学", "學")])
def test_normalization_applies_across_curated_families(modern, old):
    normalized = identity_fields({"unicode": f"U+{ord(modern):04X}", "text_source": modern}, "codh-full")
    assert normalized["written_character"] is None
    assert old in [row["char"] for row in normalized["family_members"]]
    literal = identity_fields({"unicode": f"U+{ord(old):04X}", "text_source": old}, "hilab")
    assert literal["written_character"] == old
    assert literal["identity_basis"] == "source_transcription"


def test_inspected_conflicting_source_remains_unassigned_until_human_review(monkeypatch):
    monkeypatch.setattr(visual_families, "assignment_for", lambda *args, **kwargs: {
        "written_character": None, "visual_group": {"id": "unanchored"}})
    source = {"id": "hilab:duplicate", "unicode": "U+5047", "text_source": "假"}
    result = identity_fields(source, "hilab")
    assert result["written_character"] is None and result["identity_status"] == "unassigned"
    assert result["identity_basis"] == "visual_model" and result["requires_family_scope"]
    assert result["source_label"] == "假"
    assert identity_fields(source, "hilab", human_character="仮")["written_character"] == "仮"


def test_a_code_point_sequence_is_an_identity_without_a_family():
    fields = identity_fields({"unicode": "U+30C4 U+309A", "text_source": "ツ゚"}, "codh")
    assert fields["written_character"] == "ツ゚" and fields["family_members"] == []
    reviewed = identity_fields({"unicode": "U+30C4"}, "codh", human_character="ツ゚")
    assert reviewed["written_character"] == "ツ゚"
