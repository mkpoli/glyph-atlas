# Fonts

Characters are set in Atlas Sans or Atlas Serif, whichever the reader chose; those two faces are
built and served apart from this directory (`docs/typefaces.md`). The files here draw what they
leave out. A font is a rendering aid and never evidence: an occurrence is a crop of ink, and no
glyph from a font is ever shown as one.

`GenZuiSans-Kana.woff2` is a subset of GenZui Sans for the kana that installed fonts rarely draw.

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

`tangut/` draws the Tangut labels.

- Family: `Noto Serif Tangut`, version 2.170, © 2022 The Noto Project Authors
  (<https://github.com/notofonts/tangut>), under the SIL Open Font License 1.1 as `tangut/OFL.txt`.
- Source: `NotoSerifTangut/unhinted/ttf/NotoSerifTangut-Regular.ttf` from the release asset
  `NotoSerifTangut-v2.170.zip`, SHA-256 `741ef7bca66790c51c5f2efea3c291f5a82cc2e3992d9413a020aec26dbd75b1`.
  `scripts/build_tangut_font.py` fetches it, checks the hash, and writes the files and
  `src/tangut-font.css`.
- Files: the script legend's sample 𗀀 U+17000 alone (648 B), then the other characters in code point
  order, about 256 to a file (up to 53 KB each, 1.1 MB in all), each named by its content hash. A page
  downloads only the files for the Tangut characters it shows.
- Coverage, read from the font's cmap: 6,146 characters, the iteration mark U+16FE0, U+17000–U+187F7
  of the Tangut block and U+18D00–U+18D08 of Tangut Supplement. The ideographs Unicode 17.0 and 18.0
  added (U+187F8–U+187FF, U+18D09–U+18D20) fall back to the reader's own fonts. The component blocks
  are left out: no label is a component.

Sealie draws the small seal characters. Its files are served from R2 beside the Atlas faces
(`docs/typefaces.md`), not from this directory.

- Family: `Sealie`, version 1.2.0 Regular (<https://github.com/iebb/sealie>), under the SIL Open Font
  License 1.1 with no Reserved Font Name; the build carries the release's `LICENSE-OFL.txt` as `OFL.txt`.
  Its licence header calls it "Panorama Gothic Seal Draft … Generated glyph drawings" and its name
  table "simplified geometric SVG source": the glyphs are generated drawings of the seal forms, not
  tracings of a print of 説文解字.
- Source: the release asset `Sealie-Regular.ttf`, SHA-256
  `5d949eaffa8e9b30f5a0264593cf7de22217b1cb0178bbea6ad7dfca946bc212`, which is the hash the release's
  `font-hashes.json` gives. `scripts/build_seal_font.py` fetches it, checks the hash, and writes the
  build, `src/seal-font.css` and the build's name in `apps/cloudflare/src/typefaceVersions.ts`.
- Files: the script legend's sample U+3DD16, the seal form of 篆, alone (2.5 KB), then the other
  characters in code point order, 64 to a file (44 to 111 KB each, 178 files and 12.2 MB in all). A page
  downloads only the files for the seal characters it shows.
- Coverage, read from the font's cmap: the 11,328 characters U+3D000–U+3FC3F, the whole Small Seal
  block of Unicode 18.0, none with an empty outline.

`drawn/` draws the drawn forms (docs/decisions/0014-drawn-forms.md).

- Families: `Glyph Atlas Drawn Forms` (sans) and `Glyph Atlas Drawn Forms Serif`, built by
  `scripts/build_drawn_forms.py` from the drawings in `data/vocab/drawn-forms/`, which are this
  project's own: mkpoli drew them, and `scripts/trace_form.py` traced the pictures.
- Coverage: the private-use code points of `data/vocab/drawn-forms.yaml`, from U+F0000, and nothing
  else. Each file is named by its content hash, and `src/drawn-forms.css` gives its `unicode-range`.

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

## Parts of the IDS editor

`Plangothic-ids-parts.woff2` and `NotoSansCJKjp-ids-strokes.woff2` draw the parts of the IDS parts
palette (`src/components/IdsParts.svelte`) that few installed fonts cover: the CJK strokes, CJK
Radicals Supplement, and the parts beyond the Basic Multilingual Plane (CJK Extensions B–J).
`scripts/build_ids_parts.py --font` subsets them from `static/palette/ids-parts.json`, so the files
are rebuilt with the palette. The page names them as `IDS Strokes` (U+31C0–U+31E3) and `IDS Parts`
(U+2E80–U+2EFF, U+31E4–U+31EF, U+20000–U+3FFFF), with ranges that do not overlap.

- `IDS Strokes`: Noto Sans CJK JP 2.004 (<https://github.com/notofonts/noto-cjk>, tag `Sans2.004`,
  the variable font instanced at weight 400), © 2014-2021 Adobe, under the SIL Open Font License 1.1
  with the notice in `OFL.txt`. Plangothic lacks these strokes. The Reserved Font Name is `Source`,
  which the family name does not use.
- `IDS Parts`: Plangothic P1 and P2 V2.9.5795, merged as for the vi-Hani font above, under the SIL
  Open Font License 1.1 as `OFL-Plangothic.txt`.
- Not covered: the rest of the Basic Multilingual Plane, Extension A included, comes from the
  reader's own CJK font, as for the vi-Hani font.

## Registered figures

`mj/` draws the ideographic variation sequences of the Moji_Joho collection that the form picker
offers (所󠄂 and 所󠄃 for 所): the code points `data/vocab/mj-kanji.tsv` gives more than one registered
figure, 5,057 of them with 11,382 sequences, in files of 40 code points. Beside each font, a JSON of
the same name lists its code points' sequences with their MJ figure names and marks the default
figure; `figures.json` gives each file's first and last code point. A page loads the file of the
character it shows and no other.

- Family: `MJ Figures`, a subset of IPAmj明朝 Ver.006.01 (文字情報技術促進協議会), from
  `ipamjm00601.zip`, SHA-256 `35494e0f2896f38b3f7369a8421a895cea6440a42c0a66ac95eab47d6ed25b68`,
  as distributed through 窓の杜 from <https://moji.or.jp/mojikiban/font/>.
- Licence: IPA Font License v1.0, copied in `mj/IPA_Font_License_Agreement_v1.0.txt`. The subset is
  a Derived Program under that licence and is licensed under it. The licence bars the original's
  name on a derived font, so every name record and file is called `MJ Figures`.
- To use the original font instead, point the font face in `src/lib/mjFigures.js` at `ipamjm.ttf`
  from the archive above. `scripts/build_mj_figures_font.py` rebuilds the subset: it fetches the
  archive, checks the hash and cuts the files.
