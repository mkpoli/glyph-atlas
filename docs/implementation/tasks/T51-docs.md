# T51 Datasheet template, Japanese README and citation

Goal: a reader can understand, cite and reuse a release from the repository alone.

Outputs
- `docs/datasheet-template.md`, written before T50 uses it: motivation, composition, collection
  process, preprocessing, uses, distribution, maintenance, with `{{placeholders}}` that T50 fills
  from `COUNTS.md`, `MANIFEST.json` and the source registry.
- `README.ja.md`: the README in Japanese, same content and structure; Japanese text has no space
  between Japanese and Latin characters.
- `CITATION.cff` with the concept DOI once Zenodo mints it (the maintainer mints it at the first
  release; the card records the steps).
- `docs/reports/README.md` linking the pilot and audit reports.

Rules: prose states facts about the dataset; no hype, no process narration.

Acceptance: both READMEs render on GitHub; `cffconvert --validate` passes; T50's export fills every
placeholder (an unfilled `{{…}}` fails the export).

Size: small. Depends on: nothing for the template; T50 for the final numbers.
