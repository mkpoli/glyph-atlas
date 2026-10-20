# 0007 Character reference data on the new layout

Date: 2026-10-20

## The problem

Character pages and search read more than crops: each character's card, the 異体字 graph and its
derived tier, the forms that write another grapheme, words and their spellings, and the component
index. These sit in the old `glyph-atlas` database, which is deleted after the cutover (0006).

Almost all of it by size (the cards, the edges, the derived forms) is JSON a page reads whole by one
character. Only a small part is filtered, joined or sorted in SQL.

## The decision

**What a page reads by key is an R2 object** under `v3/reference/`, gzipped JSON, written by
`scripts/atlas3/reference.py` and read through the Worker's shard reader (`atlas3Reference.ts`):

- `char/<code points>.json.gz`, one per card: `data` and `detail`, the card's grapheme id, its 異体字
  edges from both ends, its derived forms with the component pairs their substitutions rest on, the
  graphemes it writes, and the writers of its family head with `shared` worked out. `ids` gives the
  grapheme id of each character the object names that has one, for the count shards. A character
  page reads this one object.
- `summary/<shard>.json.gz`: each card's `data` under its family head, in the shard of the head, so a
  family's members are one read and a reader takes only the families it asks for. 4,096 shards keep
  one near 41 KB of JSON. `graphemeMembers` reads its key's shard; a search list reads one per family
  it shows.
- `names.json.gz`: every card's Unicode name and how often the character occurs. A name that is a
  prefix and the hex of its code point (`CJK UNIFIED IDEOGRAPH-4E00`) is kept as the prefix and the
  code point, so a search for `4e00` or `U+4E00` still finds it; the Worker ranks what a name search
  finds by occurrences.
- `ids/<shard>.json.gz` (descriptions and regional shapes by character) and
  `list/graphemes/<n>.json.gz`, `list/ligatures/<n>.json.gz` (the card lists, 500 to an object, so a
  page of the list reads at most two).
- `components/<code point>.json.gz`, one per component: the characters that hold it, in tier, size,
  code point order, grouped by tier and size so each is written once per group, code points as steps.
  `components/names.json.gz` gives what a person may type for each component. The component search
  reads the names and one list per component it asks for (two to eight) and intersects them in the
  Worker: the rarest list's first 3,000 characters, each looked up in the others, as the D1 query did.

A shard is the first hex digits of the SHA-256 of its key as UTF-8 text: three of the family head's
code point(s) (`U+3042`) for a summary, two of the character itself (`好`) for the IDS.

**What a query filters, joins or sorts on stays in D1** (`migrations-atlas/0004_reference.sql`):
`component_variants` without its pairs (the IDS editor's substitutes, read by either end), `words` and
`word_spellings` (looked up by spelling and validated by claims), and the graphemes the cards name
that the layout lacks. The component index left D1: as 1.46M rows it was 30 MB and a first load's
1.46M rows written, and the search reads it by component, which a list per component serves.

- **Characters are `graphemes` ids** wherever crops meet them. A card's character or family head the
  layout lacks is numbered as `publish.py` numbers one, through `char_text`, and every grapheme takes
  its card's family head, which also fixes the heads of graphemes a publish created without one. A
  rebuild (`build.py --replace`) renumbers graphemes, so the objects are written again after it.
- **Crops are integer ids.** A visual group is keyed by its `visual_group` code, and its samples name
  crops found in the mirror and in `crops`; a sample whose crop is not published leaves its list.
- **Only differences are sent.** Rows are diffed against the local copy in SQL (the copy attached,
  `EXCEPT` both ways) and sent in stamped parts; each load's stamps carry its generation, so a load
  that returns to an earlier copy is sent again. A load D1 started is finished on the next run, by
  its stamps in D1, even when the local copy holds none of its parts.
  Objects are compared by hash with `manifest.json.gz`, which R2 holds beside them; changed ones are
  uploaded, and the paths the manifest lists and the translation lacks are deleted. The manifest
  lists every path a load may have written before it uploads, so a stopped load leaves nothing
  unlisted, and the deletions never depend on a folder on one machine.
- `representations` is written live by form claims and moves with the forms, outside this load.

## Consequences

- On the local copies of 19–20 October 2026, D1 holds 2,161 rows in 139 KB with indexes, and a first
  load writes 4,812 rows (510 for 255 new graphemes) in one part. R2 holds 132,539 objects, 341 MB
  gzipped: 117,448 cards (largest 14 KB), 4,096 summary shards (largest 10 KB gzipped), 10,630
  component lists and their names (largest, 一, 134 KB), 256 IDS shards, 108 list pages and the
  68 KB name list. The translation holds one object at a time: 186 MB resident at most, in 3 min 48 s.
- A character page reads one reference object; the IDS editor one shard and D1; a name search the
  name list and the family shards it shows; a component search the component names and two to
  eight lists. A search list reads one summary shard per family it shows: over the 234,391 alias
  queries, 99.9% read one shard and none more than three.
- The tools that export these sources (`export_character_variants.py`, `export_components_cloudflare.py`)
  still write the old database; they move to this load with the Worker.
