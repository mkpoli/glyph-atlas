# 0002 Schema follow-ups after the first review

Date: 2026-09-11

The first schema was reviewed against the annotations the plan promises. Changes applied at once:
units may exist without a page (standalone crops) and state their granularity; classification has
its own status (unassessed, identified, ambiguous, unencoded, unidentified); a unit may carry several
variant references; iteration marks point at the units they repeat; a document may carry several
dates; the review log takes JSON values and names its target.

Changes accepted and deferred until the pilot shows the shape they need:

- Typed relationships between lines and units: ruby to its base span, 割書 container and subline
  order, reading-order edges, parent region. A role label on a line does not say which text it
  annotates.
- 見せ消ち as two linked records, the cancellation stroke and the affected text, with the textual
  state (cancelled, replacement, surviving) on the text side.
- Voicing marks with an attachment to their base unit and an assessment status (present, absent,
  illegible, unassessed), kept apart from voicing supplied by an editor.
- Text spans that point into a transcription revision by offsets, with the parsed markup mapped
  back to the raw text, so that many-to-many alignments between text and units can be recorded.
- Page images with their transforms: parent image, region on a spread, rotation and scaling, so that
  line crops taken from Honkoku-Lines can be mapped back to original pixels.
- A lifecycle state (active, superseded) separate from the review judgment (rejected, disputed), and
  segmentation as an event with input and output ids.
- Evaluation groups that join items sharing a printing block or a scan, since a split by item id
  does not separate two scans of one print.
- A transactional store for concurrent review, exporting the Parquet tables and the JSON Lines log.
