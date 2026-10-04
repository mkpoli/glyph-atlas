-- The spellings of a word (decision 0004), loaded by scripts/export_character_variants.py from
-- data/vocab/words.tsv and data/vocab/word-spellings.tsv. `words` is one word per row, its id the
-- language, historical kana and word class (`ja/ばかり/副助詞`); `word_spellings` is one source statement
-- per row that a sequence of Han characters writes a word, with its tier and quoted statement, and for a
-- transcribers' 振り仮名 row the documents and occurrences data/vocab/ruby-spellings.tsv counts. A card
-- finds the words a character writes by `spelling`, then every spelling of those words by the key.
-- Citations are metadata `word_sources`. Nothing here widens a gallery or merges a grapheme.
CREATE TABLE IF NOT EXISTS words (
 id TEXT PRIMARY KEY, language TEXT NOT NULL, reading TEXT NOT NULL, class TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS word_spellings (
 word TEXT NOT NULL, spelling TEXT NOT NULL, source TEXT NOT NULL, locator TEXT NOT NULL,
 tier TEXT NOT NULL, word_by TEXT NOT NULL, basis TEXT NOT NULL, related TEXT NOT NULL, statement TEXT NOT NULL,
 documents INTEGER, occurrences INTEGER,
 PRIMARY KEY(word,spelling,source,locator)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS word_spelling_spelling ON word_spellings(spelling);
