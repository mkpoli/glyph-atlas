"""The character layer over HTTP: candidate search, exact search, ligatures, ink, and the one write.

The case the layer exists for is U+2A708 𪜈: a character whose Unicode name says nothing about being
ト and モ set as one. These tests hold the service to the things that make the layer usable —

* a reader who types トモ is offered 𪜈 as a candidate with its own counts, before any code point is
  typed, and choosing it does not widen anything: the occurrence gallery stays exact;
* a character with no occurrence in the corpus still answers its identity, its components and its
  reading, with the count reported as zero;
* located crops and transcription hits are never added into one number or drawn as one thing;
* a character correction and a reading correction are two events on two fields, so a phonetic reading
  never overwrites the encoded written identity.

No test reaches the network: the corpus service is the `http_server` fixture, which serves files.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qsl
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from glyph_atlas import refs, tables
from glyph_atlas.review import corpus_source
from glyph_atlas.review.characters import Layers
from glyph_atlas.review.server import create_app
from glyph_atlas.review.store import ReviewRequest, Store
from glyph_atlas.schema import Box, Document, Line, Page, Unit, VariantRef

PAGE = "hk:entry:9"
LINE = PAGE + ":L3"
#: The characters the fixture page was written with: the tomo ligature, the alternate NE twice, its
#: modern katakana, ね and the kanji 子, which is ね's 字母 and 𛄧's confusable and neither's form.
WRITTEN = ["U+2A708", "U+1B127", "U+1B127", "U+30CD", "U+306D", "U+5B50"]


@pytest.fixture(autouse=True)
def no_corpus():
    """Every test starts with no corpus index; a test that wants one injects a stand-in."""
    corpus_source.connect(None)
    yield
    corpus_source.connect(None)


class FakeCorpus:
    """The corpus API exactly as the layer calls it: `handle_get(path) -> (status, type, body)`."""

    def __init__(self, *, glyphs: int = 2, lines: int = 114, state_glyphs: bool = True,
                 built: bool = True) -> None:
        self.glyphs = glyphs
        self.lines = lines
        self.state_glyphs = state_glyphs
        self.asked: list[str] = []
        self.index = type("Index", (), {"exists": staticmethod(lambda: built)})()

    def handle_get(self, path: str) -> tuple[int, str, bytes]:
        self.asked.append(path)
        route, _, query = path.partition("?")
        params = dict(parse_qsl(query))
        if route == "/api/corpus/counts":
            wanted = [char for char in (params.get("chars") or "").split(",") if char]
            return self._json({"chars": [self._count_row(char) for char in wanted]})
        if route == "/api/corpus/glyphs":
            char = params.get("char")
            return self._json({"total": self.glyphs, "items": [
                _glyph_item(f"honkoku-lines:{i + 1}", char) for i in range(self.glyphs)]})
        if route == "/api/corpus/find":
            char = params.get("char")
            return self._json({"total": self.lines, "total_is_exact": True, "items": [
                _line_item("honkoku-lines:line-1", char)]})
        return 404, "application/json", b'{"error": "no such route"}'

    def _count_row(self, char: str) -> dict:
        row = {"char": char, "codepoint": "U+2A708", "known": True, "n_line_hits": self.lines,
               "n_page_hits": 0, "n_literal": self.lines + self.glyphs, "n_annotated": 0,
               "n_occurrences": self.lines + self.glyphs, "n_units": 0, "n_located": 1,
               "corpora": ["honkoku-lines"]}
        if self.state_glyphs:
            row["n_glyphs"] = self.glyphs
        return row

    @staticmethod
    def _json(payload: dict) -> tuple[int, str, bytes]:
        return 200, "application/json", json.dumps(payload, ensure_ascii=False).encode()


class BrokenCorpus(FakeCorpus):
    """A corpus index that exists and cannot answer: the fault a view must not read as zero."""

    def __init__(self, *, status: int = 500) -> None:
        super().__init__()
        self.status = status

    def handle_get(self, path: str) -> tuple[int, str, bytes]:
        if self.status == 503:
            return 503, "application/json", json.dumps({"error": "corpus index not built"}).encode()
        raise RuntimeError("UnboundLocalError: cannot access local variable 'row' where it is not associated with a value")


def _glyph_item(item_id: str, char: str | None) -> dict:
    """A located character unit from `/api/corpus/glyphs`."""
    # The flat shape `/api/corpus/glyphs` returns: identity_key, top-level title/holder, and the
    # image facts inside `thumbnail`.
    return {"identity_key": item_id, "char": char, "codepoint": "U+2A708", "corpus": "honkoku-lines",
            "document_id": "honkoku:1", "page_id": "honkoku:1:p3", "line_id": "honkoku:1:p3:l2",
            "title": "翻刻資料", "holder": "Holder", "shelfmark": None, "review_state": "machine",
            "thumbnail": {"available": True, "role": "glyph", "licence": "CC-BY-SA-4.0",
                          "proxyable": True, "requires_review": True,
                          "rect": {"x": 10, "y": 20, "w": 30, "h": 30, "role": "glyph"},
                          "iiif_url": "https://example.org/iiif/10,20,30,30/320,/0/default.jpg"}}


def _line_item(item_id: str, char: str | None) -> dict:
    """A text match from `/api/corpus/find`: the line has a rectangle and the character none."""
    return {"occurrence_id": item_id, "char": char, "codepoint": "U+2A708", "tier": "line",
            "text_raw": "トモ", "context": "…トモ…", "rects": [
                {"x": 0, "y": 0, "w": 400, "h": 600, "role": "line", "basis": "upstream_bbox",
                 "confirmed": False, "confidence": None, "method": "line"}],
            "review_state": "machine", "review": {"state": "machine", "human_validated": False},
            "source": {"corpus": "honkoku-lines", "document_id": "honkoku:2",
                       "line_id": "honkoku:2:p1:l9", "title": "翻刻資料 2", "holder": "Holder",
                       "canvas": "https://example.org/iiif/canvas/2"},
            "thumbnail": {"role": "line", "available": True, "has_line": True, "licence": "CC-BY-SA-4.0",
                          "requires_review": True, "rect": {"x": 0, "y": 0, "w": 400, "h": 600,
                                                            "role": "line"},
                          "iiif_url": "https://example.org/iiif/0,0,400,600/320,/0/default.jpg"}}


@pytest.fixture
def dataset(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GLYPH_ATLAS_CACHE", str(tmp_path / "cache"))
    root = tmp_path / "dataset"
    root.mkdir()
    cache = tmp_path / "cache"
    image = tmp_path / "scan.jpg"
    im = Image.new("RGB", (400, 600), "white")
    draw = ImageDraw.Draw(im)
    for i in range(len(WRITTEN)):
        x, y = 20 + i % 3 * 90, 20 + i // 3 * 130
        draw.line([(x + 5, y + 4), (x + 60, y + 95)], fill="black", width=5)
    im.save(image)
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    folder = cache / "images" / digest[:2]
    folder.mkdir(parents=True)
    (folder / (digest + ".jpg")).write_bytes(image.read_bytes())

    tables.write(root / "documents.parquet", [Document(id="d", title="明治期の一葉", holder="Fixture")], Document)
    tables.write(root / "pages.parquet", [Page(id=PAGE, document_id="d", seq=9,
                 image="https://example.org/scan.jpg", width=400, height=600, sha256=digest)], Page)
    tables.write(root / "lines.parquet", [Line(id=LINE, page_id=PAGE, seq=3, text_raw="𪜈", text="𪜈",
                 box=Box(x=0, y=0, w=400, h=600))], Line)
    units = [Unit(id=f"{LINE}:u{i}", document_id="d", page_id=PAGE, line_id=LINE, seq=i,
                  unicode=code_point, reading=refs.from_code_points(code_point.split()),
                  box=Box(x=20 + i % 3 * 90, y=20 + i // 3 * 130, w=65, h=100))
             for i, code_point in enumerate(WRITTEN)]
    tables.write(root / "units.parquet", units, Unit)
    return root


def client(dataset: Path, corpus: object | None = None) -> TestClient:
    """The app under test, with the corpus API injected rather than built from the environment."""
    return TestClient(create_app(dataset, corpus=corpus))


def digest_of(dataset: Path) -> str:
    """The fixture page's checksum, which a correction has to name."""
    return Store(dataset).page(PAGE).sha256


