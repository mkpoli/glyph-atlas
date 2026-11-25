# 0003 Character identity and 字母 metadata

Date: 2026-09-19

## The problem

A written character was stored once per unit. `Unit.unicode` held the code point and `Unit.jibo` held
the 字母, so the 字母 of a code point was written into every record that carried the code point, and a
code point the tables did not know had nowhere to put one.

Unicode 18.0 made the cost visible. It added `U+1B127` 𛄧 KATAKANA LETTER ALTERNATE NE and
`U+1B128` 𛄨 KATAKANA LETTER ALTERNATE WI, the alternate katakana of the Meiji period, which until
then had been written as the kanji 子 and 井 for want of a code point. Three characters stand where
one did:

| | Character | Shape | 字母 |
| --- | --- | --- | --- |
| the kanji that was used instead | U+5B50 子 | — | — |
| the katakana letter | U+30CD ネ | ネ | 祢 |
| the alternate letter | U+1B127 𛄧 | 𛄧 | 子 |

A record of a Meiji printing holds one of the three, and the code point is what says which. The kanji
is not a substitute for the katakana any more, `confusables.txt` pairs 𛄧 with 子 rather than unifying
them, and a `jibo` column on a unit cannot say that 子 is what the form derives from while 子 is also
a character in its own right.

## The decision

The browsing hierarchy is **grapheme → character → form** (clarified 2026-09-21):

- **Grapheme** groups explicitly related written characters. 仮 and 假 share a family
  represented by 仮. Existing kana families remain: ね, ネ and its historical kana
  belong together. Small kana and ligatures retain their own families. Small seal characters
  belong to their Han character's family ([0015](0015-small-seal-forms.md)).
  **The label of a grapheme** (2026-11-24) is what stands for it where the site names one: its 旧字体, its
  head and its 简体字, each once. 圖図图, 龍竜龙, 語语, 馬马, 國国 (the head is the 简体字), 假仮, 步歩. A grapheme with
  neither is its head alone.
  - The **旧字体** is the older form the 常用漢字表 (2010) prints beside the head, where it is a character of the
    same grapheme. The pairs are the `joyo/variant` edges of `kanji-variants.tsv` (CJKVI, `joyo-variants.txt`,
    異体字（常用漢字表）), which are the 364 the 文化庁 index of the table prints (`joyo-2010` in `graphemes.yaml`,
    compared 2026-11-24). The 271 families below have one, and so have 徴巣掲撃晩暦歩歴毎渉渇痩研縁録闘麺 and
    併即戻概涙状虚錬餅温黄, whose two forms `characters.tsv` files together. 弁 has none, as its older forms
    (辨瓣辯) are three other graphemes. Neither have those whose older form is a compatibility ideograph
    ([0013](0013-finer-forms.md)).
  - The **简体字** is a character of the grapheme that the 通用规范汉字表 (2013) lists (Unihan 18.0.0 kTGH, in
    `tongyong-guifan.tsv`) and that Unihan gives as the simplified form of another of its characters
    (kSimplifiedVariant, kTraditionalVariant). Of two the first in the table stands: 线 (1336) before 缐 (7459)
    for 線, 铁 before 𫓧 for 鉄. A relation of another source alone (冦 寇, 第一批异体字) gives none. Nor is
    there one where the simplified form of the head or of its 旧字体 is of another grapheme (豊 豐: 丰; 発 發: 发;
    歴 歷: 历), or where the table lists the head itself (确黹砷簧晅): the form left is of some other member.
  - A head with no 旧字体 stands after the character of the grapheme that Unihan simplifies to its 简体字, where
    that is of another 構形 ([0012](0012-structure-layer.md)): 彎弯, whose head is the 简体字, 壺壷壶, 聰聡聪.
    A head that simplifies in the grapheme itself is such a character (線: 缐), and stands first.

  A form of the head's own 構形 that the 常用漢字表 does not print is not added: 靑 青, 說 説 (説说), 稅 税. The
  label is for showing, the 旧字体 and the head set as `ja` and the 简体字 as `zh-Hans`: the key, the address
  (`/grapheme/U+4EEE`, `/chronology/U+4EEE?scope=grapheme`), the order of a list, search, a citation key, a
  file name and what a copy button copies are the head's. JSON-LD names the term by the head, with the others as
  `alternateName`. The glyphs are never one character. A badge beside a written character
  names its grapheme by the head alone; a crop with no character is named by the label, which then stands for
  the grapheme. `scripts/build_grapheme_labels.py` writes the table (`apps/cloudflare/src/graphemeLabels.json`).
- **Character** keeps the written identity and code point. 仮 (`U+4EEE`) and 假
  (`U+5047`) remain distinct rows in `characters.tsv`, with separate counts.
- **Form** is the exact ink in a source occurrence: its unit identity, crop, placement,
  image revision and any recorded MJ/IVS/GlyphWiki/local variant identifier.

**字母** remains metadata on `Character.jibo`. Readings are independent of both
encoded identity and family membership. Shared pronunciation, derivation or visual
similarity alone does not merge characters.

