"""Click-through detail for a corpus glyph row.

Everything here runs against small synthetic corpora in ``tmp_path``: no network, no
shared dataset, no build. The resolver is exercised the way the viewer uses it — by
handing back an ``id`` the glyph list produced, and by handing it things that are not
ids at all.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from urllib.parse import quote

import pytest

from glyph_atlas import tables
from glyph_atlas.corpus import CorpusAPI, build_chars
from glyph_atlas.corpus.crops import _hng_crop_prefix
from glyph_atlas.corpus.details import (
    CONTEXT_PAD,
    DETAIL_EDGE,
    UNIT_CORPORA,
    UNIT_ID_PREFIXES,
    DetailResolver,
    detail,
    resolver_for,
)
from glyph_atlas.corpus.glyphs import GLYPHS_FILE
from glyph_atlas.corpus.glyphs import build as build_registry
from glyph_atlas.corpus.sources import ID_FAMILIES
from glyph_atlas.schema import Box, Document, Line, Page, Unit

#: The HNG mirror's real crop path (relative to a local clone) for one test unit.
HNG_CROP_PATH = "01_誠實論卷八（P.2179）/glyphs/BMP/00350.bmp"

TOMO = "\U0002a708"
PAGE_W, PAGE_H = 400, 600


def jpeg(width=PAGE_W, height=PAGE_H, colour=(222, 212, 192)) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buffer, format="JPEG")
    return buffer.getvalue()


def bmp(width=41, height=73, colour=(30, 60, 90)) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buffer, format="BMP")
    return buffer.getvalue()


def write_units(
    root: Path,
    name: str,
    *,
    image: str,
    units: list[Unit],
    licence: str = "CC-BY-4.0",
    lines: list[Line] | None = None,
    page_id: str | None = None,
) -> Path:
    out = root / name
    out.mkdir(parents=True, exist_ok=True)
    page_id = page_id or f"{name}:p1"
    doc = Document(
        id=f"d:{name}",
        title=f"{name} source",
        holder="Holding body",
        shelfmark=f"S-{name}",
        image_rights={"licence": licence, "holder": "Holding body", "attribution": "Holding body"},
    )
    page = Page(
        id=page_id,
        document_id=f"d:{name}",
        seq=1,
        image=image,
        canvas=f"https://example.invalid/record/{name}",
        width=PAGE_W,
        height=PAGE_H,
    )
    tables.write(out / "documents.parquet", [doc], Document)
    tables.write(out / "pages.parquet", [page], Page)
    tables.write(out / "units.parquet", units, Unit)
    if lines:
        tables.write(out / "lines.parquet", lines, Line)
    (out / "MANIFEST.json").write_text(
        json.dumps(
            {
                "schema_version": tables.SCHEMA_VERSION,
                "tables": {"documents": 1, "pages": 1, "units": len(units)},
                "files": {},
                "writer": "test",
                "command": "test",
            }
        ),
        encoding="utf-8",
    )
    return out


def unit(
    n: int, *, box=True, page_id="kokatsuji:p1", label="一", cp="U+4E00", crop=None, line_id=None
) -> Unit:
    return Unit(
        id=f"kokatsuji:u{n}",
        document_id="d:kokatsuji",
        page_id=page_id,
        seq=n,
        line_id=line_id,
        box=Box(x=20 * n, y=30, w=60, h=70) if box else None,
        crop=crop,
        text_source=label,
        unicode=cp,
        kind="char",
        method="import",
        active=True,
    )


@pytest.fixture(autouse=True)
def empty_image_cache(tmp_path, monkeypatch):
    """Page scans held by the repository's image cache would turn holder rows local."""
    monkeypatch.setenv("GLYPH_ATLAS_CACHE", str(tmp_path / "image-cache"))


