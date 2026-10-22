"""The actual Worker formatter produces an idempotently importable offline review chain."""
import json
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest
from test_cloudflare_import import baseline, store  # noqa: F401 -- fixture

from glyph_atlas.feedback import normalize_export
from glyph_atlas.review import cloudflare_import
from glyph_atlas.review.receipts import fingerprint

pytestmark = pytest.mark.skipif(not shutil.which("bun"), reason="Worker evidence bridge requires Bun")
SCRIPT = Path(__file__).parents[1] / "apps/cloudflare/src/routes/fixtures/captureReviewExport.ts"


def capture(publication, mode="single", records=None, origin="local", start=1):
    answer = {"image_sha256": publication["character"]["image_sha256"], "verdict": "wrong", "issue": "character", "character": "ナ"}
    records = records or [{"id": str(uuid4()), "before": publication["character"], "answer": answer}]
    result = subprocess.run(["bun", str(SCRIPT)], input=json.dumps({"publication": publication, "mode": mode, "records": records, "origin": origin, "start": start}),
                            capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


@pytest.mark.parametrize("mode", ["single", "round", "batch"])
def test_native_capture_imports_with_logical_revisions_and_original_source_ids(store, mode):  # noqa: F811
    source = capture(baseline(store), mode)
    record = source["reviews"][0]
    assert record["expected_revision"] == 0 and record["current_revision"] == 1
    assert record["event"]["target_id"] == "u"
    assert len(normalize_export(source)) == 1
    original = fingerprint(record)
    bound, report = cloudflare_import.ingest_cloudflare(store, source, apply=True)
    assert report["counts"] == {"imported": 1}, report
    assert store.unit("u").unicode == "U+30CA"
    assert bound["reviews"]
    # A later publication changes the mutable envelope; archived event and baseline stay fixed.
    republished = deepcopy(source)
    republished["publication"] = "later-publication"
    _, retry = cloudflare_import.ingest_cloudflare(store, republished, apply=True)
    assert retry["counts"] == {"duplicate": 1}, retry
    assert fingerprint(republished["reviews"][0]) == original


def test_native_review_chain_imports_without_counting_physical_marks(store):  # noqa: F811
    publication = baseline(store)
    answer = {"image_sha256": publication["character"]["image_sha256"], "verdict": "wrong", "issue": "character", "character": "ナ"}
    source = capture(publication, records=[
        {"id": str(uuid4()), "before": publication["character"], "answer": answer},
        {"id": str(uuid4()), "before": {**publication["character"], "label": "ナ", "revision": 1},
         "answer": {**answer, "issue": "crop", "character": None}},
    ])
    _, report = cloudflare_import.ingest_cloudflare(store, source, apply=True)
    assert report["counts"] == {"imported": 1}, report
    assert store.unit("u").review == "disputed"
    _, retry = cloudflare_import.ingest_cloudflare(store, source, apply=True)
    assert retry["counts"] == {"duplicate": 1}, retry


@pytest.mark.parametrize("mode", ["single", "round", "batch"])
def test_native_corpus_capture_keeps_upstream_identity_and_geometry(store, mode):  # noqa: F811
    publication = baseline(store)
    character = publication["character"]
    character.update(id="codh:200008316:crop-1", source_revision="upstream-17", source_code_point="U+624B",
                     source={"corpus": "codh-full", "page_id": "codh:200008316:p2"})
    source = capture(publication, mode, origin="corpus")
    record = source["reviews"][0]
    assert record["origin"] == "corpus"
    assert record["source_update"] == {"identity": character["id"], "source_revision": "upstream-17",
        "source": character["source"], "box": character["box"], "original_character": "手",
        "proposed_character": "ナ", "issue": "character", "applied_upstream": False}
    assert record["reviewed"]["id"] == character["id"]
    normalized = normalize_export(source)[0]
    assert normalized.source_unit_id == character["id"]
    # Human reviews normalize the logical revision from evidence, as the old export did.
    # The upstream revision remains in source_update for the external corpus workflow.
    assert normalized.source_revision == 0
    assert normalized.source_box == character["box"]
    assert "publication_snapshot" not in record


def test_a_review_under_the_page_bundle_record_follows_one_imported_from_the_old_export(store):  # noqa: F811
    publication = baseline(store)
    old = capture(publication)
    _, report = cloudflare_import.ingest_cloudflare(store, old, apply=True)
    assert report["counts"] == {"imported": 1}, report
    # glyph-atlas-v3 publishes the crop as the old site's record after that review, with its own fields.
    record = {**baseline(store)["character"], "label": "ナ", "tone": "#ab9d7c", "image_place": ["packs/p.bin", 0, 10]}
    answer = {"image_sha256": record["image_sha256"], "verdict": "wrong", "issue": "character", "character": "を"}
    new = capture({"character": record}, records=[{"id": str(uuid4()), "before": record, "answer": answer}], start=2)
    assert "image_place" not in new["reviews"][0]["publication_snapshot"]["character"]
    _, report = cloudflare_import.ingest_cloudflare(store, new, apply=True)
    assert report["counts"] == {"imported": 1}, report
    assert store.unit("u").unicode == "U+3092"
