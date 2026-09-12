# Pilot annotation protocol

What a reviewer does with a pilot page, and the conventions the truth is held to. The protocol is
written for the calibration pages first and then for the held-out pages; the numbers the
pilot publishes are measured against annotations made this way.

## What is annotated

A page package holds the page image, the lines the transcription covers, and the units the pipeline
proposed where it has run. The reviewer annotates the page in full: every graphic unit of the
transcribed text, whether or not the detector found it.

A **unit** is one written character, one ligature, one mark or one gap. The label of a unit is the
diplomatic reading, with historical spelling kept: けふ stays けふ, ゐ stays ゐ, 國 stays 國.

## Boxes

- A box is the smallest rectangle, with sides parallel to the page, that holds all the ink of the
  unit. Nothing else is inside it: no part of a neighbouring character, no ruling line.
- A unit whose strokes touch its neighbour still gets its own box; the two boxes may overlap where
  the ink is continuous. A run of characters joined by continuous strokes is annotated as one unit
  per character and the run is marked as a 連綿 group, with one box around the group.
- Voicing marks (゛゜) get their own box, marked `voicing-mark`, and the unit they belong to carries
  `voicing` = `dakuten` or `handakuten`. A mark drawn as part of the character, without a separate
  stroke group, is not a separate unit.
- 踊り字 (ゝゞヽヾ〱〲) get a box and the units they repeat are recorded on them; the repeated text is
  not transcribed twice.
- A character that cannot be read gets `kind=unreadable` and no reading. A character that is not
  there gets nothing; white paper is not annotated.
- Ruby (振り仮名) is annotated: each ruby character gets a box and the role `ruby`, and the base
  character carries `meta.ruby_ids`.

## One unit or several

| On the page | Units |
| --- | --- |
| 合字 ゟ ヿ 𬼂 | one `ligature` unit, its reading the expansion (より, こと, なり) |
| 割書 with two or three columns | one unit per character, with the column in the line's markup; the columns are read right to left |
| A gap of a known number of characters (damage, a missing sheet) | one `gap` unit per character position |
| A 見せ消ち cancellation | the cancelled text as units with `role=cancelled` and the replacement with `role=inserted` |
| A 連綿 run | one unit per character, one 連綿 group |
| A character written over two lines (rare) | two units, one per part, both pointing at each other |

## 字母 and code points

- The reading is recorded first, from what the page shows. For kana the reading is the kana sound,
  not the modern spelling of the word.
- For a kana whose form is not the modern one (a hentaigana), the 字母 is the kanji the form derives
  from, chosen from the candidate list the interface offers. The candidates come from the Unicode
  names list and the MJ文字情報一覧表 変体仮名編 joined on the code point, so a form both tables carry
  appears once.
- When two or more code points share the 字母 and the shape does not decide between them, the unit is
  left `ambiguous` and the candidates are kept with their scores. Guessing is worse than leaving it
  open: the classifier is trained on `ambiguous` units with a partial-label loss.
- For kanji the code point is the character as written. 旧字 and 新字 are never merged: 國 is
  annotated as 國. Where the written form is a registered glyph variant, the MJ文字図形名 or the IVS
  is recorded on the unit.
- Katakana keeps `script=katakana`; the 字母 is recorded when it is known, as for the 子-shaped ネ.

## Flows

1. Open the next line from the queue. The interface posts a `timing` event when the line is opened
   and another when it is left; the reviewer does not record time by hand.
2. Accept a unit whose box and label are right (`a`). Reject one that is wrong (`x`); a rejected unit
   stays in the record.
3. Move or resize a box by dragging it. Split a unit at a point (`s`) or merge a selection (`m`).
   Create a unit for a character the detector missed (`c`, then draw the box).
4. Set the reading (`r`) and choose the 字母 from the candidate list (`j`).
5. Mark a selection as an unresolved group (`g`) when the segmentation cannot be decided from the
   page; the group keeps its units and the reviewer moves on.
6. Leave a note (`n`) for the adjudicator when something is unusual: a damaged glyph, a hand
   different from the rest of the page, a transcription that looks wrong.
7. Undo (`z`) reverses the last event of this client by posting a compensating review, so the log
   stays append-only.
8. Space moves to the next line.

## Known limits of the interface

- Undoing a split or a merge is partial. The review service refuses any change to a retired unit, so
  the outputs of a split can be retired but its inputs cannot be brought back: draw the unit again
  with `c` instead. This is a property of the store, not of the interface, and it is recorded here
  because the calibration flow leans on undo.
- A page whose image is not in the cache shows its IIIF URL and can be skipped; nothing is annotated
  on it and the page is counted as skipped rather than as annotated.
- Timing is posted when a line is opened and when it is left, with the dwell in milliseconds. Timing
  events are recorded but are not part of the undo stack.

## Calibration and held-out pages

- **Calibration** (the first two pages of each item) is annotated twice: two reviewers work the same
  page without seeing each other's decisions, and a third adjudicates every disagreement (box IoU
  below 0.7, a different reading, a different 字母, or a different number of units). The result is the
  truth the alignment costs and thresholds are tuned on, and the interface's timing events give the
  minutes per page.
- **Held-out** pages (the rest of the item) are annotated once, with the machine output hidden. The
  published precision and coverage come from these pages.
- A page that shares a printing block or a scan with another page goes to the same group, so two
  scans of one print never straddle the split.

## What the reviewer is not asked to do

- Transcribe a page the transcription does not cover: those pages are not in the pilot.
- Decide whether a unit may be redistributed: rights are recorded at ingest and never rest on an
  annotation.
- Correct the transcription of a whole book: a line whose text is wrong is rejected or noted, and the
  page is left for the adjudicator.
