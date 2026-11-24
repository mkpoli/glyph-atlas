# 0012 The structure layer: 構形 between character and form

Date: 2026-11-09
Status: proposed

## The problem

A form is a category of written appearance, and a crop's identity is the grapheme it is read as.
Between them sits a distinction the model has not named: the character's **composition**. 峰
(`⿰山夆`) and 峯 (`⿱山夆`) write one character in two compositions; 草 written with 艹 and with 十十
writes one composition in two designs. A 草書 form may keep the composition of a 楷書 form, or lose
it, and one 草書 form may correspond to several 楷書 designs. A refinement tree with one parent per
form cannot carry these many-to-many relations, and `Structural analysis` is evidence about one form
with no shared entity by which to compare forms.

## The decision

Add **構形** (`structure`) as the layer between character and form:

| Layer | Code | Holds |
| --- | --- | --- |
| 字種 | `grapheme` / `character` | the intended character: its sound and meaning |
| **構形** | **`structure`** | components and their arrangement; no stroke design |
| 字体 | `form` | the design: joins, component variants, proportions, cursiveness |
| 字形 | `glyph` / crop | the ink in one occurrence |
| 書体 | `style` | 楷/行/草/篆/隸; a facet, not a layer |

A structure is a **canonical component tree**. Two forms share one when they are built from the
same components in the same arrangement, however each component is drawn; in 漢字構形學 they are
異寫字, and forms that differ in a component or its place are 異構字. Its identity is `st:` plus a
hash of the canonical text, the scheme and the policy (`glyph_atlas.structures`), never the IDS as
written. The source version is provenance on the row: a source bump that leaves a tree unchanged
keeps its id, and a policy change makes new ids, so an id issued today keeps its meaning.

A form realizes zero, one or several structures, and a structure is realized by many forms. A form
also corresponds to other forms through a typed many-to-many link (`corresponds_to`: cursive,
stroke-join, component-realization, structural, simplification). The refinement tree stays for
design; the correspondence graph is separate.

## The canonical tree

The reading starts from the trees `refs.descriptions` builds from BabelStone IDS and applies three
rules until the text is stable:

1. A part that spells a character is that character (`⿰亻匕` is 化), and a ⿰/⿱ chain flattens.
2. A radical form is the character it writes (`data/vocab/han-component-forms.tsv`). Unicode's
   equivalences hold anywhere (⺡ is 氵); CJKVI's radical variants hold in the position their row
   names (氵 at the left is 水, 灬 at the bottom is 火, 讠 at the left is 言). A CJKVI row with no
   position (甲 and 田, 才 and 手) indexes a radical and folds nothing, and the row for 䒑 is not
   read: it is 艸 at the top of 兹 and 丷 over 一 at the top of 前, 羊 and 益 (IRG entry 23).
3. Shapes the IRG lists as unifiable at level 1 are one component, read as the first its entry names
   (`data/vocab/han-component-ucv.tsv`: 戶 户 戸, 內 内, 兌 兑). The list unifies shapes where they
   vary, and two of them can be different characters elsewhere (主 and 龶), so two characters of an
   entry are one component only when the 異体字 graph also gives them as variants. Level 2 is not
   read: it pairs shapes that are different characters elsewhere (土 and 士, 壬 and 王).

A character has one structure, that of its first description; a character written as one component
(一, 人) is that component. Unicode unified its regional glyphs,
so its other descriptions are spellings of the same structure (納 is `⿰糹内` in one region and
`⿰糹內` in another; 胃 is `⿱田⺝` and `⿱田⺼`), and a written IDS that matches one names that
structure. A character the IRG unifies with another has that character's structure (内 has 內's),
unless the list gives the two as encoded apart (丷 and 八 are one component inside 兌 and two
characters on their own).

A character encoded apart whose own description is a regional shape of another character joins that
character's structure, where the 異体字 graph gives the two as one character encoded twice (Unihan's
kZVariant, CJKVI's duplicates) and CJKVI never gives them as non-cognate. 𦤀 `⿱自大` is 臭's J shape
and has 臭's `⿱自犬`; 𠾖 `⿳吅大吅` has 器's `⿳吅犬吅`. 柿 `⿰木市` is the K shape of 杮 `⿰木巿` and
keeps its own: CJKVI gives the two as characters that only look alike. A stated equivalence is not
enough, since yitizi's equivalences join 新字体 to 旧字体 (㠀 and 島). 30 characters form 15 groups,
each with the structure of the character whose shape the other is. An id still names its canonical
text, so the policy is unchanged; `⿱自大` keeps its id, and no character has it. The groups follow
the 異体字 graph, which the policy does not pin; a test lists them, so a rebuilt graph that changes
one is met with a decision.

