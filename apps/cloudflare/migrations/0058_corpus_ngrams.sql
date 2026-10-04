-- Pairs of corpus glyphs that follow each other on a line, as `scripts/export_corpus_ngrams.py`
-- finds them in the corpora: each glyph and the one after it (`first` names one pair), the text
-- their corpus transcribes, and whether the second stands below the first. Longer runs chain pairs
-- (`/atlas/runs`): rightwards by the key, leftwards through `corpus_ngram_second`; a run starts
-- from its rarest pair, read along `corpus_ngram_text` in key order.
CREATE TABLE IF NOT EXISTS corpus_ngrams (
 first TEXT PRIMARY KEY, second TEXT NOT NULL, text TEXT NOT NULL, vertical INTEGER NOT NULL
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS corpus_ngram_text ON corpus_ngrams(text, first);
CREATE INDEX IF NOT EXISTS corpus_ngram_second ON corpus_ngrams(second);
