# 0008 Forms and model votes in the new layout

Date: 2026-10-20

Two kinds of per-crop data are left in the old database: the model votes the crop inspector shows as
suggestions, and the forms tool's clustering of corpus glyphs with the forms people name for them.
Both are large and mostly static, so they go to R2, and D1 keeps what people change and what a query
filters on.

## Model votes

- One object per page, `v3/votes/<build>/<page>.json.gz`, maps each slot with a reading to
  `{cut, models, visual, context}` (`scripts/atlas3/votes.py`). `models` is the `codes` entry (field
  `models`) whose engines the votes name by place; repeated fields are left out.
- `<build>` is the layout's build id, the `codes` entry with field `build`. A rebuilt layout numbers
  crops afresh, so its votes start under a new folder and never answer another build's crop of the
  same number; the old folder goes with the old layout.
- The reading was made on cut `cut`. The reader answers it only while `crops.cut` is that cut, so a
  re-cut crop shows no reading until a publish reads its new image.
- `publish.py` rewrites a page's object when a catalogue carrying `visual` and `context` changes one
  of its readings, or a crop on it is retired. The cutover load (`crop_facts.py`) writes the live
  readings whose image is the crop's current cut, marked `loaded`, and drops the others with a
  count. A later load writes its `loaded` entries again from its copy; a publish's entries stay.
- Objects go up after the D1 part holding the model codes they name, and `out/votes/` takes each one
  as it goes up. A reader keeps an object five minutes in the edge cache and five in the isolate, so
  a replaced object can show its old readings for up to about ten minutes.
- No D1 table holds them. A suggestions request reads one object.

## Forms

- `form_families` and `form_clusters` are D1 tables the Worker writes. A cluster's number comes from
  the publisher's mirror (`codes`, field `form_cluster`, key the cluster's id), so it stays the same
  across loads; `key` keeps the id URLs and `form_decisions` use.
- Which glyphs a cluster holds is an R2 object named by its content, `v3/forms/<n>-<digest>.json.gz`:
  each glyph's crop, rank, similarity, tile, split groups, tone and image size, by rank.
  `form_clusters.list` names it. A load uploads only the lists whose content changed.
- Each family has an index object, `v3/forms/families/<code point>-<digest>.json.gz`, named by
  `form_families.list`: per cluster its list's name and its first 12 and last 24 glyphs with rank,
  tile, tone and image size. The family view reads this one object for representatives and least
  typical glyphs.
- `crops.cluster` (a column of the layout since decision record 0005) holds each crop's cluster
  number, null outside the clustering. It maps a crop to its cluster for a record's form, for checking
  a glyph decision against the clustering, and for joining claims to a family.
- A cluster decision is one row: `form`, `issue`, `character`, `written_family` and `decision` on
  `form_clusters`. Every glyph without its own decision takes it; `mixed` gives them no issue. The
  load stops when a cluster's decision is not its latest in the loaded `form_decisions`.
- `form_glyphs` holds only glyphs with their own glyph decision; a glyph's form is its `form_glyphs`
  row's, else its cluster's. A glyph outside the clustering with no decision of its own has no row.
- A decision names the list it was made on (`form_decisions.list`, whose name starts with the
  cluster's number) in place of copying the cluster's glyphs into `units` and `crops`.
- `form_state` holds `lists`, the load in place (it keys cached forms answers), and `loading`, set
  while a load sends its rows; a trigger refuses decisions meanwhile.
- The load recomputes each family's `count`, `assigned` and `rejected`. The Worker's recount after a
  decision is not built yet.
- The forms tool's form of a glyph is not `crops.form`. `crops.form` is a crop's written form, set by
  the claim that names it (decision record 0005); a forms decision never writes it.

## List retention

- An object under `v3/forms/` stays while a `form_clusters` or `form_families` row or any decision
  names it.
- Each load records the objects in the manifest `v3/forms/manifest.json.gz`, with the time a load
  first found one unnamed. A load at least two hours later (the forms answers are cached an hour,
  objects five minutes) deletes it.

## Cost

From the dry run on 20 October 2026 (110,292 collected crops' readings, the 582865e3 clustering of
1,330,492 glyphs in 12,817 clusters and 6,204 families):

- Votes: 416 page objects, 9.6 MB; for all 527,651 collected crops on 5,604 pages, about 46 MB.
- Forms lists: 12,817 objects, 70.0 MB; the largest 364 KB, the median 756 bytes. Family indexes:
  6,204 objects, 12.1 MB; the largest 84 KB, the median 366 bytes.
- D1 rows written by the first load: about 1.40M, of them 1,330,417 `crops` rows (`crops.cluster`,
  which no index covers) and about 70,000 for the families and clusters with their indexes. A later
  load writes only what changed. The first load waits for the owner's approval.
- D1 stored: 3.4 MB for the forms tables with their indexes, and 2.7 MB more in `crops`.

## Gaps against today's forms tool, and their designs

1. Crop to cluster: `crops.cluster`, filled by the load. Built.
2. The family view reading every cluster list: the family index object. Built.
3. Member previews (`tone`, `image_size`): carried in the list and index entries. Built. A member's
   document comes from `crops.book`.
4. Decisions copying the cluster's glyphs: `form_decisions.list`. Schema built; the Worker's decision
   route is not ported.
5. A corpus glyph's character following its decided form (`corpus_follow`, `form_bases`): the queue
   `corpus_follow` (decision record 0006) names the crops whose `crops.char` follows a decision, and a
   `form_bases(crop, char)` table keeps the character before the first follow so a withdrawn form
   restores it. Not built; it comes with the Worker's forms routes.
6. Republishing the clustering: `form_state` and the trigger are built. After the cutover the
   clustering load reads `work/forms/current` (`clusters.json`, `units.parquet`) in place of the old
   database's copies, and takes glyph-atlas-v3's own `form_decisions`, carrying each cluster decision
   by cluster key onto the cluster of the new clustering with that key and each glyph decision by crop.
   `export_forms_cloudflare.py`, which writes the old database, is then removed. Not built; next.
