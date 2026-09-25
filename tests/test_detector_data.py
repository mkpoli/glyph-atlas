"""Tests for `scripts/build_detector_data.py`.

The synthetic page is 1500x1200 pixels with four source boxes: one inside a single tile, one across
the tile border at x=896, one unreadable and one that pokes 30% of its area into the tile next door.
The tables, the split file and the page image are built under `tmp_path`, in a cache directory that
starts empty, so nothing here reads the repository's own cache or reaches the network.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pytest
from PIL import Image

from glyph_atlas import images, tables
from glyph_atlas.schema import Box, Document, Page, Unit

ROOT = Path(__file__).resolve().parents[1]
TILE = 1024
STRIDE = 896

_spec = importlib.util.spec_from_file_location(
    "build_detector_data", ROOT / "scripts" / "build_detector_data.py"
)
assert _spec is not None and _spec.loader is not None
build_detector_data = importlib.util.module_from_spec(_spec)
sys.modules["build_detector_data"] = build_detector_data
_spec.loader.exec_module(build_detector_data)

# The 1500x1200 page covers four tiles: x at 0 and 896, y at 0 and 896.
WIDTH, HEIGHT = 1500, 1200
BORDER = STRIDE
BOXES = (
    ("char", Box(x=100, y=100, w=80, h=80)),  # tile 0,0 only
    ("char", Box(x=860, y=300, w=100, h=100)),  # tile 0,0 whole, tile 896,0 by 64 of 100 px
    ("unreadable", Box(x=1300, y=200, w=60, h=60)),  # tile 896,0 only
    ("char", Box(x=1000, y=600, w=80, h=80)),  # a 30% sliver in tile 0,0, whole in tile 896,0
)
BID = "b1"
PAGE = f"codh:{BID}:{BID}_0001"
IMAGE = f"https://example.org/iiif/{BID}/{BID}_0001.tif"
UNIT_IDS = [f"{PAGE}:C{index:04d}" for index in range(1, len(BOXES) + 1)]


# --- fixtures ----------------------------------------------------------------------------------


def write_splits(path: Path, books: dict[str, tuple[str, str]]) -> None:
    """Write a split table: `bid` to (production, split)."""
    lines = ["# synthetic split", "bid\ttitle\tproduction\tsplit"]
    lines += [f"{bid}\t{bid}\t{production}\t{split}" for bid, (production, split) in books.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_tables(
    root: Path,
    *,
    books: dict[str, tuple[str, str]],
    pages: list[Page],
    boxes: dict[str, tuple[tuple[str, Box], ...]],
    width: int = WIDTH,
    height: int = HEIGHT,
) -> None:
    """Build a dataset directory: tables, split file and one staged page image per page."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "images").mkdir(exist_ok=True)
    (root / "cache").mkdir(exist_ok=True)
    documents = [
        Document(id=page.document_id, title=f"book {page.document_id}", production="printed/woodblock")
        for page in pages
    ]
    units = [
        Unit(
            id=f"{page.id}:C{index:04d}",
            document_id=page.document_id,
            page_id=page.id,
            seq=index,
            box=box,
            kind=kind,
        )
        for page in pages
        for index, (kind, box) in enumerate(boxes.get(page.id, ()), start=1)
    ]
    tables.write(root / "documents.parquet", documents, Document)
    tables.write(root / "pages.parquet", pages, Page)
    tables.write(root / "units.parquet", units, Unit)
    write_splits(root / "splits.tsv", books)
    for page in pages:
        stage(root, page.image, width=width, height=height)


def stage(root: Path, url: str, *, width: int = WIDTH, height: int = HEIGHT) -> Path:
    """A page image of the given size, grey with dark marks, staged under `<root>/images`."""
    image = Image.new("RGB", (width, height), (200, 200, 200))
    for x in range(0, width, 64):
        for y in range(0, height, 64):
            image.paste((40, 40, 40), (x + 8, y + 8, x + 56, y + 56))
    target = root / "images" / f"{Path(url.split('?')[0]).stem}.jpg"
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, format="JPEG", quality=95)
    return target