@pytest.fixture()
def viewer(tmp_path: Path):
    """A Kokatsuji-shaped corpus with real local bytes, plus two other corpora."""
    root = tmp_path / "work"
    root.mkdir()
    images = root / "kokatsuji" / "images"
    images.mkdir(parents=True)
    (images / "001.jpg").write_bytes(jpeg())
    line = Line(
        id="kokatsuji:L1",
        page_id="kokatsuji:p1",
        seq=1,
        box=Box(x=10, y=20, w=200, h=400),
        text_raw="一",
        text="一",
        meta={},
    )
    write_units(
        root,
        "kokatsuji",
        image=f"file:{images.relative_to(tmp_path)}/001.jpg",
        units=[unit(1, line_id="kokatsuji:L1"), unit(2, box=False), unit(3, cp="U+4E8C", label="二")],
        lines=[line],
    )

    # A CODH-shaped corpus: remote IIIF page, no local bytes, and the book id present
    # in both the image service path and the document id, as the real import produces.
    codh = root / "codh-full"
    codh.mkdir(parents=True, exist_ok=True)
    book = "100241706"
    pages = [
        Page(
            id="codh-full:p1",
            document_id=f"codh:{book}",
            seq=1,
            image=f"https://codh.rois.ac.jp/char-shape/iiif/{book}/{book}_00001_1.tif",
            width=PAGE_W,
            height=PAGE_H,
        ),
        Page(
            id="codh-full:p2",
            document_id=f"codh:{book}",
            seq=3,
            image=f"https://codh.rois.ac.jp/char-shape/iiif/{book}/{book}_00002_1.tif",
            width=PAGE_W,
            height=PAGE_H,
        ),
    ]
    units = [
        Unit(
            id="codh:u1",
            document_id=f"codh:{book}",
            page_id="codh-full:p1",
            seq=1,
            box=Box(x=1, y=2, w=30, h=40),
            text_source="五",
            unicode="U+4E94",
            kind="char",
            method="import",
            active=True,
        )
    ]
    tables.write(
        codh / "documents.parquet",
        [
            Document(
                id=f"codh:{book}",
                title="codh-full source",
                holder="Holding body",
                source_refs={"codh-char-shape": book},
                image_rights={
                    "licence": "CC-BY-SA-4.0",
                    "holder": "Holding body",
                    "attribution": "Holding body",
                },
            )
        ],
        Document,
    )
    tables.write(codh / "pages.parquet", pages, Page)
    tables.write(codh / "units.parquet", units, Unit)
    (codh / "MANIFEST.json").write_text(
        json.dumps(
            {
                "schema_version": tables.SCHEMA_VERSION,
                "tables": {"documents": 1, "pages": len(pages), "units": len(units)},
                "files": {},
                "writer": "test",
                "command": "test",
            }
        ),
        encoding="utf-8",
    )

    # A corpus whose crops are archive members that were never extracted.
    write_units(
        root,
        "hilab",
        image="https://example.invalid/iiif/hilab.tif",
        units=[
            Unit(
                id="hi:1",
                document_id="d:hilab",
                page_id="hilab:p1",
                seq=1,
                box=None,
                crop="all.zip!all/characters/U+4E00/1.jpg",
                text_source="一",
                unicode="U+4E00",
                kind="char",
                method="import",
                active=True,
            )
        ],
    )

    # An HNG-shaped corpus: a GitHub URL crop, resolved against a local clone.
    hng_crop_url = _hng_crop_prefix() + quote(HNG_CROP_PATH)
    write_units(
        root,
        "hng",
        image="https://example.invalid/iiif/hng.tif",
        units=[
            Unit(
                id="hng:jou:00350",
                document_id="d:hng",
                page_id=None,
                seq=1,
                box=None,
                crop=hng_crop_url,
                text_source="誠",
                unicode="U+8AA0",
                kind="char",
                method="import",
                active=True,
            )
        ],
    )
    clone_file = root / "hng-basic-data" / HNG_CROP_PATH
    clone_file.parent.mkdir(parents=True)
    clone_file.write_bytes(bmp())

    # A corpus whose licence forbids us serving its images.
    write_units(
        root,
        "honkoku-lines",
        image="https://example.invalid/iiif/restricted.tif",
        licence="restricted",
        units=[
            Unit(
                id="hl:u1",
                document_id="d:honkoku-lines",
                page_id="honkoku-lines:p1",
                seq=1,
                box=Box(x=5, y=6, w=30, h=40),
                text_source="六",
                unicode="U+516D",
                kind="char",
                method="import",
                active=True,
            )
        ],
    )

    directory = tmp_path / "index"
    build_chars(root, directory)
    # `root` must stay a file base: `file:` references resolve against the corpus
    # root as well as against wherever the import ran.
    api = CorpusAPI(root, directory, file_bases=[root, tmp_path])
    return api, root, directory


@pytest.fixture()
def anchored(viewer, tmp_path):
    """The same corpora plus one registered glyph anchor on a text line."""
    api, root, directory = viewer
    line = Line(
        id="kokatsuji:L1",
        page_id="kokatsuji:p1",
        seq=1,
        box=Box(x=10, y=20, w=200, h=400),
        text_raw="山" + TOMO,
        text="山" + TOMO,
        meta={"iiif_region_url": "https://example.invalid/iiif/page.tif/10,20,200,400/full/0/default.jpg"},
    )
    write_units(
        root,
        "codh-full",
        image="https://codh.rois.ac.jp/char-shape/iiif/100241706/100241706_00001_1.tif",
        units=[
            Unit(
                id="codh:u1",
                document_id="d:codh-full",
                page_id="codh-full:p1",
                seq=1,
                box=Box(x=1, y=2, w=30, h=40),
                text_source="五",
                unicode="U+4E94",
                kind="char",
                method="import",
                active=True,
            )
        ],
        lines=[line],
    )
    registry = build_registry(
        [
            {
                "char": TOMO,
                "codepoint": "U+2A708",
                "char_class": "literal_text",
                "image_service": "https://example.invalid/iiif/page.tif",
                "iiif_url": "https://example.invalid/iiif/page.tif/30,40,20,20/320,/0/default.jpg",
                "source": {
                    "corpus": "codh-full",
                    "document_id": "d:codh-full",
                    "page_id": "codh-full:p1",
                    "line_id": "kokatsuji:L1",
                    "title": "codh-full source",
                    "holder": "Holding body",
                    "image_rights": {"licence": "CC-BY-SA-4.0"},
                },
                "text": {"span_start": 1},
                "rects": [
                    {
                        "x": 10,
                        "y": 20,
                        "w": 200,
                        "h": 400,
                        "role": "line",
                        "basis": "upstream_bbox",
                        "confirmed": False,
                    },
                    {
                        "x": 30,
                        "y": 40,
                        "w": 20,
                        "h": 20,
                        "role": "glyph",
                        "basis": "machine_projection",
                        "confirmed": False,
                        "method": "projection_segmentation_padded",
                    },
                ],
                "review": {"state": "machine", "human_validated": False, "events": []},
            }
        ],
        ID_FAMILIES,
    )
    registry.save(directory / GLYPHS_FILE)
    api.index._glyphs = None  # let the index reload the registry
    key = next(iter(registry.entries))
    return api, key


