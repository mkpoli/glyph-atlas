# Neighbour agreement: crops that can be passed at a glance

`atlas review neighbour-agreement` (`src/glyph_atlas/review/neighbour_agreement.py`) counts, for
every crop in the similar-crop index, how many of its 20 nearest crops, nearest first, carry its
label before the first that does not. A crop whose 20 all agree is `agreed`. This page records what
the bar was measured against on 2026-11-20. Nothing was loaded to the site.

## Inputs

- The similar-crop index `work/similar/0a82edb62d862c2b` (classifier-penultimate-v1, built
  2026-09-27, 503,013 crops: 58,549 `ar:`, 47,168 `hk:`, 51,144 `ex:`, 1,883 `hmj:`, 321,830 `hi:`,
  22,439 `codh-omt:`). It predates most of the collection: of the 641,153 collected crops now
  pending on the site it holds 151,588.
- The local copy of the site's database (`work/atlas-v3/out/atlas.sqlite`, read 2026-11-20) for each
  crop's label today (36,099 labels in the index changed since it was built) and for the reviews:
  1,022 confirmations and 669 wrong reports.
- The migration mirror (`work/atlas-v3/out/mirror.sqlite`) for each crop's page.

## The rule

A crop's 20 nearest crops by cosine, leaving out crops of its own page. A neighbour agrees when it
is the same character, the same kana in another script, a variant of it, or a kanji written for
that kana (`consensus_relabel.agree`). The streak is the number of neighbours that agree before the
first that does not.

## Against the reviews

711 reviewed crops are in the index. A wrong-character report is tested under the label the crop
had when the index was built, where the reviewer's character disagrees with it; 108 qualify.

| least streak | confirmed (393) | wrong character (108) | bad crop (91) | joined (93) | blank (12) | wrong reading (14) |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 332 | 1 | 16 | 13 | 1 | 0 |
| 5 | 291 | 0 | 6 | 2 | 0 | 0 |
| 10 | 271 | 0 | 5 | 1 | 0 | 0 |
| 15 | 257 | 0 | 5 | 1 | 0 | 0 |
| 20 | 241 | 0 | 5 | 1 | 0 | 0 |

At the full streak 61% of the confirmed crops pass and no wrong-character crop does. The six wrong
crops that pass are crops of the right character cut badly (界, 家, 界, 附, る) and one き joined to
its neighbour: the bar is on the label, and a reviewer passing agreed crops still has to look at
the cut.

Reviewed crops are not a sample of the collection: reviewers were dealt uncertain crops first, and
nearly half of the reviewed crops in the index are wrong. The table gives how often each kind
passes, and no error rate of the agreed crops as a whole.

## What a streak does not see

- **The written form.** A first run with labels compared as plain text, before variants agreed,
  passed three wrong-character crops at a streak of 11 or more. Each was a reviewer writing the
  form on the page where the transcription had the usual one: 時 as 时, 懐 as 懷, 点 as 點. The
  neighbours carry the transcription's character too, so they agree. The streak confirms the 字種
  and never the 字体.
- **A short streak.** A streak of none holds 107 of the 108 wrong-character crops and 61 of the
  393 confirmed ones, and 59,436 crops of the index (12%). It is no mark of an error: rare
  characters and unusual hands break a streak as early as a wrong label does.
- **The nearest crop of the label alone.** The similarity to the nearest crop of the same label
  does not separate the two: at the value half the confirmed crops clear (0.959), 29 of 392 wrong
  crops clear it too, and raising it does not lower their share.

## Coverage

| | crops in the index | agreed | no streak |
| --- | ---: | ---: | ---: |
| all | 503,013 | 330,818 (66%) | 59,436 |
| `ar:` | 58,549 | 21,408 | 16,614 |
| `hk:` | 47,168 | 22,303 | 10,166 |
| `ex:` | 51,144 | 46,243 | 264 |
| `hmj:` | 1,883 | 1,161 | 168 |
| `hi:` | 321,830 | 219,508 | 32,021 |
| `codh-omt:` | 22,439 | 20,195 | 203 |

Of the 151,588 pending collected crops in the index, 87,028 (57%) are agreed, 39,362 have a
partial streak and 25,198 none.
