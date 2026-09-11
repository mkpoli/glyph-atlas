"""Render synthetic hentaigana examples for the 字母 classifier (T30).

The code points come from `data/vocab/hentaigana.tsv`. The script downloads the two open
hentaigana fonts into `cache/fonts/` (checksums pinned below, the fonts are never committed),
reads each font's cmap, and renders every code point that font covers into
`work/synthetic/kana/<code point>/<font>-<n>.png`, 128x128, `--count` renderings per code point
per font (200 by default). A code point a font does not cover is skipped and listed in the
coverage table; asking for such a code point raises `MissingGlyphError`. A `.notdef` box is never
written. Renderings carry `synthetic=true` and never enter dataset counts.

Augmentations, sampled per rendering and recorded in the manifest, ranges as of this version:

    fit              0.62-0.82, the longer side of the ink box as a fraction of the canvas
    aspect_x/y       0.95-1.05, independent horizontal and vertical stretch
    center_u/v       0.42-0.58, the glyph centre as a fraction of the canvas
    shear            +-0.2, horizontal shear
    rotation_deg     +-8, rotation about the glyph centre
    stroke_px        +-2, morphological dilation or erosion with an elliptical kernel
    elastic_*        displacement of 1.5-4.0 px from a smoothed 16x16 random field
    ink_level        0.05-0.30, the ink value against white
    paper_level      0.86-0.97, paper value; paper texture: blurred noise, sigma 8-32 px,
                     strength 0.02-0.10, plus per-pixel grain 0.00-0.02
    ink_blots        0-3 per image, semi-axes 1.0-3.5 px and 0.5-1.5 of that, 70% on the ink
    blur_sigma       0.0-1.5 Gaussian

`work/synthetic/kana/manifest.parquet` holds one row per rendering with the columns `path`,
`code_point`, `font`, `parameters` (the sampled values as JSON), `seed` and `synthetic`. `path` is
relative to the directory that holds the manifest. The renderings of one file depend only on the
seed, the code point, the font and the index, so a rerun skips files that are already there and
writes the same manifest.

    uv run python scripts/render_hentaigana.py --seed 0
    uv run python scripts/render_hentaigana.py --seed 0 --count 5 --only U+1B001,U+1B002
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import httpx
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
VOCAB = ROOT / "data" / "vocab" / "hentaigana.tsv"
USER_AGENT = "kuzushiji-atlas (+https://github.com/mkpoli/kuzushiji-atlas)"
REQUEST_PAUSE = 3.0
CANVAS = 128
COUNT = 200
GLYPH_SIZE = 192
GLYPH_PAD = 8
ELASTIC_COARSE = 16
PAPER_COARSE = 16
PAPER_TINT = (1.0, 0.985, 0.955)


class MissingGlyphError(ValueError):
    """A font has no glyph for a code point: no cmap entry, or the glyph carries no ink."""


class ChecksumError(RuntimeError):
    """A downloaded font does not match the checksum pinned in this script."""


@dataclass(frozen=True)
class FontSpec:
    name: str
    version: str
    filename: str
    url: str
    archive_sha256: str
    member: str
    font_sha256: str
    font_bytes: int
    licence: str
    licence_url: str
    source: str


FONTS: tuple[FontSpec, ...] = (
    FontSpec(
        name="ninjal-hentaigana",
        version="1.01",
        filename="ninjal_hentaigana.ttf",
        url="https://cid.ninjal.ac.jp/kana/ninjal_hentaigana.zip",
        archive_sha256="62b01c19cb40dc4b64b1e1da776fca483e19e21c2772cc3f9db9a067bedbc84d",
        member="ninjal_hentaigana.ttf",
        font_sha256="e1301406c49dffed801bc12f0bb6a148f90215d4cf7d3a7bb0831cd798f6345e",
        font_bytes=506732,
        licence="Apache-2.0",
        licence_url="https://www.apache.org/licenses/LICENSE-2.0",
        source="https://cid.ninjal.ac.jp/kana/",
    ),
    FontSpec(
        name="noto-serif-hentaigana",
        version="1.000",
        filename="NotoSerifHentaigana-Regular.ttf",
        url=(
            "https://github.com/notofonts/hentaigana/releases/download/"
            "NotoSerifHentaigana-v1.000/NotoSerifHentaigana-v1.000.zip"
        ),
        archive_sha256="b72479094ed33f7ab4a2e447cb642b928b6efedfa5fe54b2821868c863b9be50",
        member="NotoSerifHentaigana/full/ttf/NotoSerifHentaigana-Regular.ttf",
        font_sha256="64a20a2b94df46421dba7971ad54211de92ef46389a529c7310710b62ae7f4b4",
        font_bytes=265860,
        licence="OFL-1.1",
        licence_url="https://github.com/notofonts/hentaigana/blob/main/OFL.txt",
        source="https://github.com/notofonts/hentaigana/releases/tag/NotoSerifHentaigana-v1.000",
    ),
)

PARAMETER_KEYS = frozenset(
    {
        "fit",
        "aspect_x",
        "aspect_y",
        "center_u",
        "center_v",
        "shear",
        "rotation_deg",
        "stroke_px",
        "elastic_alpha_px",
        "elastic_sigma_px",
        "elastic_seed",
        "ink_level",
        "paper_level",
        "paper_sigma_px",
        "paper_strength",
        "paper_seed",
        "grain",
        "grain_seed",
        "ink_blots",
        "blur_sigma",
    }
)

MANIFEST_SCHEMA = pa.schema(
    [
        pa.field("path", pa.string()),
        pa.field("code_point", pa.string()),
        pa.field("font", pa.string()),
        pa.field("parameters", pa.string()),
        pa.field("seed", pa.int64()),
        pa.field("synthetic", pa.bool_()),
    ]
)


def code_points(path: Path = VOCAB) -> list[str]:
    """The code points of `hentaigana.tsv`, in file order, as `U+XXXX` strings."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return [line.split("\t")[0].strip() for line in lines[1:] if line.strip()]


