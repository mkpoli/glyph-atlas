# T51 Documentation for the first release

Goal: a reader can understand, cite and reuse release 0.1 from the repository alone.

Outputs
- `README.ja.md`: the README in Japanese, same content.
- `docs/datasheet-template.md`: motivation, composition, collection process, preprocessing, uses,
  distribution, maintenance, with placeholders the export fills.
- `CITATION.cff` with the Zenodo DOI once minted.
- `docs/reports/` index linking the pilot evaluation and the audit report.

Rules: prose states facts about the dataset; no hype, no process narration. Japanese text has no
space between Japanese and Latin characters.

Acceptance: both READMEs render on GitHub; `cffconvert --validate` passes.

Size: small. Depends on: T50.
