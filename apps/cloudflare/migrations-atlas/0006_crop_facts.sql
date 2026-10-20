-- Crop facts the publisher writes: the model votes the suggestions routes answer and the forms tool's
-- clustering of corpus glyphs, moved from the old database at the cutover (scripts/atlas3/crop_facts.py).
-- publish.py keeps the votes current after it. Crops are named by integer id.

-- What the crop models read in a crop's image (`visual`) and the nearby transcription's guesses
-- (`context`), made for the crop's cut `cut`; a crop re-cut since has no reading until the next publish.
-- `models` is the `codes` entry (field 'models', key the engines' JSON) whose engines the votes name by
-- place. The stored shape is scripts/atlas3/votes.py's. A crop with neither reading has no row.
CREATE TABLE votes(crop INTEGER PRIMARY KEY, cut INTEGER NOT NULL, models INTEGER, visual TEXT, context TEXT);

-- One grapheme family the forms tool clusters: `forms` lists the forms a decision may name.
CREATE TABLE form_families(code_point TEXT PRIMARY KEY, char TEXT NOT NULL, label TEXT NOT NULL,
  count INTEGER NOT NULL, cluster_count INTEGER NOT NULL, forms TEXT NOT NULL, assigned INTEGER NOT NULL,
  rejected INTEGER NOT NULL DEFAULT 0, revision TEXT NOT NULL);

-- A cluster, numbered here; `key` is its id in URLs and in `form_decisions.cluster`. Its representatives
-- are its first twelve glyphs by rank in `form_units`. `form`, `issue` and `decision` are its latest
-- cluster decision's.
CREATE TABLE form_clusters(id INTEGER PRIMARY KEY, key TEXT NOT NULL UNIQUE, family TEXT NOT NULL,
  label TEXT NOT NULL, count INTEGER NOT NULL, coherence REAL NOT NULL, shape_position INTEGER NOT NULL,
  size_position INTEGER NOT NULL, form TEXT, decision TEXT, issue TEXT);
CREATE INDEX form_cluster_family ON form_clusters(family, shape_position);

-- A corpus glyph in the clustering: its cluster and rank there, its tile's media key (`image`) and its
-- group for every "Split into k" (`split`, k = 2..8). The cluster's decision (`cluster_*`), the glyph's
-- own (`glyph_*`, when `glyph_set`) and the one that applies (`form`, `issue`, `issue_character`,
-- `written_family`) are materialised as decisions are made. A glyph kept only for a decision on it has
-- `clustered` 0, and no cluster when its cluster is not in the clustering.
CREATE TABLE form_units(crop INTEGER PRIMARY KEY, family TEXT NOT NULL, cluster INTEGER, rank INTEGER NOT NULL,
  similarity REAL NOT NULL, image TEXT, split TEXT NOT NULL,
  clustered INTEGER NOT NULL DEFAULT 1 CHECK(clustered IN (0, 1)),
  cluster_form TEXT, cluster_issue TEXT, cluster_character TEXT, cluster_family TEXT,
  glyph_set INTEGER NOT NULL DEFAULT 0, glyph_form TEXT, glyph_decision TEXT, glyph_issue TEXT,
  glyph_character TEXT, glyph_family TEXT,
  form TEXT, issue TEXT, issue_character TEXT, written_family TEXT);
CREATE INDEX form_unit_cluster ON form_units(cluster, rank);
CREATE INDEX form_unit_family ON form_units(family, form);
