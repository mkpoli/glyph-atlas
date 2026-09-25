"""Controlled crop bytes: resolution by registered id, never by path."""

from __future__ import annotations

import io
import json
from pathlib import Path
from urllib.parse import quote

import pytest

from glyph_atlas import tables
from glyph_atlas.corpus import CorpusAPI, build_chars
from glyph_atlas.corpus.crops import CropResolver, _hng_crop_prefix, sniff
from glyph_atlas.schema import Box, Document, Page, Unit

PAGE_W, PAGE_H = 400, 600


def jpeg(width=400, height=600, colour=(220, 210, 190)) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buffer, format="JPEG")
    return buffer.getvalue()


def bmp(width=41, height=73, colour=(30, 60, 90)) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buffer, format="BMP")
    return buffer.getvalue()


def build_crop_corpus(root: Path, images_dir: Path, *, licence="CC-BY-4.0") -> Path:
    """A corpus shaped like Kokatsuji: page image on disk, units with boxes."""
    out = root / "kokatsuji"
    out.mkdir(parents=True, exist_ok=True)
    images_dir.mkdir(parents=True, exist_ok=True)
    (images_dir / "001_001_1.jpg").write_bytes(jpeg())

    doc = Document(
        id="codh-omt:001",
        title="Test source",
        holder="Test holder",
        shelfmark="001",
        image_rights={"licence": licence, "holder": "Test holder", "attribution": "Test holder"},
    )
    page = Page(
        id="codh-omt:001:001_001_1",
        document_id="codh-omt:001",
        seq=1,
        image=f"file:{images_dir.relative_to(images_dir.parents[1])}/001_001_1.jpg",
        width=PAGE_W,
        height=PAGE_H,
    )
    units = [
        Unit(
            id="codh-omt:001:U1",
            document_id="codh-omt:001",
            page_id="codh-omt:001:001_001_1",
            seq=1,
            box=Box(x=20, y=30, w=80, h=90),
            text_source="一",
            unicode="U+4E00",
            kind="char",
            method="import",
            active=True,
            upstream={"source": "test", "url": "https://example.org/record/1"},
        ),
        Unit(
            id="codh-omt:001:U2",
            document_id="codh-omt:001",
            page_id="codh-omt:001:001_001_1",
            seq=2,
            box=None,
            crop=None,
            text_source="二",
            unicode="U+4E8C",
            kind="char",
            method="import",
            active=True,
        ),
        Unit(
            id="codh-omt:001:U3",
            document_id="codh-omt:001",
            page_id="codh-omt:001:001_001_1",
            seq=3,
            box=Box(x=200, y=300, w=60, h=70),
            crop="all.zip!all/characters/U+4E09/9.jpg",
            text_source="三",
            unicode="U+4E09",
            kind="char",
            method="import",
            active=True,
        ),
    ]
    tables.write(out / "documents.parquet", [doc], Document, command="test")
    tables.write(out / "pages.parquet", [page], Page)
    tables.write(out / "units.parquet", units, Unit)
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


@pytest.fixture()
def crop_api(tmp_path: Path):
    """A corpus whose images live under the corpus root, as Kokatsuji's do."""
    root = tmp_path / "work"
    root.mkdir()
    build_crop_corpus(root, root / "kokatsuji" / "images")
    directory = tmp_path / "index"
    build_chars(root, directory)
    return CorpusAPI(root, directory, file_bases=[root]), root


class TestSniff:
    @pytest.mark.parametrize(
        "payload,expected",
        [
            (b"\xff\xd8\xff\xe0rest", "image/jpeg"),
            (b"\x89PNG\r\n\x1a\nrest", "image/png"),
            (b"GIF89arest", "image/gif"),
            (b"not an image", None),
            (b"", None),
        ],
    )
    def test_media_type_comes_from_the_bytes(self, payload, expected):
        assert sniff(payload) == expected


