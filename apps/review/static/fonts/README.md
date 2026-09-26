# Reference font

`KureedoKata-Regular.woff2` is a webfont used for one thing: drawing the character labels of the
character layer — code points almost no installed font covers, such as 𛄧 U+1B127, 𛄨 U+1B128 and
𪜈 U+2A708. It is a rendering aid and never evidence: an occurrence is a crop of ink, and no glyph
from this font is ever shown as one.

- Family: `Kureedo Kata` (Kureedo カタ), an independent derivative of Klee One by Fontworks Inc.
- Licence: SIL Open Font License 1.1, copied in `OFL.txt`. The font's own name table carries the
  copyright notices of both projects and points at <https://github.com/mkpoli/kureedo>.
- Coverage for this interface, read from the file's `cmap`: kana (U+30A1–U+30FE, U+31F0–U+31FF,
  combining voiced marks U+3099–U+309C), the two alternate katakana U+1B127–U+1B128, and 𪜈 U+2A708.
  Code points outside that set fall back to the reader's own fonts, and the interface shows the code
  point beside a character whose glyph no font on the machine can draw.
- Not covered: ゟ U+309F and ヿ U+30FF.

`GenZuiSans-Kana.woff2` is the second reference font: a subset of GenZui Sans for the kana that
installed fonts rarely draw.

- Family: `GenZui Sans` (源萃ゴシック), version 0.103, by mkpoli (<https://github.com/mkpoli/GenZui>),
  derived from Noto Sans JP, Noto Sans Hentaigana, GenSeki Hentaigana Gothic and Noto Sans CJK JP.
  `OFL.txt` carries the copyright notices of GenZui's own `OFL.txt`.
- Licence: SIL Open Font License 1.1, the same `OFL.txt` in this directory, which also carries its
  notices. The only Reserved Font Name among its sources is Adobe's `Source`, which the family name
  does not use, so the subset keeps the name GenZui Sans.
- Source: `releases/sans-v0.103/GenZuiSans-Regular.ttf` at commit `705e182`, SHA-256
  `cba129c9c7584c0632335f8cb98ec486d37353d5cfcf38bfbf9ea93f593a03e8`.
  `scripts/build_genzui_subset.py` fetches it, checks the hash and writes this file.
- Coverage, read from the subset's cmap: 324 characters, every character the font has in Kana
  Extended-B, Kana Supplement, Kana Extended-A and Small Kana Extension (U+1AFF0–U+1B16F), including
  the hentaigana, U+1B11F and the Unicode 18.0 digraphs U+1B123–U+1B128, and the kana ligatures
  𪜈 U+2A708, 𬻿 U+2CEFF, 𬼀 U+2CF00 and 𬼂 U+2CF02.

## Handwriting fallbacks for the review target

The review round's target character is drawn in one hand: Kureedo Kata, then Klee One, then
LXGW WenKai TC, then LXGW WenKai, then GenZui Sans for the kana they lack.

- `Klee One` comes complete from the `@fontsource/klee-one` package (SIL Open Font License 1.1,
  © The Klee Project Authors, <https://github.com/fontworks-fonts/Klee>), split by `unicode-range`
  so a page loads only the files it needs.
- `fallback/` holds LXGW WenKai TC v1.522 (© The LXGW WenKai Project Authors,
  <https://github.com/lxgw/LxgwWenkaiTC>) and LXGW WenKai v1.522 (© LXGW,
  <https://github.com/lxgw/LxgwWenKai>, with glyphs from Klee One © The Klee Project Authors), both
  under the SIL Open Font License 1.1 as `fallback/wenkai-tc-OFL.txt` and `fallback/wenkai-OFL.txt`.
  They are cut down to the labels
  with crops in the corpus that Kureedo Kata and Klee One do not draw, listed with their crop
  counts in `fallback/characters.tsv`, and split into files of 120 characters, most frequent
  first. Neither release reserves a font name, so the subsets keep the family names.
  `scripts/build_fallback_fonts.py` rebuilds them from the release files, which it checks against
  pinned hashes; run it with `--labels work/corpus-index/chars.parquet` when the corpus grows.
- Labels no font here draws fall back to the reader's own fonts: 46 characters of CJK Extensions
  B–E, and 〱 〲 until the Kureedo Kata in this directory is a release that covers them.

## Interface font for chữ Hán-Nôm

`Plangothic-vi-Hani.woff2` draws the Nôm characters of the `vi-Hani` interface that lie beyond the
Basic Multilingual Plane (CJK Extensions B–G), which few installed fonts cover. It holds only those
characters: `scripts/build_vi_hani.py --font` subsets them from the catalogue, so the file is
rebuilt whenever `vi-Hani.json` gains one.

- Family: Plangothic (遍黑体) P1 and P2, version V2.9.5795, by Fitzgerald P. Köeingsegg
  (<https://github.com/Fitzgerald-Porthmouth-Koenigsegg/Plangothic-Project>), merged into one
  subset; the source files' SHA-256 are pinned in the script.
- Not covered: Extension A (㐌 đã, 㗂, 㨂, 䀡) and every other Han character in the Basic
  Multilingual Plane come from the reader's own CJK font. Extension A is part of GB 18030, the
  character set Chinese system fonts are built to cover, though that was not checked on each
  platform; a machine with no CJK font at all shows boxes for the whole interface, 㐌 included.
- Licence: SIL Open Font License 1.1, copied in `OFL-Plangothic.txt`. The font declares no Reserved
  Font Name.