def char_of(code_point: str) -> str:
    """`U+1B001` to the character."""
    return chr(int(code_point.removeprefix("U+"), 16))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# Fonts -----------------------------------------------------------------------------------------


_last_request: dict[str, float] = {}


def _pause_for(host: str) -> None:
    """Wait so that two requests to one host are at least `REQUEST_PAUSE` seconds apart."""
    last = _last_request.get(host)
    now = time.monotonic()
    if last is not None and now - last < REQUEST_PAUSE:
        time.sleep(REQUEST_PAUSE - (now - last))
    _last_request[host] = time.monotonic()


def download(url: str, dest: Path, *, client: httpx.Client) -> Path:
    """Fetch `url` to `dest` through a temporary file."""
    _pause_for(httpx.URL(url).host or "")
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".part")
    with client.stream("GET", url) as response:
        response.raise_for_status()
        with partial.open("wb") as handle:
            for chunk in response.iter_bytes(1 << 20):
                handle.write(chunk)
    partial.replace(dest)
    return dest


def ensure_font(spec: FontSpec, fonts_dir: Path, *, client: httpx.Client, refresh: bool = False) -> Path:
    """Return the font file, downloading and unpacking the pinned archive when it is not cached."""
    fonts_dir.mkdir(parents=True, exist_ok=True)
    target = fonts_dir / spec.filename
    if target.exists() and not refresh and sha256_file(target) == spec.font_sha256:
        return target
    archive = fonts_dir / Path(httpx.URL(spec.url).path).name
    if refresh or not archive.exists() or sha256_file(archive) != spec.archive_sha256:
        print(f"downloading {spec.url}", flush=True)
        download(spec.url, archive, client=client)
    actual = sha256_file(archive)
    if actual != spec.archive_sha256:
        raise ChecksumError(f"{archive.name} is {actual}, the script pins {spec.archive_sha256}")
    with zipfile.ZipFile(archive) as zf:
        try:
            data = zf.read(spec.member)
        except KeyError as exc:
            raise ChecksumError(f"{archive.name} holds no {spec.member}") from exc
    member_sha = hashlib.sha256(data).hexdigest()
    if member_sha != spec.font_sha256:
        raise ChecksumError(f"{spec.member} is {member_sha}, the script pins {spec.font_sha256}")
    target.write_bytes(data)
    return target