def page_of(bid: str, *, seq: int = 1, width: int = WIDTH, height: int = HEIGHT) -> Page:
    return Page(
        id=f"codh:{bid}:{bid}_{seq:04d}",
        document_id=f"codh:{bid}",
        seq=seq,
        image=f"https://example.org/iiif/{bid}/{bid}_{seq:04d}.tif",
        width=width,
        height=height,
    )


def run(root: Path, out: Path, *extra: str) -> int:
    """The command line over one dataset, with a cache directory of the dataset's own."""
    return build_detector_data.main(
        [
            "--dataset",
            str(root),
            "--splits",
            str(root / "splits.tsv"),
            "--out",
            str(out),
            "--image-cache",
            str(root / "cache"),
            *extra,
        ]
    )


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def by_origin(document: dict) -> dict[tuple[int, int], dict]:
    return {tuple(entry["origin"]): entry for entry in document["images"]}


def annotations_of(document: dict, entry: dict) -> dict[str, dict]:
    return {
        annotation["source_unit_id"]: annotation
        for annotation in document["annotations"]
        if annotation["image_id"] == entry["id"]
    }


def check_coco(document: dict) -> None:
    """The shape `models/detector/train.py` reads: ids, sizes, boxes inside their tile."""
    assert document["categories"] == [{"id": 1, "name": "character", "supercategory": "unit"}]
    assert [entry["id"] for entry in document["images"]] == list(range(1, len(document["images"]) + 1))
    ids = {entry["id"] for entry in document["images"]}
    for entry in document["images"]:
        assert entry["tile_size"] == TILE and entry["width"] == entry["height"] == TILE
        assert len(entry["origin"]) == 2 and all(isinstance(value, int) for value in entry["origin"])
        assert entry["page_id"] and entry["page_url"] and entry["page_path"] and entry["bid"]
        assert entry["document_id"].startswith("codh:")
        assert entry["split"] in ("train", "val", "test")
    for annotation in document["annotations"]:
        assert annotation["image_id"] in ids
        assert annotation["category_id"] == 1
        assert annotation["source_unit_id"]
        assert annotation["iscrowd"] in (0, 1)
        x, y, w, h = annotation["bbox"]
        assert x >= 0 and y >= 0 and w > 0 and h > 0 and x + w <= TILE and y + h <= TILE
        assert annotation["area"] == w * h


@pytest.fixture
def synthetic(tmp_path: Path) -> Path:
    """A dataset of one book, one page and the four boxes."""
    root = tmp_path / "codh-full"
    write_tables(
        root,
        books={BID: ("printed", "train")},
        pages=[page_of(BID)],
        boxes={PAGE: BOXES},
    )
    return root


# --- tiling ------------------------------------------------------------------------------------


def test_tiles_of_a_synthetic_page(synthetic: Path, tmp_path: Path) -> None:
    """A 1500x1200 page covers four tiles; only the two that hold a box are written."""
    out = tmp_path / "detector"
    assert run(synthetic, out) == 0
    document = read(out / "train.json")
    check_coco(document)

    tiles = by_origin(document)
    # The bottom row of tiles holds no box at all, and a split of two annotated tiles may keep none.
    assert set(tiles) == {(0, 0), (BORDER, 0)}
    assert all(tiles[origin]["origin"] == list(origin) for origin in tiles)
    assert len(document["annotations"]) == 5

    inside = annotations_of(document, tiles[(0, 0)])
    assert set(inside) == {UNIT_IDS[0], UNIT_IDS[1]}
    assert inside[UNIT_IDS[0]]["bbox"] == [100, 100, 80, 80]
    assert inside[UNIT_IDS[0]]["coverage"] == 1.0 and inside[UNIT_IDS[0]]["clipped"] is False
    assert inside[UNIT_IDS[1]]["bbox"] == [860, 300, 100, 100]

    # The box across the border is whole in the first tile and cut to 64 of 100 px in the second.
    across = annotations_of(document, tiles[(BORDER, 0)])
    assert set(across) == {UNIT_IDS[1], UNIT_IDS[2], UNIT_IDS[3]}
    assert across[UNIT_IDS[1]]["bbox"] == [0, 300, 64, 100]
    assert across[UNIT_IDS[1]]["coverage"] == 0.64 and across[UNIT_IDS[1]]["clipped"] is True
    assert across[UNIT_IDS[3]]["bbox"] == [1000 - BORDER, 600, 80, 80]
    assert UNIT_IDS[3] not in inside  # 24 of 80 px in the first tile is 30% of the box

    unreadable = across[UNIT_IDS[2]]
    assert unreadable["iscrowd"] == 1 and unreadable["kind"] == "unreadable"
    assert unreadable["bbox"] == [1300 - BORDER, 200, 60, 60]
    assert all(annotation["iscrowd"] == 0 for annotation in document["annotations"] if annotation is not unreadable)

    stats = (out / "stats.md").read_text(encoding="utf-8")
    assert "Source boxes: 4; dropped boxes: 0; unique boxes: 4; clipped parts under 40% dropped: 1." in stats
    assert "| train | 1 | 2 | 0 | 5 | 1 | 4 | 0 | 1 |" in stats