class TestResolution:
    def test_a_local_unit_resolves_to_a_tile_the_api_serves(self, viewer):
        api, _, _ = viewer
        payload = detail(api, "kokatsuji:u1")
        assert payload["id"] == "kokatsuji:u1"
        assert payload["unit_id"] == "kokatsuji:u1"
        assert payload["image"].startswith("/api/corpus/crop?unit_id=")
        assert payload["image_is_served_by_this_api"] is True
        assert payload["image_unavailable_reason"] is None

    def test_the_larger_edge_is_requested(self, viewer):
        api, _, _ = viewer
        assert f"w={DETAIL_EDGE}" in detail(api, "kokatsuji:u1")["image"]

    def test_a_remote_iiif_unit_resolves_to_a_holder_region_url(self, viewer):
        api, _, _ = viewer
        payload = detail(api, "codh:u1")
        assert payload["image"].startswith("https://codh.rois.ac.jp/char-shape/iiif/")
        assert f"/{DETAIL_EDGE},/0/default.jpg" in payload["image"]
        assert payload["image_is_served_by_this_api"] is False

    def test_an_anchor_resolves_by_its_identity_key(self, anchored):
        api, key = anchored
        payload = detail(api, key)
        assert payload["id"] == key
        assert payload["unit_id"] is None
        assert payload["box"] == {"x": 30, "y": 40, "w": 20, "h": 20}

    def test_an_identity_survives_being_resolved_twice(self, viewer):
        api, _, _ = viewer
        resolver = DetailResolver(api)
        assert detail(api, "kokatsuji:u1", cache=resolver) == detail(api, "kokatsuji:u1", cache=resolver)


class TestPublicFields:
    def test_the_promised_fields_are_all_present(self, viewer):
        api, _, _ = viewer
        payload = detail(api, "kokatsuji:u1")
        for field in (
            "id",
            "unit_id",
            "char",
            "label",
            "reading",
            "code_point",
            "box",
            "image",
            "proxyable",
            "licence",
            "source",
            "context_image",
            "context_box",
            "crop_box",
            "source_revision",
        ):
            assert field in payload, field

    def test_the_source_block_names_the_corpus_and_the_holder(self, viewer):
        api, _, _ = viewer
        source = detail(api, "kokatsuji:u1")["source"]
        assert source["corpus"] == "kokatsuji"
        assert source["title"] == "kokatsuji source"
        assert source["holder"] == "Holding body"
        assert source["page_id"] == "kokatsuji:p1"
        assert set(source) >= {
            "corpus",
            "document_id",
            "page_id",
            "title",
            "holder",
            "source_url",
            "image_service",
        }

    def test_the_label_is_marked_unverified(self, viewer):
        api, _, _ = viewer
        payload = detail(api, "kokatsuji:u1")
        assert payload["label"] == "一"
        assert payload["label_is_verified"] is False
        assert payload["source_label_is_verified"] is False
        assert "human confirmation" in payload["label_note"]
        assert payload["char"] == "一"
        assert payload["code_point"] == "U+4E00"

    def test_the_label_is_the_written_character_not_the_reading(self, viewer, tmp_path):
        """ネ written, ね read: the panel must show the glyph, not the reading."""
        api, root, _ = viewer
        written = Unit(
            id="kokatsuji:katakana",
            document_id="d:kokatsuji",
            page_id="kokatsuji:p1",
            seq=9,
            box=Box(x=20, y=30, w=60, h=70),
            text_source="ね",
            reading="ね",
            unicode="U+30CD",  # ネ
            kind="char",
            method="import",
            active=True,
        )
        write_units(
            root,
            "kokatsuji",
            image=(root / "kokatsuji" / "images" / "001.jpg").as_posix().replace(str(root.parent), "file:."),
            units=[written, unit(2, box=False), unit(3, cp="U+4E8C", label="二")],
            lines=[
                Line(
                    id="kokatsuji:L1",
                    page_id="kokatsuji:p1",
                    seq=1,
                    box=Box(x=10, y=20, w=200, h=400),
                    text_raw="一",
                    text="一",
                    meta={},
                )
            ],
        )
        payload = detail(api, "kokatsuji:katakana")
        assert payload["label"] == "ネ"  # written
        assert payload["char"] == "ネ"
        assert payload["source_label"] == "ね"  # the source's own transcription
        assert payload["reading"] == "ね"  # kept separate, not conflated
        assert payload["code_point"] == "U+30CD"

    def test_the_exact_original_box_is_returned(self, viewer):
        api, _, _ = viewer
        payload = detail(api, "kokatsuji:u1")
        assert payload["box"] == payload["crop_box"] == {"x": 20, "y": 30, "w": 60, "h": 70}


class TestHonestAbsence:
    def test_an_unextracted_archive_crop_has_no_image_and_says_why(self, viewer):
        api, _, _ = viewer
        payload = detail(api, "hi:1")
        assert payload["image"] is None
        assert "not been extracted" in payload["image_unavailable_reason"]


