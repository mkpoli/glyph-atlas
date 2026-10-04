# 0004 Spellings of a word

Date: 2026-10-04

## The problem

A unit records which character was written. In running text a character also writes a word, and
different characters write the same word: 計, 許 and 斗 are all read ばかり, and 等, 抔 and 杯 are all
read など. The reasons differ. 等 and 抔 share a meaning; 斗 is said to be the cursive of 計, confused
with that of 升 and 斗. These characters are not one grapheme, and a gallery of 斗 must not fill with
計.

None of the existing relations can hold this. The 異体字 graph relates two characters, as variants or
as characters of like sound or meaning, and its written relations widen galleries (`refs.WRITTEN_FOR`
outside `refs.NOT_WIDENED`); it is generated from upstream tables, none of which pairs 斗 with 計. A grapheme family groups variants of one character (0003), and a shared reading
alone never makes one. A pair would also lose the word: 等 also reads ら and ひとしい, and 杯 also
means a cup.

## The decision

A word is a row of its own, and a spelling is linked to the word, not to another spelling.

- `data/vocab/words.tsv` lists the words, with an id of language, historical kana and word class
  (`ja/ばかり/副助詞`), so that the particle のみ and 飲み are two words.
- `data/vocab/word-spellings.tsv` holds one row per source statement that a sequence of Han
  characters writes a word. A spelling is a sequence: 而已 is one spelling. The row quotes the
  source, and says whether the source names the word or only gives a reading of it (`word_by`:
  `source` or `reading`). `basis` and `related` (斗, confused in shape with 計) are filled only where
  the source gives a reason. They record what the source says about characters; a form written for
  another stays a `substitutes-for` link between forms in the ledger.
- Two spellings are related only through a word they both write: `refs.spellings_of(word)` and
  `refs.words_of(spelling)`. No pair is stored, no row enters the 異体字 graph, a grapheme family or a
  gallery, and the lists do not chain through a second word.

The tiers are the ledger's (`docs/design/form-model.md`). A dictionary, a reading table and a forum
answer state something and are `attested`, each with its weakness in the citation. A transcriber's
振り仮名 is a person's reading of an occurrence, so it is `observed`, like a reviewer's reading of a
crop.

Transcribers' 振り仮名 come in through `data/vocab/ruby-spellings.tsv` (`atlas ruby-spellings`). A
spelling enters the hand table when that table attests it in at least two documents and the project
identifies the reading as the word. The row names the reading and spelling it counts, so なと can
count for など; the counts stay in the generated table and are joined when read.

## Consequences

- A character page can show the other spellings of each word a character writes, with their
  sources, and keep its own gallery exact.
- Several spellings rest on one weak kind of source so far: 斗 on a forum answer and the crowd
  transcriptions; 杯, 而, 矣, 尓, 迠 (まで), 凢 and 約 (およそ), 茲 (これ, この), 這 and 当 (この), 時
  (ころ), 猶 and 像 (ごとし), 所以 and 由縁 (ゆゑ) and 爲 (ため) on the transcriptions alone; 焉
  (すなはち) on Wiktionary alone. 字通, 精選版 日本国語大辞典 and 漢字ペディア, searched on
  コトバンク and kanjipedia.jp on 2026-10-04, state none of these; a 古文書 reading dictionary that
  states them adds a row beside these.
- A reading of one crop as a word (this 斗 reads ばかり) is a claim about that crop and belongs in the
  assertion ledger, which has no predicate for it yet.
- A matching or normalisation policy that uses words is a new named policy when something needs it;
  `align-v1` and `export-v1` are unchanged.