class TestAllowList:
    def test_a_file_inside_the_corpus_image_dir_is_allowed(self, tmp_path):
        root = tmp_path / "work"
        root.mkdir()
        build_crop_corpus(root, root / "kokatsuji" / "images")
        resolver = CropResolver(root, file_bases=[root])
        assert resolver.allowed(root / "kokatsuji" / "images" / "001_001_1.jpg", "kokatsuji") is True

    def test_a_file_outside_is_refused(self, tmp_path):
        root = tmp_path / "work"
        root.mkdir()
        build_crop_corpus(root, root / "kokatsuji" / "images")
        secret = tmp_path / "secret.jpg"
        secret.write_bytes(jpeg(20, 20))
        resolver = CropResolver(root, file_bases=[root])
        assert resolver.allowed(secret, "kokatsuji") is False

    def test_a_traversal_path_is_refused(self, tmp_path):
        root = tmp_path / "work"
        root.mkdir()
        build_crop_corpus(root, root / "kokatsuji" / "images")
        secret = tmp_path / "secret.jpg"
        secret.write_bytes(jpeg(20, 20))
        resolver = CropResolver(root, file_bases=[root])
        escape = root / "kokatsuji" / "images" / ".." / ".." / ".." / "secret.jpg"
        assert resolver.allowed(escape, "kokatsuji") is False

    def test_one_corpus_cannot_reach_another_corpus_images(self, tmp_path):
        root = tmp_path / "work"
        root.mkdir()
        build_crop_corpus(root, root / "kokatsuji" / "images")
        other = root / "codh-full" / "images"
        other.mkdir(parents=True)
        (other / "x.jpg").write_bytes(jpeg(20, 20))
        resolver = CropResolver(root, file_bases=[root])
        assert resolver.allowed(other / "x.jpg", "kokatsuji") is False


class TestArchiveCrops:
    reference = "all.zip!all/characters/U+5047/34000647.jpg"

    @pytest.mark.parametrize("repository_cache", [False, True])
    def test_extracted_hilab_crop_is_served_without_imported_hash(self, tmp_path, repository_cache):
        root = tmp_path / "work"
        out = root / "hilab"
        out.mkdir(parents=True)
        tables.write(out / "documents.parquet", [Document(
            id="hi:dataset", title="HI Lab", holder="HI",
            image_rights={"licence": "CC-BY-4.0", "holder": "HI", "attribution": "HI"},
        )], Document)
        tables.write(out / "units.parquet", [Unit(
            id="hi:34000647", document_id="hi:dataset", crop=self.reference,
            unicode="U+5047", text_source="假", kind="char", method="import",
        )], Unit)
        base = tmp_path if repository_cache else root
        member = base / "cache/hilab/all/characters/U+5047/34000647.jpg"
        resolver = CropResolver(root, file_bases=[root, tmp_path])
        row = {"crop": self.reference}
        assert resolver.row_availability(row, "hilab")[0] is False
        assert resolver.for_unit("hi:34000647").render_available is False
        member.parent.mkdir(parents=True)
        original = jpeg(41, 73)
        member.write_bytes(original)
        assert resolver.row_availability(row, "hilab") == (True, None, "local_crop")
        result = resolver.for_unit("hi:34000647")
        assert result.render_available is True
        assert result.media_type == "image/jpeg"
        assert result.bytes_data == original
        assert result.crop_url == "/api/corpus/crop?unit_id=hi%3A34000647&w=256"
        assert resolver.row_availability(row, "kokatsuji")[0] is False

    def test_archive_member_cannot_escape_its_extraction_root(self, tmp_path):
        resolver = CropResolver(tmp_path, file_bases=[tmp_path])
        outside = tmp_path / "outside.jpg"
        outside.write_bytes(jpeg(20, 20))
        member = tmp_path / "cache/hilab/all/characters/U+5047/34000647.jpg"
        member.parent.mkdir(parents=True)
        member.symlink_to(outside)
        assert resolver.archive_member(self.reference) is None
        assert resolver.archive_member("all.zip!../../outside.jpg") is None
        assert resolver.archive_member("unregistered.zip!all/characters/U+5047/34000647.jpg") is None