def ensure_fonts(
    specs: tuple[FontSpec, ...] = FONTS, fonts_dir: Path = ROOT / "cache" / "fonts", *, refresh: bool = False
) -> list[Path]:
    """Download every font that is not cached and return the font files in `specs` order."""
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=120, follow_redirects=True) as client:
        return [ensure_font(spec, fonts_dir, client=client, refresh=refresh) for spec in specs]


def font_manifest(specs: tuple[FontSpec, ...], paths: list[Path]) -> dict[str, Any]:
    """The provenance record of the cached fonts: url, size, checksum, licence."""
    entries = []
    for spec, path in zip(specs, paths, strict=True):
        archive = path.parent / Path(httpx.URL(spec.url).path).name
        entries.append(
            {
                "name": spec.name,
                "version": spec.version,
                "url": spec.url,
                "archive": archive.name,
                "archive_bytes": archive.stat().st_size,
                "archive_sha256": spec.archive_sha256,
                "member": spec.member,
                "file": path.name,
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
                "licence": spec.licence,
                "licence_url": spec.licence_url,
                "source": spec.source,
            }
        )
    return {"fonts": entries}


def write_font_manifest(specs: tuple[FontSpec, ...], paths: list[Path]) -> Path:
    """Write `cache/fonts/manifest.json`; rerunning over the same cache writes the same bytes."""
    target = paths[0].parent / "manifest.json"
    text = json.dumps(font_manifest(specs, paths), indent=2, ensure_ascii=False) + "\n"
    target.write_text(text, encoding="utf-8")
    return target


# cmap ------------------------------------------------------------------------------------------


def sfnt_tables(data: bytes) -> dict[str, tuple[int, int]]:
    """The table directory of a TTF or OTF: tag to (offset, length)."""
    if data[:4] == b"ttcf":
        raise ValueError("font collections are not supported")
    count = struct.unpack_from(">H", data, 4)[0]
    tables = {}
    for index in range(count):
        tag, _checksum, offset, length = struct.unpack_from(">4sIII", data, 12 + 16 * index)
        tables[tag.decode("latin-1")] = (offset, length)
    return tables


def _cmap_format_4(data: bytes, start: int) -> dict[int, int]:
    seg_x2 = struct.unpack_from(">H", data, start + 6)[0]
    segments = seg_x2 // 2
    ends = struct.unpack_from(f">{segments}H", data, start + 14)
    starts = struct.unpack_from(f">{segments}H", data, start + 16 + seg_x2)
    deltas = struct.unpack_from(f">{segments}h", data, start + 16 + 2 * seg_x2)
    offsets_at = start + 16 + 3 * seg_x2
    offsets = struct.unpack_from(f">{segments}H", data, offsets_at)
    chars: dict[int, int] = {}
    for index in range(segments):
        for code in range(starts[index], ends[index] + 1):
            if code == 0xFFFF:
                continue
            if offsets[index] == 0:
                glyph = (code + deltas[index]) & 0xFFFF
            else:
                position = offsets_at + 2 * index + offsets[index] + 2 * (code - starts[index])
                if position + 2 > len(data):
                    continue
                glyph = struct.unpack_from(">H", data, position)[0]
                if glyph:
                    glyph = (glyph + deltas[index]) & 0xFFFF
            if glyph:
                chars[code] = glyph
    return chars