def test_a_base_plus_mark_identity_is_one_character_to_the_index(dataset):
    """ツ + U+309A is one character in two code points; the index keys the pair it stores."""
    store = Store(dataset)
    store.record(ReviewRequest(target_id=f"{LINE}:u0", field="unicode", new="U+30C4 U+309A",
                               client_id="reviewer-1"))
    layer = Layers(store)
    assert layer.per_character["U+30C4 U+309A"] == 1
    assert [unit.id for unit, *_ in layer.rows("U+30C4 U+309A")] == [f"{LINE}:u0"]


def test_a_space_written_as_a_character_is_indexed_by_its_code_point(dataset):
    """An ideographic space is a written unit too; the index keys it and keeps building."""
    store = Store(dataset)
    store.record(ReviewRequest(target_id=f"{LINE}:u0", field="unicode", new="U+3000",
                               client_id="reviewer-1"))
    layer = Layers(store)
    assert layer.per_character["U+3000"] == 1


def test_gallery_exposes_registered_crop_urls_and_source_attribution(dataset):
    corpus = FakeCorpus()
    corpus.handle_get = lambda _path: corpus._json({
        "total": 1, "grid_safe": True, "items": [{
            "id": "codh-omt:001:337", "char": "と", "corpus": "kokatsuji",
            "source": {"title": "Fixture source", "holder": "Fixture library"},
            "render_available": True,
            "thumbnail": {"available": True, "role": "glyph", "proxyable": True,
                          "crop_url": "/api/corpus/crop?unit_id=codh-omt%3A001%3A337", "iiif_url": None},
        }],
    })
    response = client(dataset, corpus).get("/layers/gallery")
    assert response.status_code == 200
    tile = response.json()["items"][0]
    assert tile["image"].startswith("/api/corpus/crop?unit_id=")
    assert tile["holder"] == "Fixture library" and tile["title"] == "Fixture source"


def test_gallery_passes_bounded_numeric_shuffle_seed(dataset, monkeypatch):
    calls = []
    def gallery(limit, *, seed):
        calls.append((limit, seed))
        return {"items": [], "total": 0}
    monkeypatch.setattr(corpus_source, "gallery", gallery)
    api = client(dataset)
    assert api.get("/layers/gallery", params={"limit": 17, "seed": 1299}).status_code == 200
    assert calls == [(17, 1299)]
    for seed in ["abc", -1, 2**53 + 1]:
        assert api.get("/layers/gallery", params={"seed": seed}).status_code == 422


# The candidate list ------------------------------------------------------------------------------

