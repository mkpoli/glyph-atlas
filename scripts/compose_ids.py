"""Draw characters from their Ideographic Description Sequences, and measure how well that works.

    uv run scripts/compose_ids.py draw ⿰亻哥 > 亻哥.svg
    uv run scripts/compose_ids.py bench --sample 400
    uv run scripts/compose_ids.py calibrate

`draw` writes one SVG. `bench` redraws encoded characters from their own sequences with nothing
taken from the character itself, and scores each against the font's real glyph: the overlap of
their ink (intersection over union, at 96 px), and the ratio of their stroke widths (at 400 px). It
reports the composer beside the plainest way to compose, each operand's own glyph squeezed into its
share, on the same characters. `calibrate` fits `compose.STEM_CURVE`, stroke width against density,
on the font's own ideographs.
"""

from __future__ import annotations

import json
import random

import numpy as np
import typer

from glyph_atlas import compose

app = typer.Typer(add_completion=False)


def _composer(**options) -> compose.Composer:
    return compose.Composer(compose.Font(compose.font_file()), compose.japanese_sequences(), **options)


@app.command()
def draw(sequence: str) -> None:
    """Print the SVG of one sequence."""
    path = compose.svg_path(_composer().compose(sequence))
    print(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {compose.EM} {compose.EM}"><path d="{path}"/></svg>')


@app.command()
def bench(sample: int = 400, seed: int = 3, size: int = 96) -> None:
    """Leave-one-out overlap of redrawn encoded characters with their real glyphs."""
    composer = _composer()
    pool = []
    for char, seq in sorted(composer.sequences.items()):
        if composer.font.has(char):
            try:
                tree = compose.parse(seq)
            except ValueError:
                continue
            if isinstance(tree, tuple) and (tree[0] in compose.AXIS or tree[0] in compose.ENCLOSE):
                pool.append((char, seq))
    chosen = random.Random(seed).sample(pool, min(sample, len(pool)))
    plain = compose.Composer(composer.font, composer.sequences, hosted=False, weighted=False)
    unit = compose.EM / 400
    scores: dict[str, list[float]] = {"composer": [], "plain": []}
    widths: list[tuple[float, float]] = []
    for char, seq in chosen:
        glyph = composer.font.glyph(char)
        real = compose.raster([compose.Placed(glyph, glyph.box, glyph.box)], size)
        drawn = []
        for item in (composer, plain):
            item.exclude = {char}
            try:
                drawn.append(item.compose(seq))
            except LookupError:
                break
        if len(drawn) < 2:
            continue
        for name, placed in zip(scores, drawn):
            mask = compose.raster(placed, size)
            scores[name].append(float((mask & real).sum()) / max(float((mask | real).sum()), 1.0))
        made = compose.stems(compose.raster(drawn[0], 400), unit)
        true = compose.stems(compose.raster([compose.Placed(glyph, glyph.box, glyph.box)], 400), unit)
        widths.append((made[0] / true[0], made[1] / true[1]))
    ratios = np.array(widths)
    print(json.dumps({"characters": len(scores["composer"]), "pool": len(pool),
                      **{name: round(sum(v) / len(v), 3) for name, v in scores.items()},
                      "stroke_width_ratio": [round(float(np.nanmedian(ratios[:, axis])), 3) for axis in (0, 1)]}))


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