A small seal character (Unicode's Seal block) is proposed the structure of its Han equivalent, the
`kSEAL_MCJK` of SealSources.txt: 𾲟 has 臭's `⿱自犬`, and 𽌇 has 萅's `⿱艸旾`, apart from 春
`⿱𡗗日`. The file calls the value the modern CJK equivalent and says nothing of structure. The value can
follow the seal form's composition (𽀅 has 叓, where 吏 is written today) and need not; how many
differ is not measured. The site marks a proposed structure with an asterisk until a reader files
the seal character. 書 is 聿 over 者 in seal (說文繫傳 卷六 聿部, 從聿者聲), so
its seal character has no structure, nor have those of 邃 and 𨘈, which Sealie's figures draw with 穴
and 虍 over the whole, nor those whose value several share: 94 Han characters (折, 殺, 民) are each
the value of more than one.

Seal script has no 之繞: it writes 辵 whole at the left, so the seal character of ⿺辶X or ⿺辵X is
proposed ⿰辵X (𽓻 `⿰辵禺`, of 遇 `⿺辶禺`). 153 are, each read in Sealie's figures. ⿰辵X and ⿺辵X are
other layouts of ⿺辶X and stand under it (𽓻 and 𨑡 `⿺辵土`), as a structure a reader folds does, with
nobody named; a reader's own fold is followed first. The ids are of `⿰辵禺` and `⿺辵土`, so the layouts
can be told apart, and the rule is not part of the policy. The rule reads the label, so a typed
`⿲辵木木`, which 林 does not shorten, stands under `⿺辶⿰木木` and not 林's `⿺辶林`.

The rules are wider than Unicode's unification. The IRG lists 艸 and 艹, 火 and 灬, 网 and 罒 as not
unifiable for encoding, and they are one component here; so are the simplified radicals 讠, 钅, 纟
and 饣 with 言, 金, 糸 and 食 (說, 説 and 说 are one structure). Characters the IRG lists as
encoded apart share a structure when only a unified component separates them (敱 and 敳, by 攵 and
攴).

## Limits

- A regional difference Unicode unified inside one code point is one structure (槱 is `⿰木⿱酉灬`
  in one region and `⿱梄灬` in another).
- BabelStone describes some characters alike (未 and 末 are both `⿻一木`; 由, 甲 and 申), and a
  radical form can make two characters alike (釖 `⿰金刀` and 釗 `⿰金刂`). They share a structure.
  A structure is compared among the forms of one character, where this does no harm.
- A description two characters hold names one of them: the character whose own structure it is,
  else the lowest code point.
- A character's regional shape, chosen as a form, is a spelling of that character's structure,
  even where the same description written alone is another character's: 杮's K shape `⿰木市`
  stands under 杮's `⿰木巿`, and written alone it names 柿.
- A label can hold BabelStone's number for an unencoded component (即 is `⿰{18}卩`); such a
  structure has no written IDS.
- The id does not carry the versions of the two component tables. `structures.TABLES` pins their
  digests to the policy, so a table that changes is met with a decision about a new policy.
- 忄 has no position in CJKVI's table and stays 忄; every description writes it so.
- Of 97,680 described characters, 188 have only approximate, subtracting or unrepresentable
  descriptions (七 is `〾⿻一乚`, 乌 is `㇯鸟丶`, 𠇇 is `⿰亻？`). Each is one component, itself, as
  it is inside the characters built from it (切, 坞). Its ids are new ones under the same policy.
  A written `⿰亻？` is 亻 beside a part no character names: it has a structure of its own and is
  not 𠇇's, since `？` stands for any such part.
- A character the source does not describe has no structure: 々, 〻, 〇, and every script but Han
  and small seal.

## Cases and sources

- 峰 `U+5CF0` `⿰山夆` and 峯 `U+5CEF` `⿱山夆` are two structures of one character
  (`data/vocab/han-ids.tsv`; BabelStone IDS.TXT, file date 2025-06-27).
- 說 `U+8AAA` and 説 `U+8AAC` are one structure, `⿰言兌`: the IRG list's entry 27 unifies 兌 兑 兊.
- 艹 `U+8279` is `⿻一⿰丨丨(GJ)` and `⿰十十(HTK)` in BabelStone, so `⿱⿰十十早` reads `⿱艸早`: a
  joined 艹 and two 十 are one structure and two designs.
- 氵 `U+6C35` is 水 at the left in `data/vocab/han-component-forms.tsv` (CJKVI radical-variant), so
  `⿰氵骨` and `⿰水骨` are one structure.
- 花's `⿱艹化` and `⿱艹⿰亻匕(P)` read one structure, `⿱艸化`, because `⿰亻匕` is 化.
- A 草書 form has no IDS derivation of its own. Its structure is a claim, by a reviewer or a source;
  `style` (書体) never decides it.

## Consequences

- A character has one derived structure; a grapheme has several through its characters, and a form
  can be claimed for several. The interface lists them; it never forces one parent.
- The derivation is a proposal; a `has_structure` claim is the authority. A form with no derivable
  structure stays unassigned.
- The `structures` table is immutable. A canonicalization change makes new ids and leaves claims as
  they were written.
- The site reads each character's structure from its IDS reference shards, which hold every
  described character, and derives none. A reader files a form under a structure
  (`POST /atlas/forms/<form>/structure`): the form is named by its id or by its written value, so a
  description nobody has chosen for a crop is filed like any other, and the structure by a character
  that has it, a description the shards hold, a regional shape of a character, or its id once a
  claim has named it. The structure's row is written with its first claim. The site's database is
  the ledger, so no publication carries structure rows to it.
- Where forms are grouped (`/layers/structures`, the inspector's form bar), a form stands under the
  structure readers filed it under, the one most of them name, and under its derived one with no
  claim. The inspector and the character page open the structures as a board of trays, where a
  reader moves a form to another structure by dragging it, with two clicks, or from the keyboard.
  A move is the reader's one filing of the form: their claims of its other structures are taken
  back in the same save, and putting it back where its description puts it takes the filing back.
- A reader folds one structure into another (`folds_into`, `POST /atlas/structures/folds`): the two
  are one 構形, and the forms of the first stand under the second wherever forms are grouped. The
  derivation keeps apart what differs in a component the fold tables do not unify (⿰糸𰌼 and ⿰糸㝛
  beside ⿰糸宿), and a fold is a reader's word that such a difference is below 構形. It is a claim
  about the structure, so it holds for every form built so, typed later or not; rows and ids stay as
  they are. A structure folds into one other, folds are followed to their end, one that would lead
  back is refused, and a structure readers fold two ways folds nowhere until they agree. On the
  board a tray is folded by moving it into another, where it stands as a tray within, and the
  reader's own fold is split off again.
- A description the site names a character or a form by (a corpus character Unicode lacks, a form
  a reviewer typed: `⿰月兊`) has its structure in the same shards, under its own text, so it joins
  the characters built as it is (脫, 脱). The reference load reads the list of them
  (`export_character_variants.py --described`); a description typed since the last load has no
  structure on the site until the next one, as it has no drawing.
- The correspondence link and the 構形 row in the picker follow.

## References

- `docs/design/form-model.md`, the form model this extends.
- `data/sources/babelstone-ids.yaml`, `data/sources/cjkvi-variants.yaml`,
  `data/sources/unicode-ucd.yaml`, `data/sources/iwds.yaml`.
- `data/vocab/han-ids.tsv`, `data/vocab/han-component-forms.tsv`,
  `data/vocab/han-component-ucv.tsv`.
- IRG, UCS Ideograph Unifiable Component Variations List, version 2026-10-20, read through the
  IWDS data file (`data/sources/iwds.yaml`); the entry numbers are the list's. The document's
  number and its definition of the two levels were not checked against the published PDF.
- 王寧『漢字構形學導論』(商務印書館, 2015), for 異寫字 and 異構字; cited from memory of the
  framework, not checked against the page.
- SealSources.txt of Unicode 18.0 (`data/sources/unicode-ucd.yaml`).
- 說文繫傳 卷六 (四庫全書本), 聿部, on 書: 著也從聿者聲. Wikisource,
  https://zh.wikisource.org/wiki/說文繫傳_(四庫全書本)/卷六, read 2026-11-24.
- Sealie 1.3.1, the figures the layouts were read in (`docs/typefaces.md`).
- `src/glyph_atlas/structures.py`, the derivation.
- `apps/cloudflare/migrations-atlas/0017_structures.sql`, the table.