def test_suggest_offers_the_ligature_for_its_two_characters(dataset):
    """トモ is two code points and the printing is one: the row for 𪜈 comes first, with its counts."""
    api = client(dataset)
    answer = api.get("/layers/suggest", params={"q": "トモ"}).json()
    first = answer["items"][0]
    assert first["code_point"] == "U+2A708" and first["char"] == "𪜈"
    assert first["rank"] == 1 and "ligature" in first["reason"]
    assert first["reading"] == "トモ"
    assert first["kind"] == "ligature"
    assert [component["char"] for component in first["ligature"]["components"]] == ["ト", "モ"]
    assert first["occurrence_count"] == 1, "one 𪜈 is on the fixture page"
    assert first["candidates"]["known"] is False, "no corpus index is connected in this test"
    # A two-kana query names what it spells, so 𪜈 is the answer and ト and モ are not offered as if
    # they were the query: a ト followed by a モ is not this character.
    assert {row["code_point"] for row in answer["items"]} == {"U+2A708"}


def test_typing_does_not_rescan_occurrences(dataset, monkeypatch):
    api = client(dataset)

    def no_scan(*args, **kwargs):
        raise AssertionError("autocomplete walked the dataset after startup")

    monkeypatch.setattr(api.app.state.store, "unit_snapshot", no_scan)
    for query in ["トモ", "ネ", "トキ", "トテ", "ヰ", "トモ"]:
        response = api.get("/layers/suggest", params={"q": query})
        assert response.status_code == 200
        assert response.json()["items"]


def test_suggest_reaches_historical_forms_from_the_modern_kana(dataset):
    """ネ leads to the alternate NE, ヨリ to both yori ligatures, 子 to the kana written as it."""
    api = client(dataset)
    ne = api.get("/layers/suggest", params={"q": "ネ"}).json()["items"]
    assert ne[0]["code_point"] == "U+30CD"
    assert ne[1]["code_point"] == "U+1B127", "the alternate NE ranks next to the letter it alternates"
    assert "U+1B127" in {row["code_point"] for row in ne}, "the Meiji page prints 𛄧, not ネ"
    yori = {row["code_point"] for row in api.get("/layers/suggest", params={"q": "ヨリ"}).json()["items"]}
    assert {"U+309F", "U+1B126"} <= yori
    ko = {row["code_point"] for row in api.get("/layers/suggest", params={"q": "子"}).json()["items"]}
    assert {"U+5B50", "U+1B098", "U+1B127"} <= ko
    assert api.get("/layers/suggest", params={"q": ""}).json()["status"] == "idle"
    nothing = api.get("/layers/suggest", params={"q": "U+FFFF"}).json()
    assert nothing["items"] == [] and nothing["hint"]


def test_older_kana_are_not_labeled_as_unicode_18_alternates(dataset):
    api = client(dataset)
    candidates = api.get("/layers/suggest", params={"q": "エ", "limit": 48}).json()["items"]
    archaic_e = next(row for row in candidates if row["code_point"] == "U+1B000")
    assert archaic_e["age"] == "6.0"
    assert archaic_e["rank"] > 2
    assert "Unicode 18.0" not in archaic_e["reason"] and "alternate" not in archaic_e["reason"]
    ne = api.get("/layers/suggest", params={"q": "ネ"}).json()["items"]
    alternate = next(row for row in ne if row["code_point"] == "U+1B127")
    assert alternate["rank"] == 2
    assert alternate["reason"] == "the alternate ネ of Unicode 18.0"


def test_search_is_exact_and_the_expansions_are_choices(dataset):
    api = client(dataset)
    exact = api.get("/layers/search", params={"q": "𛄧"}).json()
    assert exact["total"] == 1
    keys = {option["key"]: option for option in exact["expansions"]}
    assert keys["grapheme"]["code_point"] == "U+306D" and keys["grapheme"]["enabled"] is False
    assert keys["grapheme"]["count"] == 9  # ね has ten forms and one of them is 𛄧

    widened = api.get("/layers/search", params={"q": "𛄧", "expand": "grapheme"}).json()
    found = {row["code_point"] for row in widened["results"]}
    assert {"U+1B127", "U+306D", "U+30CD", "U+1B098"} <= found
    assert "U+5B50" not in found, "子 is confusable with 𛄧, not a form of the same grapheme"
    assert all(row.get("reason") for row in widened["results"])
    assert widened["match"]["occurrences"]["expanded"] is True

    # The 字母 relation is the other expansion, and it is not a grapheme: 子 adds kana, not shapes.
    jibo = api.get("/layers/search", params={"q": "子", "expand": "jibo"}).json()
    assert {row["code_point"] for row in jibo["results"]} >= {"U+5B50", "U+1B098", "U+1B127"}
    assert api.get("/layers/search", params={"q": "子"}).json()["total"] == 1
    assert api.get("/layers/search", params={"q": "𪜈", "expand": "nonsense"}).status_code == 422


def test_search_for_a_character_answers_that_character_and_no_other(dataset):
    api = client(dataset)
    answer = api.get("/layers/search", params={"q": "𪜈"}).json()
    assert answer["terms"]["kind"] == "character"
    assert [row["code_point"] for row in answer["results"]] == ["U+2A708"]
    assert api.get("/layers/search", params={"q": "u+2a708"}).json()["match"]["code_point"] == "U+2A708"
    assert api.get("/layers/search", params={"q": "U+1B127"}).json()["match"]["code_point"] == "U+1B127"
    nothing = api.get("/layers/search", params={"q": "U+FFFF"}).json()
    assert nothing["match"] is None and nothing["results"] == [] and nothing["hint"]


# The layers themselves ---------------------------------------------------------------------------

