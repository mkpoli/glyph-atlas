# T50 Release export, crops and attribution

Goal: a release directory that a user can download and cite, with only what may be redistributed.

Read first: `docs/licensing.md`, T16, `docs/schema.md` (exports), `docs/plan.md` section 6 step 8.

Outputs
- `atlas export <dir>... --out out/<version> --licence CC-BY-SA-4.0 --review reviewed,adjudicated
  [--crops] [--include-machine]`: merged tables filtered by eligibility of image and text rights and
  by review state; crops as `crops/<unit id>.jpg` cut from cached pages or through IIIF region URLs
  when the licence allows redistribution, and IIIF region URLs only otherwise; `ATTRIBUTION.md`
  from T16; `COUNTS.md` by provenance (imported, aligned, reviewed) and by source; `datasheet.md`
  from `docs/datasheet-template.md` with the numbers filled in; `CHECKSUMS.txt`.
- Derived normalisation columns (`modern_kana`, `shinji`) computed for `units` at export from T04,
  never stored in `work/`.
- A Hugging Face dataset card and a Zenodo metadata JSON generated from the same numbers.

Edge cases: a document eligible for text and not for images (record kept, no crop); units without a
page (HI Lab crops copied from the cache).

Tests: export of a fixture with mixed rights yields the expected files and counts.

Acceptance: `out/0.1/` for the pilot builds; the datasheet's numbers equal `COUNTS.md`.

Size: medium. Depends on: T16, T31, T42.
