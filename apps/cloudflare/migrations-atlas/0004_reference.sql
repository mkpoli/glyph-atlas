-- Character reference data that a Worker query filters, joins or sorts on (decision record 0007).
-- The cards, the 異体字 graph, derived forms and writers are R2 objects under `v3/reference/`, read by
-- key; these tables hold the rest.

-- Component substitutions and how many characters state each, for the IDS editor's substitutes, read
-- by either end. Components are text: many are not characters of the atlas. The character pairs
-- behind a substitution are in the cards' objects, where a derived list cites them.
CREATE TABLE component_variants(a TEXT NOT NULL, b TEXT NOT NULL, count INTEGER NOT NULL, PRIMARY KEY(a, b)) WITHOUT ROWID;
CREATE INDEX component_variant_b ON component_variants(b);

-- Words and their spellings (decision record 0004). A spelling can be several characters, so it is text.
CREATE TABLE words(id TEXT PRIMARY KEY, language TEXT NOT NULL, reading TEXT NOT NULL, class TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE word_spellings(word TEXT NOT NULL, spelling TEXT NOT NULL, source TEXT NOT NULL, locator TEXT NOT NULL,
  tier TEXT NOT NULL, word_by TEXT NOT NULL, basis TEXT NOT NULL, related TEXT NOT NULL, statement TEXT NOT NULL,
  documents INTEGER, occurrences INTEGER, PRIMARY KEY(word, spelling, source, locator)) WITHOUT ROWID;
CREATE INDEX word_spelling_spelling ON word_spellings(spelling);

-- The component search: which characters (by code point) hold a component, at which tier and size,
-- and what a person may type for each component.
CREATE TABLE han_components(component TEXT NOT NULL, tier INTEGER NOT NULL, size INTEGER NOT NULL,
  code_point INTEGER NOT NULL, n INTEGER NOT NULL, direct INTEGER NOT NULL,
  PRIMARY KEY(component, tier, size, code_point)) WITHOUT ROWID;
CREATE TABLE han_component_names(query TEXT PRIMARY KEY, component TEXT NOT NULL, total INTEGER NOT NULL) WITHOUT ROWID;

-- How often a character occurs, by code point as `names` files it, so a name search ranks the
-- characters it finds; a character absent here has none.
CREATE TABLE name_order(code_point INTEGER PRIMARY KEY, occurrence INTEGER NOT NULL);