def test_clipping_rule_keeps_forty_percent() -> None:
    """A part that keeps exactly 40% of its box is an annotation; 39% is not."""
    kept = build_detector_data.place_box(Box(x=984, y=100, w=100, h=100), WIDTH, HEIGHT)
    tiles = {placement.tile: placement for placement in kept}
    assert set(tiles) == {build_detector_data.Tile(0, 0), build_detector_data.Tile(BORDER, 0)}
    part = tiles[build_detector_data.Tile(0, 0)]
    assert part.box == Box(x=984, y=100, w=40, h=100)
    assert part.coverage == 0.4 and part.clipped is True

    dropped = build_detector_data.place_box(Box(x=985, y=100, w=100, h=100), WIDTH, HEIGHT)
    assert {placement.tile for placement in dropped} == {build_detector_data.Tile(BORDER, 0)}
    assert dropped[0].coverage == 1.0 and dropped[0].clipped is False


def test_box_larger_than_a_tile() -> None:
    """A box bigger than a tile is kept once, in the tile that holds its centre."""
    whole = build_detector_data.place_box(Box(x=0, y=0, w=WIDTH, h=HEIGHT), WIDTH, HEIGHT)
    assert len(whole) == 1
    assert whole[0].tile == build_detector_data.Tile(0, 0)
    assert whole[0].box == Box(x=0, y=0, w=TILE, h=TILE)
    assert whole[0].clipped is True

    wide = build_detector_data.place_box(Box(x=200, y=500, w=1200, h=60), WIDTH, HEIGHT)
    assert [placement.tile for placement in wide] == [build_detector_data.Tile(0, 0)]
    assert wide[0].box == Box(x=200, y=500, w=824, h=60)
    assert wide[0].coverage == 0.6867

    # A box one tile square is not larger than a tile: the parts next door are under 40%.
    square = build_detector_data.place_box(Box(x=0, y=0, w=TILE, h=TILE), WIDTH, HEIGHT)
    assert [placement.tile for placement in square] == [build_detector_data.Tile(0, 0)]


def test_tile_origins_cover_the_page() -> None:
    assert build_detector_data.tile_origins(1500) == [0, STRIDE]
    assert build_detector_data.tile_origins(1200) == [0, STRIDE]
    assert build_detector_data.tile_origins(1024) == [0]
    assert build_detector_data.tile_origins(600) == [0]
    assert build_detector_data.tile_origins(3000) == [0, STRIDE, 1792, 2688]
    assert build_detector_data.tile_origins(3000)[-1] + TILE >= 3000


# --- the empty tile cap ------------------------------------------------------------------------


def test_empty_tile_limit_is_the_largest_share_under_five_percent() -> None:
    limit = build_detector_data.empty_tile_limit
    assert limit(100) == 5
    assert limit(19) == 1
    assert limit(18) == 0
    assert limit(0) == 0
    for annotated in range(400):
        keep = limit(annotated)
        if keep:
            assert keep / (annotated + keep) <= 0.05
        assert (keep + 1) / (annotated + keep + 1) > 0.05