class TestHNGStandaloneCrop:
    """An HNG unit: no page, no box, a GitHub URL resolved against a local clone."""

    def test_a_cloned_crop_is_served_by_this_api(self, viewer):
        api, _, _ = viewer
        payload = detail(api, "hng:jou:00350")
        assert payload["source"]["corpus"] == "hng"
        assert payload["box"] is None
        assert payload["image"] == "/api/corpus/crop?unit_id=hng%3Ajou%3A00350&w=480"
        assert payload["image_is_served_by_this_api"] is True
        assert payload["image_unavailable_reason"] is None

    def test_without_a_clone_the_raw_mirror_url_is_used(self, tmp_path, monkeypatch):
        """No local clone: the detail still resolves, pointing straight at the mirror."""
        monkeypatch.setenv("GLYPH_ATLAS_CACHE", str(tmp_path / "image-cache"))
        root = tmp_path / "work"
        root.mkdir()
        crop_url = _hng_crop_prefix() + quote(HNG_CROP_PATH)
        write_units(
            root,
            "hng",
            image="https://example.invalid/iiif/hng.tif",
            units=[
                Unit(
                    id="hng:jou:00350",
                    document_id="d:hng",
                    page_id=None,
                    seq=1,
                    box=None,
                    crop=crop_url,
                    text_source="誠",
                    unicode="U+8AA0",
                    kind="char",
                    method="import",
                    active=True,
                )
            ],
        )
        directory = tmp_path / "index"
        build_chars(root, directory)
        api = CorpusAPI(root, directory, file_bases=[root, tmp_path])
        payload = detail(api, "hng:jou:00350")
        assert payload["image"] == crop_url
        assert payload["image_is_served_by_this_api"] is False
        assert payload["image_unavailable_reason"] is None

    def test_a_unit_that_is_not_a_grid_row_is_not_resolvable(self, viewer):
        """Only rows the glyph list can return have a detail, so the ids always agree."""
        api, _, _ = viewer
        with pytest.raises(KeyError):
            detail(api, "kokatsuji:u2")

    def test_a_renderable_unit_keeps_its_exact_box(self, viewer):
        api, _, _ = viewer
        payload = detail(api, "kokatsuji:u1")
        assert payload["box"] == {"x": 20, "y": 30, "w": 60, "h": 70}
        assert payload["crop_box"] == payload["box"]

    def test_a_corpus_without_lines_gets_a_labelled_viewport_not_a_line(self, viewer):
        """CODH records no line, so the context is a derived window, and says so."""
        api, _, _ = viewer
        payload = detail(api, "codh:u1")
        assert payload["context_basis"] == "derived_viewport"
        assert payload["context_image"] is not None
        assert payload["context_box"] != payload["box"]

    def test_the_derived_viewport_is_padded_around_the_real_box(self, viewer):
        api, _, _ = viewer
        payload = detail(api, "codh:u1")
        box, context = payload["box"], payload["context_box"]
        pad = round(max(box["w"], box["h"]) * CONTEXT_PAD)
        assert context["x"] == max(0, box["x"] - pad)
        assert context["y"] == max(0, box["y"] - pad)
        assert context["x"] + context["w"] == min(PAGE_W, box["x"] + box["w"] + pad)
        assert context["y"] + context["h"] == min(PAGE_H, box["y"] + box["h"] + pad)

    def test_the_derived_viewport_never_escapes_the_registered_page(self, viewer, tmp_path):
        api, root, _ = viewer
        edge = Unit(
            id="codh:edge",
            document_id="d:codh-full",
            page_id="codh-full:p1",
            seq=2,
            box=Box(x=390, y=590, w=8, h=8),
            text_source="端",
            unicode="U+7AEF",
            kind="char",
            method="import",
            active=True,
        )
        write_units(root, "codh-full", image="https://example.invalid/iiif/page.tif", units=[edge])
        api.index._glyphs = None
        context = detail(api, "codh:edge")["context_box"]
        assert context["x"] >= 0 and context["y"] >= 0
        assert context["x"] + context["w"] <= PAGE_W
        assert context["y"] + context["h"] <= PAGE_H

    def test_the_character_box_is_never_replaced_by_the_viewport(self, viewer):
        api, _, _ = viewer
        payload = detail(api, "codh:u1")
        assert payload["box"] == {"x": 1, "y": 2, "w": 30, "h": 40}
        assert payload["crop_box"] == payload["box"]

    def test_context_uses_the_line_rectangle_not_the_page(self, viewer):
        api, _, _ = viewer
        payload = detail(api, "kokatsuji:u1")
        assert payload["context_box"] == {"x": 10, "y": 20, "w": 200, "h": 400}
        assert payload["context_box"] != payload["crop_box"]

    def test_no_page_size_means_no_viewport(self, viewer, tmp_path):
        """Without a registered page size there is nothing to clamp to, so it is None."""
        api, root, _ = viewer
        out = root / "codh-full"
        page = Page(
            id="codh-full:nowidth",
            document_id="d:codh-full",
            seq=9,
            image="https://example.invalid/iiif/page.tif",
            width=0,
            height=0,
        )
        tables.write(out / "pages.parquet", [page], Page)
        write_units(
            root,
            "codh-full",
            image="https://example.invalid/iiif/page.tif",
            units=[
                Unit(
                    id="codh:u1",
                    document_id="d:codh-full",
                    page_id="codh-full:p1",
                    seq=1,
                    box=Box(x=1, y=2, w=30, h=40),
                    text_source="五",
                    unicode="U+4E94",
                    kind="char",
                    method="import",
                    active=True,
                )
            ],
        )
        tables.write(out / "pages.parquet", [page], Page)
        assert detail(api, "codh:u1")["context_image"] is None


class TestLicence:
    def test_an_open_unit_is_proxyable(self, viewer):
        api, _, _ = viewer
        payload = detail(api, "kokatsuji:u1")
        assert payload["licence"] == "CC-BY-4.0"
        assert payload["proxyable"] is True

    def test_a_restricted_image_is_not_served_by_this_api(self, viewer):
        api, _, _ = viewer
        payload = detail(api, "hl:u1")
        assert payload["proxyable"] is False
        assert payload["licence"] == "restricted"
        assert payload["image"] is None or payload["image"].startswith("https://")


