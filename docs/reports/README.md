# Reports

Evaluation and audit reports for the pilot. The cards named below write them.

| Report | Content | Card | Status |
| --- | --- | --- | --- |
| [pilot-calibration.md](pilot-calibration.md) | calibration pages, units, agreement before adjudication, minutes per page per reviewer, disagreement categories | T25 | not yet written |
| [pilot-evaluation.md](pilot-evaluation.md) | evaluation tables, precision against coverage curves, failure categories with examples, the run configuration hash | T26 | not yet written |
| `audit-<sample id>.md` | weighted precision of box, of label and of both together, Wilson intervals per stratum, cluster bootstrap over pages | T42 | one file per audit sample, named by sample id |
| [audit-scratch-stub-s0-n200.md](audit-scratch-stub-s0-n200.md) | the audit of a 205-unit sample of 20 calibration pages whose boxes came from a stub detector | T42 | a code-path check, not a measurement; replaced by the pilot sample |

The audit reports take the sample id in the file name, `docs/reports/audit-<sample id>.md`. The
scratch report is a worked example of the format and of the commands behind it, and says at the top
that its numbers mean nothing; the pilot run is the first sample that measures anything.

## DOI and the first deposit

`CITATION.cff` has no `doi` yet: the concept DOI is minted at the first Zenodo deposit. The steps for
the maintainer:

1. Run `atlas export` for the first release and check the release directory, including
   `MANIFEST.json`, `CHECKSUMS.txt` and the datasheet.
2. Deposit the release directory on Zenodo. The upload carries the dataset licence CC BY-SA 4.0 and
   the metadata JSON that `atlas export` writes; Zenodo mints a DOI for the version and a concept
   DOI for the record that covers later versions.
3. Add the concept DOI to `CITATION.cff` as `doi:`, and add the version DOI to the release notes and
   to the Zenodo metadata JSON of the following build.
4. Run `.venv/bin/cffconvert --validate` and commit `CITATION.cff`.
