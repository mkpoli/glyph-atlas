# Consensus relabel: crops that look like another character

`atlas review consensus-relabel` (`src/glyph_atlas/review/consensus_relabel.py`) relabels a crop
when its nearest crops in the similar-crop index and the classifier agree that it shows another
character, and its box holds one whole character. This page records what it was measured against on
2026-10-04. Nothing was applied.

## Inputs

- The similar-crop index `work/similar/0a82edb62d862c2b` (classifier-penultimate-v1, 503,013 crops:
  58,549 `ar:`, 47,168 `hk:`, 51,144 `ex:`, 1,883 `hmj:`, 321,830 `hi:`, 22,439 `codh-omt:`), with
  the labels and pages of the latest catalogue exports in place of the index's own (13,116 labels
  changed since the index was built on 2026-09-27).
- The site's review export (`/atlas/reviews.json`, 1,222 events read on 2026-10-04): 1,105 crops
  with a last verdict, of which 645 confirmed, 207 wrong character, 114 bad crop, 89 joined
  characters, 38 wrong reading and 12 blank. The index holds 136 of the wrong-character reports
  (70 `ar:`, 38 `hk:`, 28 `hi:`) and 287 of the confirmed crops.
- The assertion ledger (`/atlas/ledger.json`, 3,900 claims): 33 subjects of observed or editorial
  claims, protected with the reviewed crops.

## The rule

The ten nearest crops by cosine, leaving out crops of the crop's own page (the Ainu records and the
detector cut the same glyph twice), vote. A neighbour agrees with the crop's label when it is the same
character, the same kana in another script, a variant of it, or a kanji written for that kana (者
and は). The best other label must carry at least `share` of the ten with mean similarity
`similarity`, from crops of at least two documents, with at most `own` neighbours carrying the
crop's label. The classifier must give the other label `p_target` or more, and `margin` more than
the crop's label. The box then has to hold one whole character, judged against the median box of its
line: not blank (under 2% dark pixels), not two characters (1.4 times the median along the line and
1.4 times longer that way than across, or 2.2 times the median area), not a part of one (under 0.35
of the median area or side).

| setting | share | similarity | own | p_target | margin |
| --- | ---: | ---: | ---: | ---: | ---: |
| strict | 0.9 | 0.90 | 0 | 0.9 | 0.5 |
| default | 0.8 | 0.85 | 1 | 0.8 | 0.5 |
| loose | 0.7 | 0.75 | 2 | 0.5 | 0.3 |

Two tests were added after the first sample of 48 default relabels, which held 8 plain errors: a
stroke filed as シ, ミ or し among strokes filed the same way in one book (now two documents are
required), and kanji read for their kana shape (八→ハ, は→者, ツ→川). The rule was otherwise tuned on
the reviews only.

## Against the reviews

Every reviewed crop is judged with the label the reviewer saw, protection lifted. Over the whole
index:

| setting | relabels of reviewed crops | to the reviewer's character | other character | confirmed crops changed | bad crop | joined | wrong reading |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| strict | 32 | 27 | 1 | 1 of 287 | 1 | 0 | 2 |
| default | 50 | 43 | 1 | 1 of 287 | 2 | 0 | 3 |
| loose | 69 | 52 | 1 | 1 of 287 | 11 | 0 | 4 |

At `default` the rule finds 43 of the 136 wrong-character reports: 25 of 70 `ar:`, 18 of 38 `hk:` and
none of the 28 `hi:`. `atlas review consensus-relabel --reviews` on `work/ainu-characters-20260929-shift`
gives the same 43, 1, 1 and 2 for its `ar:` and `hk:` crops.

The neighbour vote alone (8 of 10 neighbours) named the reviewer's character for 73 of the 136, but
also relabelled 7 confirmed crops, 22 bad crops and 10 joined ones. The box test, run over every
reviewed crop the classifier read, marks 11 of 11 blank crops, 49 of 85 joined crops and 49 of 100
bad crops, and 30 of 192 confirmed crops (mostly large kanji in lines of kana). Among the crops that
pass the vote and the classifier at `default` it withholds 789 of 4,550 (252 blank, 321 joined, 216
part of a character). Half a kanji cut across its middle still passes it when its half reads as
another character (者 cut to 土, 輩 to 小); those are most of the bad crops `loose` relabels.

## Unreviewed crops

Relabels proposed among crops nobody reviewed, over the whole index (dry run, scripts over the same
functions):

| setting | `ar:` | `hk:` | `ex:` | `hi:` | `codh-omt:` | total |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| strict | 793 | 343 | 2 | 421 | 8 | 1,567 |
| default | 1,382 | 567 | 10 | 1,256 | 18 | 3,233 |
| loose | 2,344 | 924 | 23 | 3,673 | 52 | 7,016 |

On `work/ainu-characters-20260929-shift` the command proposes 812 / 1,440 / 2,459 relabels
(`ar:` 792 / 1,382 / 2,345, `hk:` 20 / 58 / 114). The `hk:` numbers are lower than the index-wide
ones because about 470 of those crops are placements the aligner rejected; the command never changes
a crop whose review state is not `machine`, as the block-shift repair does not.

## By eye

Of 48 random `default` relabels from the command's dry run on `work/ainu-characters-20260929-shift`
(contact sheet in the pull request), 30 are plainly right, 18 uncertain (cursive kanji, an
abbreviation such as 箇 written ケ, a dakuten the crop may lack) and none plainly wrong.

An earlier sample of 48 over the whole index, after the two added tests, held 34 crops of the
collection (30 right, 4 uncertain, none wrong) and 14 HI Lab glyphs (1 right, 11 uncertain, 2
wrong). HI Lab glyphs have no page or box in the corpus tables, so the box test cannot judge them,
and the rule found none of the 28 HI Lab wrong-character reports. Corpus glyphs are not relabelled
by the command, which works on review datasets.

## Running it

    atlas review consensus-relabel DATASET --out REPORT.json --dry-run \
      --protect PROTECT.txt --reviews reviews.json --export CATALOGUE.sqlite ...

`--apply` records each relabel as a model event with its evidence (the vote, the five nearest crops,
the classifier's reading and the setting) under `feedback_identity`. `--undo --apply` restores the
label, script and `feedback_identity` note every relabel of `neighbour-consensus-v1` replaced, except
on a crop a person reviewed or another pass relabelled since; `--undo` alone lists them. A crop named
in `--reviews` is protected like one named in `--protect`.
