"""Draw characters from their Ideographic Description Sequences, and measure how well that works.

    uv run scripts/compose_ids.py draw ⿰亻哥 > 亻哥.svg
    uv run scripts/compose_ids.py bench                 # every compound jōyō kanji
    uv run scripts/compose_ids.py bench --chars all --sample 400
    uv run scripts/compose_ids.py calibrate
    uv run --extra detector scripts/compose_ids.py learn

`draw` writes one SVG. `bench` redraws encoded characters from their own sequences with nothing
taken from the character itself, and scores each against the font's real glyph: the overlap of
their ink (intersection over union, at 96 px; mean and tenth percentile), how many come out with
their outline length within 15% of the real glyph's, and the ratio of their stroke widths (at
400 px). Characters that cannot be drawn are counted and listed. It reports the composer beside the
plainest way to compose, each operand's own glyph squeezed into its share, each scored on its own.
`calibrate` fits `compose.STEM_CURVE`, stroke width against density, on the font's own ideographs.
`learn` trains the layout `compose_layout` describes and caches it, for `draw` and `bench` to use.
"""

from __future__ import annotations

import json
import random

import numpy as np
import typer

from glyph_atlas import compose, compose_layout
from glyph_atlas.images import cache_root

app = typer.Typer(add_completion=False)


def _layout_path():
    return cache_root() / "compose" / compose_layout.LAYOUT_FILE


def _composer(**options) -> compose.Composer:
    """The composer, with the learned layout when `learn` has made it."""
    composer = compose.Composer(compose.Font(compose.font_file(), compose.fallback_files()), compose.japanese_sequences(), **options)
    if _layout_path().exists():
        composer.layout = compose_layout.LayoutModel.load(_layout_path())
    return composer


@app.command()
def draw(sequence: str) -> None:
    """Print the SVG of one sequence."""
    path = compose.svg_path(_composer().compose(sequence))
    print(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {compose.EM} {compose.EM}"><path d="{path}"/></svg>')


UNIHAN_URL = "https://www.unicode.org/Public/16.0.0/ucd/Unihan.zip"
UNIHAN_SHA256 = "b8f000df69de7828d21326a2ffea462b04bc7560022989f7cc704f10521ef3e0"


def joyo() -> list[str]:
    """The jōyō kanji, by Unihan 16.0's kJoyoKanji, fetched once into `<cache>/ucd/unihan/`."""
    import hashlib
    import io
    import zipfile

    import httpx

    from glyph_atlas.images import cache_root

    path = cache_root() / "ucd" / "unihan" / "Unihan-16.0.0.zip"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(httpx.get(UNIHAN_URL, follow_redirects=True, timeout=300).content)
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != UNIHAN_SHA256:
        raise ValueError(f"{path} does not match its pinned SHA-256.")
    text = zipfile.ZipFile(io.BytesIO(data)).read("Unihan_OtherMappings.txt").decode()
    return [chr(int(line.split("\t")[0].removeprefix("U+"), 16)) for line in text.splitlines() if "\tkJoyoKanji\t" in line]


@app.command()
def bench(chars: str = "joyo", sample: int = 0, seed: int = 3, size: int = 96) -> None:
    """Redraw encoded kanji from their own sequences with nothing taken from them, against their glyphs.

    `chars` is "joyo" (every jōyō kanji with a compound sequence, the targets' set) or "all"
    (every drawn kanji with one); `sample` draws that many at random from it, 0 takes them all.
    """
    composer = _composer()
    names = joyo() if chars == "joyo" else sorted(composer.sequences)
    pool = []
    for char in names:
        seq = composer.sequences.get(char)
        if seq is None or not composer.font.has(char):
            continue
        try:
            tree = compose.parse(seq)
        except ValueError:
            continue
        if isinstance(tree, tuple) and (tree[0] in compose.AXIS or tree[0] in compose.ENCLOSE):
            pool.append((char, seq))
    chosen = random.Random(seed).sample(pool, sample) if sample else pool
    plain = compose.Composer(composer.font, composer.sequences, hosted=False, weighted=False)
    unit = compose.EM / 400
    results: dict[str, dict] = {"composer": {"overlap": [], "failed": []}, "plain": {"overlap": [], "failed": []}}
    outlines, widths = [], []
    for char, seq in chosen:
        glyph = composer.font.glyph(char)
        real = [compose.Placed(glyph, glyph.box, glyph.box)]
        truth = compose.raster(real, size)
        for name, item in (("composer", composer), ("plain", plain)):
            item.exclude = {char}
            try:
                placed = item.compose(seq)
            except LookupError:
                results[name]["failed"].append(char)
                continue
            mask = compose.raster(placed, size)
            results[name]["overlap"].append(float((mask & truth).sum()) / max(float((mask | truth).sum()), 1.0))
            if name == "composer":
                made = sum(item.ink("", p.part) * (p.scale[0] + p.scale[1]) / 2 for p in placed)
                outlines.append(made / item.ink(char))
                drawn = compose.stems(compose.raster(placed, 400), unit)
                true = compose.stems(compose.raster(real, 400), unit)
                widths.append((drawn[0] / true[0], drawn[1] / true[1]))
    report = {"set": chars, "characters": len(chosen)}
    for name, result in results.items():
        overlap = np.array(result["overlap"])
        report[name] = {"drawn": len(overlap), "failed": len(result["failed"]),
                        "overlap_mean": round(float(overlap.mean()), 3), "overlap_p10": round(float(np.quantile(overlap, 0.1)), 3)}
    ratio = np.array(outlines)
    report["composer"]["outline_within_15pct"] = round(float(((ratio > 0.85) & (ratio < 1.15)).mean()), 3)
    report["composer"]["stroke_width_ratio"] = [round(float(np.nanmedian(np.array(widths)[:, axis])), 3) for axis in (0, 1)]
    report["composer"]["failed_characters"] = "".join(results["composer"]["failed"])
    print(json.dumps(report, ensure_ascii=False))


@app.command()
def learn(rows: str = "", workers: int = 3) -> None:
    """Learn where a whole character's two operands go, from every character that cuts cleanly
    (needs PyTorch). `rows` reuses examples saved by an earlier run, one JSON object per line."""
    composer = _composer()
    composer.layout = None
    if rows:
        with open(rows) as lines:
            examples = [json.loads(line) for line in lines]
        examples = [r for r in examples if r["op"] in compose_layout.SLOT and len(r["kids"]) == 2]
    else:
        examples = compose_layout.examples(composer, workers)
    model = compose_layout.train(composer, examples)
    _layout_path().parent.mkdir(parents=True, exist_ok=True)
    model.save(_layout_path())
    print(json.dumps({"examples": len(examples), "model": str(_layout_path())}))


@app.command()
def calibrate(sample: int = 1500, seed: int = 7) -> None:
    """Fit stroke width against density on the font's own ideographs, for `compose.STEM_CURVE`."""
    composer = _composer()
    pool = sorted(char for char in composer.sequences if composer.font.has(char))
    rows = []
    for char in random.Random(seed).sample(pool, min(sample, len(pool))):
        glyph = composer.font.glyph(char)
        widths = compose.stems(compose.raster([compose.Placed(glyph, glyph.box, glyph.box)], 400), compose.EM / 400)
        if all(np.isfinite(widths)):
            rows.append((composer.ink(char), *widths))
    data = np.array(rows)
    fits = [np.polyfit(data[:, 0] / 1000, data[:, axis], 1) for axis in (1, 2)]
    print(json.dumps({"characters": len(data), "STEM_CURVE": [[round(b, 2), round(a, 3)] for a, b in fits]}))


if __name__ == "__main__":
    app()
