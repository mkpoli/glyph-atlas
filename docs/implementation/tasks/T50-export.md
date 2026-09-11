# T50 Release export, crops and attribution

Goal: a release directory that a user can download and cite, holding only what may be
redistributed, with the derived normalisations computed under a named policy.

Read first: `docs/licensing.md`, T04, T16, `docs/schema.md` (exports), `docs/datasheet-template.md`
(T51, written first).

Outputs
- `atlas export <dir>... --out out/<version> --licence CC-BY-SA-4.0 --review
  reviewed,double-reviewed,adjudicated [--include-machine] [--crops] --normalisation
  export-v1`: merged tables (T01 merge) filtered by review state and by eligibility (T06), checked
  separately for the annotations (always CC BY-SA 4.0), the text rights of the document and the
  image rights of the document; a document whose text is eligible and whose images are not keeps
  its records with coordinates and gets no crops; referential closure is maintained (every unit's
  page, line and document present).
- Crops as `crops/<bucket>/<id with ':' replaced by '_'>.jpg` from cached pages or through IIIF
  region URLs, only where the image rights allow redistribution; HI Lab crops copied from the
  cache.
- Derived columns on `units` from `data/vocab/normalisation-policies.yaml` (`export-v1`:
  `modern_kana` from the hentaigana table, `shinji` from the `shinji-kyuji` rows of the
  equivalence table); the policy name and version written into `MANIFEST.json`.
- `ATTRIBUTION.md` from T16; `COUNTS.md` by provenance (`method` and `review`) and by source;
  `datasheet.md` filled from the template; `CHECKSUMS.txt`; `MANIFEST.json` with the input
  dataset manifests, the policy, the command.
- A Hugging Face dataset card and a Zenodo metadata JSON generated from the same numbers.

Tests: export of a fixture with mixed rights and review states yields the expected files, counts,
closure, and crop names.

Acceptance: `out/0.1/` for the pilot builds; the datasheet's numbers equal `COUNTS.md`.

Size: medium. Depends on: T04, T16, T42, T51.
