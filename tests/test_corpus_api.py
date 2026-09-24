"""The HTTP contract the gallery and autocomplete depend on."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_corpus_index import build_corpus

from glyph_atlas.corpus import CorpusAPI, build_chars, build_occurrences
from glyph_atlas.corpus.api import NOT_PROXYABLE_REASON, PROXYABLE
from glyph_atlas.corpus.fastapi_router import build_query
from glyph_atlas.corpus.glyphs import GLYPHS_FILE
from glyph_atlas.corpus.glyphs import build as build_registry
from glyph_atlas.corpus.occurrence import TOMO
from glyph_atlas.corpus.sources import ID_FAMILIES


def get(api: CorpusAPI, target: str):
    status, _, body = api.handle_get(target)
    return status, json.loads(body.decode("utf-8"))


@pytest.fixture()
def api(tmp_path: Path) -> CorpusAPI:
    root = tmp_path / "shared-work"
    root.mkdir()
    build_corpus(root)
    directory = tmp_path / "index"
    build_chars(root, directory)
    # A warm index: the per-character file is what a queried character has after its
    # first request. autobuild stays off so the tests exercise the read path.
    build_occurrences(TOMO, root, directory)
    return CorpusAPI(root, directory, autobuild=False)


@pytest.fixture()
def api_cold(tmp_path: Path) -> CorpusAPI:
    """A built summary with no per-character file: the cold-query case."""
    root = tmp_path / "shared-work"
    root.mkdir()
    build_corpus(root)
    directory = tmp_path / "index"
    build_chars(root, directory)
    return CorpusAPI(root, directory, autobuild=False)


def test_gallery_checks_sample_rows_without_scanning_an_unrelated_probe(api, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("gallery must not scan a corpus to verify an unrelated probe")

    monkeypatch.setattr(api.crops, "verify", forbidden)
    status, result = get(api, "/api/corpus/glyphs?limit=12&seed=41")
    assert status == 200 and "items" in result


@pytest.fixture()
def api_with_glyph(tmp_path: Path) -> CorpusAPI:
    """A corpus plus one registered machine-located glyph rectangle."""
    root = tmp_path / "shared-work"
    root.mkdir()
    build_corpus(root)
    directory = tmp_path / "index"
    build_chars(root, directory)
    build_occurrences(TOMO, root, directory)
    registry = build_registry(
        [
            {
                "char": TOMO,
                "codepoint": "U+2A708",
                "char_class": "literal_text",
                "image_service": "https://example.org/iiif/S-1/canvas1",
                "iiif_url": "https://example.org/iiif/S-1/canvas1/12,24,8,9/320,/0/default.jpg",
                "crop_file": None,
                "crop_sha256": None,
                "crop_bytes": None,
                "source": {
                    "corpus": "honkoku-lines",
                    "document_id": "hl:DOC",
                    "page_id": "hl:DOC:1",
                    "line_id": "hl:DOC_1_000",
                    "title": "Test book",
                    "holder": "Test holder",
                    "image_rights": {"licence": "PDM-1.0"},
                },
                "text": {"span_start": 2},
                "rects": [
                    {
                        "x": 10,
                        "y": 20,
                        "w": 40,
                        "h": 900,
                        "role": "line",
                        "basis": "upstream_bbox",
                        "confirmed": False,
                    },
                    {
                        "x": 12,
                        "y": 24,
                        "w": 8,
                        "h": 9,
                        "role": "glyph",
                        "basis": "machine_projection",
                        "confirmed": False,
                        "method": "projection_segmentation_padded",
                        "heuristic_score": 0.75,
                    },
                ],
                "review": {"state": "machine", "human_validated": False, "events": []},
            }
        ],
        ID_FAMILIES,
    )
    registry.save(directory / GLYPHS_FILE)
    return CorpusAPI(root, directory, autobuild=False)


class TestCodePointParsing:
    @pytest.mark.parametrize("spelling", ["U%2B2A708", "U+2A708", "U 2A708", "%F0%AA%9C%88"])
    def test_every_spelling_reaches_the_same_character(self, api, spelling):
        status, payload = get(api, f"/api/corpus/find?char={spelling}&limit=1")
        assert status == 200
        assert payload["codepoint"] == "U+2A708"

    def test_a_multi_character_query_is_rejected(self, api):
        status, payload = get(api, "/api/corpus/find?char=abc")
        assert status == 400
        assert "given" in payload


class TestCounts:
    def test_n_glyphs_is_the_only_renderable_number(self, api):
        status, payload = get(api, "/api/corpus/counts?chars=%F0%AA%9C%88")
        row = payload["chars"][0]
        assert status == 200
        assert row["n_glyphs"] == row["n_glyph_rects"] + row["n_units"]
        # text evidence is counted, and is not renderable
        assert row["n_line_hits"] > 0
        assert row["renderable"] is False

    def test_a_separate_to_and_mo_never_raises_the_ligature_count(self, api):
        _, payload = get(api, "/api/corpus/counts?chars=%E3%83%88,%E3%83%A2,%F0%AA%9C%88")
        rows = {r["char"]: r for r in payload["chars"]}
        assert rows["ト"]["n_literal"] == 3
        assert rows["モ"]["n_literal"] == 3
        assert rows["𪜈"]["n_literal"] == 3
        # the kana are not ligatures and carry no ligature reading
        assert rows["ト"]["kind"]["is_ligature"] is False
        assert rows["𪜈"]["kind"]["typed_as"] == "トモ"

    def test_batch_is_capped(self, api):
        status, _ = get(api, "/api/corpus/counts?chars=" + ",".join(["x"] * 250))
        assert status == 400

    def test_semantics_are_published_with_the_numbers(self, api):
        _, payload = get(api, "/api/corpus/counts?chars=%F0%AA%9C%88")
        assert "n_glyphs" in payload["count_semantics"]


class TestGlyphs:
    def test_an_anchor_leads_and_is_renderable(self, api_with_glyph):
        status, payload = get(api_with_glyph, "/api/corpus/glyphs?char=U%2B2A708")
        assert status == 200
        assert payload["anchors_pinned_first"] is True
        assert payload["n_glyph_rects"] == 1
        item = payload["items"][0]
        assert item["source_kind"] == "machine_located_anchor"
        assert item["grid_safe"] is True
        assert item["thumbnail"]["role"] == "glyph"
        assert item["thumbnail"]["available"] is True
        assert item["thumbnail"]["iiif_url"].startswith("https://example.org/")
        assert item["id"] and item["unit_id"] is None

    def test_the_width_parameter_rebuilds_the_url(self, api_with_glyph):
        _, payload = get(api_with_glyph, "/api/corpus/glyphs?char=U%2B2A708&w=512")
        assert "/512,/0/default.jpg" in payload["items"][0]["thumbnail"]["iiif_url"]

    def test_no_line_rectangles_are_ever_returned(self, api_with_glyph):
        _, payload = get(api_with_glyph, "/api/corpus/glyphs?char=U%2B2A708")
        for item in payload["items"]:
            assert item["thumbnail"]["role"] == "glyph"
            assert item["box"] != {"x": 10, "y": 20, "w": 40, "h": 900}

    def test_a_char_with_no_glyph_returns_an_empty_but_labeled_grid(self, api):
        status, payload = get(api, "/api/corpus/glyphs?char=U%2B2A708")
        assert status == 200
        assert payload["items"] == []
        assert payload["grid_safe"] is True

    def test_classification_needs_a_locatable_character(self, api):
        status, _ = get(api, "/api/corpus/glyphs?char=abc")
        assert status == 400


class TestFind:
    def test_total_is_labelled_a_raw_hit_count(self, api):
        _, payload = get(api, "/api/corpus/find?char=U%2B2A708")
        assert payload["total_kind"] == "raw_hit_count"
        assert payload["merges_are_proven_only"] is True
        assert payload["probable_glyph_groups_are_estimate"] is True

    def test_tiers_separate_located_from_text_only(self, api):
        _, payload = get(api, "/api/corpus/find?char=U%2B2A708&limit=50")
        tiers = {item["tier"] for item in payload["items"]}
        assert "page_text" in tiers
        for item in payload["items"]:
            if item["tier"] == "page_text":
                assert item["rects"] == [] and item["has_crop"] is False

    def test_located_filter_keeps_only_real_rectangles(self, api):
        _, payload = get(api, "/api/corpus/find?char=U%2B2A708&located=1&limit=50")
        assert all(item["has_crop"] for item in payload["items"])

    def test_pagination_is_stable(self, api):
        _, first = get(api, "/api/corpus/find?char=U%2B2A708&limit=2&offset=0")
        _, second = get(api, "/api/corpus/find?char=U%2B2A708&limit=2&offset=2")
        ids = [i["occurrence_id"] for i in first["items"]]
        ids += [i["occurrence_id"] for i in second["items"]]
        assert len(set(ids)) == len(ids)


class TestThumbRoleHonesty:
    def test_a_line_is_reported_as_a_line_even_when_a_glyph_was_asked_for(self, api):
        """The bug this guards: labelling a whole line as a glyph."""
        _, found = get(api, "/api/corpus/find?char=U%2B2A708&tier=line_rect&limit=1")
        occurrence_id = found["items"][0]["occurrence_id"]
        status, payload = get(api, f"/api/corpus/thumb?occurrence_id={occurrence_id}&role=glyph")
        assert status == 200
        assert payload["requested_role"] == "glyph"
        assert payload["role"] == "line"  # what it actually is
        assert payload["role_matched_request"] is False
        assert payload["has_glyph"] is False

    def test_an_unlocated_hit_says_why_it_has_no_image(self, api):
        _, found = get(api, "/api/corpus/find?char=U%2B2A708&tier=page_text&limit=1")
        occurrence_id = found["items"][0]["occurrence_id"]
        _, payload = get(api, f"/api/corpus/thumb?occurrence_id={occurrence_id}")
        assert payload["available"] is False
        assert "not a crop" in payload["reason"]


class TestRightsAndPrivacy:
    def test_licences_are_canonical_and_nd_is_not_proxyable(self):
        assert "PDM-1.0" in PROXYABLE
        assert "CC-BY-4.0" in PROXYABLE
        assert "CC-BY-NC-ND-4.0" not in PROXYABLE
        assert "CC-BY-NC-ND-4.0" in NOT_PROXYABLE_REASON
        # the hyphenated form is what importers store; the spaced form must not be here
        assert "CC BY 4.0" not in PROXYABLE
        assert "unknown" not in PROXYABLE

    def test_sources_and_stats_leak_no_absolute_path(self, api):
        for target in ("/api/corpus/sources", "/api/corpus/stats"):
            _, payload = get(api, target)
            blob = json.dumps(payload)
            assert "/tmp/" not in blob and "/home/" not in blob
            if target.endswith("stats"):
                assert payload["directory"] == Path(payload["directory"]).name


class TestRootPassing:
    def test_an_index_builds_from_the_root_it_was_given(self, tmp_path):
        """The root travels with the index; it is never taken from a default."""
        from glyph_atlas.corpus import CorpusIndex

        root = tmp_path / "shared-work"
        root.mkdir()
        build_corpus(root)
        directory = tmp_path / "index"
        build_chars(root, directory)
        idx = CorpusIndex(directory, root)
        status = idx.build_occurrences(TOMO)
        assert status["records"] > 0
        assert idx.has_occurrences(TOMO) is True
        # A different root finds nothing, rather than silently reading the default.
        elsewhere = CorpusIndex(tmp_path / "other-index", tmp_path / "elsewhere")
        assert elsewhere.build_occurrences(TOMO)["records"] == 0


class TestNoBuildInRequestPath:
    def test_a_cold_character_is_answered_not_built(self, api_cold):
        """Building a common character inside a request is what exhausted the server."""
        status, payload = get(api_cold, "/api/corpus/find?char=U%2B2A708")
        assert status == 200
        assert payload["state"] == "not_indexed"
        # null, not zero: zero would render downstream as "there are none"
        assert payload["total"] is None
        assert payload["total_is_exact"] is False
        assert payload["available"] is False
        assert payload["returned"] == 0
        # the exact count is still available, without a scan
        assert payload["known_occurrences"] > 0
        assert payload["known_occurrences_are_exact"] is True
        assert payload["build_hint"]["bounded"] is True

    def test_a_cold_character_creates_no_file(self, api_cold):
        get(api_cold, "/api/corpus/find?char=U%2B2A708")
        assert api_cold.index.has_occurrences(TOMO) is False

    def test_index_status_reports_without_building(self, api_cold):
        status, payload = get(api_cold, "/api/corpus/index-status?char=U%2B2A708")
        assert status == 200
        assert payload["state"] == "known_but_not_indexed"
        assert payload["indexed"] is False

    def test_counts_still_work_while_a_character_is_unindexed(self, api_cold):
        status, payload = get(api_cold, "/api/corpus/counts?chars=%F0%AA%9C%88")
        assert status == 200
        assert payload["chars"][0]["n_glyphs"] >= 0
        assert payload["chars"][0]["n_line_hits"] > 0

    def test_a_capped_build_says_it_is_truncated(self, tmp_path):
        from glyph_atlas.corpus import CorpusIndex

        root = tmp_path / "shared-work"
        root.mkdir()
        build_corpus(root)
        directory = tmp_path / "index"
        build_chars(root, directory)
        idx = CorpusIndex(directory, root)
        status = idx.build_occurrences(TOMO, max_records=1)
        assert status["records"] == 1
        assert status["truncated"] is True


class TestMissingIndex:
    def test_a_missing_index_is_a_503_without_paths(self, tmp_path):
        api = CorpusAPI(tmp_path / "shared-work", tmp_path / "nope")
        status, payload = get(api, "/api/corpus/find?char=a")
        assert status == 503
        assert "/tmp/" not in json.dumps(payload)


class TestFastApiAdapter:
    def test_the_query_builder_encodes_code_points_and_repetition(self):
        query = build_query(char="U+2A708", corpus=["a", "b"], limit=2)
        assert "char=U%2B2A708" in query
        assert "corpus=a&corpus=b" in query

    def test_an_ampersand_in_a_value_does_not_split_the_query(self):
        assert build_query(q="a&b=c") == "q=a%26b%3Dc"

    def test_the_router_serves_the_same_payload_as_the_object(self, api):
        fastapi = pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient

        from glyph_atlas.corpus.fastapi_router import corpus_router

        app = fastapi.FastAPI()
        app.include_router(corpus_router(api=api))  # share the instance
        client = TestClient(app)
        response = client.get("/api/corpus/counts", params={"chars": TOMO})
        assert response.status_code == 200
        assert response.json()["chars"][0]["codepoint"] == "U+2A708"


class TestGlyphsRouteWithoutChar:
    """The route must accept the no-character homepage sample.

    The framework-free layer always did; the FastAPI signature made `char` required,
    so the mounted route answered 422 for the exact URL the homepage uses.
    """

    @pytest.fixture()
    def client(self, api):
        fastapi = pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient

        from glyph_atlas.corpus.fastapi_router import corpus_router

        app = fastapi.FastAPI()
        app.include_router(corpus_router(api=api))
        return TestClient(app)

    def test_no_char_is_a_200_not_a_422(self, client):
        response = client.get("/api/corpus/glyphs", params={"limit": 4})
        assert response.status_code == 200, response.text
        assert response.json()["returned"] <= 4

    def test_the_sample_and_the_framework_free_layer_agree(self, client, api):
        over_http = client.get("/api/corpus/glyphs", params={"limit": 4}).json()
        in_process = get(api, "/api/corpus/glyphs?limit=4")[1]
        assert set(over_http) == set(in_process)
        assert over_http["total"] == in_process["total"]

    def test_a_character_still_works_on_the_same_route(self, client):
        response = client.get("/api/corpus/glyphs", params={"char": TOMO, "limit": 2})
        assert response.status_code == 200
        assert response.json()["char"] == TOMO

    def test_the_sample_publishes_its_item_schema(self, api):
        _, payload = get(api, "/api/corpus/glyphs?limit=4")
        assert "schema" in payload
        assert payload["render_available_all"] is True

    def test_every_sample_item_is_renderable_and_glyph_only(self, api):
        _, payload = get(api, "/api/corpus/glyphs?limit=10")
        for item in payload["items"]:
            assert item["render_available"] is True
            assert item["thumbnail"]["available"] is True
            assert item["thumbnail"]["role"] == "glyph"
            # a render path must actually be present
            assert item["thumbnail"].get("crop_url") or item["thumbnail"].get("iiif_url")

    def test_a_page_or_line_rectangle_is_never_in_the_sample(self, api):
        _, payload = get(api, "/api/corpus/glyphs?limit=10")
        boxes = [i["box"] for i in payload["items"] if i["box"]]
        assert all(b["w"] < 1000 and b["h"] < 1000 for b in boxes)
