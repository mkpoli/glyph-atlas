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

R2 bills a write by the object, whatever its size, so the text layer is stored as few, larger
objects. 120,747 clone pages hold text.

**Only page packs name crops.** `scripts/atlas3/text.py` writes two kinds of object and uploads
only those whose hash changed:

- **Under `v3/text/<build>/`,** the layout's build id, read as `layoutBuild` reads it for votes
  (0008), so that no object names crops of another build:
  - page packs, `pack/<entry>/<n>.json.gz`: pages 32n to 32n + 31 of one book, each with its
    lines, rubies, tokens and their crops. A pack is found from the book and the page number
    alone. A pack over 256 KB decoded is split in halves, `<n>.0` and `<n>.1`, until each half
    fits;
  - `pages.json.gz`: the set of text pages that have a crop, as a bitmap of about 15 KB, and the
    list of split packs. It is small and read often, so an isolate nearly always holds it.
- **Under `v3/text/`, with no crop ids:**
  - lexeme shards, 1,024 by the hash of the 語彙素ID. A lexeme's entry holds its spellings,
    counts and periods, its cropped pages (the text pages where its tokens had crops at the last
    refresh, most crops first, up to 2,000), and its page index up to 2,000 pages;
  - page index chunks of 20,000 pages for the lexemes that occur on more than 2,000 pages;
  - ruby pair shards, 1,024 by the hash of the pair, with each pair's counts, cropped pages and
    occurrences (text page, line, offsets), and chunks of 20,000 occurrences for the largest pairs;
  - search shards: 1,024 for spellings by each character of the surface, 256 for lemmas and 256
    for lemma readings by their first two kana.

**A gallery is assembled at read time,** from the page packs, which hold the current crops:

- The Worker reads the lexeme's cropped pages first, then pages the bitmap marks that were cropped
  after the last refresh, in the order of the page index. Pages of one book share packs, so a
  pack read usually yields several of them.
- One request reads at most 12 objects and decodes at most 1 MB: the lexeme shard, `pages.json.gz`
  and up to 10 packs. It returns the crops found, up to 24, and a cursor to the next page.
- A lexeme whose cropped pages are current fills a gallery in one round of reads. A lexeme whose
  tokens are rarely aligned returns fewer crops per request, and its next page continues from the
  cursor.
- A pair gallery reads the same way, from the pair's cropped pages.

**D1** holds no token rows. `words` gains a `lexeme` column: one migration, then one rewrite of
each of the 20 word rows.

**Rebuilds.** `text.py` runs after every `publish.py` run. What it rewrites follows what changed:

- **Crops** (a publication): the packs of the pages touched, and `pages.json.gz`.
- **A clone page's text, or the dictionary of a book:** that page's pack, the lexeme, index and
  pair shards its tokens and rubies left or joined, the search shards of those spellings, lemmas
  and readings, and 0009's ranked lists.
- **A witness year,** whether from `books.py` or from a publication's `document_dating`: the
  lexeme shards of the book's tokens, whose periods depend on it. A year that crosses into another
  dictionary's range also re-parses the book, as above.
- **Cropped pages:** refreshed once a week in one batch. The refresh rewrites only the shards
  whose cropped pages changed. Between refreshes the bitmap finds newer crops.
- **A new layout build:** every pack and `pages.json.gz` under the new build id, and a refresh of
  cropped pages. The old packs stay until the next build replaces them, so a Worker still holding
  the old build id finds them.

### Performance and cost

Figures are from the full parse above, a 150-entry sample for object sizes, and R2's prices for
Standard storage: $0.015 per GB-month, $4.50 per million writes (PUT and LIST) and $0.36 per
million reads. The account is past the free tier, so every operation is billed. R2 rounds a
month's usage up to the next million operations and the next GB-month.

| | Objects | Size, gzipped |
| --- | ---: | ---: |
| Page packs | about 8,600 | about 200 MB, by estimate |
| Lexeme shards and index chunks | 1,024 and about 1,100 | about 40 MB, by estimate |
| Ruby pair shards (0009) | 1,024 and a few chunks | about 60 MB |
| Search shards | 1,536 | under 20 MB, by estimate |
| `pages.json.gz` and the manifest | 2 | under 1 MB |

A pack decodes to 26 KB at the median and 417 KB at the 99th percentile before the 256 KB split;
the densest unsplit pack would be 2.5 MB. A page decodes to 3 KB at the median and parses in well
under a millisecond. The edge cache holds the compressed object, so every read decodes and parses
it again unless the isolate still holds it (32 objects, five minutes, `atlas3Media.ts`).

| | Writes | Cost at the rate |
| --- | ---: | ---: |
| First load | about 13,300 | $0.06 |
| A publish run | the packs touched (one or two per book), `pages.json.gz` and the manifest: two writes, one read, one listing | under $0.001 |
| A weekly refresh of cropped pages | at most 2,048 | at most $0.01 |
| A new layout build | about 8,600 and a refresh | $0.05 |
| Storage | about 0.35 GB | $0.005 a month |

One page per object would have made the first load about 550,000 writes ($2.48) and every publish
one write per page. Lists of crops per lexeme would have added one write per lexeme of each book
published, 185 for the median book and 1,491 at the 90th percentile.

- **Reads:** a crop inspector reads its pack, the same one its Ruby row reads. A gallery request
  reads at most 12 objects, in two rounds of six; a million requests at the bound are $4.32.
- **Worker CPU:** a gallery request decodes at most 1 MB of JSON.
- **Build:** with one process on a loaded machine, reading the clone took 10 minutes, the parse 14,
  and the ruby candidates 20. Four workers should bring the whole build near 12 minutes. It peaked at
  1.9 GB of memory. Ruby candidates are looked up once per distinct base and reading, and each page
  is re-parsed only when its input changes.

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

1. `atlas text build`: rubies (0009, step 1) and tokens from the clone alone, with the ruby test as
   its figures.
2. The character maps from the datasets' lines to the clone's, and the crops of each token and
   ruby base through the mirror, with the share of units linked as its figure. They need the
   datasets and `out/mirror.sqlite`, which step 1 does not read.
3. The dictionary choice, by the ruby test over the whole corpus.
4. `scripts/atlas3/text.py`: packs and shards, dry run first, with the write count of each run.
5. Worker routes, the crop and page surfaces, and the lexeme page.
6. `writes_word` with lexemes, and the `lexeme` column of `words.tsv`.

0009's steps 3 to 6 follow on the same objects.

## Consequences

- Every parsed token of the transcriptions has a word entry or is marked `unknown`, and every
  aligned crop that the character map reaches knows which word it helps write.
- The parse is a machine reading. Rubies correct it where they exist, and claims correct it on any
  crop a reviewer checks.
- Rubies whose reading no dictionary entry has stand out (`ruby-unmatched`). They are the readings
  a reviewer most needs to see.
