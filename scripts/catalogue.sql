-- The tables of a publication catalogue: what export_cloudflare.py and export_cloudflare_corpus.py
-- write and scripts/atlas3/publish.py reads. Column names, order and types are those the site's first
-- database held, so a catalogue reads the same to every tool. A catalogue holds plain rows: no
-- trigger, and no table or index only the site's database used.

-- A collected crop (`origin` 'local'), a corpus glyph's review overlay ('corpus') or a crop taken off
-- the site ('retired'). `data` is the crop's whole record as the site serves it.
CREATE TABLE IF NOT EXISTS units (
 id TEXT PRIMARY KEY, origin TEXT NOT NULL, character TEXT,
 reading TEXT, family TEXT, visual_group TEXT, production TEXT NOT NULL,
 category TEXT NOT NULL, state TEXT NOT NULL, revision INTEGER NOT NULL,
 quiz INTEGER NOT NULL, priority INTEGER NOT NULL, shuffle INTEGER NOT NULL,
 data TEXT NOT NULL, snapshot TEXT NOT NULL, context TEXT NOT NULL, visual TEXT NOT NULL,
 document TEXT, style TEXT NOT NULL DEFAULT 'unassessed', written_form TEXT,
 -- How the letterforms were made, which orders a run's occurrences: type and other machine faces 2,
 -- the hand and the woodblock 0, anything else 1.
 hand_order INTEGER GENERATED ALWAYS AS (CASE
  WHEN production='printed/type' OR production LIKE 'printed/type/%' OR production IN ('printed/phototype','printed/digital','typewritten') THEN 2
  WHEN production IN ('handwritten','inscribed','printed','printed/woodblock','printed/engraved','printed/lithograph','printed/stencil')
    OR production LIKE 'inscribed/%' OR style IN ('cursive','running') THEN 0
  ELSE 1 END) VIRTUAL);

-- A corpus glyph: its record is `size` bytes at `offset` in the pack `object`. `label` is the
-- character its record is shown with while it has no written character.
CREATE TABLE IF NOT EXISTS corpus_units (
 id TEXT PRIMARY KEY, character TEXT, family TEXT, visual_group TEXT,
 shuffle INTEGER NOT NULL, object TEXT NOT NULL, offset INTEGER NOT NULL, size INTEGER NOT NULL,
 production TEXT NOT NULL DEFAULT 'unknown', named INTEGER NOT NULL DEFAULT 0, style TEXT NOT NULL DEFAULT 'unassessed',
 document TEXT,
 hand_order INTEGER GENERATED ALWAYS AS (CASE
  WHEN production='printed/type' OR production LIKE 'printed/type/%' OR production IN ('printed/phototype','printed/digital','typewritten') THEN 2
  WHEN production IN ('handwritten','inscribed','printed','printed/woodblock','printed/engraved','printed/lithograph','printed/stencil')
    OR production LIKE 'inscribed/%' OR style IN ('cursive','running') THEN 0
  ELSE 1 END) VIRTUAL,
 label TEXT);

-- The documents a corpus export read, with their titles.
CREATE TABLE IF NOT EXISTS corpus_documents (document TEXT PRIMARY KEY, n INTEGER NOT NULL, title TEXT) WITHOUT ROWID;

-- An image: `size` bytes at `offset` in the pack `object`.
CREATE TABLE IF NOT EXISTS media (
 key TEXT PRIMARY KEY, object TEXT NOT NULL, offset INTEGER NOT NULL,
 size INTEGER NOT NULL, content_type TEXT NOT NULL);

-- A page the catalogue's crops were cut from: its facts and its crops in order.
CREATE TABLE IF NOT EXISTS pages (id TEXT PRIMARY KEY, document TEXT NOT NULL, data TEXT NOT NULL) WITHOUT ROWID;

-- A run of two or three neighbouring glyphs, keyed by its first. `text` is its glyphs' characters,
-- `graphemes` the same folded to each character's family head, `hand_order` and `shuffle` its first
-- glyph's, `document` its first glyph's.
CREATE TABLE IF NOT EXISTS unit_ngrams (
 first TEXT NOT NULL, size INTEGER NOT NULL, second TEXT NOT NULL, third TEXT, text TEXT, document TEXT,
 vertical INTEGER NOT NULL DEFAULT 1, hand_order INTEGER NOT NULL DEFAULT 1, shuffle INTEGER NOT NULL DEFAULT 0, graphemes TEXT,
 PRIMARY KEY (first, size)) WITHOUT ROWID;

-- The character cards and the names a search finds them by.
CREATE TABLE IF NOT EXISTS characters (
 code_point TEXT PRIMARY KEY, character TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
 data TEXT NOT NULL, detail TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS aliases (
 query TEXT NOT NULL, code_point TEXT NOT NULL, rank INTEGER NOT NULL,
 PRIMARY KEY(query,code_point));

-- Site-wide values, each a JSON document.
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);

-- A document's date on one axis, as the resolver settled it from its claims.
CREATE TABLE IF NOT EXISTS document_dating (
 document TEXT NOT NULL, axis TEXT NOT NULL CHECK (axis IN ('witness','composed')),
 kind TEXT NOT NULL CHECK (kind IN ('composed','copied','colophon','annotated','printed','edition','exemplar','produced','other')),
 start INTEGER, end INTEGER, precision TEXT NOT NULL, qualifier TEXT, uncertain INTEGER NOT NULL DEFAULT 0,
 text TEXT NOT NULL, label TEXT NOT NULL, status TEXT NOT NULL CHECK (status IN ('single','agreed','disputed')),
 claims TEXT NOT NULL, calendar TEXT NOT NULL, conversion TEXT, source TEXT NOT NULL, resolver TEXT NOT NULL,
 export TEXT NOT NULL, PRIMARY KEY (document, axis), CHECK (start IS NULL OR end IS NULL OR start <= end)) WITHOUT ROWID;

-- A crop id retired in favour of another.
CREATE TABLE IF NOT EXISTS unit_redirects (id TEXT PRIMARY KEY, target TEXT NOT NULL) WITHOUT ROWID;

-- The 異体字 graph, component substitutions, the writers of each grapheme, and words with their spellings.
CREATE TABLE IF NOT EXISTS character_variants (
 a TEXT NOT NULL, b TEXT NOT NULL, relation TEXT NOT NULL, source TEXT NOT NULL, detail TEXT NOT NULL,
 written INTEGER NOT NULL, widens INTEGER NOT NULL,
 PRIMARY KEY(a,b,relation,source)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS component_variants (
 a TEXT NOT NULL, b TEXT NOT NULL, count INTEGER NOT NULL, pairs TEXT NOT NULL,
 PRIMARY KEY(a,b)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS grapheme_writers (
 writer TEXT NOT NULL, grapheme TEXT NOT NULL, own INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(writer,grapheme)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS words (
 id TEXT PRIMARY KEY, language TEXT NOT NULL, reading TEXT NOT NULL, class TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS word_spellings (
 word TEXT NOT NULL, spelling TEXT NOT NULL, source TEXT NOT NULL, locator TEXT NOT NULL,
 tier TEXT NOT NULL, word_by TEXT NOT NULL, basis TEXT NOT NULL, related TEXT NOT NULL, statement TEXT NOT NULL,
 documents INTEGER, occurrences INTEGER,
 PRIMARY KEY(word,spelling,source,locator)) WITHOUT ROWID;
