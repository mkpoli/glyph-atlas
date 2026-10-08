"""Render hangul crops from fonts for the classifier, and write their manifests.

The syllables are those of the Korean Wikisource scan transcriptions (`work/wikisource-ko-scans`)
and of 노걸대언해 (`work/ko-nogeoldae-431023.json`, revision 431023 as the MediaWiki API answers it,
fetched when it is missing). Wiki
markup is dropped, a link keeps its shown text, and the text is split with
`glyph_atlas.clusters.clusters`. A class is one printed shape, keyed by
`glyph_atlas.clusters.shape_key` and spelt as the atlas spells a key (`U+1112 U+119E`); a shape used
fewer than `--min-uses` times is left out. Labels are never rewritten.

The split is by font family, as the CODH manifests split by book: Noto Sans CJK KR, Noto Serif CJK
KR and Malgun Gothic train, Gulim validates, Batang tests. The 18 faces are Noto Sans CJK KR Thin to
Black (7), Noto Serif CJK KR ExtraLight to Black (7), Malgun Gothic Regular and Bold, Batang and
Gulim. A face that cannot draw a syllable as one block is not used for it. Each crop is drawn with a
seeded size, ink and paper tone, a little blur and ±5% scale, then cut to its ink.

    python models/classifier/build_hangul.py
    python models/classifier/build_hangul.py --limit 40 --samples 3

The output is `work/classifier-hangul/{train,val,test}.parquet` with crops under
`work/classifier-hangul/crops/`; `build_combined.py --extra work/classifier-hangul` adds them to the
training manifests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import shlex
import sys
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pyarrow.parquet as pq
from build_manifests import QUALITY, SPLITS, Crop, crop_path, manifest_path
from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFilter, ImageFont, features

from glyph_atlas import net, refs, tables
from glyph_atlas.clusters import (
    HANGUL_TONE_MARKS,
    clusters,
    is_hangul_choseong,
    is_hangul_jongseong,
    is_hangul_jungseong,
    shape_key,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "work" / "classifier-hangul"
WIKISOURCE_TEXTS = ROOT / "work" / "wikisource-ko-scans" / "page_texts.parquet"
NOGEOLDAE = ROOT / "work" / "ko-nogeoldae-431023.json"
#: 노걸대언해 on Korean Wikisource at the revision the inventory is pinned to.
NOGEOLDAE_URL = ("https://ko.wikisource.org/w/api.php?action=query&prop=revisions&revids=431023"
                 "&rvprop=content|ids&rvslots=main&format=json&formatversion=2")
WORKERS = min(8, os.cpu_count() or 1)

TEMPLATE = re.compile(r"\{\{([^{}]*)\}\}")
TAG = re.compile(r"<[^>]*>")
LINK = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
#: Templates that set printed text smaller, beside or under the line: their arguments are printed.
PRINTED_TEMPLATES = frozenset({"작게", "더작게", "더 작게", "더더작게", "크게", "더크게", "분주", "아랫주", "du", "u"})
#: `{{SIC|as printed|corrected}}`: only the first argument is on the page.
FIRST_ARGUMENT_TEMPLATES = frozenset({"SIC", "sic"})
#: The Hangul fillers, which stand for a missing letter and print nothing.
FILLERS = frozenset({"\u115f", "\u1160", "\u3164"})

SPLIT_BY_FAMILY = {
    "Noto Sans CJK KR": "train",
    "Noto Serif CJK KR": "train",
    "Malgun Gothic": "train",
    "Gulim": "val",
    "Batang": "test",
}


@dataclass(frozen=True)
class FontSpec:
    path: Path
    family: str
    style: str
    index: int | None = None


@dataclass(frozen=True)
class FontFace:
    path: Path
    family: str
    style: str
    index: int

    @property
    def name(self) -> str:
        return f"{self.family} {self.style}"

    @property
    def split(self) -> str:
        return SPLIT_BY_FAMILY[self.family]


@dataclass(frozen=True)
class InventoryCounts:
    counts: Counter[str]
    merge_groups: dict[str, Counter[str]]

    @property
    def raw_classes(self) -> int:
        return sum(len(group) for group in self.merge_groups.values())

    @property
    def raw_uses(self) -> int:
        return sum(sum(group.values()) for group in self.merge_groups.values())


FONT_SPECS = [
    *[
        FontSpec(Path(f"/usr/share/fonts/opentype/noto/NotoSansCJK-{style}.ttc"), "Noto Sans CJK KR", style)
        for style in ("Thin", "Light", "DemiLight", "Regular", "Medium", "Bold", "Black")
    ],
    *[
        FontSpec(Path(f"/usr/share/fonts/opentype/noto/NotoSerifCJK-{style}.ttc"), "Noto Serif CJK KR", style)
        for style in ("ExtraLight", "Light", "Regular", "Medium", "SemiBold", "Bold", "Black")
    ],
    FontSpec(Path("/mnt/c/Windows/Fonts/malgun.ttf"), "Malgun Gothic", "Regular", 0),
    FontSpec(Path("/mnt/c/Windows/Fonts/malgunbd.ttf"), "Malgun Gothic", "Bold", 0),
    FontSpec(Path("/mnt/c/Windows/Fonts/batang.ttc"), "Batang", "Regular", 0),
    FontSpec(Path("/mnt/c/Windows/Fonts/gulim.ttc"), "Gulim", "Regular", 0),
]


def _template_text(match: re.Match[str]) -> str:
    name, *arguments = (part.strip() for part in match.group(1).split("|"))
    positional = [argument for argument in arguments if "=" not in argument]
    if name in PRINTED_TEMPLATES:
        return "".join(positional)
    if name in FIRST_ARGUMENT_TEMPLATES:
        return positional[0] if positional else ""
    return ""


def _link_text(match: re.Match[str]) -> str:
    target, shown = match.group(1), match.group(2)
    if shown is None and ":" in target:
        return ""  # a category or another namespace, not text on the page
    return shown if shown is not None else target


def strip_wiki_markup(text: str) -> str:
    """The printed text of a page: templates that set printed text keep it, other templates, tags and
    namespace links are dropped, and a link keeps the text it shows."""
    previous = None
    while previous != text:
        previous = text
        text = TEMPLATE.sub(_template_text, text)
    text = TAG.sub("", text)
    return LINK.sub(_link_text, text)


def is_hangul_cluster(cluster: str) -> bool:
    """Whether a written character is Hangul that prints: jamo or a syllable, not only fillers.

    Tone marks attach to whatever precedes them, so they do not decide it.
    """
    letters = [char for char in cluster if ord(char) not in HANGUL_TONE_MARKS]
    if not letters or all(char in FILLERS for char in letters):
        return False
    return all(
        is_hangul_choseong(char) or is_hangul_jungseong(char) or is_hangul_jongseong(char)
        or 0xAC00 <= ord(char) <= 0xD7A3 or 0x3131 <= ord(char) <= 0x318E
        for char in letters
    )


def class_key(cluster: str) -> str:
    """The classifier class key for one Hangul cluster."""
    return refs.to_code_point(unicodedata.normalize("NFC", shape_key(cluster)))


def raw_class_key(cluster: str) -> str:
    """The atlas key for a cluster before classifier shape merging."""
    return refs.to_code_point(unicodedata.normalize("NFC", cluster))


def inventory_from_texts(texts: Iterable[str]) -> Counter[str]:
    """Count shape-normalised Hangul class keys in stripped text."""
    return inventory_with_merge_groups(texts).counts


def inventory_with_merge_groups(texts: Iterable[str]) -> InventoryCounts:
    """Count Hangul class keys and retain the raw spellings each shape key merged."""
    counts: Counter[str] = Counter()
    merge_groups: dict[str, Counter[str]] = defaultdict(Counter)
    for text in texts:
        for cluster in clusters(strip_wiki_markup(text)):
            if is_hangul_cluster(cluster):
                key = class_key(cluster)
                counts[key] += 1
                merge_groups[key][raw_class_key(cluster)] += 1
    return InventoryCounts(counts, dict(merge_groups))


def nogeoldae_content(payload: dict) -> str:
    """The wikitext of the pinned revision, or ValueError when the answer is not that revision."""
    try:
        revision = payload["query"]["pages"][0]["revisions"][0]
        content = revision["slots"]["main"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise ValueError(f"not a revision of 노걸대언해: {str(payload)[:200]}") from error
    if revision.get("revid") != 431023 or not isinstance(content, str) or not content.strip():
        raise ValueError(f"expected revision 431023 with text, got {revision.get('revid')}")
    return content


def fetch_nogeoldae(path: Path) -> None:
    """Fetch the pinned revision and keep it only once it is that revision: the API answers an error,
    such as maxlag, with status 200, and a cached error would fail every later run."""
    part = path.with_name(path.name + ".fetch")
    net.download(NOGEOLDAE_URL, part, expected="json", refresh=True)
    nogeoldae_content(json.loads(part.read_text(encoding="utf-8")))
    os.replace(part, path)


def read_inventory(page_texts: Path = WIKISOURCE_TEXTS, nogeoldae: Path = NOGEOLDAE) -> InventoryCounts:
    """Read both inventory sources and count Hangul class keys."""
    texts = pq.read_table(page_texts, columns=["text_raw"]).column("text_raw").to_pylist()
    if not nogeoldae.exists():
        fetch_nogeoldae(nogeoldae)
    content = nogeoldae_content(json.loads(nogeoldae.read_text(encoding="utf-8")))
    return inventory_with_merge_groups([*(text or "" for text in texts), content])


def filter_min_uses(counts: Counter[str], min_uses: int) -> tuple[Counter[str], dict[str, int]]:
    """Keep classes with at least ``min_uses`` occurrences and summarize what was dropped."""
    counts = Counter(counts)
    kept = Counter({key: uses for key, uses in counts.items() if uses >= min_uses})
    dropped = counts - kept
    return kept, {"classes": len(dropped), "uses": sum(dropped.values())}


def selected_keys(counts: Counter[str], limit: int | None) -> list[str]:
    """Keys ordered by frequency descending, then key ascending, with an optional limit."""
    keys = [key for key, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]
    return keys[:limit] if limit is not None else keys


def largest_merge_groups(
    merge_groups: dict[str, Counter[str]],
    *,
    limit: int = 10,
) -> list[dict[str, object]]:
    """The largest raw-spelling groups collapsed into one classifier class."""
    groups = [
        (key, group)
        for key, group in merge_groups.items()
        if len(group) > 1
    ]
    groups.sort(key=lambda item: (-len(item[1]), -sum(item[1].values()), item[0]))
    return [
        {
            "key": key,
            "raw_spellings": len(group),
            "uses": sum(group.values()),
            "spellings": [
                {"key": raw_key, "uses": uses}
                for raw_key, uses in sorted(group.items(), key=lambda item: (-item[1], item[0]))
            ],
        }
        for key, group in groups[:limit]
    ]


def resolve_fonts(*, required: bool = False) -> list[FontFace]:
    """The configured faces that are available on this system."""
    faces: list[FontFace] = []
    missing: list[str] = []
    for spec in FONT_SPECS:
        if not spec.path.exists():
            missing.append(str(spec.path))
            continue
        index = spec.index if spec.index is not None else _find_ttc_family(spec.path, spec.family)
        if index is None:
            missing.append(f"{spec.path} family {spec.family}")
            continue
        faces.append(FontFace(spec.path, spec.family, spec.style, index))
    if required and missing:
        raise SystemExit("missing Hangul classifier fonts: " + ", ".join(missing))
    return faces


def _find_ttc_family(path: Path, family: str) -> int | None:
    for index in range(32):
        try:
            font = ImageFont.truetype(str(path), 96, index=index, layout_engine=ImageFont.Layout.RAQM)
        except OSError:
            return None
        if font.getname()[0] == family:
            return index
    return None


@lru_cache(maxsize=512)
def load_font(path: str, index: int, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size, index=index, layout_engine=ImageFont.Layout.RAQM)


@lru_cache(maxsize=64)
def cmap(path: str, index: int) -> frozenset[int]:
    try:
        font = TTFont(path, fontNumber=index)
    except TypeError:
        font = TTFont(path)
    with font:
        return frozenset(font.getBestCmap())


def has_cmap_coverage(face: FontFace, text: str) -> bool:
    return all(ord(char) in cmap(str(face.path), face.index) for char in text)


def shapes_as_one_block(font: ImageFont.FreeTypeFont, text: str) -> bool:
    """Reject multi-code-point clusters that shape as separate side-by-side glyphs."""
    if len(text) <= 1:
        return True
    nfc = unicodedata.normalize("NFC", text)
    shaped = font.getlength(text)
    if nfc != text:
        return abs(shaped - font.getlength(nfc)) <= 2
    separate = sum(font.getlength(char) for char in text)
    return separate > 0 and shaped <= 0.9 * separate


def can_render(face: FontFace, text: str, *, size: int = 96) -> bool:
    if not features.check("raqm"):
        raise RuntimeError("Pillow RAQM support is required to shape Hangul jamo clusters")
    if not has_cmap_coverage(face, text):
        return False
    font = load_font(str(face.path), face.index, size)
    return shapes_as_one_block(font, text)


def nonblank(image: Image.Image) -> bool:
    """Whether a greyscale crop has enough contrast to contain ink."""
    if image.mode != "L":
        image = image.convert("L")
    extrema = image.getextrema()
    return extrema[1] - extrema[0] >= 20


def render_crop(
    face: FontFace,
    text: str,
    key: str,
    n: int,
    target: Path,
) -> bool:
    """Render one deterministic greyscale JPEG crop."""
    seed = int.from_bytes(hashlib.sha256(f"{key}\0{face.name}\0{n}".encode()).digest()[:8], "big")
    rng = random.Random(seed)
    size = rng.randint(48, 220)
    font = load_font(str(face.path), face.index, size)
    if not has_cmap_coverage(face, text) or not shapes_as_one_block(font, text):
        return False

    paper = rng.randint(210, 255)
    ink = rng.randint(0, min(70, paper - 120))
    pad = size // 2 + 10 + rng.randint(0, 8)
    probe = Image.new("L", (1, 1), paper)
    box = ImageDraw.Draw(probe).textbbox((0, 0), text, font=font)
    width = max(1, box[2] - box[0])
    height = max(1, box[3] - box[1])
    canvas = Image.new("L", (width + pad * 2, height + pad * 2), paper)
    draw = ImageDraw.Draw(canvas)
    draw.text((pad - box[0], pad - box[1]), text, font=font, fill=ink)

    scale = rng.uniform(0.95, 1.05)
    if abs(scale - 1.0) > 0.01:
        canvas = canvas.resize(
            (max(1, round(canvas.width * scale)), max(1, round(canvas.height * scale))),
            Image.Resampling.LANCZOS,
        )
    if rng.random() < 0.7:
        canvas = canvas.filter(ImageFilter.GaussianBlur(rng.uniform(0.0, 1.0)))

    mask = canvas.point(lambda value: 255 if abs(value - paper) >= 40 else 0)
    ink_box = mask.getbbox()
    if ink_box is None:
        return False
    margin = rng.randint(2, 6)
    crop_box = (
        max(0, ink_box[0] - margin),
        max(0, ink_box[1] - margin),
        min(canvas.width, ink_box[2] + margin),
        min(canvas.height, ink_box[3] + margin),
    )
    if crop_box[2] <= crop_box[0] or crop_box[3] <= crop_box[1]:
        return False
    crop = canvas.crop(crop_box)
    if not nonblank(crop):
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    crop.save(target, format="JPEG", quality=QUALITY)
    return True


def records_for_key(
    *,
    out: Path,
    key: str,
    samples: int,
    faces: Sequence[FontFace],
    render: bool,
) -> tuple[list[Crop], dict[str, list[str]], bool, int]:
    """Build all records for one class key.

    Returns records, unrenderable faces by key, whether a training face rendered the key, and missing
    crop count for manifest-only runs.
    """
    text = refs.to_char(key)
    # A syllable printed with no initial (ᅵᆫ) has the filler as its initial for the font to shape it
    # as one block; the class key leaves the filler out, as shape_key does.
    if is_hangul_jungseong(text[0]):
        text = "\u115f" + text
    renderable: list[FontFace] = []
    unrenderable: dict[str, list[str]] = defaultdict(list)
    for face in faces:
        if can_render(face, text):
            renderable.append(face)
        else:
            unrenderable[key].append(face.name)
    has_training_font = any(face.split == "train" for face in renderable)
    if not has_training_font:
        return [], unrenderable, False, 0

    records: list[Crop] = []
    missing = 0
    for face in renderable:
        for n in range(samples):
            unit_id = f"syn:hangul:{face.name}:{key}:{n}"
            split = face.split
            target = out / "crops" / crop_path(Path(), split, unit_id)
            if render:
                if not render_crop(face, text, key, n, target):
                    unrenderable[key].append(face.name)
                    continue
            elif not target.exists():
                missing += 1
                continue
            records.append(
                Crop(
                    unit_id=unit_id,
                    crop=manifest_path(out / "crops", split, unit_id),
                    label=key,
                    code_point=key,
                    document_id=f"syn:hangul:{face.family}",
                    page_id="",
                    split=split,
                    production="unknown",
                    kind="char",
                    script="hangul",
                )
            )
    return records, unrenderable, True, missing


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--min-uses", type=int, default=3)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--manifest-only", action="store_true")
    args = parser.parse_args(argv)
    # Anchored to the working directory but not resolved, so a symlinked output keeps its repo path.
    args.out = args.out.absolute()
    if args.samples < 1:
        raise SystemExit("--samples must be at least 1")
    if args.min_uses < 1:
        raise SystemExit("--min-uses must be at least 1")
    if not features.check("raqm"):
        raise SystemExit("Pillow RAQM support is required")

    inventory = read_inventory()
    counts, min_uses_dropped = filter_min_uses(inventory.counts, args.min_uses)
    keys = selected_keys(counts, args.limit)
    faces = resolve_fonts(required=True)
    records: dict[str, list[Crop]] = {split: [] for split in SPLITS}
    unrenderable: dict[str, list[str]] = defaultdict(list)
    without_training: list[str] = []
    missing = 0
    for key in keys:
        built, failures, has_training_font, missing_for_key = records_for_key(
            out=args.out,
            key=key,
            samples=args.samples,
            faces=faces,
            render=not args.manifest_only,
        )
        if not has_training_font:
            without_training.append(key)
            continue
        for failed_key, names in failures.items():
            unrenderable[failed_key].extend(names)
        missing += missing_for_key
        for record in built:
            records[record.split].append(record)

    command = "python " + " ".join(shlex.quote(part) for part in (argv if argv is not None else sys.argv[1:]))
    args.out.mkdir(parents=True, exist_ok=True)
    crops = {
        split: tables.write(args.out / f"{split}.parquet", records[split], Crop, command=command)
        for split in SPLITS
    }
    summary = {
        "clusters": len({record.code_point for rows in records.values() for record in rows}),
        "inventory": {
            "classes_before_merging": inventory.raw_classes,
            "uses_before_merging": inventory.raw_uses,
            "classes_after_merging": len(inventory.counts),
            "uses_after_merging": sum(inventory.counts.values()),
            "min_uses": args.min_uses,
            "classes_after_min_uses": len(counts),
            "uses_after_min_uses": sum(counts.values()),
            "dropped_by_min_uses": min_uses_dropped,
        },
        "largest_merge_groups": largest_merge_groups(inventory.merge_groups),
        "crops": crops,
        "fonts_used": [face.name for face in faces],
        "unrenderable_by_font": {key: sorted(set(names)) for key, names in sorted(unrenderable.items()) if names},
    }
    if without_training:
        summary["clusters_without_training_font"] = without_training
    if missing:
        summary["missing_crop_files"] = missing
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
