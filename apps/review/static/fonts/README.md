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
- Not covered: ゟ U+309F, ヿ U+30FF and the Unicode 18.0 digraphs U+1B123–U+1B126.

`NotoSerifHentaigana-Regular.ttf` is the second reference font, for the hentaigana blocks
(U+1B000–U+1B11E, U+1B120–U+1B122, read from its cmap).

- Family: `Noto Serif Hentaigana`, version 1.000, by the Noto Project Authors
  (<https://github.com/notofonts/hentaigana>), with the nipponia and Kazuhiro Yamada credits in its
  own name table.
- Licence: SIL Open Font License 1.1, the same `OFL.txt` in this directory.
- It does not cover U+1B11F, U+1B123–U+1B128, ゟ or ヿ, so `ReferenceGlyph` states the code point
  for those instead of drawing a glyph that would come out as a blank box.

The file was copied unchanged from the font project's build output; it carries no metadata or private
block, and no path from the machine it was built on.

## Interface font for chữ Hán-Nôm

`Plangothic-vi-Hani.woff2` draws the Nôm characters of the `vi-Hani` interface that lie beyond the
Basic Multilingual Plane (CJK Extensions B–G), which few installed fonts cover. It holds only those
characters: `scripts/build_vi_hani.py --font` subsets them from the catalogue, so the file is
rebuilt whenever `vi-Hani.json` gains one.

- Family: Plangothic (遍黑体) P1 and P2, version V2.9.5795, by Fitzgerald P. Köeingsegg
  (<https://github.com/Fitzgerald-Porthmouth-Koenigsegg/Plangothic-Project>), merged into one
  subset; the source files' SHA-256 are pinned in the script.
- Licence: SIL Open Font License 1.1, copied in `OFL-Plangothic.txt`. The font declares no Reserved
  Font Name.