def _cmap_format_6(data: bytes, start: int) -> dict[int, int]:
    first, count = struct.unpack_from(">HH", data, start + 6)
    glyphs = struct.unpack_from(f">{count}H", data, start + 10)
    return {first + index: glyph for index, glyph in enumerate(glyphs) if glyph}


def _cmap_format_12(data: bytes, start: int, *, constant: bool = False) -> dict[int, int]:
    groups = struct.unpack_from(">I", data, start + 12)[0]
    chars: dict[int, int] = {}
    for index in range(groups):
        first, last, glyph = struct.unpack_from(">III", data, start + 16 + 12 * index)
        for code in range(first, last + 1):
            chars[code] = glyph if constant else glyph + (code - first)
    return chars


def cmap_code_points(path: Path) -> dict[int, int]:
    """The font's character map: code point to glyph id, from its best Unicode subtable."""
    data = path.read_bytes()
    start, _length = sfnt_tables(data)["cmap"]
    count = struct.unpack_from(">H", data, start + 2)[0]
    records = []
    for index in range(count):
        platform, encoding, offset = struct.unpack_from(">HHI", data, start + 4 + 8 * index)
        subtable = start + offset
        fmt = struct.unpack_from(">H", data, subtable)[0]
        if fmt == 4:
            chars = _cmap_format_4(data, subtable)
        elif fmt == 6:
            chars = _cmap_format_6(data, subtable)
        elif fmt == 12:
            chars = _cmap_format_12(data, subtable)
        elif fmt == 13:
            chars = _cmap_format_12(data, subtable, constant=True)
        else:
            chars = {}
        records.append(((platform, encoding), fmt, chars))
    preferred = ((3, 10), (0, 6), (0, 4), (3, 1), (0, 3), (0, 2), (0, 1), (0, 0), (3, 0))
    for key in preferred:
        for platform_encoding, _fmt, chars in records:
            if platform_encoding == key and chars:
                return chars
    return max((chars for _key, _fmt, chars in records), key=len, default={})


@dataclass
class Glyph:
    """One character rasterised once and cropped to its ink box, values from 0 to 1."""

    code_point: str
    mask: np.ndarray


@dataclass
class LoadedFont:
    """A font file with its cmap and an open FreeType face."""

    spec: FontSpec
    path: Path
    cmap: dict[int, int]
    face: ImageFont.FreeTypeFont

    def covers(self, code_point: str) -> bool:
        return int(code_point.removeprefix("U+"), 16) in self.cmap

    def glyph(self, code_point: str) -> Glyph:
        """The ink of one code point; a code point the font lacks raises."""
        if not self.covers(code_point):
            raise MissingGlyphError(f"{self.spec.name} has no glyph for {code_point}")
        return load_glyph(self.face, code_point)


def open_font(spec: FontSpec, path: Path) -> LoadedFont:
    """Read the cmap and open the face of a font file."""
    return LoadedFont(
        spec=spec,
        path=path,
        cmap=cmap_code_points(path),
        face=ImageFont.truetype(str(path), GLYPH_SIZE),
    )


def load_glyph(face: ImageFont.FreeTypeFont, code_point: str) -> Glyph:
    """Rasterise one character at `GLYPH_SIZE` and crop it to its ink box.

    A character the face cannot draw (blank ink) raises `MissingGlyphError`; callers that may meet
    such a character check the cmap first, which gives the better message.
    """
    character = char_of(code_point)
    box = face.getbbox(character)
    if box is None:
        raise MissingGlyphError(f"no glyph for {code_point}")
    left, top = math.floor(box[0]), math.floor(box[1])
    right, bottom = math.ceil(box[2]), math.ceil(box[3])
    if right <= left or bottom <= top:
        raise MissingGlyphError(f"the glyph of {code_point} is blank")
    canvas = Image.new("L", (right - left + 2 * GLYPH_PAD, bottom - top + 2 * GLYPH_PAD), 0)
    ImageDraw.Draw(canvas).text((GLYPH_PAD - left, GLYPH_PAD - top), character, font=face, fill=255)
    mask = np.asarray(canvas, dtype=np.float32) / 255.0
    if not mask.any():
        raise MissingGlyphError(f"the glyph of {code_point} is blank")
    return Glyph(code_point=code_point, mask=mask)