class TestHNGCrops:
    """HNG crops: a GitHub URL, resolved against a local clone when there is one."""

    #: The real prefix computed from data/sources/hng-basic-data.yaml: mirror + pinned commit.
    prefix = _hng_crop_prefix()
    path = "01_誠實論卷八（P.2179）/glyphs/BMP/00350.bmp"
    crop_url = prefix + quote(path)

    @pytest.fixture(autouse=True)
    def isolated_cache(self, tmp_path, monkeypatch):
        """The default clone location must never see the repository's real cache."""
        monkeypatch.setenv("GLYPH_ATLAS_CACHE", str(tmp_path / "isolated-cache"))

    def test_prefix_is_read_from_the_source_file(self):
        assert self.prefix == (
            "https://raw.githubusercontent.com/chise/hng-basic-data/"
            "e2174a30844b8100c34af1c0dbe1e301f186883e/"
        )

    def test_a_clone_under_the_corpus_root_is_served_without_a_fetch(self, tmp_path):
        root = tmp_path / "work"
        out = root / "hng"
        out.mkdir(parents=True)
        tables.write(out / "documents.parquet", [Document(
            id="hng:jou", title="HNG source", holder="HNG",
            image_rights={"licence": "CC-BY-SA-4.0", "holder": "HNG", "attribution": "HNG"},
        )], Document)
        tables.write(out / "units.parquet", [Unit(
            id="hng:jou:00350", document_id="hng:jou", crop=self.crop_url,
            unicode="U+8AA0", text_source="誠", kind="char", method="import",
        )], Unit)
        resolver = CropResolver(root, file_bases=[root])
        row = {"crop": self.crop_url}
        # No clone yet: the resolver has nothing local, but the row is still
        # renderable through the mirror.
        assert resolver.row_availability(row, "hng") == (True, None, "remote_iiif")
        result = resolver.for_unit("hng:jou:00350")
        assert result.render_available is True
        assert result.mode == "remote_iiif"
        assert result.iiif_url == self.crop_url
        assert result.bytes_data is None

        clone_file = root / "hng-basic-data" / self.path
        clone_file.parent.mkdir(parents=True)
        original = bmp()
        clone_file.write_bytes(original)
        resolver._cache.clear()  # the remote-fallback answer above must not stick
        assert resolver.row_availability(row, "hng") == (True, None, "local_crop")
        result = resolver.for_unit("hng:jou:00350")
        assert result.render_available is True
        assert result.mode == "local_crop"
        assert result.media_type == "image/bmp"
        assert result.bytes_data == original
        assert result.crop_url == "/api/corpus/crop?unit_id=hng%3Ajou%3A00350&w=256"
        # A crop registered for HNG is not served under any other corpus name.
        assert resolver.row_availability(row, "kokatsuji")[0] is False

    def test_the_default_clone_location_is_the_cache_directory(self, tmp_path, monkeypatch):
        """`cache/hng-basic-data` (or $GLYPH_ATLAS_CACHE) is tried even off the corpus root."""
        monkeypatch.setenv("GLYPH_ATLAS_CACHE", str(tmp_path / "cache"))
        clone_file = tmp_path / "cache" / "hng-basic-data" / self.path
        clone_file.parent.mkdir(parents=True)
        original = bmp()
        clone_file.write_bytes(original)
        resolver = CropResolver(tmp_path / "elsewhere", file_bases=[tmp_path / "elsewhere"])
        found = resolver.archive_member(self.crop_url)
        assert found == clone_file
        assert found.read_bytes() == original

    def test_a_clone_path_cannot_escape_its_root(self, tmp_path):
        resolver = CropResolver(tmp_path, file_bases=[tmp_path])
        outside = tmp_path / "outside.bmp"
        outside.write_bytes(bmp(20, 20))
        member = tmp_path / "hng-basic-data" / self.path
        member.parent.mkdir(parents=True)
        member.symlink_to(outside)
        assert resolver.archive_member(self.crop_url) is None
        escape = self.prefix + quote("../../outside.bmp")
        assert resolver.archive_member(escape) is None

    def test_an_unrecognised_url_is_not_treated_as_a_crop_reference(self, tmp_path):
        resolver = CropResolver(tmp_path, file_bases=[tmp_path])
        assert resolver.archive_member("https://example.org/not-hng/foo.bmp") is None


class TestCropEndpoint:
    def test_a_registered_unit_serves_real_image_bytes(self, crop_api):
        api, _ = crop_api
        status, content_type, body = api.handle_get("/api/corpus/crop?unit_id=codh-omt%3A001%3AU1&w=64")
        assert status == 200
        assert content_type == "image/jpeg"
        assert body.startswith(b"\xff\xd8\xff")  # a real JPEG
        assert len(body) > 200

    def test_the_served_crop_is_the_requested_box(self, crop_api):
        """A 80x90 box must not come back as the whole 400x600 page."""
        from PIL import Image

        api, _ = crop_api
        _, _, small = api.handle_get("/api/corpus/crop?unit_id=codh-omt%3A001%3AU1&w=64")
        with Image.open(io.BytesIO(small)) as image:
            assert max(image.size) <= 64
            assert image.width / image.height > 0.5  # box is taller than wide

    @pytest.mark.parametrize(
        "unit_id,status",
        [
            ("codh-omt%3A001%3AU2", 404),  # no box and no crop
            ("codh-omt%3A001%3AU3", 404),  # archive member, not extracted
            ("nope%3A1", 403),  # unknown id
            ("..%2F..%2Fetc%2Fpasswd", 403),  # a path is not an id
            ("%2Fetc%2Fpasswd", 403),
            ("file%3A%2Fetc%2Fpasswd", 403),
        ],
    )
    def test_everything_that_is_not_a_registered_unit_is_refused(self, crop_api, unit_id, status):
        api, _ = crop_api
        got, _, body = api.handle_get(f"/api/corpus/crop?unit_id={unit_id}")
        assert got == status
        assert json.loads(body).get("reason") or json.loads(body).get("error")

    def test_a_missing_id_is_a_400_not_a_path_lookup(self, crop_api):
        api, _ = crop_api
        status, _, body = api.handle_get("/api/corpus/crop")
        assert status == 400
        assert "unit_id" in json.loads(body)["error"]


