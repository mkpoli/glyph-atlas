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
   names (氵 at the left is 水, 灬 at the bottom is 火). A CJKVI row with no position (甲 and 田, 才
   and 手) indexes a radical and folds nothing.
3. Shapes the IRG lists as unifiable at level 1 are one component, read as the first the entry names
   (`data/vocab/han-component-ucv.tsv`: 戶 户 戸, 內 内, 兌 兑). The list unifies shapes where they
   vary, and two of them can be different characters elsewhere (主 and 龶), so two characters of an
   entry are one component only when the 異体字 graph also gives them as variants. Level 2 is not
   read: its entries list characters the variation tells apart (壬 and 王: 任 and 仼).

A character has one structure, that of its first description. Unicode unified its regional glyphs,
so its other descriptions are spellings of the same structure (納 is `⿰糹内` in one region and
`⿰糹內` in another; 胃 is `⿱田⺝` and `⿱田⺼`), and a written IDS that matches one names that
structure. A character the IRG unifies with another has that character's structure (内 has 內's).

Rule 2 is wider than Unicode's unification: the IRG lists 艸 and 艹, 火 and 灬, 网 and 罒 as not
unifiable for encoding, and they are one component here.

## Limits

- A regional difference Unicode unified inside one code point is one structure (槱 is `⿰木⿱酉灬`
  in one region and `⿱梄灬` in another).
- BabelStone describes some characters alike (未 and 末 are both `⿻一木`), and they share a
  structure. A structure is compared among the forms of one character, where this does no harm.
- 忄 has no position in CJKVI's table and stays 忄; every description writes it so.
- Of 97,680 described characters, 188 have only approximate, subtracting or unrepresentable
  descriptions and no structure.

## Cases and sources

- 峰 `U+5CF0` `⿰山夆` and 峯 `U+5CEF` `⿱山夆` are two structures of one character
  (`data/vocab/han-ids.tsv`; BabelStone IDS.TXT, file date 2025-06-27).
- 說 `U+8AAA` and 説 `U+8AAC` are one structure, `⿰言兌`: IRG N2978 entry 27 unifies 兌 兑 兊.
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
- This change adds the canonical tree, the derivation, the `structures` table and the
  `has_structure` predicate. The claim route, the correspondence link and the 構形 row in the picker
  follow; the claim route also adds `structures` to the publication's ledger lists
  (`ledger.FORM_TABLES`, `prepare_publication.LEDGER_LINES`), and until then no structure row is
  published.
- `/layers/structure` still names the IDS descriptions; when the layer's routes arrive it becomes
  `/layers/ids`, so "structure" names one thing.

## References

- `docs/design/form-model.md`, the form model this extends.
- `data/sources/babelstone-ids.yaml`, `data/sources/cjkvi-variants.yaml`,
  `data/sources/unicode-ucd.yaml`, `data/sources/iwds.yaml`.
- `data/vocab/han-ids.tsv`, `data/vocab/han-component-forms.tsv`,
  `data/vocab/han-component-ucv.tsv`.
- IRG N2978, UCS Ideograph Unifiable Component Variations List, version 2026-10-20
  (https://www.unicode.org/irg/docs/n2978-Complete.pdf), read through the IWDS data file.
- 王寧『漢字構形學導論』(商務印書館, 2015), for 異寫字 and 異構字; cited from memory of the
  framework, not checked against the page.
- `src/glyph_atlas/structures.py`, the derivation.
- `apps/cloudflare/migrations-atlas/0017_structures.sql`, the table.