def test_empty_tiles_are_kept_at_most_five_percent(tmp_path: Path) -> None:
    """A 1500x9000 page has twenty tiles; nineteen boxes leave one empty tile, which is kept."""
    for count, expected in ((19, 20), (18, 18)):
        root = tmp_path / f"page{count}"
        page = page_of(BID, width=1500, height=9000)
        tiles = build_detector_data.tile_grid(page.width, page.height)
        assert len(tiles) == 20
        boxes = tuple(
            ("char", Box(x=tile.x + 492, y=tile.y + 492, w=40, h=40)) for tile in tiles[:count]
        )
        write_tables(root, books={BID: ("printed", "train")}, pages=[page], boxes={page.id: boxes}, width=1500, height=9000)
        out = tmp_path / f"out{count}"
        assert run(root, out) == 0
        document = read(out / "train.json")
        check_coco(document)
        assert len(document["images"]) == expected
        assert len(document["annotations"]) == count
        with_annotations = {annotation["image_id"] for annotation in document["annotations"]}
        empty = [entry for entry in document["images"] if entry["id"] not in with_annotations]
        assert len(empty) == expected - count
        if empty:
            # The one kept empty tile is the first of the empty ones in page order.
            assert empty[0]["origin"] == [STRIDE, 896 * 9]


def test_spread_indices_are_even_and_in_order() -> None:
    assert build_detector_data.spread_indices(1, 1) == [0]
    assert build_detector_data.spread_indices(100, 5) == [0, 20, 40, 60, 80]
    assert build_detector_data.spread_indices(10, 3) == [0, 3, 6]
    assert build_detector_data.spread_indices(4, 9) == [0, 1, 2, 3]
    assert build_detector_data.spread_indices(0, 3) == []


# --- the split ---------------------------------------------------------------------------------


def test_committed_split_is_fixed_by_sha1_order() -> None:
    """`data/splits/codh.tsv`: 44 books, four in test and two in val, chosen by sha1(bid)."""
    books = build_detector_data.read_splits(ROOT / "data" / "splits" / "codh.tsv")
    assert len(books) == 44
    assert Counter(book.split for book in books.values()) == {"train": 38, "val": 2, "test": 4}
    assert {book.production for bid, book in books.items() if book.split == "test"} == {
        "printed",
        "handwritten",
        "unknown",
    }
    strata: dict[str, list[str]] = defaultdict(list)
    for book in books.values():
        strata[book.production].append(book.bid)
    rank = {"test": 0, "val": 1, "train": 2}
    for bids in strata.values():
        order = sorted(bids, key=lambda bid: hashlib.sha1(bid.encode()).hexdigest())
        splits = [books[bid].split for bid in order]
        assert splits == sorted(splits, key=rank.__getitem__)


def test_split_is_by_book_and_the_output_is_reproducible(tmp_path: Path) -> None:
    """Every tile of a book lands in that book's split, and two runs write the same bytes."""
    root = tmp_path / "codh-full"
    books = {"b1": ("printed", "train"), "b2": ("printed", "val"), "b3": ("handwritten", "test")}
    pages = [page_of(bid) for bid in books]
    boxes = {page.id: (("char", Box(x=100 + 10 * seq, y=100, w=60, h=60)),) for seq, page in enumerate(pages)}
    write_tables(root, books=books, pages=pages, boxes=boxes)

    first, second = tmp_path / "one", tmp_path / "two"
    assert run(root, first) == 0
    assert run(root, second) == 0
    for name in ("train", "val", "test"):
        assert (first / f"{name}.json").read_bytes() == (second / f"{name}.json").read_bytes()
    one = (first / "stats.md").read_text(encoding="utf-8").replace(str(first), "OUT")
    two = (second / "stats.md").read_text(encoding="utf-8").replace(str(second), "OUT")
    assert one == two

    for bid, (_, split) in books.items():
        document = read(first / f"{split}.json")
        check_coco(document)
        others = [read(first / f"{name}.json") for name in ("train", "val", "test") if name != split]
        assert {entry["bid"] for entry in document["images"]} == {bid}
        assert all(entry["split"] == split for entry in document["images"])
        assert not [entry for other in others for entry in other["images"] if entry["bid"] == bid]