class TestLicenceGate:
    def test_a_non_proxyable_crop_is_not_served_by_this_api(self, tmp_path):
        root = tmp_path / "work"
        root.mkdir()
        build_crop_corpus(root, root / "kokatsuji" / "images", licence="CC-BY-NC-ND-4.0")
        directory = tmp_path / "index"
        build_chars(root, directory)
        api = CorpusAPI(root, directory, file_bases=[root])
        status, _, body = api.handle_get("/api/corpus/crop?unit_id=codh-omt%3A001%3AU1")
        assert status in (403, 404)
        payload = json.loads(body)
        assert payload["render_available"] is False
        assert "CC-BY-NC-ND-4.0" in (payload["reason"] or "")


class TestGlyphDescriptorHonesty:
    def test_render_available_is_true_only_with_verified_bytes(self, crop_api):
        api, _ = crop_api
        status, _, body = api.handle_get("/api/corpus/glyphs?char=%E4%B8%80&limit=5")
        payload = json.loads(body)
        assert status == 200
        served = [i for i in payload["items"] if i["thumbnail"].get("crop_url")]
        assert served, "a corpus with local bytes must offer a crop url"
        for item in served:
            assert item["render_available"] is True
            assert item["thumbnail"]["available"] is True

    def test_a_unit_without_bytes_is_not_advertised_as_renderable(self, crop_api):
        api, _ = crop_api
        # A page corpus cannot claim the standalone HI Lab archive.
        _, _, body = api.handle_get("/api/corpus/glyphs?char=%E4%B8%89&limit=5")
        for item in json.loads(body)["items"]:
            if item["unit_id"] == "codh-omt:001:U3":
                assert item["render_available"] is False
                assert item["thumbnail"]["available"] is False
                assert "not registered for this corpus" in (item["thumbnail"]["reason"] or "")

    def test_the_homepage_sample_never_claims_unavailable_bytes(self, crop_api):
        api, _ = crop_api
        _, _, body = api.handle_get("/api/corpus/glyphs?limit=10")
        for item in json.loads(body)["items"]:
            if item["thumbnail"]["available"]:
                assert item["thumbnail"].get("crop_url") or item["thumbnail"].get("iiif_url")


class TestCapabilityIsVerified:
    def test_capability_is_proven_by_resolving_a_real_unit(self, crop_api):
        api, _ = crop_api
        corpus = api._corpus("kokatsuji")
        verdict = api.crops.verify(corpus)
        assert verdict["mode"] == "local_crop"
        assert verdict["verified"] is True
        assert verdict["unit_id"]

    def test_an_unverifiable_corpus_reports_why(self, tmp_path):
        root = tmp_path / "work"
        root.mkdir()
        # A HI Lab shaped corpus: units carry archive members and no local bytes.
        out = root / "hilab"
        out.mkdir()
        doc = Document(
            id="hi:1",
            title="HI Lab",
            holder="HI",
            image_rights={"licence": "CC-BY-SA-4.0", "holder": "HI", "attribution": "HI"},
        )
        unit = Unit(
            id="hi:1",
            document_id="hi:1",
            page_id=None,
            crop="all.zip!all/characters/U+4E00/1.jpg",
            text_source="一",
            unicode="U+4E00",
            kind="char",
            method="import",
            active=True,
        )
        tables.write(out / "documents.parquet", [doc], Document)
        tables.write(out / "units.parquet", [unit], Unit)
        resolver = CropResolver(root, file_bases=[root])
        verdict = resolver.verify(
            __import__("glyph_atlas.corpus.sources", fromlist=["_describe"])._describe(out, None)
        )
        assert verdict["verified"] is False
        assert "not been extracted" in (verdict["reason"] or "")


