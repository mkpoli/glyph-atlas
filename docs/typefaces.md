# Typefaces

The site sets characters in one of two faces, Atlas Sans and Atlas Serif. Each draws every Han
ideograph of Unicode 18 (the 98,701 of `data/vocab/characters.tsv`, through Extension J), the
radicals, strokes, kana, hangul syllables and CJK punctuation, in one design per range:

| Face | Sources, in the order a character is looked up |
|---|---|
| Atlas Sans | Noto Sans CJK JP 2.004 at weight 400, Plangothic P1 and P2 V2.9.5795 |
| Atlas Serif | Noto Serif CJK JP 2.003 at weight 400, Jigmo, Jigmo2 and Jigmo3 20250912 |

The serif sources lack one ideograph, 𫠞 U+2B81E, which Atlas Serif takes from Plangothic P1. The
files hold outlines and no shaping tables, so the conjoining hangul jamo are left to a font that
joins them.

`scripts/build_atlas_fonts.py` builds both and pins every source by SHA-256. Each face holds
115,787 characters.

## Files

A face is cut into small WOFF2 files, and a page downloads the files of the characters it shows:

- `c0` to `c7`: the 2,048 Han characters the corpus writes most, 256 to a file in that order
  (`data/vocab/typeface-core.txt`). The first file covers 59.6% of the corpus's Han crops and the
  eight together 94.8%.
- `u0` to `u2`: the Han characters of the interface's own text that the core list lacks
  (`data/vocab/typeface-interface.txt`), so a page of interface text set in a face downloads a few
  files.
- `<hhh>`: the remaining characters of the 256 code points U+hhh00 to U+hhhFF.
- `bold/`: the `c`, `u`, kana (`030`) and full-width (`0ff`) files at weight 700, from the Noto
  fonts alone. A browser draws bold text from the faces declared bold and from no other, so the
  style sheet declares every file without a bold cut at weight 700 too: a character outside the
  bold files is drawn in its regular weight.
- `figures/<hhh>`: the registered variation sequences of those 256 code points, with the characters
  they vary, in the family `Atlas Sans Figures` or `Atlas Serif Figures`. Text with no variation
  selector never downloads one. `figures/<hhh>.json` lists the sequences the file draws, by base
  character, as selectors counted from U+E0100.

| Face | Files | Size | Median file | Largest file |
|---|---|---|---|---|
| Atlas Sans, characters | 468 | 18.1 MB | 41 KB | 59 KB |
| Atlas Sans, bold | 13 | 0.5 MB | 40 KB | 48 KB |
| Atlas Sans, sequences | 250 | 2.6 MB | 2 KB | 39 KB |
| Atlas Serif, characters | 468 | 28.5 MB | 63 KB | 99 KB |
| Atlas Serif, bold | 13 | 0.6 MB | 53 KB | 63 KB |
| Atlas Serif, sequences | 329 | 7.1 MB | 5 KB | 149 KB |

`index.json` lists each file's characters as ranges, its size and hash, and the sequences a figures
file draws by collection. `sans.css` and `serif.css` declare a face's files with those ranges
(13 KB in Brotli), which is how a browser knows the file a character is in; `sans-figures.css` and
`serif-figures.css` do the same for the sequences.

## Registered sequences

The IVD of 2026-08-03 registers 39,509 sequences. What each face draws of them:

| Collection | Registered | Atlas Sans | Atlas Serif |
|---|---|---|---|
| Adobe-Japan1 | 14,684 | 14,682 | 14,684 |
| CAAPH | 198 | 0 | 198 |
| Hanyo-Denshi | 13,045 | 0 | 13,045 |
| KRName | 36 | 0 | 36 |
| MSARG | 154 | 0 | 154 |
| Moji_Joho | 11,392 | 0 | 11,385 |

Atlas Sans draws the Adobe-Japan1 sequences alone: no sans design of the other collections exists
among the sources. In Atlas Serif the Adobe-Japan1 sequences are Noto's and the rest are Jigmo's,
which are GlyphWiki's drawings of the registered figures and can differ from a registrant's own
glyph. The Moji_Joho collection's reference font, IPAmj明朝, stays a face of its own
(`MJ Figures`, `apps/review/static/fonts/README.md`): the IPA Font License and the SIL Open Font
License do not combine in one file.

A sequence is set in the first font that maps it (`apps/review/src/lib/figures.js`): MJ Figures,
then the figure file of the reader's face, then under Atlas Sans the serif one. A sequence none of
them maps is drawn as its character.

## Serving

A build is a folder named by the hash of its sources, the core list and the build's format, and no
file changes under that name. It is uploaded once to R2 and named in
`apps/cloudflare/src/typefaceVersions.ts`; the Worker serves it under `/atlas/fonts/<version>/`,
cached for good, and a font request does not count toward the request limit.

```sh
uv run scripts/build_atlas_fonts.py                 # cache/atlas-fonts/<version>/
rclone copy cache/atlas-fonts/<version> r2:glyph-atlas/fonts/<version> --exclude '*.part'
```

An upload writes one R2 object per file: 2127 for a whole build.

## Small seal

The Atlas faces hold no small seal character. Sealie 1.3.1 Medium with the correction of U+3EA9E that follows that release, its outlines enlarged 1.3 times to
the size of a Han character beside it, draws the Small Seal block
(U+3D000–U+3FC3F) as a build of its own, stored and served the same way under its own version
(`SEAL_FONT_VERSIONS`, newest first): `seal/s.woff2` for the script legend's sample and `seal/000.woff2` onwards, 64
characters to a file, with the licence as `OFL.txt`. `apps/review/src/seal-font.css` declares the
files and is part of the app's style sheet; `apps/review/static/fonts/README.md` records the source.

```sh
uv run scripts/build_seal_font.py                   # cache/seal-font/<version>/
rclone copy cache/seal-font/<version> r2:glyph-atlas/fonts/<version>
```

An upload writes 179 R2 objects.

## Licences

- Noto Sans CJK JP (© 2014-2021 Adobe) and Noto Serif CJK JP (© 2017-2024 Adobe): SIL Open Font
  License 1.1, with the Reserved Font Name `Source`, which neither family name uses.
- Plangothic P1 and P2: Copyright (c) 2024 Fitzgerald P. Köeingsegg, SIL Open Font License 1.1, no
  Reserved Font Name declared.
- Jigmo, Jigmo2 and Jigmo3: Koichi Kamichi, from the GlyphWiki contributors' glyphs, CC0 1.0.

Atlas Sans and Atlas Serif are distributed under the SIL Open Font License 1.1. A build carries
the licence as `OFL.txt` and every source's copyright notice as `NOTICE.txt`, and each file's name
table has the notices of the sources it draws from.
