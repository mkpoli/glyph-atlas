# T30 Synthetic hentaigana renderings

Goal: rendered examples of hentaigana code points for bootstrapping the 字母 classifier.

Read first: `data/vocab/hentaigana.tsv`, `data/sources/ninjal-hentaigana.yaml`,
https://github.com/notofonts/hentaigana.

Inputs: NINJAL変体仮名フォント 1.01 (Apache-2.0; covers U+1B001 to U+1B11E) and Noto Serif
Hentaigana (OFL 1.1), downloaded by the script into `cache/fonts/` with their checksums recorded;
never committed.

Outputs
- `scripts/render_hentaigana.py --seed 0` → `work/synthetic/kana/<code point>/<font>-<n>.png`,
  200 per code point per font at 128×128, with augmentation ranges in the script header: stroke
  width by morphology ±2 px, shear ±0.2, elastic distortion, ink blots, paper texture, Gaussian
  blur σ ≤ 1.5, rotation ±8°; `work/synthetic/kana/manifest.parquet` (path, code point, font,
  parameters, seed).
- The script reads each font's cmap and renders only code points the font has; a missing glyph
  is an error, never a `.notdef` box. Coverage per font is printed.
- Renderings carry `synthetic=true` in the manifest and never enter dataset counts.

Tests: rendering of three code points with one font in `tmp_path`; a code point absent from a
font raises.

Acceptance: every code point in `hentaigana.tsv` rendered with at least one font, the coverage
table and a sample sheet attached to the pull request.

Size: small. Depends on: nothing.