def test_a_holder_page_held_in_the_image_cache_is_cropped_locally(tmp_path, monkeypatch):
    from glyph_atlas import images

    monkeypatch.setenv("GLYPH_ATLAS_CACHE", str(tmp_path / "cache"))
    service = "https://codh.rois.ac.jp/char-shape/iiif/a/b.tif"
    row = {"box": {"x": 10, "y": 20, "w": 30, "h": 40}, "image": service, "image_licence": "CC-BY-SA-4.0"}
    resolver = CropResolver(tmp_path)
    assert resolver.row_availability(row, "codh-full") == (True, None, "remote_iiif")
    scan = tmp_path / "scan.jpg"
    scan.write_bytes(jpeg())
    images.register(scan, service)
    # The same resolver sees a scan the cache gained after it started.
    assert resolver.row_availability(row, "codh-full") == (True, None, "local_crop")
    data, media_type, reason = resolver.local_page_crop(service, row["box"], corpus_name="codh-full")
    assert data and (media_type, reason) == ("image/jpeg", None)
    # A page named by a full-size request of the held service is the same scan.
    request = {**row, "image": service + "/full/max/0/default.jpg"}
    assert resolver.row_availability(request, "codh-full") == (True, None, "local_crop")
    assert resolver.page_image_path("https://codh.rois.ac.jp/char-shape/iiif/a/c.tif", "codh-full") is None


def test_a_held_page_the_licence_forbids_serving_stays_with_the_holder(tmp_path, monkeypatch):
    from glyph_atlas import images

    monkeypatch.setenv("GLYPH_ATLAS_CACHE", str(tmp_path / "cache"))
    service = "https://example.org/iiif/page"
    scan = tmp_path / "scan.jpg"
    scan.write_bytes(jpeg())
    images.register(scan, service)
    row = {"box": {"x": 10, "y": 20, "w": 30, "h": 40}, "image": service, "image_licence": "restricted"}
    assert CropResolver(tmp_path).row_availability(row, "ainu-records") == (True, None, "remote_iiif")


@pytest.fixture()
def hng_api(tmp_path, monkeypatch):
    """An HNG-shaped corpus, browsable through the gallery API like HI Lab's."""
    monkeypatch.setenv("GLYPH_ATLAS_CACHE", str(tmp_path / "isolated-cache"))
    root = tmp_path / "work"
    root.mkdir()
    out = root / "hng"
    out.mkdir()
    path = "01_誠實論卷八（P.2179）/glyphs/BMP/00350.bmp"
    crop_url = _hng_crop_prefix() + quote(path)
    tables.write(out / "documents.parquet", [Document(
        id="hng:jou", title="HNG source", holder="HNG",
        image_rights={"licence": "CC-BY-SA-4.0", "holder": "HNG", "attribution": "HNG"},
    )], Document, command="test")
    tables.write(out / "units.parquet", [Unit(
        id="hng:jou:00350", document_id="hng:jou", crop=crop_url,
        unicode="U+8AA0", text_source="誠", kind="char", method="import",
    )], Unit)
    (out / "MANIFEST.json").write_text(
        json.dumps(
            {
                "schema_version": tables.SCHEMA_VERSION,
                "tables": {"documents": 1, "units": 1},
                "files": {},
                "writer": "test",
                "command": "test",
            }
        ),
        encoding="utf-8",
    )
    clone_file = root / "hng-basic-data" / path
    clone_file.parent.mkdir(parents=True)
    clone_file.write_bytes(bmp())
    directory = tmp_path / "index"
    build_chars(root, directory)
    return CorpusAPI(root, directory, file_bases=[root]), root


class TestHNGGlyphsEndpoint:
    """HNG glyphs come back through the same gallery/search surface as HI Lab's."""

    def test_a_cloned_crop_is_advertised_as_renderable(self, hng_api):
        api, _ = hng_api
        status, _, body = api.handle_get(f"/api/corpus/glyphs?char={quote('誠')}&limit=5")
        assert status == 200
        payload = json.loads(body)
        items = [i for i in payload["items"] if i["unit_id"] == "hng:jou:00350"]
        assert items, "the HNG unit must be a candidate for its character"
        item = items[0]
        assert item["render_available"] is True
        assert item["render_capability"] == "local_crop"
        assert item["thumbnail"]["available"] is True
        assert item["thumbnail"].get("crop_url")
