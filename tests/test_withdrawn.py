import yaml

from glyph_atlas import withdrawn


def test_every_withdrawal_states_its_reason_and_evidence():
    data = yaml.safe_load(withdrawn.PATH.read_text(encoding="utf-8"))
    for entry in data["withdrawals"]:
        assert entry["reason"] and entry["evidence"] and entry["decided"] and entry["documents"]
    assert "hl:68eb3417ed31bd40b47322289459eeec" in withdrawn.documents()


def test_the_corpus_range_holds_exactly_the_document_glyphs():
    low, high = withdrawn.corpus_range("hl:ab")
    inside = ["hl:ab_0_000:be9c:1", "hl:ab_33_016:be9c:25"]
    outside = ["hl:ab", "hl:abc_0_000:be9c:1", "hl:aa_0_000:x:1", "hl:ac_0_000:x:1"]
    assert all(low <= i < high for i in inside) and not any(low <= o < high for o in outside)


def test_a_release_cuts_no_crop_of_a_withdrawn_document(monkeypatch):
    from glyph_atlas import export
    from glyph_atlas.schema import Document, Licence, Rights

    document = Document(id="hl:gone", title="t", image_rights=Rights(licence=Licence.PDM, attribution="a"))
    assert export.images_ok(document, Licence.CC_BY_SA_4)
    monkeypatch.setattr(withdrawn, "documents", lambda: frozenset({"hl:gone"}))
    assert not export.images_ok(document, Licence.CC_BY_SA_4)


def test_form_clustering_admits_no_glyph_of_a_withdrawn_document(tmp_path, monkeypatch):
    from glyph_atlas import form_quality

    monkeypatch.setenv("ATLAS_FORM_DECISIONS", str(tmp_path / "decisions.jsonl"))
    admission = form_quality.Admission(tmp_path)
    monkeypatch.setattr(withdrawn, "documents", lambda: frozenset({"hl:gone"}))
    assert admission.reason({"id": "hl:gone_0_000:x:1", "document_id": "hl:gone"}) == "withdrawn"