def test_ligature_identity_is_answered_with_an_empty_occurrence_index(dataset):
    api = client(dataset)
    absent = api.get("/layers/characters/U+1B126").json()  # 𛄦, never printed in this corpus
    assert absent["occurrence_count"] == 0
    assert absent["occurrences"]["total"] == 0
    assert [component["char"] for component in absent["ligature"]["components"]] == ["ヨ", "リ"]
    assert absent["ligature"]["reading"] == "ヨリ"
    # The character table holds no reading for a digraph, whose name is not a kana name; the reading
    # the shape is read as comes from the ligature layer, and the row reports it as `reading`.
    assert absent["readings"] == [] and absent["reading"] == "ヨリ"
    assert absent["samples"] == [] and absent["script"] == "katakana"

    tomo = api.get("/layers/characters/U+2A708").json()
    assert [component["char"] for component in tomo["ligature"]["components"]] == ["ト", "モ"]
    assert tomo["ligature"]["reading"] == "トモ" and tomo["script"] == "han"
    assert "katakana" in tomo["ligature"]["kind"], "encoded as Han, used as a katakana ligature"
    assert tomo["grapheme"]["code_point"] == "U+2A708", "a ligature is not a form of its components"
    assert "U+30E2" not in [form["code_point"] for form in tomo["characters"]]
    assert api.get("/layers/characters/U+309F").json()["ligature"]["reading"] == "より"


def test_occurrences_are_the_ink_and_samples_name_their_form(dataset):
    api = client(dataset)
    card = api.get("/layers/characters/U+306D").json()
    assert card["occurrence_count"] == 1
    sample = card["samples"][0]
    assert sample["id"] == LINE + ":u4" and sample["exact"] is True
    assert sample["document_title"] == "明治期の一葉"
    assert "machine" in sample, "a view has to be able to say a record is a machine proposal"
    assert sample["method"] == "import" and sample["review"] == "machine"
    assert api.get(sample["image"]).status_code == 200
    assert api.get(sample["image"]).headers["content-type"] == "image/jpeg"

    widened = api.get("/layers/characters/U+306D", params={"expand": "grapheme"}).json()
    forms = {row["code_point"] for row in widened["samples"]}
    assert "U+1B127" in forms and "U+30CD" in forms
    paged = api.get("/layers/occurrences", params={"code_point": "U+1B127", "limit": 1}).json()
    assert paged["counts"]["total"] == 2 and len(paged["items"]) == 1
    assert paged["counts"]["by_character"] == [{"code_point": "U+1B127", "count": 2}]


def test_graphemes_and_ligatures_are_browsable(dataset):
    api = client(dataset)
    gallery = api.get("/layers/graphemes", params={"forms": "multiple", "group": "kana"}).json()
    rows = {row["code_point"]: row for row in gallery["items"]}
    assert "U+306D" in rows and rows["U+306D"]["character_count"] >= 10
    assert rows["U+306D"]["occurrence_count"] == 4  # ね, ネ and two 𛄧
    assert all(row["character_count"] > 1 for row in gallery["items"])
    assert "U+2A708" not in rows, "𪜈 is a Han-encoded ligature and is not a kana grapheme"
    one = api.get("/layers/graphemes/U+1B127").json()
    assert one["code_point"] == "U+306D" and one["char"] == "ね"
    assert {form["code_point"] for form in one["characters"]} >= {"U+306D", "U+30CD", "U+1B127"}

    ligatures = api.get("/layers/ligatures").json()
    by_code_point = {row["code_point"]: row for row in ligatures["items"]}
    assert set(by_code_point) == set(refs.ligatures())
    assert by_code_point["U+2A708"]["occurrence_count"] == 1
    assert by_code_point["U+2A708"]["reading"] == "トモ"
    assert [component["code_point"] for component in by_code_point["U+2A708"]["components"]] == ["U+30C8", "U+30E2"]


def test_summary_counts_the_layer_and_the_ink(dataset):
    """The summary answers from the tables; the ink is counted once the index is in hand."""
    api = client(dataset)
    cold = api.get("/layers/summary").json()
    assert cold["characters"] == len(refs.characters())
    assert cold["ligatures"] == len(refs.ligatures())
    assert cold["occurrences"]["total"] == len(WRITTEN), "startup prepares the occurrence index"
    assert cold["corpus"]["ready"] is False
    assert [layer["key"] for layer in cold["layers"]] == ["grapheme", "character", "occurrence"]

    api.get("/layers/characters/U+2A708")   # builds the occurrence index
    warm = api.get("/layers/summary").json()
    assert warm["occurrences"]["total"] == len(WRITTEN)
    assert warm["occurrences"]["characters_used"] == len(set(WRITTEN))


