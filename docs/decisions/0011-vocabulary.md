# 0011 Vocabulary: sequence, n-gram and run

Date: 2026-10-29
Status: proposed

## The problem

One object carries three names. A reader meets **sequence**: the address `/sequence/<text>`, the
"Related sequences" links, the "Sequence" search candidate. The analysis layer calls the counted
sequences **n-grams**: `/atlas/ngrams/:n` and `NgramGrid`. The storage layer calls them **runs**: the
`runs`, `run_stats` and `run_written` tables, the `atlas-runs` database, `/atlas/runs` and the
`v3/runs/` answers. The interface mixes the last two, with `RunTile` beside `NgramGrid`.

"Run" has two further senses in the repository: a pipeline run (`atlas align --run pilot-v1`,
`work/<run>`) and a ruby span (0009). Nothing states which word belongs to which layer, so each new
surface chooses again.

## The decision

Three registers, one boundary.

**Reader-facing: sequence.** Two or more characters written next to each other on a line. One place
it was written is an **occurrence**; the shape written there is a **written form**. The lengths are
**pair** (two) and **trigram** (three). The address is `/sequence/<text>`.

**Analysis: n-gram.** The counts of length two and three: `/atlas/ngrams/:n` and `run_stats`.
"n-gram" names the counted object, not the page. The code spelling is `ngram`.

**Storage and API: run.** `runs`, `run_stats`, `run_written`, `atlas-runs`, `/atlas/runs`,
`/atlas/runs/related`, `v3/runs/`. Internal. The other senses stay qualified: pipeline run, ruby
run.

The rules:

1. Reader-facing strings never say "run".
2. New interface code says "sequence"; `ngram` only for counts.
3. New storage code may say "run"; it stays behind the API and the interface.
4. Documentation qualifies the other senses: pipeline run, ruby run.

## Why the store keeps its name

"Run" is accurate for a consecutive span and is the word the storage layer already uses. The public
address already says sequence. A rename would touch the D1 tables, the R2 prefix, the Worker routes,
the publisher, the client and the tests for no reader-facing gain. If the store is renamed later, it
is one coordinated change across those layers; a component-only rename would leave the client
further from the API.

## Consequences

- The mapping is written down; the next contributor does not infer it from three layers.
- `RunTile`, `RunView`, `RunStrip` and `NgramGrid` keep their names. They are internal, and the
  boundary is now stated. A later rename follows the full migration.
- The schema's `granularity: sequence` (an unresolved run) is a data value, not the public noun. The
  IDS keeps its qualified "ideographic description sequence".

## References

- 0005 `docs/decisions/0005-storage-rebuild.md`, the `atlas-runs` layout.
- 0009 `docs/decisions/0009-ruby.md`, the ruby run.
- `src/glyph_atlas/ngrams.py`, `apps/review/src/lib/ngrams.js`,
  `apps/cloudflare/src/routes/runs.ts`, `apps/review/src/locales/en.json`.
- `docs/schema.md`, the `granularity` values.