# Augmentation ----------------------------------------------------------------------------------


def rendering_rng(seed: int, code_point: str, font: str, index: int) -> np.random.Generator:
    """The generator of one rendering, independent of the order renderings are produced in."""
    key = f"{seed}|{code_point}|{font}|{index}".encode()
    return np.random.default_rng(int.from_bytes(hashlib.sha256(key).digest()[:8], "big"))


def sample_parameters(rng: np.random.Generator) -> dict[str, Any]:
    """Draw the augmentation parameters of one rendering; the key set is `PARAMETER_KEYS`."""
    blots = []
    for _ in range(int(rng.integers(0, 4))):
        radius = float(rng.uniform(1.0, 3.5))
        blots.append(
            {
                "u": round(float(rng.uniform(0.0, 1.0)), 4),
                "v": round(float(rng.uniform(0.0, 1.0)), 4),
                "rx_px": round(radius, 3),
                "ry_px": round(radius * float(rng.uniform(0.5, 1.5)), 3),
                "angle_deg": round(float(rng.uniform(0.0, 180.0)), 2),
                "on_ink": bool(rng.random() < 0.7),
            }
        )
    return {
        "fit": round(float(rng.uniform(0.62, 0.82)), 4),
        "aspect_x": round(float(rng.uniform(0.95, 1.05)), 4),
        "aspect_y": round(float(rng.uniform(0.95, 1.05)), 4),
        "center_u": round(float(rng.uniform(0.42, 0.58)), 4),
        "center_v": round(float(rng.uniform(0.42, 0.58)), 4),
        "shear": round(float(rng.uniform(-0.2, 0.2)), 4),
        "rotation_deg": round(float(rng.uniform(-8.0, 8.0)), 3),
        "stroke_px": int(np.clip(round(float(rng.normal(0.0, 1.1))), -2, 2)),
        "elastic_alpha_px": round(float(rng.uniform(1.5, 4.0)), 3),
        "elastic_sigma_px": round(float(rng.uniform(4.0, 9.0)), 3),
        "elastic_seed": int(rng.integers(0, 2**31)),
        "ink_level": round(float(rng.uniform(0.05, 0.30)), 4),
        "paper_level": round(float(rng.uniform(0.86, 0.97)), 4),
        "paper_sigma_px": round(float(rng.uniform(8.0, 32.0)), 3),
        "paper_strength": round(float(rng.uniform(0.02, 0.10)), 4),
        "paper_seed": int(rng.integers(0, 2**31)),
        "grain": round(float(rng.uniform(0.0, 0.02)), 4),
        "grain_seed": int(rng.integers(0, 2**31)),
        "ink_blots": blots,
        "blur_sigma": round(float(rng.uniform(0.0, 1.5)), 3),
    }


def _elastic(mask: np.ndarray, params: dict[str, Any]) -> np.ndarray:
    """Displace the ink by a smoothed random field; the field is derived from `elastic_seed`."""
    height, width = mask.shape
    rng = np.random.default_rng(int(params["elastic_seed"]))
    sigma = float(params["elastic_sigma_px"]) * ELASTIC_COARSE / CANVAS
    planes = []
    for plane in rng.uniform(-1.0, 1.0, size=(2, ELASTIC_COARSE, ELASTIC_COARSE)):
        blurred = cv2.GaussianBlur(plane.astype(np.float32), (0, 0), sigma)
        planes.append(blurred / (float(blurred.std()) or 1.0))
    columns, rows = np.meshgrid(np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32))
    alpha = float(params["elastic_alpha_px"])
    dx = cv2.resize(planes[0], (width, height), interpolation=cv2.INTER_LINEAR).astype(np.float32) * alpha
    dy = cv2.resize(planes[1], (width, height), interpolation=cv2.INTER_LINEAR).astype(np.float32) * alpha
    return cv2.remap(
        mask,
        columns + dx,
        rows + dy,
        cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )


def _paper_texture(params: dict[str, Any]) -> np.ndarray:
    """Blurred noise, unit standard deviation, finer than `paper_sigma_px` on the canvas."""
    rng = np.random.default_rng(int(params["paper_seed"]))
    noise = rng.normal(0.0, 1.0, size=(PAPER_COARSE, PAPER_COARSE)).astype(np.float32)
    sigma = float(params["paper_sigma_px"]) * PAPER_COARSE / CANVAS
    if sigma > 0.05:
        noise = cv2.GaussianBlur(noise, (0, 0), sigma)
    noise = cv2.resize(noise, (CANVAS, CANVAS), interpolation=cv2.INTER_CUBIC)
    return noise / (float(noise.std()) or 1.0)


def render_one(glyph: Glyph, params: dict[str, Any]) -> Image.Image:
    """One augmented 128x128 RGB rendering of `glyph` under `params`."""
    mask = glyph.mask
    rows, columns = mask.shape
    scale = float(params["fit"]) * CANVAS / max(rows, columns)
    width = max(2, round(columns * scale * float(params["aspect_x"])))
    height = max(2, round(rows * scale * float(params["aspect_y"])))
    ink = cv2.resize(mask, (width, height), interpolation=cv2.INTER_AREA)

    stroke = int(params["stroke_px"])
    if stroke:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * abs(stroke) + 1, 2 * abs(stroke) + 1))
        if stroke > 0:
            ink = cv2.dilate(ink, kernel)
        else:
            thinned = cv2.erode(ink, kernel)
            ink = thinned if float(thinned.max()) >= 0.05 else ink * 0.5

    ink = _elastic(ink, params)

    theta = math.radians(float(params["rotation_deg"]))
    shear = float(params["shear"])
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    a00, a01 = cos_t, cos_t * shear - sin_t
    a10, a11 = sin_t, sin_t * shear + cos_t
    center_x = float(params["center_u"]) * CANVAS
    center_y = float(params["center_v"]) * CANVAS
    source_x, source_y = width / 2.0, height / 2.0
    transform = np.array(
        [
            [a00, a01, center_x - (a00 * source_x + a01 * source_y)],
            [a10, a11, center_y - (a10 * source_x + a11 * source_y)],
        ],
        dtype=np.float32,
    )
    ink = cv2.warpAffine(
        ink,
        transform,
        (CANVAS, CANVAS),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )

    paper = float(params["paper_level"]) + float(params["paper_strength"]) * _paper_texture(params)
    grain = np.random.default_rng(int(params["grain_seed"])).normal(
        0.0, float(params["grain"]), size=(CANVAS, CANVAS)
    )
    paper = np.clip(paper + grain.astype(np.float32), 0.0, 1.0)
    ink = np.clip(ink, 0.0, 1.0)
    ink_level = float(params["ink_level"])
    value = paper * (1.0 - ink) + ink_level * ink

    blots = params["ink_blots"]
    if blots:
        value = _composite_blots(value, ink, blots, ink_level)

    sigma = float(params["blur_sigma"])
    if sigma > 0.05:
        value = cv2.GaussianBlur(value, (0, 0), sigma)
    value = np.clip(value, 0.0, 1.0)[:, :, None] * np.array(PAPER_TINT, dtype=np.float32)
    return Image.fromarray(np.rint(np.clip(value, 0.0, 1.0) * 255.0).astype(np.uint8), "RGB")