def test_historical_family_keeps_written_characters_readings_and_ink_distinct(dataset):
    units = list(tables.read(dataset / "units.parquet", Unit))
    for index, (char, reading) in enumerate([("仮", "か"), ("假", "かり"), ("假", "け")]):
        units.append(Unit(
            id=f"variant:{index}", document_id="d", page_id=PAGE, line_id=LINE,
            seq=10 + index, unicode=refs.to_code_point(char), reading=reading,
            box=Box(x=20 + index * 30, y=20, w=25, h=50),
            variants=[VariantRef(scheme="local", id=f"ink-{index}")],
        ))
    tables.write(dataset / "units.parquet", units, Unit)
    api = client(dataset)
    modern = api.get("/layers/characters/仮").json()
    old = api.get("/layers/characters/假").json()
    assert modern["code_point"] == "U+4EEE" and old["code_point"] == "U+5047"
    assert modern["occurrence_count"] == 1 and old["occurrence_count"] == 2
    assert {sample["reading"] for sample in old["samples"]} == {"かり", "け"}
    assert {sample["label"] for sample in old["samples"]} == {"假"}
    assert {sample["written_character"] for sample in old["samples"]} == {"假"}
    family = api.get("/layers/graphemes/假").json()
    assert family["code_point"] == "U+4EEE" and family["label"] == "仮 = 假"
    assert family["character_count"] == 2 and family["occurrence_count"] == 3
    assert family["relation"] == "shinjitai-kyujitai"
    assert family["evidence"][0]["url"].startswith("https://www.bunka.go.jp/")
    assert [(row["char"], row["occurrence_count"]) for row in family["characters"]] == [("仮", 1), ("假", 2)]
    assert api.get("/layers/graphemes/U+4EEE").json() == family
    grouped = api.get("/layers/occurrences", params={"code_point": "仮", "expand": "grapheme"}).json()
    assert grouped["counts"]["total"] == 3 and grouped["counts"]["exact_total"] == 1
    assert {row["code_point"] for row in grouped["items"]} == {"U+4EEE", "U+5047"}
    assert len({row["id"] for row in grouped["items"]}) == 3
    assert {row["variants"][0]["id"] for row in grouped["items"]} == {"ink-0", "ink-1", "ink-2"}
    assert {row["grapheme"]["code_point"] for row in grouped["items"]} == {"U+4EEE"}
    for query in ["仮", "假", "U+5047"]:
        rows = api.get("/layers/graphemes", params={"q": query, "group": "han"}).json()["items"]
        assert [row["code_point"] for row in rows] == ["U+4EEE"]
        suggestions = api.get("/layers/suggest", params={"q": query}).json()["items"]
        assert {row["char"] for row in suggestions} >= {"仮", "假"}
        for row in suggestions:
            if row["char"] in ("仮", "假"):
                assert row["grapheme"]["label"] == "仮 = 假"
    # Grouping never changes the stored source character, reading or crop.
    stored = Store(dataset).unit("variant:1")
    assert stored.unicode == "U+5047" and stored.reading == "かり" and stored.box.x == 50


def test_background_import_refreshes_existing_layer_counts_without_review_events(dataset, tmp_path):
    api = client(dataset)
    assert api.get("/layers/characters/假").json()["occurrence_count"] == 0
    additions = tmp_path / "additions"
    additions.mkdir()
    tables.write(additions / "units.parquet", [Unit(
        id="new:old-character", document_id="d", page_id=PAGE, line_id=LINE,
        unicode="U+5047", reading="かり", box=Box(x=20, y=20, w=25, h=50),
    )], Unit)
    publisher = Store(dataset)
    assert publisher.import_generation() == 0
    publisher.import_dataset(additions)
    assert publisher.import_generation() == 1 and not publisher.events()
    assert api.get("/layers/summary").json()["occurrences"]["total"] == len(WRITTEN) + 1
    assert api.get("/layers/characters/假").json()["occurrence_count"] == 1
    assert api.get("/layers/graphemes/仮").json()["occurrence_count"] == 1
    publisher.import_dataset(additions)
    assert publisher.import_generation() == 1


# The corpus service and the catalogue file -------------------------------------------------------

def test_corpus_counts_reach_the_candidate_row(dataset):
    """Typing トモ shows 𪜈 with 2 crops and 114 lines, and the two are never one number."""
    api = client(dataset, FakeCorpus(glyphs=2, lines=114))
    row = api.get("/layers/suggest", params={"q": "トモ"}).json()["items"][0]
    assert row["code_point"] == "U+2A708"
    assert row["candidates"]["glyphs"] == 2 and row["candidates"]["lines"] == 114
    assert row["candidates"]["total"] == 116
    assert row["candidates"]["known"] is True

    card = api.get("/layers/characters/U+2A708").json()
    assert card["candidates"]["glyphs"] == 2 and card["candidates"]["lines"] == 114
    assert card["occurrence_count"] == 1, "a corpus glyph is a lead, not an imported occurrence"

    candidates = api.get("/layers/candidates", params={"code_point": "U+2A708"}).json()
    # Glyphs are asked for as glyphs, so two crops survive a hundred line hits — and the text
    # occurrences that cost a corpus walk are not read at all on this path.
    assert len(candidates["glyph_items"]) == 2
    assert candidates["glyph_items"][0]["id"] == "honkoku-lines:1"
    assert candidates["glyph_items"][0]["image"].endswith("/320,/0/default.jpg")
    assert candidates["glyph_items"][0]["identity_key"] == "honkoku-lines:1"
    assert candidates["lines"] == 114, "the count comes from the cached summary, not from a scan"
    assert "line_items" not in candidates, "a text match is never fetched behind a tile"

    hits = api.get("/layers/candidates/hits", params={"code_point": "U+2A708"}).json()
    assert hits["total"] == 114 and len(hits["items"]) == 1, "text matches are paged and on request"
    line = hits["items"][0]
    assert line["role"] == "line" and line["located"] is False
    assert line["rect"]["role"] == "line", "the rectangle is the line's, not the character's"
    assert api.get("/layers/summary").json()["corpus"]["ready"] is True


def test_a_count_the_corpus_does_not_state_stays_unknown(dataset):
    """`n_located` and `n_units` are not glyph counts, so a corpus that states no `n_glyphs` says so."""
    api = client(dataset, FakeCorpus(glyphs=2, lines=114, state_glyphs=False))
    row = api.get("/layers/suggest", params={"q": "トモ"}).json()["items"][0]
    assert row["candidates"]["glyphs"] is None, "located rectangles are not glyphs"
    assert row["candidates"]["lines"] == 114
    assert row["candidates"]["total"] == 114, "only the numbers that are stated are added"
    assert row["code_point"] == "U+2A708", "the candidate is found without the corpus counts"


