# 0013 Finer forms: a variation sequence or a description, never a compatibility ideograph

Date: 2026-11-15
Status: proposed

## The problem

A 字種's 字体 row lists every code point of its family. 說 has six: 説, 說, 说, 𧧘, and the
compatibility ideographs U+F96F and U+F9A1, which Unicode encodes twice more for round trips with
KS X 1001 and which are drawn as 說. The row shows one form three times, and the 構形 chip names it
three times.

A form finer than a code point has had two names. 突 with its dot is the compatibility ideograph
U+FA55 and the variation sequence 突 U+7A81 U+E0103 (MJ030283), and crops are filed under the first
while the form picker offers the second.

## The decision

A 字体 is named at one of three levels, the broadest that tells it apart:

| Level | Name | Example |
| --- | --- | --- |
| Unified ideograph | its code point | 說 `U+8AAA`, 説 `U+8AAC`, 说 `U+8BF4` |
| Registered figure | a variation sequence | 突 `U+7A81 U+E0103` (Moji_Joho, MJ030283) |
| Described form | an ideographic description sequence | `⿰月兊` |

A CJK compatibility ideograph is a code point that stands for a unified ideograph, and normalisation
writes the unified ideograph in its place. It is no form. The figure it was encoded for is named by
a variation sequence of the unified ideograph:

1. the Moji_Joho sequence of the IVD, where the MJ文字情報一覧表 gives the compatibility code point
   as the figure's implementation (89 rows of `data/vocab/mj-kanji.tsv`: U+FA55 is `7A81_E0103`);
2. else Unicode's standardized variation sequence, which every one of the 1,002 has (U+F96F is
   `8AAA FE00`, U+F9A1 is `8AAA FE01`).

Twelve code points of the compatibility block are unified ideographs (U+FA0E, U+FA0F, U+FA11,
U+FA13, U+FA14, U+FA1F, U+FA21, U+FA23, U+FA24, U+FA27, U+FA28, U+FA29) and stay characters. The
test is the normalisation, never the block.

The 構形 is unchanged: a compatibility ideograph has had its unified ideograph's (decision 0012).

## Steps

1. **Cards and the form bar.** A card lists a compatibility ideograph among its 字体 only while crops
   are filed under it, and the form bar and bulk targets never offer one. 說 lists 説, 說, 说 and 𧧘.
2. **What a reviewer types.** A compatibility ideograph given as a crop's form or character is
   filed as its sequence under the unified ideograph.
3. **The crops already filed.** In the copy of 2 November 2026, 85 crops sit under 18 compatibility
   code points (突 U+FA55 has 40); 14 of the 18 have a Moji_Joho sequence, and 練 U+F996, 蓮 U+F999,
   滛 U+FA98 and 杓 U+2F8DC have the standardized one alone. Each moves to its unified ideograph
   with the sequence as its form. After it the condition of step 1 holds for no card.
4. **The families.** A compatibility ideograph leaves `members` in the character layer and stays a
   name its unified ideograph is found by.

## Limits

- A standardized sequence is drawn by a font that implements it, and many draw the base character.
  The Moji_Joho sequences have the site's own font (`/fonts/mj/`).
- A compatibility ideograph KS X 1001 encodes for a second reading (U+F96F, U+F9A1) has the unified
  ideograph's glyph, so its sequence names no visible difference.
- The local review store keeps a compatibility ideograph as the reviewer wrote it
  (`review/corpus_reviews.py`); step 2 changes that.

## Sources

- Unicode, `StandardizedVariants.txt`, version 18.0.0 (2026-07-29),
  https://www.unicode.org/Public/UCD/latest/ucd/StandardizedVariants.txt, read 2026-11-15: 1,002
  rows named `CJK COMPATIBILITY IDEOGRAPH-`. `data/sources/unicode-ucd.yaml`.
- The twelve unified ideographs: the code points of U+F900–U+FAFF named as compatibility ideographs
  with no decomposition in the Unicode Character Database 15.0.0, as Python's `unicodedata` reads it.
- 文字情報基盤, MJ文字情報一覧表 Ver.006.02, `data/vocab/mj-kanji.tsv`: the columns 実装したUCS and
  IVS.
- The IDS composer the site's described forms are drawn with, and the author's other IDS tools, key
  a part as one character with at most one variation selector, and read a compatibility mapping as
  one code point standing for another; this decision follows them.
- `docs/decisions/0012-structure-layer.md`, `docs/design/form-model.md`.
