# 0008 Forms and model votes in the new layout

Date: 2026-10-20

Two kinds of per-crop data are left in the old database: the model votes the crop inspector shows as
suggestions, and the forms tool's clustering of corpus glyphs with the forms people name for them.
Both are large and mostly static, so they go to R2, and D1 keeps only what people change.

## Model votes

- One object per page, `v3/votes/<page>.json.gz`, maps each slot with a reading to
  `{cut, models, visual, context}` (`scripts/atlas3/votes.py`). `models` is the `codes` entry (field
  `models`) whose engines the votes name by place; repeated fields are left out.
- The reading was made on cut `cut`. The reader answers it only while `crops.cut` is that cut, so a
  re-cut crop shows no reading until a publish reads its new image.
- `publish.py` rewrites a page's object when a catalogue carrying `visual` and `context` changes one
  of its readings, or a crop on it is retired. The cutover load (`crop_facts.py`) writes the live
  readings whose image is the crop's current cut and drops the others with a count.
- No D1 table holds them. A suggestions request reads one object, which the edge and the isolate keep
  five minutes.

## Forms

- `form_families` and `form_clusters` are D1 tables the Worker writes. A cluster's number comes from
  the publisher's mirror (`codes`, field `form_cluster`, key the cluster's id), so it stays the same
  across loads; `key` keeps the id URLs and `form_decisions` use.
- Which glyphs a cluster holds is an immutable R2 list, `v3/forms/<lists>/<cluster>.json.gz`: each
  glyph's crop, rank, similarity, tile and split groups, by rank. `lists` names the folder, from the
  clustering's revision and a digest of its lists, and `form_families.lists` points to it.
- A cluster decision is one row: `form`, `issue`, `character`, `written_family` and `decision` on
  `form_clusters`. Every glyph without its own decision takes it; `mixed` gives them no issue.
- `form_glyphs` holds only glyphs with their own decision, and glyphs outside the clustering kept for
  a decision made on them. A glyph's form is its `form_glyphs` row's, else its cluster's.
- The family's `count`, `assigned` and `rejected` are recomputed from these when a decision is made.

## Cost

From the dry run on 20 October 2026 (110,292 collected crops' readings, the 582865e3 clustering of
1,330,492 glyphs in 12,817 clusters and 6,204 families):

- Votes: 416 page objects, 9.6 MB; for all 527,651 collected crops on 5,604 pages, about 46 MB.
- Forms lists: 12,817 objects, 69.5 MB; the largest 362 KB, the median 744 bytes.
- D1: 3.1 MB and about 70,000 rows written (families and clusters with their indexes). `form_glyphs`
  grows with the glyphs people decide one by one.

## Not covered yet

- Which cluster a crop is in. Lists are read by cluster, so a record's form and a glyph decision,
  which name crops, need a map from crop to cluster: `crops.cluster` (1.33M rows written per
  clustering) or a per-page R2 object.
- A corpus glyph's character following its decided form (`corpus_follow`, `form_bases`).
- Republishing the clustering: `export_forms_cloudflare.py` still writes the old database.
