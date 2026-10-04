# Form model

How the Atlas records what a crop is, what it looks like, and who said so. Every statement about a
crop, a form or a grapheme is an attributed assertion in one ledger; current values are a resolution
of that ledger, kept as indexed tables for the galleries. Reclassifying a form changes the current
interpretation of the collection, while the crops, the source statements and each reviewer's claims
stay recoverable with their original meaning.

## Entities

**Reference data.** Characters (`data/vocab/characters.tsv`), their Ideographic Description Sequences
(`han-ids.tsv`), source variant edges (`kanji-variants.tsv`, served from D1 `character_variants`) and
derived component variants (`character_derived`, tier `derived`: forms up to two component
substitutions make of a character). They come from sources and are read only. Crops never attach to them directly.

**Grapheme.** A stable id with a scope and a display label. The Atlas default grouping is one scope.
Which forms and characters a grapheme holds is decided by a versioned grouping policy (the curated
`graphemes.yaml` with the shape-variant rule of `refs`), never by a parent pointer stored on a crop.
Administrative reduction (MJ 縮退), encoding equivalence (compatibility ideographs) and orthographic
identity are separate predicates, even where the default policy groups their endpoints together.

**Form.** A stable opaque id for a category of written appearance. A form needs no name, no encoding
and no structure. Forms make a tree by refinement: each has at most one parent, and a root has none.
Every other relation between forms is a typed link: resembles, transitional-between, derived-from
(あ from 安), substitutes-for, and written-for a grapheme or character. A form can be written for
several graphemes (子 as the kanji 子 and as the kana ね); a crop's own identity claim says which one it
reads as.

**Representation.** A way of naming a form: a Unicode sequence, an Ideographic Variation Sequence, an
MJ id, a GlyphWiki name, an IDS (kept exactly as written; a normalised IDS is only a versioned search
key), or a private-use code point with its namespace and version. A form can have several, and a
representation is never its identity. A code point anchors at most one broad form, the encoded form
of that character; narrower forms can carry the same representation as an approximation.

**Structural analysis.** An attributed component tree for a form, with its scheme and version, the
regions it applies to, and its evidence. Several can coexist for one form.