def _composite_blots(
    value: np.ndarray, ink: np.ndarray, blots: list[dict[str, Any]], ink_level: float
) -> np.ndarray:
    """Draw the sampled ink blots, moving the ones marked `on_ink` onto the nearest ink pixel."""
    layer = np.zeros((CANVAS, CANVAS), np.uint8)
    inked = np.argwhere(ink > 0.25)
    for blot in blots:
        x = float(blot["u"]) * (CANVAS - 1)
        y = float(blot["v"]) * (CANVAS - 1)
        if blot["on_ink"] and len(inked):
            nearest = inked[np.argmin(((inked - np.array([y, x], np.float32)) ** 2).sum(axis=1))]
            y, x = float(nearest[0]), float(nearest[1])
        axes = (max(1, round(blot["rx_px"])), max(1, round(blot["ry_px"])))
        cv2.ellipse(
            layer,
            (round(x), round(y)),
            axes,
            float(blot["angle_deg"]),
            0,
            360,
            255,
            -1,
            cv2.LINE_AA,
        )
    blot = cv2.GaussianBlur(layer.astype(np.float32) / 255.0, (0, 0), 0.8)
    return value * (1.0 - blot) + ink_level * blot


# Run -------------------------------------------------------------------------------------------


def render_row(
    out_dir: Path, path: Path, code_point: str, font: str, params: dict[str, Any], seed: int
) -> dict[str, Any]:
    """One manifest row."""
    return {
        "path": path.relative_to(out_dir).as_posix(),
        "code_point": code_point,
        "font": font,
        "parameters": json.dumps(params, sort_keys=True, separators=(",", ":")),
        "seed": seed,
        "synthetic": True,
    }


