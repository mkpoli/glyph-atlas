# 0014 Drawn forms: a private-use code point and a drawing for a shape nothing else names

Date: 2026-11-17
Status: proposed

## The problem

A 字体 is named by a code point, a variation sequence of the IVD or an ideographic description
sequence (decision 0013). A cursive form written in one stroke has none of the three. 事 is written
in the corpus as a zigzag over a wide loop with a tail, close to る, in 4,203 of the 8,347 glyphs of
its forms clustering (revision 582865e3b2b68c53), and in a shape close to 方 in 42 more.
Neither has a code point or a registered figure, and neither is built from components a description
could list. The Forms tool and the form bar could name neither.

## The decision

A form of this kind is a **drawn form**: one code point of Supplementary Private Use Area-A, given by
`data/vocab/drawn-forms.yaml`, with a drawing.

| What | Where |
| --- | --- |
| The table: code point, the grapheme the form writes, its script, a description in words, the crops it was drawn from | `data/vocab/drawn-forms.yaml` |
| The drawing: one filled path in a 1000-unit em | `data/vocab/drawn-forms/<code point>.svg` |
| The brush strokes the drawing is made from | `data/vocab/drawn-forms/<code point>.strokes.json`, `scripts/draw_form.py` |
| The font, the stylesheet and the Worker's table | `scripts/build_drawn_forms.py` |

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
- **How it is set.** A font built from the drawings maps each code point to its glyph, and
  `ScriptText` sets a drawn form in it, in the colour of the script the table gives. The font's
  `unicode-range` is the table's code points, so only a page that shows one downloads it.
- **Where the membership is held.** The character layer (`refs`) reads the table and holds each
  drawn form as a character of its family, so the local review service, a clustering and a reference
  load list it like any other member. The reference objects and the forms clustering published before
  the table do not hold it: until they are published again, the Worker adds a family's drawn forms to
  its card and its palette, answers a drawn form's card from its grapheme's, and files the `graphemes`
  row first made for one under that grapheme.

A drawing is an editorial normalisation: the centre lines and stroke widths of one crop the table
names, drawn again as even brush strokes, under that crop's licence. It labels a group of crops and is
never shown as an occurrence.

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

- The two forms of 事: the clusters of forms revision 582865e3b2b68c53 on
  https://glyphatlas.org/ja/forms/U+4E8B, read 2026-11-16, grouped into the two forms by mkpoli on the
  same day. The cluster keys are in `data/vocab/drawn-forms.yaml`.
- `docs/decisions/0013-finer-forms.md`, `docs/design/form-model.md` (the `pua` scheme and its
  namespace and version).