class TestRefusals:
    @pytest.mark.parametrize(
        "identity",
        [
            "/etc/passwd",
            "../../etc/passwd",
            "file:/etc/passwd",
            "https://example.invalid/iiif/page.tif/1,2,3,4/480,/0/default.jpg",
            "http://example.invalid/",
            "codh-omt:001:1/../../etc/passwd",
            "a b",
            "",
            "x" * 400,
            "unit\x00id",
        ],
    )
    def test_anything_that_is_not_an_identity_raises_key_error(self, viewer, identity):
        api, _, _ = viewer
        with pytest.raises(KeyError):
            detail(api, identity)

    def test_an_unknown_but_well_formed_id_raises_key_error(self, viewer):
        api, _, _ = viewer
        with pytest.raises(KeyError):
            detail(api, "codh-omt:999:nope")

    def test_a_known_id_is_not_refused(self, viewer):
        api, _, _ = viewer
        assert detail(api, "kokatsuji:u1")["id"] == "kokatsuji:u1"


class TestNoDisclosure:
    def test_no_output_field_carries_a_filesystem_path(self, viewer, tmp_path):
        api, root, _ = viewer
        for identity in ("kokatsuji:u1", "codh:u1", "hi:1", "hl:u1", "kokatsuji:u3"):
            blob = json.dumps(detail(api, identity), ensure_ascii=False)
            assert str(tmp_path) not in blob
            assert "/home/" not in blob
            assert "file:" not in blob
            assert str(root) not in blob

    def test_a_local_crop_is_described_by_a_digest_not_a_path(self, viewer):
        api, _, _ = viewer
        blob = json.dumps(detail(api, "kokatsuji:u1"), ensure_ascii=False)
        assert "images/001.jpg" not in blob
        assert "crop" not in blob or "crop_box" in blob


class TestSourceRevision:
    def test_the_digest_is_stable_across_calls(self, viewer):
        api, _, _ = viewer
        assert (
            detail(api, "kokatsuji:u1")["source_revision"] == detail(api, "kokatsuji:u1")["source_revision"]
        )

    def test_different_glyphs_have_different_digests(self, viewer):
        api, _, _ = viewer
        assert (
            detail(api, "kokatsuji:u1")["source_revision"] != detail(api, "kokatsuji:u3")["source_revision"]
        )

    def test_the_digest_changes_when_the_box_changes(self, viewer, tmp_path):
        api, root, _directory = viewer
        before = detail(api, "kokatsuji:u1")["source_revision"]
        moved = unit(1, line_id="kokatsuji:L1")
        moved.box = Box(x=999, y=30, w=60, h=70)
        write_units(
            root,
            "kokatsuji",
            image=(root / "kokatsuji" / "images" / "001.jpg").as_posix().replace(str(tmp_path), "file:."),
            units=[moved, unit(2, box=False), unit(3, cp="U+4E8C", label="二")],
            lines=[
                Line(
                    id="kokatsuji:L1",
                    page_id="kokatsuji:p1",
                    seq=1,
                    box=Box(x=10, y=20, w=200, h=400),
                    text_raw="一",
                    text="一",
                    meta={},
                )
            ],
        )
        after = detail(api, "kokatsuji:u1")["source_revision"]
        assert before != after

    def test_the_digest_is_not_claimed_to_be_an_image_hash(self, viewer):
        api, _, _ = viewer
        payload = detail(api, "kokatsuji:u1")
        assert "not of image bytes" in payload["source_revision_note"]


class TestBounded:
    def test_the_cache_does_not_grow_without_limit(self, viewer):
        api, _, _ = viewer
        resolver = DetailResolver(api, cache_size=2)
        for identity in ("kokatsuji:u1", "codh:u1", "hi:1", "hl:u1"):
            detail(api, identity, cache=resolver)
        assert len(resolver._cache) <= 2

    def test_the_line_cache_is_bounded_too(self, viewer):
        api, _, _ = viewer
        resolver = DetailResolver(api, cache_size=1)
        detail(api, "kokatsuji:u1", cache=resolver)
        assert len(resolver._lines) <= 1

    def test_one_resolver_serves_every_corpus(self, viewer):
        api, _, _ = viewer
        resolver = DetailResolver(api)
        for identity, corpus in (
            ("kokatsuji:u1", "kokatsuji"),
            ("codh:u1", "codh-full"),
            ("hi:1", "hilab"),
            ("hl:u1", "honkoku-lines"),
            ("hng:jou:00350", "hng"),
        ):
            assert detail(api, identity, cache=resolver)["source"]["corpus"] == corpus


