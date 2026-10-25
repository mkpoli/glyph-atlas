# 0010 Tokens: the word each stretch of text writes

Date: 2026-10-25
Status: proposed

## The problem

The atlas follows a written character in four directions. A crop is an image. `has_grapheme` says
which character it is. Alignment places it in a transcription line. A 振り仮名 over it gives a
reading (0009). The one thing it cannot say is which word the character helps write. The 落 of
救荒事冝's 村落 (crop `20x00015`) does not know that it is the second character of a noun, and the
noun cannot list its crops in other books.

Hand word rows (0004) and `writes_word` claims answer this one word at a time. They hold 20 words.
The transcriptions hold about 40.3 million characters of running text.

## What an analyser gives

The 通時コーパス project of the National Institute for Japanese Language and Linguistics publishes
a UniDic for each period of the language, built for the MeCab analyser (ver. 2025.12, implemented by
小木曽智信). The atlas uses 近世江戸口語UniDic and 近代文語UniDic, with 近世上方口語UniDic beside them.
They are light analysis packages, compiled for MeCab with no lexicon table. Vibrato builds its own
dictionaries from that table, so it cannot load these ones. The atlas therefore runs them through
MeCab (`fugashi`).

Each token gives a surface, a lemma (語彙素), the lemma's reading, a word class, a conjugation, a
kana form and a 語彙素ID. The ID is the same in every period dictionary: 村落 is 21520 in all three.
It can therefore key a word across books of different periods.

Measured on the clone at `be63dc2` over every entry, main text only, with 近世江戸口語UniDic:

| | Count |
| --- | ---: |
| Characters | 40,274,658 |
| Tokens | 30,792,685 |
| Distinct 語彙素 | 98,227 |
| of them, seen once | 15,079 |
| Distinct (surface, 語彙素) | 182,924 |

The speed was taken under a load average near 20; the sample ran at 94,000 a second.
| Characters parsed a second, one process | 49,280 |

### Rubies test the parse

A right ruby says how a transcriber read its base, so every ruby is a test item. The test parses
each line with every ruby base forced to one token, and passes a ruby when the token's kana match
it. Kana are compared on a key that folds historical spelling (やう and よう, くわ and か, medial
は行 and わ行), iteration marks, small kana and voicing marks. Transcribers often leave voicing
unmarked (てかた for 手形), so voicing is ignored.

| | Rubies | Share |
| --- | ---: | ---: |
| Tested: right, not overlapping another, 〻 readings left out | 1,390,696 | |
| The parse's entry matches | 622,953 | 44.8% |
| A different entry of the same characters matches (ruby choice) | 234,730 | 16.9% |
| Passed with ruby choice | 857,683 | 61.7% |
| No single entry spells the base | 234,552 | 16.9% |
| Entries exist, none has the reading | 298,741 | 21.5% |

The last row includes notes typed in ruby brackets (`[寫本]`, `也`, `法師品`) beside true 義訓. The
pass rate is therefore a floor. On a 150-entry sample, keeping all 30 candidates rather than 10 raised
the ruby-choice row by about two points.

The parse misses a ruby in three ways:
- **The parse cut the base:** 支給 became 支（サワル）and 給（タベル）. The dictionary has no 支給.
- **The parse chose another entry of the same characters:** 七夕 read しちせき where the ruby gives
  たなばた, and 昔話 read むかしばなし where it gives むかしがたり.
- **No entry has the reading:** 村落（ざいご）and 熱閙（にきはひ）. These are the 義訓, 熟字訓 and
  transcriber errors that 0009 calls `whole`.

## The decision

### What is parsed

- **The clone's lines are the text.** Each page of the みんなで翻刻 clone is parsed once, at a
  recorded clone revision. The `ar:` and `hl:` datasets hold their own copies of the lines.
  Collection wraps each document `？` as `【？】`, and Honkoku-Lines keeps its own snapshot, so their
  offsets are not the clone's, and parsing them as well would count each text twice.
