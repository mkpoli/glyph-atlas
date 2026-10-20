-- Character reference data, which character pages and search filter and join on (decision record 0007).
-- A character named in a gallery or a widening join is a `graphemes` id; text keys stay where the
-- Worker only looks a row up by what was typed or by a Unicode code point. No table names a crop by
-- text id: the crops a visual group shows are integer ids in `visual_samples`.

-- A character's card: `data` the summary lists show, `detail` the whole card. `code_point` is the
-- URL key (`U+4EFF`, or several code points for a sequence); the character itself is `graphemes.text`.
CREATE TABLE characters(grapheme INTEGER PRIMARY KEY, code_point TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
  data TEXT NOT NULL, detail TEXT NOT NULL);

-- The crops each visual group of a card shows (`detail.visual_analysis.groups[grp]`): role 0 its
-- representatives, 1 its anchors, at `rank` in that list. The card's entries keep their other fields.
CREATE TABLE visual_samples(grapheme INTEGER NOT NULL, grp INTEGER NOT NULL, role INTEGER NOT NULL,
  rank INTEGER NOT NULL, crop INTEGER NOT NULL, PRIMARY KEY(grapheme, grp, role, rank)) WITHOUT ROWID;

-- The 異体字 graph between graphemes, read from either end; `widens` lets a gallery take in the other's crops.
CREATE TABLE character_variants(a INTEGER NOT NULL, b INTEGER NOT NULL, relation TEXT NOT NULL, source TEXT NOT NULL,
  detail TEXT NOT NULL, written INTEGER NOT NULL, widens INTEGER NOT NULL,
  PRIMARY KEY(a, b, relation, source)) WITHOUT ROWID;
CREATE INDEX character_variant_b ON character_variants(b);

-- Each grapheme's derived forms with their routes, as JSON.
CREATE TABLE character_derived(a INTEGER PRIMARY KEY, forms TEXT NOT NULL);

-- Forms that write a grapheme besides their own; `own` is 0 for a form with no character of its own.
CREATE TABLE grapheme_writers(writer INTEGER NOT NULL, grapheme INTEGER NOT NULL, own INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY(writer, grapheme)) WITHOUT ROWID;
CREATE INDEX grapheme_writer_grapheme ON grapheme_writers(grapheme);

-- Component substitutions with their count and the character pairs behind them. Components are
-- text: many are not characters of the atlas.
CREATE TABLE component_variants(a TEXT NOT NULL, b TEXT NOT NULL, count INTEGER NOT NULL, pairs TEXT NOT NULL,
  PRIMARY KEY(a, b)) WITHOUT ROWID;
CREATE INDEX component_variant_b ON component_variants(b);

-- Words and their spellings (decision record 0004). A spelling can be several characters, so it is text.
CREATE TABLE words(id TEXT PRIMARY KEY, language TEXT NOT NULL, reading TEXT NOT NULL, class TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE word_spellings(word TEXT NOT NULL, spelling TEXT NOT NULL, source TEXT NOT NULL, locator TEXT NOT NULL,
  tier TEXT NOT NULL, word_by TEXT NOT NULL, basis TEXT NOT NULL, related TEXT NOT NULL, statement TEXT NOT NULL,
  documents INTEGER, occurrences INTEGER, PRIMARY KEY(word, spelling, source, locator)) WITHOUT ROWID;
CREATE INDEX word_spelling_spelling ON word_spellings(spelling);

-- What a form is anchored to (`forms.anchor`); written by form claims, never changed or removed.
CREATE TABLE representations(id TEXT PRIMARY KEY,
  scheme TEXT NOT NULL CHECK (scheme IN ('unicode','ivs','ids','mj','glyphwiki','pua')),
  value TEXT NOT NULL, namespace TEXT, version TEXT,
  CHECK (scheme <> 'pua' OR (namespace IS NOT NULL AND version IS NOT NULL))) WITHOUT ROWID;
CREATE INDEX representation_value ON representations(value);
CREATE TRIGGER representation_kept BEFORE UPDATE ON representations BEGIN SELECT RAISE(ABORT, 'form_immutable'); END;
CREATE TRIGGER representation_kept_delete BEFORE DELETE ON representations BEGIN SELECT RAISE(ABORT, 'form_immutable'); END;

-- Ideographic description sequences per Unicode character, for the structure view and the IDS editor.
CREATE TABLE han_ids(char TEXT PRIMARY KEY, sequences TEXT NOT NULL, shapes TEXT) WITHOUT ROWID;
-- The component search: which code points hold a component, at which tier and size, and what a
-- person may type for each component.
CREATE TABLE han_components(component TEXT NOT NULL, tier INTEGER NOT NULL, size INTEGER NOT NULL,
  code_point TEXT NOT NULL, n INTEGER NOT NULL, direct INTEGER NOT NULL,
  PRIMARY KEY(component, tier, size, code_point)) WITHOUT ROWID;
CREATE TABLE han_component_names(query TEXT PRIMARY KEY, component TEXT NOT NULL, total INTEGER NOT NULL) WITHOUT ROWID;
