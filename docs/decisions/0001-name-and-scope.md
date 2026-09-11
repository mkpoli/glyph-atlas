# 0001 Name and scope

Date: 2026-09-11

## Name

The repository is `glyph-atlas`, in full *Glyph Atlas: A Dataset of Character Forms in Pre-modern Books
of the Sinosphere*; in Japanese 漢字文化圏古典籍文字データセット「字形大圖譜」, written with 圖 and found
under 字形大図譜 as well. The slogan is Let's 集字!. The Python package is `glyph_atlas`. An atlas
is a bound set of plates that locate things; the dataset locates every character on its page and
keeps the plates addressable. The word already names the per-copy character catalogue in a sibling
project, so the vocabulary stays the same across tools. The repository was first published as
`kuzushiji-atlas`.

## Scope

In: pre-modern writing of the Sinosphere on paper, manuscript and printed, up to the 19th century:
Chinese characters and the scripts written with them in China, Korea, Japan and Vietnam (kana, hangul,
chữ Nôm and the like), with any character-level annotation that can be tied to an open page image;
characters, marks and ligatures. The first sources are Japanese.

Out: modern print (明治 typefaces are covered by NDL and CODH datasets already), 木簡 and 金石文
(different imaging and separate databases), glyph outlines and fonts.

## Licence

Data licence CC BY-SA 4.0, the licence of the two largest upstreams (CODH, みんなで翻刻). Code MIT.
CC BY 4.0 material composes into BY-SA; CC BY-SA 3.0 material composes into 4.0 as an adaptation;
non-commercial or permission-only material stays out of release builds.
