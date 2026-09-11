# T30 Synthetic hentaigana renderings

Goal: rendered examples of every hentaigana code point for bootstrapping the 字母 classifier.

Read first: `data/vocab/hentaigana.tsv`, `data/sources/ninjal-hentaigana.yaml`,
https://github.com/notofonts/hentaigana.

Inputs: NINJAL変体仮名フォント (Apache-2.0), Noto Serif Hentaigana (OFL 1.1), both downloaded by the
script into `cache/fonts/`, never committed.

Outputs
- `scripts/render_hentaigana.py` → `work/synthetic/kana/<code point>/<n>.png`, 200 per code point
  per font, with augmentation: stroke width, slant, elastic distortion, ink blots, paper texture,
  blur, rotation ±8°; a `manifest.parquet` (path, code point, font, parameters).
- Renderings are marked `synthetic` and never enter counts of the dataset.

Tests: rendering of three code points with one font in `tmp_path`.

Acceptance: 287 code points rendered with both fonts; sample sheet image attached to the pull
request.

Size: small. Depends on: nothing.
