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

The crops of a pair are divided in two by shape, starting from the two labels. A pair counts when
at most one crop filed under the variant, or 3% of them, falls on the label's side. A crop of the
label on the variant's side is written as the variant when it is near the variant's crops there
(mean similarity to the 10 nearest of 0.8 or more, and 0.1 more than to the label's) and its box
holds one whole character.

## Result

88 of the 111 pairs are told apart. Of 5,656 crops of a label on the variant's side, 447 are written
as it. Up to 22 of each pair's crops were read by eye where a picture was on disk; crops of 経, 悪,
雁 and 花 that HI Lab (くずし字データセット, 東京大学史料編纂所, CC BY 4.0) files under the 新字 are among them.

| label → variant | found | of them collected | by eye |
| --- | ---: | ---: | --- |
| 時 → 时 | 183 | 104 | 时; all 96 pictures drawn on the sheets |
| 候 → 𠋫 | 117 | 2 | of one kind; whether it is 𠋫 was not settled |
| 也 → 𬻿 | 60 | 55 | two kinds: one book's are 𬻿, two books' are another cursive 也 |
| 悪 → 惡 | 27 | 0 | 惡 |
| 経 → 經 | 12 | 0 | 經 |
| 万 → 萬 | 9 | 1 | the collected one 萬 |
| 雁 → 鴈 | 9 | 0 | 鴈 |
| 座 → 坐 | 6 | 5 | 坐 |
| 花 → 芲 | 6 | 0 | not settled |
| 切 → 功 | 4 | 0 | 切; the pair is a look-alike |
| 余 → 餘, 歌 → 哥, 伝 → 傳, 条 → 條 | 3, 3, 2, 2 | 0 | 餘 and 傳; the rest have no picture on disk |
| 煙 → 烟, 陣 → 陳, 十 → 拾, 師 → 帥 | 1 each | 1, 1, 0, 0 | 烟 and 拾; the other two not settled |

Not told apart, with the variant's crops that fall on the label's side: 国/國 (8 of 25), 島/嶋,
与/與, 当/當, 数/數, 声/聲 and 17 others. There the split follows the hand or the source.

## What was tried before

A ranking along the direction between the two labels' means put 嶋 at the head of 嶋 → 島, since
most crops filed under 島 are written 嶋, and strokes, kana and joined cuts among the highest of
every pair. A crop nearer the variant's crops than its own label's failed the same way for 時: its
label's nearest crops are 时 too.

## Bad cuts

A crop's mean similarity to the 10 nearest crops of its own label tells bad cuts from sound ones.
Against the reviews in the local copy (the last of 2026-10-19), on collected crops with a box:

| below | confirmed (371) | bad crop (90) | joined (93) | blank (12) | wrong character (108) |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0.5 | 20 | 58 | 59 | 11 | 93 |
| 0.6 | 27 | 68 | 72 | 12 | 94 |
| 0.7 | 34 | 77 | 83 | 12 | 96 |

The thresholds were read off these same reviews, which are not a sample of the collection. Of the
148,304 pending collected crops of labels with 15 crops or more, 13,747 lie below 0.5 and 3,323 of
them carry no suspect mark on the site. Of 60 of those drawn at random about four in five are
strokes, joined cuts or another character; from 0.5 to 0.6 about half are sound, and from 0.6 most.
