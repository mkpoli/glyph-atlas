# T16 Rights resolver and attribution

Goal: a licence, an evidence URL and an attribution string on every document, from the holder's
own statements, and a generated attribution file.

Read first: `docs/licensing.md`, `data/vocab/holders.yaml`, the provider table on the Honkoku-Lines
card, `src/kuzushiji_atlas/schema.py` (`Rights`, `Licence`).

Outputs
- `data/vocab/licences.yaml`: known licence URLs and labels mapped to `Licence` values (Creative
  Commons deed URLs in every language variant, `PDM-1.0`, `CC0`, `RS-NOC-CR`, the NDL
  `iiif_license.html` page, the 京都大学 reuse page, the 国立公文書館 secondary-use page, the 木簡庫
  help page), each with `evidence` and `eligible: true|false|per-item`.
- `src/kuzushiji_atlas/rights.py`: `resolve(licence_string, licence_url, holder) -> Rights`;
  `manifest_rights(manifest_json) -> Rights | None` reading IIIF `license`, `rights`, `attribution`,
  `requiredStatement`; `eligible(rights, target=Licence.CC_BY_SA_4) -> bool`.
- `atlas rights resolve <dir>` fills `documents.image_rights` where empty, fetching the manifest
  when the document has one; `atlas rights report <dir>` prints documents and lines by licence and
  by eligibility; `atlas rights attribution <dir> --out ATTRIBUTION.md` writes one entry per source
  and per holder present, with the required strings.

Edge cases: a holder table entry with `per-item` rights (国書データベース), where only the manifest
decides; conflicting statements between `items.tsv` and the manifest (record both, prefer the
manifest, flag in `meta`).

Tests: mapping of ten licence strings; a manifest fixture with `requiredStatement`; the attribution
file for a two-source fixture.

Acceptance: `rights report work/honkoku-lines` reproduces the per-licence line counts of the
Honkoku-Lines card within 1%.

Size: medium. Depends on: T13.