The Han families currently cover 271 unambiguous one-modern/one-old pairs from the
first character cell of the [Agency for Cultural Affairs Jōyō index](https://www.bunka.go.jp/kokugo_nihongo/sisaku/joho/joho/kijun/naikaku/kanji/joyokanjisakuin/).
`graphemes.yaml` records each pair, relation and source digest. Multi-form entries
and compatibility ideographs are outside this initial set. Broader alignment
policies remain separate from browsing families.

### 简体字 and 新字体 (2026-11-25)

A 简体字 of the 通用规范汉字表 (2013) or a 新字体 of the 常用漢字表 (2010) is of the grapheme of its full form when it writes that one
character: 对對, 动動, 实實, 黄黃, 状狀. Before, a form joined only when every variant relation it had led to one family, so a
form in wide use stayed apart for its other relations (动 also relates to 働). What a form is for is now read from the
standards' own tables, with Unihan and OpenCC, and 423 of the 608 forms outside their full form's grapheme join it.
185 stay apart (`data/vocab/grapheme-apart.tsv`, each with its reasons): the form is also itself (后, 价, 谷), writes
several characters (发, 历, 弁, 枪, 个), is a headword of the 說文解字 or the 廣韻 read otherwise (听, 从, 机), is listed beside its
full form (着, 了), or is a loan for it.

Two full forms of one form that two sources call variants, and nothing holds apart, are one character too and join
with it (駡 and 罵 with 骂). A dictionary holds two apart by the readings it gives both. Where none reads both there is
nothing to compare, and the join rests on the two sources: 駡, 鈎 and 綉 are later shapes neither dictionary heads an
entry with, while 罵, 鉤 and 繡 are headwords, and asking for a shared reading would part every such pair. A non-cognate
tag of cjkvi's is no word of two characters where its own database also calls the pair 異体字: 奬 and 獎 are tagged so and
are 異体字 in its hydzd, dypytz, twedu and 戸籍 tables, as 状 and 狀 are, and the list it comes from also holds shapes ISO/IEC
10646 encodes apart. Two entries of the 常用漢字表 are two characters (個 and 箇), and so is a pair called a loan whose forms
the 說文解字 heads an entry with each (蕩, as 𣿴, and 盪).

`glyph_atlas.shape_families` states each rule with its sources, and `data/vocab/grapheme-rulings.tsv` the pairs ruled
on by hand. The joined grapheme keeps the key of the full form's, unless the form is better known in Japan (没 over 沒,
黄 over 黃).

- 教育部・国家语言文字工作委员会『通用规范汉字表』附件1 规范字与繁体字、异体字对照表, 国发〔2013〕23号, as transcribed by
  Wikisource and mandel59 (`data/sources/tongyong-guifan.yaml`); nos. 1151–1508 compared with the official scan.
- 文化庁『常用漢字表』(2010), through CJKVI `joyo-variants.txt` (`joyo-2010` in `graphemes.yaml`).
- 『說文解字』 in the data of shuowen.org (`data/sources/shuowen.yaml`), not collated with a printing, and
  『廣韻』(澤存堂本) in nk2028's tshet-uinh-data (`data/sources/guangyun-zecuntang.yaml`), into
  `data/vocab/headword-readings.tsv`.

Character and occurrence queries stay exact by default. `expand=grapheme` requests
the whole family; selecting a written character returns its own occurrences.

`Unit.jibo` and `Candidate.jibo` are removed. `Unit.unicode` keeps the code point, which is the
record's reference into the character layer.

The character table covers every kana of the kana blocks and every CJK unified ideograph: the
ideographs are the 字母 the kana point at and the characters a source text is written in, so a table
without them could not answer what 子 is. It does not cover the enclosed kana forms (㋕) or Kana
Extended-B, whose letters are the tone marks of Taiwanese kana.

Where a fact comes from is recorded per fact. The 字母 of a hentaigana is Unicode's own `derived
from` chart note first, then MJ's 表, then a hand-written row that cites its source; the 音価 map of
the kana and the grapheme of each is hand-written in `data/vocab/graphemes.yaml` with a citation per
row. The kana assignments use the encoded letter identities and cited historical descriptions.
Shared appearance or 字母 alone does not establish a family.

`Script` gains `han`, Unicode's name for the script of the kanji, and keeps `kanji`, which older
records write for it. The character layer states `han`; a unit imported before this decision keeps
what it said.

## Consequences

- The six Unicode 18.0 kana are characters the dataset can record, with their 字母, their reading and
  the kana they are forms of. A project still on Unicode 17 can read a record of U+1B127 as a code
  point it does not know, which is what a code point is for.
- A reviewer searching for ね finds ten forms, 𛄧 among them, through `atlas character --grapheme`.
- `data/vocab/characters.tsv` is 9 MB of generated table in git. Rebuilding it needs the cached UCD
  files of one release, which are not in git; `docs/schema.md` carries the commands.
- The 字母 of a unit is now derived, so it cannot be corrected on a unit. Correcting it means
  correcting the character layer, which changes it for every record of that character. That is the
  intent: a 字母 that is wrong is wrong everywhere.
- A code point the table does not hold answers `None` rather than raising, so an importer that meets a
  character outside the covered blocks records it and gets no 字母, which is what it already did for
  a kana whose derivation no source stated.
- `tables.SCHEMA_VERSION` is 2. A dataset written before this decision holds a `units.jibo` column
  that the model no longer reads, so it is read as if the column were absent and loses the letter on
  the way out. Re-importing a source rebuilds the units with the code point, which is what the letter
  is derived from.