class TestCacheInvalidation:
    """A saved correction is only trustworthy while the row it describes is unchanged.

    So the resolver drops its cache when any underlying table or the glyph registry
    changes, and ``source_revision`` moves whenever the label, box or provenance does.
    """

    def test_a_rebuilt_table_invalidates_a_cached_detail(self, viewer):
        api, root, _ = viewer
        resolver = DetailResolver(api)
        before = detail(api, "kokatsuji:u1", cache=resolver)
        assert resolver.get("kokatsuji:u1") == before  # now cached

        moved = unit(1, line_id="kokatsuji:L1")
        moved.box = Box(x=111, y=222, w=33, h=44)
        write_units(
            root,
            "kokatsuji",
            image=(root / "kokatsuji" / "images" / "001.jpg").as_posix().replace(str(root.parent), "file:."),
            units=[moved, unit(2, box=False), unit(3, cp="U+4E8C", label="二")],
            lines=[
                Line(
                    id="kokatsuji:L1",
                    page_id="kokatsuji:p1",
                    seq=1,
                    box=Box(x=10, y=20, w=200, h=400),
                    text_raw="一",
                    text="一",
                    meta={},
                )
            ],
        )

        assert resolver.invalidate_if_changed() is True
        after = detail(api, "kokatsuji:u1", cache=resolver)
        assert after["box"] == {"x": 111, "y": 222, "w": 33, "h": 44}
        assert after["source_revision"] != before["source_revision"]

    def test_an_unchanged_corpus_keeps_the_cache(self, viewer):
        api, _, _ = viewer
        resolver = DetailResolver(api)
        detail(api, "kokatsuji:u1", cache=resolver)
        assert resolver.invalidate_if_changed() is False
        assert "kokatsuji:u1" in resolver._cache

    def test_a_changed_glyph_registry_invalidates_the_cache(self, anchored, tmp_path):
        api, key = anchored
        resolver = DetailResolver(api)
        detail(api, key, cache=resolver)

        registry_path = api.directory / GLYPHS_FILE
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
        payload["glyphs"][0]["x"] = 99
        payload["glyphs"][0]["w"] = 7
        registry_path.write_text(json.dumps(payload), encoding="utf-8")

        assert resolver.invalidate_if_changed() is True
        api.index._glyphs = None
        assert detail(api, key, cache=resolver)["box"]["x"] == 99

    def test_the_provenance_stamp_is_published_without_paths(self, viewer):
        api, _, _ = viewer
        payload = detail(api, "kokatsuji:u1")
        assert payload["provenance_stamp"]
        assert "/" not in payload["provenance_stamp"]
        assert "stale" in payload["provenance_stamp_note"]


class TestStaleness:
    def test_a_relabelled_unit_changes_the_source_revision(self, viewer):
        api, root, _ = viewer
        before = detail(api, "kokatsuji:u1")["source_revision"]
        relabelled = unit(1, line_id="kokatsuji:L1", label="乙", cp="U+4E59")
        write_units(
            root,
            "kokatsuji",
            image=(root / "kokatsuji" / "images" / "001.jpg").as_posix().replace(str(root.parent), "file:."),
            units=[relabelled, unit(2, box=False), unit(3, cp="U+4E8C", label="二")],
            lines=[
                Line(
                    id="kokatsuji:L1",
                    page_id="kokatsuji:p1",
                    seq=1,
                    box=Box(x=10, y=20, w=200, h=400),
                    text_raw="一",
                    text="一",
                    meta={},
                )
            ],
        )
        after = detail(api, "kokatsuji:u1")["source_revision"]
        assert after != before

    def test_the_same_row_yields_the_same_revision(self, viewer):
        api, _, _ = viewer
        resolver = DetailResolver(api)
        first = detail(api, "kokatsuji:u1", cache=resolver)["source_revision"]
        resolver.invalidate_if_changed()
        assert detail(api, "kokatsuji:u1", cache=resolver)["source_revision"] == first


class TestLookupIsRoutedNotBudgeted:
    """The bound on a lookup is routing, not a counter over already-filtered rows."""

    def test_a_known_prefix_opens_exactly_one_corpus(self, viewer):
        api, _, _ = viewer
        resolver = DetailResolver(api)
        assert resolver._corpus_order("codh-omt:001:1") == ["kokatsuji"]
        assert resolver._corpus_order("hi:1") == ["hilab"]
        assert resolver._corpus_order("hl:abc") == ["honkoku-lines"]
        assert resolver._corpus_order("codh:1:x") == ["codh-full"]
        assert resolver._corpus_order("hng:jou:00350") == ["hng"]
        assert UNIT_ID_PREFIXES["hng-kiridashi:"] == "hng-kiridashi"

    def test_an_unfamiliar_prefix_still_looks_everywhere(self, viewer):
        api, _, _ = viewer
        resolver = DetailResolver(api)
        assert resolver._corpus_order("nothing-like-an-id") == list(UNIT_CORPORA)

    def test_an_unknown_id_returns_without_caching_a_miss(self, viewer):
        api, _, _ = viewer
        resolver = DetailResolver(api)
        with pytest.raises(KeyError):
            detail(api, "codh-omt:999:does-not-exist", cache=resolver)
        assert resolver._cache == {}

    def test_an_id_from_the_wrong_corpus_is_not_found_under_its_prefix(self, viewer):
        """Routing is by prefix, so a real id must not appear under a foreign one."""
        api, _, _ = viewer
        with pytest.raises(KeyError):
            detail(api, "hi:kokatsuji:u1")


class TestResolverIsSharedPerApi:
    """`detail()` must not rebuild a resolver on every click."""

    def test_the_same_api_gets_the_same_resolver(self, viewer):
        api, _, _ = viewer
        assert resolver_for(api) is resolver_for(api)

    def test_detail_reuses_the_shared_resolver(self, viewer):
        api, _, _ = viewer
        detail(api, "kokatsuji:u1")
        shared = resolver_for(api)
        assert shared._cache, "the shared resolver should have cached the answer"
        assert detail(api, "kokatsuji:u1") == shared._cache["kokatsuji:u1"]

    def test_a_different_api_gets_its_own_resolver(self, tmp_path):
        first = tmp_path / "a"
        second = tmp_path / "b"
        for base in (first, second):
            root = base / "work"
            root.mkdir(parents=True)
            images = root / "kokatsuji" / "images"
            images.mkdir(parents=True)
            (images / "001.jpg").write_bytes(jpeg())
            write_units(root, "kokatsuji", image=f"file:{images.relative_to(base)}/001.jpg", units=[unit(1)])
            build_chars(root, base / "index")
        api_a = CorpusAPI(first / "work", first / "index", file_bases=[first / "work", first])
        api_b = CorpusAPI(second / "work", second / "index", file_bases=[second / "work", second])
        assert resolver_for(api_a) is not resolver_for(api_b)

    def test_an_explicit_cache_still_wins(self, viewer):
        api, _, _ = viewer
        mine = DetailResolver(api)
        detail(api, "kokatsuji:u1", cache=mine)
        assert "kokatsuji:u1" in mine._cache


