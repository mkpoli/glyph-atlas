"""Native compatibility checks use source code from AINU_RECORDS_DIR in disposable data trees."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
import yaml

from glyph_atlas.ainu_native import NativeAinuError, text_sha256, validate_source_updates


@pytest.fixture
def source(tmp_path: Path) -> Path:
    configured = os.environ.get("AINU_RECORDS_DIR")
    if not configured or not shutil.which("bun"):
        pytest.skip("native integration requires Bun and AINU_RECORDS_DIR")
    upstream = Path(configured).expanduser()
    root = tmp_path / "source"
    for relative in (
        "scripts/lib/corrections.ts", "scripts/lib/markup.ts", "scripts/lib/allographs.ts",
        "scripts/lib/sources.ts", "scripts/lib/wordlist-layout.ts",
        "scripts/lib/transcription-supplements.ts", "src/lib/source-classification.ts",
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(upstream / relative, target)
    adjustment_module = upstream / "scripts/lib/conversion-adjustments.ts"
    if adjustment_module.exists():
        shutil.copyfile(adjustment_module, root / "scripts/lib/conversion-adjustments.ts")
        write_json(root / "data/editorial/conversion-adjustments.json", [])
    write_json(root / "data/editorial/transcriptions.json", [])
    write_json(root / "data/editorial/wordlist-layouts.json", {})
    write_json(root / "data/raw/manifest.json", {"harvestedAt": "fixture"})
    (root / "data/editorial/corrections").mkdir()
    (root / "data/sources.yaml").write_text(yaml.safe_dump({"sources": [{
        "slug": "fixture", "title": "Fixture", "genre": "lexicon", "kind": "wordlist",
        "witnesses": [{"slug": "copy", "parts": [
            {"label": "First", "entry": "first-entry"},
            {"label": "Second", "entry": "second-entry"},
        ]}],
    }]}), encoding="utf-8")
    for entry in ("first-entry", "second-entry"):
        write_json(root / f"data/raw/entries/{entry}.json", {
            "id": entry, "canvases": [{"id": f"canvas:{entry}", "imageUrl": "fixture:image"}],
        })
        page_text(root, "【右丁】\n天　アイヌ\n【左丁】\n地　モシリ", entry=entry)
    return root


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def page_text(root: Path, text: str, *, entry: str = "first-entry") -> None:
    write_json(root / f"data/raw/transcriptions/{entry}/0000.json", {
        "id": "fixture-page", "entryId": entry, "index": 0, "canvasId": f"canvas:{entry}",
        "text": text, "status": "completed", "notes": [],
    })


def proposal(root: Path, *, entry: str = "first-entry", **changes: object) -> dict:
    page = json.loads((root / f"data/raw/transcriptions/{entry}/0000.json").read_text())
    return {"entry": entry, "page_index": 0, "canvas": page["canvasId"],
            "text_sha256": text_sha256(page["text"]), "correction": {
                "id": "fixture-edit", "line": 1, "original": "アイヌ", "corrected": "アイノ",
                "note": "Synthetic test only", **changes,
            }}


def test_draft_preserves_existing_records_and_uses_native_volume_address(source: Path) -> None:
    target = source / "data/editorial/corrections/fixture/copy-2/p1.json"
    existing = [{"id": "existing-edit", "line": 2, "original": "モシリ", "corrected": "モシル",
                 "note": "Existing fixture"}]
    write_json(target, existing)
    before = target.read_bytes()
    result = validate_source_updates(source, [proposal(source, entry="second-entry")])
    assert result["validated"], result["conflicts"]
    file = result["files"][0]
    assert file["path"] == "data/editorial/corrections/fixture/copy-2/p1.json"
    assert file["before"] == before.decode()
    assert file["before_sha256"] == text_sha256(before.decode())
    assert json.loads(file["after"])[0] == existing[0]
    assert target.read_bytes() == before, "validation must not alter source data"
    # Round-trip the returned draft through the native loader in this disposable source tree.
    target.write_text(file["after"], encoding="utf-8")
    followup = proposal(source, entry="second-entry", id="next-fixture", original="天", corrected="空")
    assert validate_source_updates(source, [followup])["validated"]
    duplicate = validate_source_updates(source, [proposal(source, entry="second-entry")])
    assert not duplicate["validated"] and duplicate["files"] == []
    assert "already exists" in duplicate["conflicts"][0]["reason"]


def test_stale_text_and_canvas_are_conflicts(source: Path) -> None:
    original = proposal(source)
    page_text(source, "天　別の語")
    stale = validate_source_updates(source, [original])
    assert not stale["validated"] and "transcription changed" in stale["conflicts"][0]["reason"]
    moved = proposal(source)
    moved["canvas"] = "another-canvas"
    result = validate_source_updates(source, [moved])
    assert not result["validated"] and "canvas changed" in result["conflicts"][0]["reason"]


def test_duplicate_and_overlapping_submissions_return_no_files(source: Path) -> None:
    item = proposal(source)
    for batch in ([item, item], [item, proposal(source, id="other-edit")]):
        result = validate_source_updates(source, batch)
        assert not result["validated"] and result["files"] == []


def test_ruby_requires_native_complete_field_matching(source: Path) -> None:
    page_text(source, "【右丁】\n《振り仮名：漢字｜かな》")
    for fields in ({}, {"rubyField": "rt", "original": "か"}):
        item = proposal(source, original="かな", corrected="カナ", **fields) if not fields else proposal(
            source, corrected="カナ", **fields
        )
        assert not validate_source_updates(source, [item])["validated"]
    valid = proposal(source, original="かな", corrected="カナ", rubyField="rt", rubyBase="漢字")
    assert validate_source_updates(source, [valid])["validated"]


def test_native_physical_lines_and_allograph_encoding(source: Path) -> None:
    page_text(source, "【十二丁】\n【ママ】\n天　子コ")
    item = proposal(source, line=2, original="ネコ", corrected="ネク")
    result = validate_source_updates(source, [item])
    assert result["validated"], result["conflicts"]
    assert json.loads(result["files"][0]["after"])[0]["original"] == "ネコ"


def test_wordlist_entry_must_match_native_extraction(source: Path) -> None:
    good = proposal(source)
    good["correction"]["entry"] = {"gloss": "天", "form": "アイヌ"}
    result = validate_source_updates(source, [good])
    assert result["validated"], result["conflicts"]
    good["correction"]["entry"]["gloss"] = "違う見出し"
    result = validate_source_updates(source, [good])
    assert not result["validated"] and "wordlist entry" in result["conflicts"][0]["reason"]


@pytest.mark.parametrize("change", [
    {"id": "Bad/Id"}, {"line": True}, {"original": "アイヌ", "corrected": "アイヌ"},
    {"note": " "}, {"page": 1}, {"kind": "inference"},
])
def test_invalid_native_records_are_refused(source: Path, change: dict) -> None:
    result = validate_source_updates(source, [proposal(source, **change)])
    assert not result["validated"] and result["files"] == []


def test_missing_native_checkout_is_explicit(tmp_path: Path) -> None:
    with pytest.raises(NativeAinuError, match="no native correction validator"):
        validate_source_updates(tmp_path, [])