# --- the tiles on disk -------------------------------------------------------------------------


def test_materialise_writes_tiles_and_prunes_stale_ones(synthetic: Path, tmp_path: Path) -> None:
    out = tmp_path / "detector"
    (out / "tiles").mkdir(parents=True)
    stale = out / "tiles" / "left-over.jpg"
    stale.write_bytes(b"not a tile")

    assert run(synthetic, out, "--materialise") == 0
    document = read(out / "train.json")
    check_coco(document)
    names = sorted(entry["file_name"] for entry in document["images"])
    assert names == ["codh_b1_b1_0001_0_0.jpg", f"codh_b1_b1_0001_{STRIDE}_0.jpg"]
    assert sorted(path.name for path in (out / "tiles").glob("*.jpg")) == names
    assert not stale.exists()

    for entry in document["images"]:
        target = Path(entry["tile_path"])
        assert target.name == entry["file_name"] and target.is_file()
        assert target.is_absolute()
        with Image.open(target) as tile:
            assert tile.size == (TILE, TILE)
            assert tile.mode == "RGB"
            if entry["origin"] == [STRIDE, 0]:
                # The page is 1500 px wide, so this tile is white from x=604 on and grey before it.
                assert tile.getpixel((900, 500)) == (255, 255, 255)
                assert tile.getpixel((100, 500)) != (255, 255, 255)

    written = {path.name: path.read_bytes() for path in (out / "tiles").glob("*.jpg")}
    assert run(synthetic, out, "--materialise") == 0
    assert {path.name: path.read_bytes() for path in (out / "tiles").glob("*.jpg")} == written

    stats = (out / "stats.md").read_text(encoding="utf-8")
    assert "Materialised tiles: 2 files" in stats


def test_the_staged_page_is_preferred_and_the_cache_path_is_kept(synthetic: Path, tmp_path: Path) -> None:
    """The import's own file is cut from; a page the import left no file for comes from the cache."""
    cache = synthetic / "cache"
    record = images.register(synthetic / "images" / "b1_0001.jpg", IMAGE, root=cache)
    relative = f"{record.sha256[:2]}/{record.sha256}.jpg"
    out = tmp_path / "detector"
    assert run(synthetic, out) == 0
    entry = read(out / "train.json")["images"][0]
    assert entry["cache_path"] == relative
    assert Path(entry["page_path"]) == synthetic / "images" / "b1_0001.jpg"

    (synthetic / "images" / "b1_0001.jpg").unlink()
    assert run(synthetic, out) == 0
    entry = read(out / "train.json")["images"][0]
    assert entry["cache_path"] == relative
    assert Path(entry["page_path"]) == cache / relative
    assert Path(entry["page_path"]).is_file()


def test_a_tile_falls_back_to_the_next_source(tmp_path: Path) -> None:
    """A staged page that was removed after the page was read is cut from the cached copy."""
    source = stage(tmp_path, IMAGE)
    target = tmp_path / "tile.jpg"
    job = ((tmp_path / "gone.jpg", source), 0, 0, target)
    assert build_detector_data._write_tile(job, TILE, 90) == target.stat().st_size
    with Image.open(target) as tile:
        assert tile.size == (TILE, TILE)
    assert build_detector_data._write_tile(((tmp_path / "gone.jpg",), 0, 0, target), TILE, 90) is None


def test_a_page_with_no_image_is_left_out(tmp_path: Path) -> None:
    """A page whose image is nowhere is counted and holds no tiles."""
    root = tmp_path / "codh-full"
    page = page_of(BID)
    write_tables(root, books={BID: ("printed", "train")}, pages=[page], boxes={page.id: BOXES})
    (root / "images" / "b1_0001.jpg").unlink()
    out = tmp_path / "detector"
    assert run(root, out) == 0
    document = read(out / "train.json")
    assert document["images"] == [] and document["annotations"] == []
    stats = (out / "stats.md").read_text(encoding="utf-8")
    assert "Pages: 0 of 1 with an image (1 without)." in stats
    assert f"Pages left out, their image in neither the staged directory nor the cache: 1 ({page.id})." in stats