def test_a_corpus_fault_is_not_an_empty_corpus(dataset):
    """An index that cannot answer is a fault: the view is told so, and no zero is invented."""
    api = client(dataset, BrokenCorpus())
    listing = api.get("/layers/candidates", params={"code_point": "U+2A708"})
    assert listing.status_code == 502, listing.text
    assert "could not be read" in listing.json()["detail"]

    card = api.get("/layers/characters/U+2A708").json()
    assert card["candidates"]["status"] == "error"
    assert card["candidates"]["known"] is False
    assert card["candidates"]["total"] is None and card["candidates"]["glyphs"] is None
    assert card["occurrence_count"] == 1, "the layer still answers; only the corpus part is degraded"

    row = api.get("/layers/suggest", params={"q": "トモ"}).json()["items"][0]
    assert row["code_point"] == "U+2A708" and row["candidates"]["status"] == "error"
    assert row["candidates"]["glyphs"] is None, "a fault is never rendered as a count of zero"

    hits = api.get("/layers/candidates/hits", params={"code_point": "U+2A708"})
    assert hits.status_code == 502, hits.text

    # An index that is simply not built is the other word: nothing to read, and a retry is useful.
    cold = client(dataset, BrokenCorpus(status=503))
    assert cold.get("/layers/candidates", params={"code_point": "U+2A708"}).status_code == 200
    assert cold.get("/layers/characters/U+2A708").json()["candidates"]["status"] == "not-loaded"
    assert cold.get("/layers/candidates/hits",
                    params={"code_point": "U+2A708"}).json()["status"] == "not-loaded"


def test_a_corpus_api_without_a_built_index_is_not_ready(dataset):
    """Readiness is the index existing, not the API object existing: `ready` says which."""
    api = client(dataset, FakeCorpus(built=False))
    assert api.get("/layers/summary").json()["corpus"]["ready"] is False
    assert client(dataset, FakeCorpus(built=True)).get("/layers/summary").json()["corpus"]["ready"] is True


def test_the_real_corpus_package_answers_the_same_shape(dataset):
    """When the corpus package is installed, its own `CorpusAPI` is what the adapter reads.

    No index is built here; what is checked is the interface the adapter depends on — a
    `handle_get(path)` that answers `(status, content_type, body)` and a `index.exists()` — so a
    change to either is caught here rather than in a live deployment.
    """
    pytest.importorskip("glyph_atlas.corpus.api")
    from glyph_atlas.corpus.api import CorpusAPI

    api = CorpusAPI(dataset.parent, dataset.parent / "no-such-index")
    assert callable(getattr(api, "handle_get", None))
    status, content_type, body = api.handle_get("/api/corpus/counts?chars=%F0%AA%9C%88")
    assert status in (200, 503), f"unexpected status {status}"
    assert "json" in content_type
    assert json.loads(body.decode("utf-8")) is not None
    assert callable(getattr(api.index, "exists", None))


def test_no_corpus_index_still_answers_the_character_layer(dataset):
    """A deployment without the corpus package has no leads; it does not have zeroes pretending to be."""
    api = client(dataset)
    row = api.get("/layers/suggest", params={"q": "トモ"}).json()["items"][0]
    assert row["code_point"] == "U+2A708" and row["occurrence_count"] == 1
    assert row["candidates"]["known"] is False
    assert row["candidates"]["glyphs"] is None and row["candidates"]["total"] is None
    card = api.get("/layers/characters/U+2A708").json()
    assert card["candidates"]["imported"] == 1
    assert api.get("/layers/summary").json()["corpus"]["ready"] is False


# The one write -----------------------------------------------------------------------------------

def test_a_correction_keeps_the_character_and_the_reading_apart(dataset):
    """The layers are two events: a reading never rewrites the code point it was written with."""
    api = client(dataset)
    unit = api.get("/layers/characters/U+30CD").json()["samples"][0]
    payload = {"id": str(uuid4()), "client_id": "fixture-reviewer", "revision": unit["revision"],
               "image_sha256": digest_of(dataset), "character": "𪜈", "reading": "とも",
               "note": "the ligature, not ネ", "verdict": "wrong", "issue": "character"}

    answer = api.post(f"/layers/units/{unit['id']}", json=payload).json()
    assert answer["changed"] == ["character", "script", "reading"]
    assert answer["layers"]["code_point"] == "U+2A708"
    assert answer["layers"]["character"] == "𪜈"
    assert answer["layers"]["reading"] == "とも"
    assert answer["layers"]["jibo"] == []
    assert [row["field"] for row in answer["results"]] == ["unicode", "script", "reading", "review"]

    log = [event for event in Store(dataset).events() if event.target_id == unit["id"]]
    written = {event.field: json.loads(event.evidence)["layer"] for event in log if event.field != "review"}
    assert written == {"unicode": "character", "script": "script", "reading": "reading"}
    assert json.loads(next(e for e in log if e.field == "unicode").evidence)["from"] == "U+30CD"
    review = json.loads(next(e for e in log if e.field == "review").evidence)
    assert review["kind"] == "character-review", "the review export reads this kind"
    assert review["correction"]["reading"] == "とも"
    assert review["layer_correction"]["changed"] == ["character", "script", "reading"]

    stored = next(row for row, _ in Store(dataset).unit_snapshot(unit["id"]) if row.active)
    assert stored.unicode == "U+2A708", "the encoded written identity is stored on the unit"
    assert stored.reading == "とも"
    # 𪜈 is encoded as a Han ideograph, so the script label follows the layer to `han` rather than
    # to the katakana the shape is used as: the layer states the encoding, the ligature states the use.
    assert str(stored.script) == "han"
    assert api.get("/layers/characters/U+2A708").json()["occurrence_count"] == 2
    assert api.get("/layers/characters/U+30CD").json()["occurrence_count"] == 0

    # A reading-only correction leaves the identity exactly where it was.
    again = api.get("/layers/characters/U+2A708").json()["samples"][0]
    second = api.post(f"/layers/units/{again['id']}", json={
        **payload, "id": str(uuid4()), "revision": again["revision"], "character": None,
        "reading": "とも", "issue": "reading", "client_id": "second-reviewer"}).json()
    assert second["changed"] == ["reading"]
    assert second["layers"]["code_point"] == "U+2A708"
    stored = next(row for row, _ in Store(dataset).unit_snapshot(again["id"]) if row.active)
    assert stored.unicode == "U+2A708" and stored.reading == "とも"


