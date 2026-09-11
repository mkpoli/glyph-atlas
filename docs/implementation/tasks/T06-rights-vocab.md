# T06 Rights vocabulary and resolver

Goal: turn the licence strings, URLs and holder names that upstreams use into `Rights` records
with evidence, so that every importer records rights the same way.

Read first: `docs/licensing.md`, `data/vocab/holders.yaml`, `src/kuzushiji_atlas/schema.py`
(`Rights`, `Licence`), the provider table on the Honkoku-Lines card.

Outputs
- `data/vocab/licences.yaml`: one entry per known statement: `match` (a licence id such as
  `CC-BY-4.0`, `PDM-1.0`, `CC0-1.0`, `RS-NOC-CR`, or a URL such as the Creative Commons deed URLs
  in every language variant, `https://dl.ndl.go.jp/ja/iiif_license.html`,
  `https://rmda.kulib.kyoto-u.ac.jp/reuse`, `https://www.digital.archives.go.jp/secondary-use`,
  `https://mokkanko.nabunken.go.jp/ja/?c=help`), `licence` (a `Licence` value), `evidence`,
  `eligible` (`true`, `false`, `per-item`), `obligations` (free text: credit line, change notice,
  link), `checked` date.
- `src/kuzushiji_atlas/rights.py`: `resolve(*, licence=None, url=None, holder=None) -> Rights`
  (unknown input gives `Licence.UNKNOWN` with the raw strings kept in `attribution` for later
  review); `manifest_rights(manifest) -> Rights | None` reading IIIF Presentation 2 `license`,
  `attribution` and 3 `rights`, `requiredStatement`; `eligible(rights, target) -> bool` where
  `target` is the release licence (`CC-BY-SA-4.0`): PD, PDM, CC0, CC BY 4.0, CC BY-SA 3.0 and 4.0,
  CC BY-SA 2.1 JP and `bespoke-free` are eligible; NC, ND, RS-NOC-CR, restricted and unknown are
  not.
- `atlas rights table` prints the vocabulary as Markdown for `docs/licensing.md`.

Edge cases: `(unspecified)` from Honkoku-Lines; a holder listed as `per-item`; two URLs for one
licence (http and https, `deed.ja` and `deed.en`).

Tests: twelve inputs covering each licence value; a manifest fixture with `requiredStatement`;
`eligible` for every `Licence` value.

Acceptance: `resolve` maps every distinct `image_license` and `image_license_url` pair that occurs
in `items.tsv` of Honkoku-Lines (listed in the pull request) to a non-unknown licence except
`(unspecified)`.

Size: small. Depends on: nothing.
