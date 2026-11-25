# Written forms: crops that are written as a variant of their label

`atlas review written-forms` (`src/glyph_atlas/review/written_forms.py`) finds the crops filed under
one character that are written as a variant of it, and `atlas review written-form-sheets` draws them
book by book. This page records what it found on 2026-11-24. Nothing was relabelled or loaded to the
site.

## Inputs

- The similar-crop index `work/similar/0a82edb62d862c2b` (classifier-penultimate-v1, built
  2026-09-27, 503,013 crops), 498,667 of them held by the site under a character.
- Each crop's character, book and suspect mark from the local copy of the site's database
  (`work/atlas-v3/out/atlas.sqlite`, read 2026-11-24), and its box from the page bundles
  (`work/atlas-v3/out/pages`).
- The variant tables behind `refs.variants`: 111 pairs stated by two tables or more have 15 crops
  under each character.

## The rule

Crops that are one picture filed under both characters are left out (HI Lab files some so). The
rest of a pair's crops are divided in two by shape, starting from the two labels. A pair counts when
at most one crop filed under the variant, or 3% of them, falls on the label's side, and at most half
of the label's crops fall on the variant's. A crop of the label on the variant's side is written as
the variant when it is near the variant's crops there (mean similarity to the 10 nearest of 0.8 or
more, and 0.1 more than to the label's) and its box holds one whole character. Glyphs cut by their
source (HI Lab, 徒然草) have no box on a page and are not judged by one: 219 of the crops found.

## Result

78 of the 111 pairs are told apart. Of 5,337 crops of a label on the variant's side, 389 pass the
rule. Of the collected crops among them every one with a picture was read by eye but for eight of
the 104 of 時, which the sheets cut off.

| label → variant | found | of them collected | by eye |
| --- | ---: | ---: | --- |
| 時 → 时 | 183 | 104 | 时 |
| 候 → 𠋫 | 117 | 2 | of one kind; whether it is 𠋫 was not settled |
| 也 → 𬻿 | 60 | 55 | two kinds: one book's are 𬻿, two books' another cursive 也 |
| 万 → 萬 | 9 | 1 | the collected one 萬 |
| 座 → 坐 | 6 | 5 | 坐 |
| 切 → 功 | 4 | 0 | 切; the pair is a look-alike |
| 歌 → 哥, 条 → 條 | 3, 2 | 0 | no picture on disk |
| 煙 → 烟 | 2 | 2 | 烟 |
| 十 → 拾 | 1 | 0 | 拾 |
| 陣 → 陳, 師 → 帥 | 1, 1 | 1, 0 | not settled |

Read by eye alone: no set of crops with a recorded written form was at hand. The variant's crops are
no such set either. Of HI Lab's, 616 of 653 时, all 223 𠋫 and 578 of 579 𬻿 stood under 時, 候 and 也
when the index was built and were filed under the variant since.

Not told apart, 33 pairs: 国/國, 島/嶋, 当/當, 数/數 and 声/聲 among them, where the split follows
the hand or the source, and 悪/惡, 経/經 and 雁/鴈, where most crops of the variant are a
picture filed under both.

## What was tried before

A ranking along the direction between the two labels' means put 嶋 at the head of 嶋 → 島, since
most crops filed under 島 are written 嶋, and strokes, kana and joined cuts among the highest of
every pair. A crop nearer the variant's crops than its own label's failed the same way for 時: its
label's nearest crops are 时 too.