def test_a_correction_records_the_issues_the_reviewer_offers(dataset):
    """`merged` is an issue the picker offers; the layer records it rather than refusing it."""
    api = client(dataset)
    unit = api.get("/layers/characters/U+5B50").json()["samples"][0]
    answer = api.post(f"/layers/units/{unit['id']}", json={
        "id": str(uuid4()), "client_id": "fixture-reviewer", "revision": unit["revision"],
        "image_sha256": digest_of(dataset), "character": "U+2A708", "reading": None,
        "verdict": "wrong", "issue": "merged", "note": "two characters in one crop"}).json()
    assert answer["changed"] == ["character", "script"]
    review = json.loads(next(e for e in Store(dataset).events()
                             if e.target_id == unit["id"] and e.field == "review").evidence)
    assert review["issue"] == "merged" and review["layer_correction"]["changed"] == ["character", "script"]


def test_a_retried_correction_answers_the_first_result(dataset):
    """A retry after a lost response is the same request: same answer, no second event, no 409.

    The body names the revision it was written against, so a retry sent after the occurrence has
    moved on would fail a stale-revision check even though nothing about it is wrong. The first
    request's answer is the answer; a *different* body under the same id is a client bug.
    """
    api = client(dataset)
    unit = api.get("/layers/characters/U+30CD").json()["samples"][0]
    payload = {"id": str(uuid4()), "client_id": "fixture-reviewer", "revision": unit["revision"],
               "image_sha256": digest_of(dataset), "character": "𪜈", "reading": "とも",
               "verdict": "wrong", "issue": "character", "note": "retry me"}

    first = api.post(f"/layers/units/{unit['id']}", json=payload)
    assert first.status_code == 200, first.text
    events = len(Store(dataset).events())
    assert first.json()["changed"] == ["character", "script", "reading"]

    # The identical body, sent again after the occurrence has already changed: the same answer.
    retry = api.post(f"/layers/units/{unit['id']}", json=payload)
    assert retry.status_code == 200, retry.text
    assert retry.json()["duplicate"] is True
    assert retry.json()["changed"] == ["character", "script", "reading"]
    assert retry.json()["layers"]["code_point"] == "U+2A708"
    assert [row["id"] for row in retry.json()["results"]] == [row["id"] for row in first.json()["results"]]
    assert len(Store(dataset).events()) == events, "a retry writes nothing"

    # A later, genuine edit moves the occurrence on; the retry is still the first answer.
    stored = next(row for row, _ in Store(dataset).unit_snapshot(unit["id"]) if row.active)
    assert stored.unicode == "U+2A708"
    again = api.get("/layers/characters/U+2A708").json()["samples"]
    assert api.post(f"/layers/units/{unit['id']}", json={**payload, "id": str(uuid4()),
                    "revision": next(row["revision"] for row in again if row["id"] == unit["id"]),
                    "reading": "トモ", "issue": "reading"}).status_code == 200
    later = api.post(f"/layers/units/{unit['id']}", json=payload)
    assert later.status_code == 200 and later.json()["duplicate"] is True
    assert len(Store(dataset).events()) == events + 2, "the retry added no event of its own"

    # The same id with a different body is refused as a client error, not answered as a duplicate.
    changed = api.post(f"/layers/units/{unit['id']}", json={**payload, "character": "U+5B50"})
    assert changed.status_code == 400, changed.text
    assert "submission id" in changed.json()["detail"]


def test_a_correction_refuses_a_second_character_and_a_stale_revision(dataset):
    api = client(dataset)
    unit = api.get("/layers/characters/U+306D").json()["samples"][0]
    base = {"id": str(uuid4()), "client_id": "fixture-reviewer", "revision": unit["revision"],
            "image_sha256": digest_of(dataset), "reading": None, "verdict": "wrong",
            "issue": "character", "note": ""}
    assert api.post(f"/layers/units/{unit['id']}", json={**base, "character": "トモ"}).status_code == 422
    assert api.post(f"/layers/units/{unit['id']}", json={**base, "character": "U+306D"}).status_code == 422
    # A reading is one character, unless the character is a ligature and the reading is the one the
    # layer states for it: 𪜈 reads トモ, and 子 does not read とも.
    plain = api.get("/layers/characters/U+5B50").json()["samples"][0]
    two_kana = {**base, "id": str(uuid4()), "revision": plain["revision"], "character": None,
                "reading": "とも"}
    assert api.post(f"/layers/units/{plain['id']}", json=two_kana).status_code == 422
    ligature = api.get("/layers/characters/U+2A708").json()["samples"][0]
    allowed = {**base, "id": str(uuid4()), "revision": ligature["revision"], "character": None,
               "reading": "とも", "issue": "reading"}
    assert api.post(f"/layers/units/{ligature['id']}", json=allowed).status_code == 200
    assert api.post(f"/layers/units/{unit['id']}", json={**base, "revision": 99,
                    "character": "ね"}).status_code == 409
    assert api.post(f"/layers/units/{unit['id']}", json={**base, "image_sha256": "0" * 64,
                    "character": "ね"}).status_code == 409
    assert api.get("/layers/characters/U+FFFF").status_code == 404
    assert api.get("/layers/occurrences", params={"code_point": "U+FFFF"}).status_code == 404