class TestCropResolverIsInvalidatedToo:
    """A rebuilt table must not leave the crop resolver serving the old row's bytes."""

    def test_invalidation_clears_the_crop_resolvers_caches(self, viewer):
        api, root, _ = viewer
        resolver = DetailResolver(api)
        detail(api, "kokatsuji:u1", cache=resolver)
        assert api.crops.for_unit("kokatsuji:u1", edge=DETAIL_EDGE).bytes_data

        write_units(
            root,
            "kokatsuji",
            image=(root / "kokatsuji" / "images" / "001.jpg").as_posix().replace(str(root.parent), "file:."),
            units=[unit(1, line_id="kokatsuji:L1")],
            lines=[
                Line(
                    id="kokatsuji:L1",
                    page_id="kokatsuji:p1",
                    seq=1,
                    box=Box(x=10, y=20, w=200, h=400),
                    text_raw="一",
                    text="一",
                    meta={},
                )
            ],
        )
        assert resolver.invalidate_if_changed() is True
        assert api.crops._cache == {}
        assert api.crops._page_paths == {}
        assert api.crops._verified == {}


class TestCodhSourceLink:
    """The source link selects the registered canvas and character rectangle."""

    def test_the_link_opens_the_viewer_at_the_book_and_the_box(self, viewer):
        api, _, _ = viewer
        url = detail(api, "codh:u1")["record_url"]
        assert url.startswith("https://codh.rois.ac.jp/char-shape/app/icv-kuzushiji/?")
        assert "manifest=https%3A%2F%2Fcodh.rois.ac.jp%2Fchar-shape%2Fbook%2F" in url
        assert "book%2F100241706%2Fmanifest.json" in url  # book from the image service
        assert "xywh=1%2C2%2C30%2C40" in url
        assert "xywh_highlight=border" in url

    def test_canvas_selection_does_not_depend_on_partial_page_order(self, viewer):
        """Missing pages and gaps in seq do not change the selected canvas."""
        api, root, _ = viewer
        write_units(
            root,
            "codh-full",
            image="https://codh.rois.ac.jp/char-shape/iiif/100241706/100241706_00001_1.tif",
            units=[
                Unit(
                    id="codh:p1",
                    document_id="codh:100241706",
                    page_id="codh-full:p1",
                    seq=1,
                    box=Box(x=1, y=2, w=30, h=40),
                    text_source="五",
                    unicode="U+4E94",
                    kind="char",
                    method="import",
                    active=True,
                )
            ],
        )
        out = root / "codh-full"
        pages = [
            Page(
                id="codh-full:p1",
                document_id="codh:100241706",
                seq=1,
                image="https://codh.rois.ac.jp/char-shape/iiif/100241706/100241706_00001_1.tif",
                width=PAGE_W,
                height=PAGE_H,
            ),
            Page(
                id="codh-full:p2",
                document_id="codh:100241706",
                seq=3,
                image="https://codh.rois.ac.jp/char-shape/iiif/100241706/100241706_00002_1.tif",
                width=PAGE_W,
                height=PAGE_H,
            ),
        ]
        tables.write(out / "pages.parquet", pages, Page)
        api.index._glyphs = None
        url = detail(api, "codh:p1")["record_url"]
        assert "canvas%2F100241706_00001_1" in url
        assert "pos=" not in url

    def test_the_book_id_comes_from_the_registered_image_service(self, viewer):
        """A real CODH row: book id in the page service, box in source pixels."""
        api, root, _ = viewer
        book = "200008316"
        codh = root / "codh-full"
        pages = [
            Page(
                id="codh-full:q1",
                document_id=f"codh:{book}",
                seq=1,
                image=f"https://codh.rois.ac.jp/char-shape/iiif/{book}/{book}_00026_2.tif",
                width=PAGE_W,
                height=PAGE_H,
            ),
            Page(
                id="codh-full:q2",
                document_id=f"codh:{book}",
                seq=52,
                image=f"https://codh.rois.ac.jp/char-shape/iiif/{book}/{book}_00027_1.tif",
                width=PAGE_W,
                height=PAGE_H,
            ),
        ]
        tables.write(codh / "pages.parquet", pages, Page)
        write_units(
            root,
            "codh-full",
            image=pages[1].image,
            units=[
                Unit(
                    id="codh:b",
                    document_id=f"codh:{book}",
                    page_id="codh-full:q2",
                    seq=52,
                    box=Box(x=2305, y=1498, w=81, h=210),
                    text_source="五",
                    unicode="U+4E94",
                    kind="char",
                    method="import",
                    active=True,
                )
            ],
        )
        tables.write(codh / "pages.parquet", pages, Page)
        api.index._glyphs = None
        url = detail(api, "codh:b")["record_url"]
        assert "book%2F200008316%2Fmanifest.json" in url
        assert "canvas%2F200008316_00027_1" in url
        assert "pos=" not in url
        assert "xywh=2305%2C1498%2C81%2C210" in url

    def test_a_crop_url_is_never_the_source_link(self, viewer):
        api, _, _ = viewer
        for identity in ("kokatsuji:u1", "codh:u1", "hi:1", "hl:u1"):
            url = detail(api, identity)["record_url"]
            assert url is None or "/api/corpus/crop" not in url

    def test_a_local_corpus_gets_no_invented_viewer_link(self, viewer):
        api, _, _ = viewer
        assert detail(api, "kokatsuji:u1")["record_url"] is None


