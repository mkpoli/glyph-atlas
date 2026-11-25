# 0014 Drawn forms: a private-use code point and a drawing for a shape nothing else names

Date: 2026-11-17
Status: proposed

## The problem

A 字体 is named by a code point, a variation sequence of the IVD or an ideographic description
sequence (decision 0013). A cursive form written in one stroke has none of the three. 事 is written
in the corpus as a zigzag over a wide loop with a tail, close to る, in 4,203 of the 8,347 glyphs of
its forms clustering (revision 582865e3b2b68c53), and in a shape close to 方 in 42 more. A third
cursive shape was supplied as a drawing on 2026-11-18 and has no cluster yet. None of the three has
an encoded code point or a registered figure, and none is built from components a description could
list. The Forms tool and the form bar could name none of them.

## The decision

A form of this kind is a **drawn form**: one code point of Supplementary Private Use Area-A, given by
`data/vocab/drawn-forms.yaml`, with a drawing.

| What | Where |
| --- | --- |
| The table: code point, the grapheme the form writes, its script, a description in words, the crops a form was found in, where it has any, and who drew it | `data/vocab/drawn-forms.yaml` |
| The drawings, a sans and a serif one: each one filled path in a 1000-unit em | `data/vocab/drawn-forms/<code point>.sans.svg`, `.serif.svg` |
| The picture the drawings were traced from | `data/vocab/drawn-forms/<code point>.source.png`, `scripts/trace_form.py` |
| The fonts, the stylesheet and the Worker's table | `scripts/build_drawn_forms.py` |

- **Name.** A drawn form is a `pua` representation (docs/design/form-model.md): its value is the code
  point, its namespace `glyph-atlas` and its version the table's. `representation.typed` gives that
  for a code point of the table and refuses every other private-use character, as before.
- **Code points.** They start at U+F0000. One is never given to another form, and a form keeps its
  code point when it is redrawn; the table's version changes only if that is broken. The area is clear
  of the Hanyang 구결자 (U+F67E–U+F77C), which are crops' characters.
- **Whose form.** A drawn form is a character of the site's own table and a member of the family of
  the grapheme it writes, as 𬻿 is of 也's. A crop written as one is filed under it inside that family,
  and a cluster or glyph the Forms tool decides as one stays in its family. It is offered only for
  the grapheme the table names.
- **Where it shows.** The grapheme's card lists its drawn forms among its 字体 with their crops
  counted, each has a character page under that grapheme, the form bar offers them with the family's
  characters, and the Forms tool's palette offers them after the family's characters.
- **How it is set.** Two fonts built from the drawings map each code point to its glyph, a sans cut and
  a serif one. `ScriptText` sets a drawn form in the sans cut, and in the serif cut inside text set in
  a serif face, such as the form chips, in the colour of the script the table gives. A font's
  `unicode-range` is the table's code points, so only a page that shows one downloads it.
- **Where the membership is held.** The character layer (`refs`) reads the table and holds each
  drawn form as a character of its family, so the local review service, a clustering and a reference
  load list it like any other member. The reference objects and the forms clustering published before
  the table do not hold it: until they are published again, the Worker adds a family's drawn forms to
  its card and its palette, answers a drawn form's card from its grapheme's, and files the `graphemes`
  row first made for one under that grapheme.

A drawing is an editorial normalisation of the form, made by hand: it labels a group of crops and is
never shown as an occurrence. A form supplied before its crops are grouped stays a name without a
count until a clustering gives it one. The three forms of 事 were drawn by mkpoli and traced from the
pictures kept beside them. For text sizes, the trace widens each figure by 30%. The two cuts share a scale
fitted to an 880-unit box with a five-unit allowance on each edge. F0001 and F0002 use that allowance
for ink. For the heavier る-shaped F0000, the serif outline expands by 1.16 units and the sans outline
contracts by 2.32 before widening. These adjustments bring its effective stroke width in each cut to
the mean of the other two forms. We estimate stroke width as twice the filled area divided by the
total perimeter of the finished outline, including the inner edges of loops. Strokes built from knots, plain traces of a crop and a crop's centre lines
drawn again were each tried first and set aside.

## Limits

- Outside the site the code point means nothing: copied text, an export or a citation carries a
  private-use character that only the table explains. The namespace and version stored with the
  representation say which table.
- Text the browser sets outside `ScriptText`, such as a native tooltip or the window title, shows the
  reader's own font, which has no glyph there.
- A drawn form has no 構形 (decision 0012): nothing states its components.
- It is filed and counted in the category of the grapheme it writes, and a kanji whose only other
  members are drawn forms keeps the forms family of its variants.
- A family's stored visual analysis is reused only while its members are those it was made on, so a
  family given a drawn form is analysed again at the next reference export.
- A family's counts are kept at the edge an hour, so a card can show a drawn form without its count
  for that long after the Worker first lists it.

## Sources

- The three forms of 事: the clusters of forms revision 582865e3b2b68c53 on
  https://glyphatlas.org/ja/forms/U+4E8B, read 2026-11-16, grouped into two forms by mkpoli on the
  same day, and a third supplied as a drawing on 2026-11-18. The cluster keys and the third's
  provenance are in `data/vocab/drawn-forms.yaml`.
- The two cursive forms of 時 (U+F0003, U+F0004): the clusters of forms revision 582865e3b2b68c53 on
  https://glyphatlas.org/ja/forms/U+6642, read and grouped by mkpoli on 2026-11-24. Cluster keys and
  provenance are in `data/vocab/drawn-forms.yaml`.
- `docs/decisions/0013-finer-forms.md`, `docs/design/form-model.md` (the `pua` scheme and its
  namespace and version).
