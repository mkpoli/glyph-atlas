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
| Registered figure | a variation sequence of the IVD | 突 `U+7A81 U+E0103` (Moji_Joho, MJ030283) |
| Described form | an ideographic description sequence | `⿰月兊` |

A description names a part by its registered figure where the part's shape is what differs. The IVD
registers 遇 with one dot or with the 禺 whose left stroke rises above its bar, never both, so the
form with both is `⿺辶󠄁禺󠄁`: 辶 `U+8FB6 U+E0101` (Adobe-Japan1, CID+15403) and 禺 `U+79BA U+E0101`
(Adobe-Japan1, CID+13571). The IDS editor offers the figures the composer draws of a chosen part.

A described form or a registered figure is one of a 字種's 字体 while a crop of it carries the form: the claim
on the crop says so, and the card, the form bar and the bulk targets list it with the crops that have it
(`carried_forms`, migration 0020).

A CJK compatibility ideograph is a code point that stands for a unified ideograph, and normalisation
writes the unified ideograph in its place. It is no form, and is written as what it stands for:

1. the Moji_Joho sequence of the IVD, where the MJ文字情報一覧表 gives the compatibility code point
   as a figure's implementation (89 rows of `data/vocab/mj-kanji.tsv`: U+FA55 is `7A81_E0103`);
2. else its unified ideograph (U+F96F and U+F9A1 are 說 U+8AAA).

Unicode also gives each of the 1,002 a standardized variation sequence (U+F96F is `8AAA FE00`). It
is not used: the IVD registers none of them, and for the 913 with no registered figure a shape that
differs is better told by a description.

Twelve code points of the compatibility block are unified ideographs (U+FA0E, U+FA0F, U+FA11,
U+FA13, U+FA14, U+FA1F, U+FA21, U+FA23, U+FA24, U+FA27, U+FA28, U+FA29) and stay characters. The
test is the normalisation, never the block.

The 構形 is unchanged: a compatibility ideograph has had its unified ideograph's (decision 0012).

## Steps

1. **Cards and the form bar.** A card lists a compatibility ideograph among its 字体 only while crops
   are filed under it, and the form bar and bulk targets never offer one. 說 lists 説, 說, 说 and 𧧘.
2. **What a reviewer types.** A compatibility ideograph given as a crop's form is filed as what it
   stands for (`representation.typed`, in Python and in the Worker).
3. **The crops already filed.** In the copy of 2 November 2026, 85 crops sit under 18 compatibility
   code points (突 U+FA55 has 40); 14 of the 18 have a Moji_Joho sequence, and 練 U+F996, 蓮 U+F999,
   滛 U+FA98 and 杓 U+2F8DC have none. Each moves to its unified ideograph, with the sequence as its
   form where there is one. After it the condition of step 1 holds for no card.
4. **Search and addresses.** A compatibility ideograph typed into a search, a correction or a
   character's address is its unified ideograph.
5. **The families.** A compatibility ideograph leaves `members` in the character layer and stays a
   name its unified ideograph is found by.

## Limits

- A sequence is drawn by a font that maps it. The Moji_Joho sequences have the site's own font
  (`/fonts/mj/`), cut to the characters with more than one registered figure.
- A compatibility ideograph KS X 1001 encodes for a second reading (U+F96F, U+F9A1) has the unified
  ideograph's glyph and names no visible difference.
- A compatibility ideograph of another standard whose glyph differs (杓 U+2F8DC, from CNS 11643) has
  no registered figure here; its shape is kept by a description, or lost.
- A compatibility ideograph a reviewer gives as a crop's character, types into a search or names in
  an address is kept as written, by the Worker (`input.ts`) and by the local review store
  (`review/corpus_reviews.py`); step 4 changes that. The Forms tool takes a family member as a form
  without this reading, so it changes with step 5.

## Sources

- Unicode, Ideographic Variation Database, `IVD_Sequences.txt` of 2025-07-14,
  https://www.unicode.org/ivd/data/2025-07-14/IVD_Sequences.txt, read 2026-11-24: the sequences of
  遇 U+9047, 辶 U+8FB6 and 禺 U+79BA; their figures compared in IPAmj明朝 Ver.006.01 and Noto Sans CJK JP.
- Unicode, `StandardizedVariants.txt`, version 18.0.0 (2026-07-29),
  https://www.unicode.org/Public/UCD/latest/ucd/StandardizedVariants.txt, read 2026-11-15: 1,002
  rows named `CJK COMPATIBILITY IDEOGRAPH-`. `data/sources/unicode-ucd.yaml`.
- The twelve unified ideographs: the code points of U+F900–U+FAFF named as compatibility ideographs
  with no decomposition in the Unicode Character Database, the same twelve in versions 15.0.0 and
  16.0.0 as Python's `unicodedata` reads them.
- 文字情報基盤, MJ文字情報一覧表 Ver.006.02, `data/vocab/mj-kanji.tsv`: the columns `implemented_code_point`
  (実装したUCS) and `ivs`.
- The IDS composer the site's described forms are drawn with, and the author's other IDS tools, key
  a part as one character with at most one variation selector, and read a compatibility mapping as
  one code point standing for another; this decision follows them.
- `docs/decisions/0012-structure-layer.md`, `docs/design/form-model.md`.