def write_manifest(path: Path, rows: list[dict[str, Any]]) -> int:
    """Write the manifest table, every column present even when there are no rows."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=MANIFEST_SCHEMA), path, compression="zstd")
    return len(rows)


def render_dataset(
    fonts: list[LoadedFont],
    wanted: list[str],
    out_dir: Path,
    *,
    seed: int = 0,
    count: int = COUNT,
    progress: bool = True,
) -> list[dict[str, Any]]:
    """Render `count` images of every code point every font covers; return the manifest rows."""
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    started = time.monotonic()
    for font in fonts:
        covered = [code_point for code_point in wanted if font.covers(code_point)]
        if progress:
            print(f"{font.spec.name}: {len(covered)} of {len(wanted)} code points", flush=True)
        for position, code_point in enumerate(covered, 1):
            directory = out_dir / code_point
            glyph = None
            for index in range(count):
                path = directory / f"{font.spec.name}-{index}.png"
                params = sample_parameters(rendering_rng(seed, code_point, font.spec.name, index))
                if not path.exists():
                    directory.mkdir(parents=True, exist_ok=True)
                    if glyph is None:
                        glyph = font.glyph(code_point)
                    render_one(glyph, params).save(path, format="PNG")
                rows.append(render_row(out_dir, path, code_point, font.spec.name, params, seed))
            if progress and (position % 25 == 0 or position == len(covered)):
                print(
                    f"  {position}/{len(covered)} {code_point}, {len(rows)} renderings, "
                    f"{time.monotonic() - started:.0f} s",
                    flush=True,
                )
    write_manifest(out_dir / "manifest.parquet", rows)
    return rows


def write_sample_sheet(
    out_dir: Path,
    code_points_wanted: list[str],
    fonts: list[LoadedFont],
    *,
    tile: int = 64,
    columns: int = 24,
) -> Path:
    """Tile the first rendering of every covered code point and font into one sheet."""
    tiles: list[tuple[str, str, Path]] = []
    for code_point in code_points_wanted:
        for font in fonts:
            if not font.covers(code_point):
                continue
            path = out_dir / code_point / f"{font.spec.name}-0.png"
            if path.exists():
                tiles.append((code_point, font.spec.name, path))
    label_height = 10
    rows = max(1, math.ceil(len(tiles) / columns))
    sheet = Image.new("RGB", (columns * tile, rows * (tile + label_height)), (255, 255, 255))
    draw = ImageDraw.Draw(sheet)
    label_font = ImageFont.load_default(size=9)
    for index, (code_point, _font, path) in enumerate(tiles):
        x = (index % columns) * tile
        y = (index // columns) * (tile + label_height)
        with Image.open(path) as image:
            sheet.paste(image.convert("RGB").resize((tile, tile), Image.LANCZOS), (x, y))
        draw.text((x + 2, y + tile), code_point, fill=(0, 0, 0), font=label_font)
    target = out_dir / "sample-sheet.png"
    sheet.save(target, format="PNG")
    return target


def coverage_table(fonts: list[LoadedFont], wanted: list[str]) -> list[str]:
    """One line per font and one for the union, each with the code points it does not cover."""
    width = max([len(font.spec.name) for font in fonts] + [8])
    lines = [f"{'font':<{width}}  covered  missing"]
    for font in fonts:
        missing = [code_point for code_point in wanted if not font.covers(code_point)]
        lines.append(
            f"{font.spec.name:<{width}}  {len(wanted) - len(missing)}/{len(wanted)}  "
            f"{', '.join(missing) or '-'}"
        )
    union = [code_point for code_point in wanted if not any(font.covers(code_point) for font in fonts)]
    lines.append(
        f"{'any font':<{width}}  {len(wanted) - len(union)}/{len(wanted)}  {', '.join(union) or '-'}"
    )
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--count", type=int, default=COUNT, help=f"renderings per code point (default {COUNT})"
    )
    parser.add_argument("--out", type=Path, default=ROOT / "work" / "synthetic" / "kana")
    parser.add_argument("--fonts-dir", type=Path, default=ROOT / "cache" / "fonts")
    parser.add_argument("--only", default=None, help="comma-separated code points, e.g. U+1B001,U+1B002")
    parser.add_argument("--refresh-fonts", action="store_true", help="download the fonts again")
    parser.add_argument("--no-sample-sheet", action="store_true")
    parser.add_argument(
        "--require-full-coverage",
        action="store_true",
        help="exit non-zero when a code point has no font at all",
    )
    args = parser.parse_args(argv)

    wanted = code_points()
    if args.only:
        wanted = [code_point.strip().upper() for code_point in args.only.split(",") if code_point.strip()]
        unknown = [code_point for code_point in wanted if code_point not in code_points()]
        if unknown:
            parser.error(f"not in {VOCAB.name}: {', '.join(unknown)}")

    paths = ensure_fonts(FONTS, args.fonts_dir, refresh=args.refresh_fonts)
    manifest_path = write_font_manifest(FONTS, paths)
    for spec, path in zip(FONTS, paths, strict=True):
        print(f"{spec.name} {spec.version} {spec.licence} {path.name} {sha256_file(path)}")
    print(f"fonts recorded in {manifest_path}")

    fonts = [open_font(spec, path) for spec, path in zip(FONTS, paths, strict=True)]
    for line in coverage_table(fonts, wanted):
        print(line)

    rows = render_dataset(fonts, wanted, args.out, seed=args.seed, count=args.count)
    print(f"{len(rows)} renderings, {args.count} per code point per font, seed {args.seed} -> {args.out}")
    print(f"manifest {args.out / 'manifest.parquet'}")
    if not args.no_sample_sheet:
        print(f"sample sheet {write_sample_sheet(args.out, wanted, fonts)}")

    union = [code_point for code_point in wanted if not any(font.covers(code_point) for font in fonts)]
    if union:
        print(f"no font covers {', '.join(union)}")
    return 1 if union and args.require_full_coverage else 0


if __name__ == "__main__":
    raise SystemExit(main())