class TestAnchorIdentitiesBeforeRefusal:
    """A registered key is ours, so it is matched before the shape check rejects it."""

    @pytest.fixture()
    def odd_anchor(self, viewer):
        api, _root, directory = viewer
        key = "corpus|doc@example|page:1|line/2|U+2A708|3|literal_text"
        registry = build_registry(
            [
                {
                    "char": TOMO,
                    "codepoint": "U+2A708",
                    "char_class": "literal_text",
                    "image_service": "https://example.invalid/iiif/page.tif",
                    "iiif_url": "https://example.invalid/iiif/page.tif/5,6,7,8/320,/0/default.jpg",
                    "source": {
                        "corpus": "codh-full",
                        "document_id": "d:codh-full",
                        "page_id": "codh-full:p1",
                        "line_id": None,
                        "title": "t",
                        "holder": "h",
                        "image_rights": {"licence": "CC-BY-SA-4.0"},
                    },
                    "text": {"span_start": 3},
                    "rects": [
                        {
                            "x": 0,
                            "y": 0,
                            "w": 100,
                            "h": 100,
                            "role": "line",
                            "basis": "upstream_bbox",
                            "confirmed": False,
                        },
                        {
                            "x": 5,
                            "y": 6,
                            "w": 7,
                            "h": 8,
                            "role": "glyph",
                            "basis": "machine_projection",
                            "confirmed": False,
                        },
                    ],
                    "review": {"state": "machine", "human_validated": False, "events": []},
                }
            ],
            {},
        )
        # Register it under the odd key rather than the derived one.
        entry = next(iter(registry.entries.values()))
        entry.identity_key = key
        registry.entries = {key: entry}
        registry.save(directory / GLYPHS_FILE)
        api.index._glyphs = None
        return api, key

    def test_a_registered_key_with_separators_resolves(self, odd_anchor):
        api, key = odd_anchor
        payload = detail(api, key)
        assert payload["id"] == key
        assert payload["box"] == {"x": 5, "y": 6, "w": 7, "h": 8}

    def test_an_unregistered_key_of_the_same_shape_is_still_refused(self, odd_anchor):
        api, key = odd_anchor
        with pytest.raises(KeyError):
            detail(api, key.replace("doc@example", "somebody-else"))

    def test_a_path_is_still_refused_even_with_a_registry_present(self, odd_anchor):
        api, _ = odd_anchor
        for bad in ("/etc/passwd", "../../etc/passwd", "https://example.invalid/x"):
            with pytest.raises(KeyError):
                detail(api, bad)


class TestAnchorsGetAViewerNotAnImage:
    def test_the_registry_image_url_is_not_reused_as_the_source_link(self, anchored):
        api, key = anchored
        payload = detail(api, key)
        assert payload["record_url"] != payload["image"]
        assert payload["record_url"] is None or "/0/default.jpg" not in payload["record_url"]


class TestCodhAnchorGetsTheViewerLink:
    @pytest.fixture()
    def codh_anchor(self, viewer):
        api, _root, directory = viewer
        book = "100241706"
        key = f"codh-full|codh:{book}|codh-full:p2|-|U+4E94|1|literal_text"
        registry = build_registry(
            [
                {
                    "char": "五",
                    "codepoint": "U+4E94",
                    "char_class": "literal_text",
                    "image_service": f"https://codh.rois.ac.jp/char-shape/iiif/{book}/{book}_00002_1.tif",
                    "source": {
                        "corpus": "codh-full",
                        "document_id": f"codh:{book}",
                        "page_id": "codh-full:p2",
                        "line_id": None,
                        "title": "t",
                        "holder": "h",
                        "image_rights": {"licence": "CC-BY-SA-4.0"},
                    },
                    "text": {"span_start": 1},
                    "rects": [
                        {
                            "x": 0,
                            "y": 0,
                            "w": 100,
                            "h": 100,
                            "role": "line",
                            "basis": "upstream_bbox",
                            "confirmed": False,
                        },
                        {
                            "x": 11,
                            "y": 12,
                            "w": 13,
                            "h": 14,
                            "role": "glyph",
                            "basis": "machine_projection",
                            "confirmed": False,
                        },
                    ],
                    "review": {"state": "machine", "human_validated": False, "events": []},
                }
            ],
            {},
        )
        entry = next(iter(registry.entries.values()))
        entry.identity_key = key
        registry.entries = {key: entry}
        registry.save(directory / GLYPHS_FILE)
        api.index._glyphs = None
        return api, key

    def test_a_codh_anchor_gets_the_viewer_at_its_canvas(self, codh_anchor):
        api, key = codh_anchor
        url = detail(api, key)["record_url"]
        assert url.startswith("https://codh.rois.ac.jp/char-shape/app/icv-kuzushiji/?")
        assert "book%2F100241706%2Fmanifest.json" in url
        assert "canvas%2F100241706_00002_1" in url
        assert "pos=" not in url
        assert "xywh=11%2C12%2C13%2C14" in url
