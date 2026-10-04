"""Draw characters from their Ideographic Description Sequences, and measure how well that works.

    uv run scripts/compose_ids.py draw ⿰亻哥 > 亻哥.svg
    uv run scripts/compose_ids.py bench --sample 400

`draw` writes one SVG. `bench` redraws encoded characters from their own sequences with the
character itself barred as a host, and scores each against the font's real glyph by the overlap of
their ink (intersection over union, at 96 px). It reports the composer beside the plainest way to
compose, each operand's own glyph squeezed into its share, on the same characters.
"""

from __future__ import annotations

import json
import random

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
    scores: dict[str, list[float]] = {"composer": [], "plain": []}
    for char, seq in chosen:
        glyph = composer.font.glyph(char)
        real = compose.raster([compose.Placed(glyph, glyph.box, glyph.box)], size)
        drawn = []
        for item in (composer, plain):
            item.exclude = {char}
            try:
                drawn.append(compose.raster(item.compose(seq), size))
            except LookupError:
                break
        if len(drawn) < 2:
            continue
        for name, mask in zip(scores, drawn):
            scores[name].append(float((mask & real).sum()) / max(float((mask | real).sum()), 1.0))
    print(json.dumps({"characters": len(scores["composer"]), "pool": len(pool),
                      **{name: round(sum(v) / len(v), 3) for name, v in scores.items()}}))


if __name__ == "__main__":
    app()
