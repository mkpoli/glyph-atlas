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

A structure is a **canonical component tree**. Its identity is the tree and the policy version, not
the IDS: `st:` plus a hash of the canonical text, the scheme and the policy
(`glyph_atlas.structures`). The source version is provenance on the row, not identity: a source bump
that leaves a tree unchanged keeps its id. An IDS is a representation of a structure, kept as
written, as it is for a form.

A form realizes zero, one or several structures, and a structure is realized by many forms. A form
also corresponds to other forms through a typed many-to-many link (`corresponds_to`: cursive,
stroke-join, component-realization, structural, simplification). The refinement tree stays for
design; the correspondence graph is separate.

The canonicalization reads the trees `refs.descriptions` builds from BabelStone IDS: a named part is
read as the character it spells, a ⿰/⿱ chain flattens, and every leaf is read through
`data/vocab/han-component-forms.tsv`. Unicode's radical equivalences hold anywhere (⺡ is 氵), and
CJKVI's radical variants hold only in the position their row names (氵 at the left is 水, 艹 at the
top is 艸). A CJKVI row with no position (甲 and 田, 才 and 手) indexes a radical, not a composition,
and stays distinct. A policy change makes new ids, so an id issued today keeps its meaning. This
change adds the canonical tree, the derivation, the `structures` table and the `has_structure`
predicate. The claim route, the correspondence link and the 構形 row in the picker follow.

## Cases and sources

- 峰 `U+5CF0` `⿰山夆` and 峯 `U+5CEF` `⿱山夆` are two structures of one character
  (`data/vocab/han-ids.tsv`; BabelStone IDS.TXT, file date 2025-06-27).
- 艹 `U+8279` is `⿻一⿰丨丨(GJ)` and `⿰十十(HTK)` in BabelStone. `⿱⿰十十早` reads `⿱艸早`
  (艸 is the canonical component; 艹 and 十十 are its designs), so a joined 艹 and two 十 are one
  structure and two designs.
- 氵 `U+6C35` is 水 in `data/vocab/han-component-forms.tsv` (CJKVI radical-variant), so `⿰氵骨` and
  `⿰水骨` are one structure.
- 花's `⿱艹化` and `⿱艹⿰亻匕(P)` read one structure, `⿱艸化`, because `⿰亻匕` is 化.
- A 草書 form has no IDS derivation of its own. Its structure is a claim, by a reviewer or a source;
  `style` (書体) never decides it.

## Consequences

- A character can have several structures through its forms; a form can realize several. The
  interface lists them; it never forces one parent.
- The derivation is a proposal; the claim route writes it as a `derived` claim, and the claim is the
  authority. A form with no derivable structure stays unassigned.
- The `structures` table is immutable. A canonicalization change rewrites ids, not claims. The source
  version is provenance on the row.
- The claim route also adds `structures` to the publication's ledger lists
  (`ledger.FORM_TABLES`, `prepare_publication.LEDGER_LINES`); until then no structure row is
  published.
- `/layers/structure` still names the IDS descriptions; when the layer's routes arrive it becomes
  `/layers/ids`, so "structure" names one thing.

## References

- `docs/design/form-model.md`, the form model this extends.
- `data/sources/babelstone-ids.yaml`, `data/sources/cjkvi-variants.yaml`,
  `data/sources/unicode-ucd.yaml`.
- `data/vocab/han-ids.tsv`, `data/vocab/han-component-forms.tsv`.
- `src/glyph_atlas/structures.py`, the derivation.
- `apps/cloudflare/migrations-atlas/0017_structures.sql`, the table.