def test_layer_rebuild_reuses_page_lookups_and_refreshes_missing_images(dataset, monkeypatch):
    from glyph_atlas.review.characters import Layers
    from glyph_atlas.review.server import cached_image

    store = Store(dataset)
    page = store.page(PAGE)
    image = cached_image(page.sha256)
    original = store.page
    calls = []

    def page_lookup(page_id):
        calls.append(page_id)
        return original(page_id)

    monkeypatch.setattr(store, "page", page_lookup)
    first = Layers(store)
    assert sum(first.per_character.values()) == len(WRITTEN)
    assert calls == [PAGE], "one scan needs one page lookup, even during startup"

    image.unlink()
    calls.clear()
    second = Layers(store)
    assert second.per_character == {}, "a new rebuild must see removed cached images"
    assert calls == [PAGE]


def test_layer_fallback_checks_image_index_once_per_rebuild(dataset, monkeypatch):
    from types import SimpleNamespace

    from glyph_atlas import images
    from glyph_atlas.review import characters

    store = Store(dataset)
    page = store.page(PAGE)
    monkeypatch.setattr(store, "page", lambda page_id: page.model_copy(update={"sha256": None}))
    monkeypatch.setattr(characters, "_url_index", lambda stamp: {
        page.image: SimpleNamespace(sha256=page.sha256),
    })
    index = images.images_root() / "index.parquet"
    original = Path.stat
    stats = []

    def stat(path, *args, **kwargs):
        if path == index:
            stats.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)
    layer = characters.Layers(store)
    assert sum(layer.per_character.values()) == len(WRITTEN)
    assert stats == [index], "missing index lookups are also shared within a rebuild"


def test_http_image_lookups_are_shared_only_for_one_request(dataset, monkeypatch):
    from glyph_atlas.review.server import cached_image

    api = client(dataset)
    digest = digest_of(dataset)
    image = cached_image(digest)
    directory = image.parent
    original = Path.is_dir
    probes = []

    def is_dir(path):
        if path == directory:
            probes.append(path)
        return original(path)

    @api.app.get("/test/image-lookups")
    def image_lookups():
        return {"present": [cached_image(digest) is not None for _ in range(100)]}

    api.app.router.routes.insert(0, api.app.router.routes.pop())
    monkeypatch.setattr(Path, "is_dir", is_dir)
    assert all(api.get("/test/image-lookups").json()["present"])
    assert probes == [directory]
    image.unlink()
    assert not any(api.get("/test/image-lookups").json()["present"])
    assert probes == [directory, directory], "the next HTTP request performs fresh filesystem checks"


def test_normalized_family_candidates_use_scoped_identity_counts(dataset, monkeypatch):
    calls = []

    def candidates(char, limit, offset, *, scope, visual_group):
        calls.append((char, limit, offset, scope, visual_group))
        return {"total": 1, "family_total": 12, "assigned_count": 3, "unassigned_count": 9,
                "items": [{"id": "codh:form", "source_label": "仮", "char": "仮",
                           "written_character": None, "identity_status": "unassigned"}]}

    monkeypatch.setattr(corpus_source, "candidates", candidates)
    api = client(dataset, FakeCorpus(glyphs=999))
    card = api.get("/layers/characters/U+5047").json()
    assert card["default_scope"] == "grapheme"
    assert api.get("/layers/characters/U+30CD").json()["default_scope"] == "character"
    reply = api.get("/layers/candidates", params={"code_point": "U+5047", "scope": "grapheme",
                    "visual_group": "shape:one", "limit": 20, "offset": 10}).json()
    assert calls == [("假", 20, 10, "grapheme", "shape:one")]
    assert reply["glyphs"] == reply["total"] == 1 and reply["family_total"] == 12
    assert reply["unassigned_count"] == 9 and reply["assigned_count"] == 3
    assert reply["glyph_items"][0]["written_character"] is None
    assert reply["glyph_items"][0]["source_label"] == "仮"
    assert api.get("/layers/candidates", params={"code_point": "U+5047", "scope": "reading"}).status_code == 422


def test_visual_group_preview_serves_only_registry_samples(dataset, tmp_path, monkeypatch):
    from glyph_atlas import visual_families

    image = tmp_path / "sample.png"
    Image.new("RGB", (10, 10), "white").save(image)
    monkeypatch.setattr(visual_families, "get_sample_image", lambda identity: image if identity == "codh:sample" else None)
    api = client(dataset)
    response = api.get("/layers/visual-groups/samples/codh:sample/image")
    assert response.status_code == 200 and response.headers["content-type"] == "image/png"
    assert response.content == image.read_bytes()
    assert api.get("/layers/visual-groups/samples/unknown/image").status_code == 404


def test_normalized_kana_family_defaults_to_overview_with_family_count(dataset):
    corpus = FakeCorpus(glyphs=3)
    count_row = corpus._count_row
    corpus._count_row = lambda char: {**count_row(char), "requires_family_scope": True,
                                      "counts_kind": "source_transcription_classes", "n_family_glyphs": 17}
    card = client(dataset, corpus).get("/layers/characters/U+30CD").json()
    assert card["default_scope"] == "grapheme"
    assert card["candidates"]["family_glyphs"] == 17
    assert card["candidates"]["source_glyphs"] == 3
    assert card["candidates"]["requires_family_scope"] is True