- **The reading text of a line:** the characters `koji` marks `main` or `inserted` (the corrected
  text of a 見せ消ち) that stand outside every ruby's reading field. A correction nested in a
  reading (`《振り仮名：恵｜《見せ消ち：カ｜メグ》》`) takes the role `inserted`, so membership is
  tested by field, as `rubies.marks` tests it. Struck-out text, rubies, notes, 送り仮名 and 返り点
  are left out. A 割書 is
  parsed as a run of its own. A damage mark (`gap`, `unreadable`) ends a run, so no token spans
  one.
- **Each character keeps its raw offset.** A token records the offsets of the characters it holds,
  so a 割書 or a damage mark inside its bounds is never part of it.
- **Kanbun is not parsed.** A line with 返り点 is read in another order than it is written. Its
  tokens are left empty until a reading order is built.
- **Dictionary:** chosen by the book's witness year. Books before 1868, and undated books, use
  近世江戸口語UniDic. Books of 1868 or later use 近代文語UniDic. Every token records the dictionary
  and its version. Before the first load, the ruby test above runs with each dictionary over the
  whole corpus, and the cut-over year is kept only if the test supports it.

### Rubies steer the parse

- **Boundaries:** a right ruby's base is forced to start and end at token boundaries (MeCab
  partial parsing). A base that holds no other ruby is forced to be one token. A base that holds
  one, such as 孿胎 over 孿, is bounded at its ends only, and the inner base is forced in turn.
  Run boundaries come first: a base with a damage mark in it (`乙■丙`) is not forced, and its ruby
  stays a pair with no token.
- **Choice:** among the dictionary's entries for a one-token base, the one whose kana match the
  ruby on the key wins. A base whose reading is several tokens is compared on their joined kana.
- **No entry with that reading:** the token keeps the parse's best entry and is marked
  `ruby-unmatched`.
- **No entry for the surface:** a forced base that no entry spells, such as 支給, becomes one
  `unknown` token. Its lexeme fields are empty and the ruby's reading stays with it. An unknown
  token counts under its spelling and has no lexeme page.

### A token row

The id is `(clone revision, entry, page, line, first offset)`. It holds while that line is
unchanged. A row records:

- the surface and the raw offsets of its characters;
- the 語彙素ID, lemma, lemma reading, word class, conjugation and kana form, all empty for an
  `unknown` token;
- the dictionary and its version;
- `basis`: `parsed`, `ruby` (a ruby chose the entry), `ruby-unmatched` or `unknown`.

Every token is in the `parsed` tier, a machine reading. It never becomes `observed` or `attested`,
and the site labels it as the analyser's reading.

### Tokens reach crops through a character map

A unit is placed on a dataset line, and that line was derived from a clone line. Line positions do
not agree between them: collection numbers lines after dropping blank ones, and Honkoku-Lines keeps
its snapshot's index. The build therefore pairs the lines of a page by aligning the two sequences
of lines on their reading text, and pairs only lines whose texts agree. It then maps each paired
line's characters to the clone line's offsets:

- `ar:` lines carry the transcriber's text in `meta` wherever collection changed it, so the map
  removes the `【】` that collection added.
- `hl:` lines are mapped character by character.
- A line with no pair, or a character that differs between the paired texts, gets no offset, and
  the units on it link to no token. The share of units linked is one of the build's figures.

The units placed on a token's characters (`rubies.unit_tokens`) become crop ids through
`out/mirror.sqlite` (0009). Ruby bases join crops through the same map.

From a crop one reaches its character and image, its line, its token and that token's lexeme. From
the lexeme one reaches its spellings, every token of each spelling and those tokens' crops. From a
ruby pair one reaches the lexemes its rb was parsed as.

### Lexemes are word entries

- A 語彙素 is a word entry with a page. The page lists its spellings (the surfaces of its tokens)
  with counts, its books by period, its tokens and their crops.