**Crop.** The occurrence: a `Unit` and its image region. Its evidence versions are immutable records
of the pixels a claim was made about (`crop_versions`): the crop's id, the checksum of the source
image (a page, a pre-cut crop file, or a corpus record's source revision) and the box on it in
whole pixels. The version id is those three joined, `{unit}@{pixels}@{x},{y},{w},{h}`, with an empty
box for a whole pre-cut file, so SQLite and Python derive the same id from the same record; a crop
with no image checksum, or a box of anything but whole numbers, has none. A recrop or a
re-segmentation makes a new version; the old one stays. A version is removed only with its crop when
a withdrawn document is taken off the site.

**Facets.** Style, production, cursiveness, date, script and hand, set on a crop or inherited from its
page and document. A document's dates are claims of the ledger, one `date_<kind>` assertion for each
date a source states (`docs/schema.md`, `dates`). A value set on the crop stays distinguishable from an inherited one. A sub-form is
made only when the shape differs in a way no facet explains.

**Hand.** A distinguishable writing hand, possibly anonymous, spanning crops and documents. Attributing
a hand to a person, and production events (scribe, calligrapher, carver, printer, reproduction of an
exemplar), are separate entities.

## The assertion ledger

Every claim is one row of `assertions`:

| Field | Meaning |
| --- | --- |
| `subject`, `predicate`, `object` or `value` | what is claimed: an entity id, or a typed value |
| `scope` | the grouping or editorial scope the claim holds in; empty for the default |
| `alternative_set` | shared by the members of one "F or G" claim |
| `tier` | `attested` (a source states it), `observed` (a person looked), `derived` (an algorithm) |
| `asserted_by`, `asserted_at` | the journal actor (account or reviewer id) or source, and when |
| `confidence`, `confidence_scheme` | a score and what it measures; a score is never read as a probability |
| `method`, `run` | how the claim was produced, and which run produced it |
| `legacy` | the journal row the claim was migrated from |

`assertion_evidence` names what supports a claim: a crop version, a page locator, or a source row and
its hash. `assertion_premises` names the assertions a derivation rests on. Rows are never updated. A
claim about a crop is deleted only once the crop has left the site, as a withdrawn document's crops
do, and its evidence, premises and actions with it.

Acceptance is its own row in `assertion_actions`: `accept`, `reject`, `retract` (by the asserter) or
`adjudicate` (by an adjudicator). A predicate catalogue (`data/ledger.json`) states each
predicate's subject and object types and its cardinality, and every write is checked against it.

### Resolution

The current value of each slot (subject, predicate, scope, and for a many-valued predicate the object)
is computed from the ledger by one SQL resolver, which the Worker, the local review service and the
publication run alike. Its result is materialised in `current_claims`, which carries the supporting
assertion ids, the crop version it was resolved against and the resolver version. Galleries read these
rows and never rebuild the graph per request.

1. A claim is live when nobody retracted it and its crop evidence is the crop's current version.
2. Each person's latest accept or reject of a claim counts; the asserter's own claim counts as one
   acceptance. A claim stands while its acceptances outnumber its rejections.
3. The latest adjudication of a live claim decides the slot.
4. Otherwise one standing value is current; several standing values make the slot `disputed`, and
   every claim stays; live claims that all fell to rejections make it `rejected`.
5. A person's new claim in a single-valued slot retracts their own earlier one in the same write.

## Rules

- Unsorted is the absence of a current form claim. "Examined, unresolved", "unreadable" and "candidate
  rejected" are explicit, distinct states.
- A crop between two forms gets an alternative set ("F or G", each with its confidence), or a
  transitional form M linked to F and G.
- Merging forms is a reversible resolution (`merged_into`, followed one hop); both ids and their
  history stay. Splitting makes new child forms, and only supported memberships move. Re-parenting
  supersedes the refinement assertion; crop assignments keep their form ids.
- When two people disagree, both claims stay and an adjudication picks the current one.
- A bulk decision records the exact crops and their evidence versions, and never covers crops added
  later.
- A scribe's error is two claims: the intended identity and the written form.
- A ligature (合字) has an identity that is a sequence of graphemes. One character cut into two crops
  gives both a part-of claim to one occurrence.
- A claim made on an earlier evidence version needs reassessment, or a new claim on the current version
  that names the old one as its premise.

### Identity and form

Assigning a form never changes a crop's identity on its own. When the form's representation is an
encoded character in the same grapheme as the crop's identity (仮 and 假, は and 𛂞), the interface
performs both in one action and records two assertions, one for the form and one for the identity.
Anything else records the form only. Replaying the Forms decision log and Quick Review's form bar
corrections through the ledger must reproduce the galleries as they stand; that test is written
before Forms writes to the ledger.

## Where the earlier records go

| Record | Becomes |
| --- | --- |
| `units.written_form` and the `written_forms` journal | form assertions; each distinct value becomes one form with that representation. A form equal to the label, which that layer stored as no form, is recovered as a confirmation from the journal's saved request where it is there, and left unknown where it is not |
| Forms `form_families` | work queues and browse scopes |
| `form_clusters`, `form_units` | versioned machine proposals (`clustering_runs`); a form exists once a person adopts or names one |
| `form_decisions` | assertions; `form_bases` and the allowed list are removed |
| `visual_group` | a proposal, then removed |
| `Unit.variants` (MJ, IVS, GlyphWiki ids) | representations of forms |
| `characters.grapheme` | output of the grouping policy, versioned and reproducible |
| component-variant derivations | assertions with premises: the IDS analyses, the substitution, the attesting pairs, the algorithm version and its parameters; the CJK-block exclusion is algorithm policy |
| style and production columns | materialised views resolved from assertions |

The journals (`events`, `form_decisions`, `written_forms`) are kept as archival evidence once
migrated. While a capability moves, one writer is live for it; at the end there is one model.

## Migration order

Each step is one pull request with a usable result of its own.

1. Crop evidence versions.
2. The assertion ledger, the predicate catalogue, review actions and the materialised views.
3. Forms and representations, and the move of written forms into them.
4. The 字形 picker and Quick Review's form bar write form assertions under the identity-and-form rule.
5. Forms decisions write assertions, identity becomes its own assertion, and the live-log replay is
   the acceptance test.
6. Clustering runs as proposals; adopt, split, merge and re-parent in the API and the interface.
7. Grapheme grouping as a scoped, versioned policy that reproduces the current families; derivation
   premises in the ledger.
8. Style and production through assertions, set and inherited values distinguishable.
9. Hands, then attribution to people, then production events.
10. The read-path cutover everywhere, and removal of the superseded writable tables and of the
    journals' live use.
