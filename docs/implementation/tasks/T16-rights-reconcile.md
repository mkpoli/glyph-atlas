# T16 Rights reconciliation across sources and the attribution file

Goal: one rights statement per document, with every piece of evidence kept, and an attribution
file that names each source and holder present in a dataset directory.

Read first: T06, `docs/licensing.md`, the outputs of T13, T14, T15.

Outputs
- `atlas rights resolve <dir>`: for every document, gathers evidence (the upstream licence string,
  the IIIF manifest's rights fields, the holder table, per-item statements on 国書データベース read
  from the manifest), stores all of it in `meta.rights_evidence` (a list of `{source, licence,
  url, fetched}`), and sets `image_rights` by precedence: the holder's per-item statement, then the
  manifest, then the upstream dataset's field, then the holder table. Disagreements are kept in
  the evidence list and counted.
- `atlas rights report <dir>`: documents, pages, lines and units by licence and by eligibility for
  the release licence; disagreements listed.
- `atlas rights attribution <dir> --out ATTRIBUTION.md`: one entry per source and per holder
  present, with the required credit line, licence, URL, and the obligations from
  `licences.yaml`; unchanged and modified material distinguished.

Edge cases: RS-NOC-CR (contractual restrictions; never eligible, holder named in the report for
follow-up); a holder whose terms page changed since `checked` (a `--recheck` flag refetches and
diffs).

Tests: a fixture with three documents whose evidence agrees, disagrees, and is missing.

Acceptance: on `work/honkoku-lines`, the report's per-licence line counts equal the card's provider
table exactly, and every difference between the upstream field and the manifest is listed in the
pull request.

Size: medium. Depends on: T06, T13.