- A row of `words.tsv` gains an optional `lexeme`, a 語彙素ID, which is filled by hand when the
  word and the lexeme are the same word. Rows are never linked to lexemes by matching. UniDic reads
  在郷 ザイキョウ, the rubies read it ざいごう, and 0004's word ざいご is cited from a dictionary.
  A matcher would join or split these three wrongly.
- **Claims:** `writes_word` takes either a word id or a lexeme, `lex:<語彙素ID>`. The route checks a
  lexeme against its R2 object, and the claim shows its lemma and reading. A claim on a crop
  outranks the parse there. When a reviewer sets a word, the inspector offers the crop's parsed
  lexeme first.

### Storage: one text build, served from R2

The ruby choice needs the rubies while the parse runs, so rubies and tokens are one build.
`atlas text build` writes `text.sqlite` with 0009's pairs and occurrences, the tokens and the
character maps.

`scripts/atlas3/text.py` writes these objects under `v3/text/<build>/`. `<build>` is the layout's
build id, read as `layoutBuild` reads it for votes (0008), so an object never names crops of
another build. Only objects whose hash changed are uploaded.

- One object per page with the page's lines, each with its rubies, its tokens and their crops.
- One object per lexeme with its spellings, counts and periods, and the first page of its tokens.
- Token lists of 500 occurrences per object for each lexeme and each spelling, in book and page
  order, each occurrence with its book, page, line, offsets and crops. A gallery reads one list
  object a page.
- Search shards:
  - spellings, by each character of the surface;
  - lemmas, by lemma text;
  - lemma readings, by their first two kana.

The size is estimated at 0.5 GB gzipped in about 400,000 objects, most of it the token lists of
particles; the first build measures it. D1 holds no token rows. `words` gains a `lexeme` column:
one migration, then one rewrite of each of the 20 word rows.

**Rebuilds.** `text.py` runs after every `publish.py` run, and when a book's witness year changes in
`books.py`. It rebuilds a page's objects when any of these changes:

- the clone revision;
- the dictionary chosen for its book, or that dictionary's version;
- its crops.

A changed token rewrites the lexeme and spelling objects it left and the ones it joined. A book
whose witness year changes rewrites the lexeme objects of its tokens, whose periods depend on it,
even when its tokens stay the same. A new layout build writes the whole tree under the new build
id. The old tree stays until the next build replaces it, so a Worker still holding the old build
id finds its objects.

### What the site shows

The site serves the analyser's output for each token: the lemma, its readings, word class and
語彙素ID. The dictionary is never published. The sources page names UniDic and its version as the
analyser's dictionary.

### Surfaces

- **Crop inspector:** a Word row with the crop's token and lexeme. It shows the token with this crop
  marked, the lemma and reading, and the basis (`ruby`, `ruby-unmatched`, `unknown`), and links to
  the lexeme page.
- **Page view:** the tokens of each line are marked as words, and hovering one outlines its crops on
  the scan.
- **Lexeme page,** `/lexeme/<語彙素ID>`: spellings with counts, a crop gallery per spelling, books by
  period, and the rubies on its tokens.
- **Search:** by lemma, lemma reading, spelling or any character of a spelling.

## Order of work

1. `atlas text build`: rubies (0009, step 1), the character maps and the tokens, with the ruby test
   and the share of units linked as its figures.
2. The dictionary choice, by the ruby test over the whole corpus.
3. `scripts/atlas3/text.py`: R2 objects under the build id, dry run first.
4. Worker routes, the crop and page surfaces, and the lexeme page.
5. `writes_word` with lexemes, and the `lexeme` column of `words.tsv`.

0009's steps 3 to 6 follow on the same objects.

## Consequences

- Every parsed token of the transcriptions has a word entry or is marked `unknown`, and every
  aligned crop that the character map reaches knows which word it helps write.
- The parse is a machine reading. Rubies correct it where they exist, and claims correct it on any
  crop a reviewer checks.
- Rubies whose reading no dictionary entry has stand out (`ruby-unmatched`). They are the readings
  a reviewer most needs to see.
