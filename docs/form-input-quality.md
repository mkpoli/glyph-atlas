# Form clustering input quality

Shape clustering assumes that each crop belongs to its labelled character family.
An incorrect character-to-box pairing violates that assumption before embedding
begins. A tighter cluster can still contain the wrong character.

On 2026-09-26, the published form decision export contained 293 decisions. Reports
of wrong characters or damaged crops covered 3,272 glyphs. The clustering input
selection used only an active character record, a family, and available pixels.
It admitted rejected alignments and ignored these reports and production metadata.

| Source | Evidence in the source tables | Treatment |
| --- | --- | --- |
| 北海随筆, legacy Atlas alignment | All 40 boxed lines have backward steps in their token-to-box order. The earlier repair report records spaces receiving boxes and neighbouring characters receiving one another's crops. | Withhold machine placements on broken lines; retain human-confirmed placements. |
| 陸奥国郷帳 volume 5 | 936 of 938 boxed lines have backward steps; 11,427 of 12,885 boxed units carry `review=rejected`. | Exclude 955 of its 957 existing form candidates. |
| `68eb3417ed31bd40b47322289459eeec`, 風俗畫報臨時増刊第百二十八號 洪水被害録（下） | Movable type is recorded in `production-overrides.yaml`. Of 53,782 boxed units, 46,316 carry `review=rejected`; 19,284 entered form clustering. | Apply the existing review production scope and exclude all 19,284 from form clustering. |

The backward-step counts above compare successive boxed tokens' vertical positions.
The admission check is narrower: it requires non-overlapping positions in the same
physical column and respects horizontal lines. A move between separate columns is
allowed. These counts describe extraction defects, not an estimate of character
recognition accuracy.

Recent 北海随筆 reviews also cover `ar:hokkai-zuihitsu--tsukuba:…` OCR imports.
Those records differ from the legacy `hk:…` alignment. The saved corrections include
夕→朝, 夕→川, 壇→理, 辞→弾 and 自→を. In their saved OCR contexts, 朝 and を are one
position earlier than the labelled crop, while 川 is one position later. 理 and 弾
do not occur in their respective OCR contexts. The evidence supports both local
shifts and recognition errors. Repairing their positions requires checking the
corresponding source lines; a book-wide offset cannot resolve these cases. The
clustering cleanup does not relabel them.

## Admission

`atlas forms cluster` checks source review state, withheld repair metadata, line
order, multi-glyph segmentation, production metadata, and saved form decisions
before embedding. A current character-review export can also be supplied with
`--reviews`. Its crop and source label must match the candidate before its decision
is used. Form decisions follow the existing glyph-over-cluster precedence;
withdrawals and inheritance resolve through the same decision log as the UI.

A `mixed` cluster remains available for form assignment. A `character` or `crop`
report excludes its affected glyphs. A human confirmation or an effective form
assignment preserves a placement despite a machine rejection or broken line. The
production scope and explicit human error reports still apply. Machine eligibility
never marks a glyph human-reviewed.

Every generated revision carries `quality.json`. Its fingerprint includes the
admission policy, review evidence, production overrides, and document metadata.

## Audit and cleanup

Read the site's current exports before generating a revision:

```sh
mkdir -p work/form-reviews
curl --fail 'https://atlas.mkpo.li/atlas/forms/decisions.jsonl' \
  -o work/form-reviews/decisions.jsonl
curl --fail 'https://atlas.mkpo.li/atlas/reviews.json?include_processed=true' \
  -o work/form-reviews/reviews.json
export ATLAS_FORM_DECISIONS=work/form-reviews/decisions.jsonl

atlas forms audit --reviews work/form-reviews/reviews.json \
  --out work/form-reviews/audit.json
atlas forms clean work/forms/current --out work/forms-quality \
  --reviews work/form-reviews/reviews.json
```

`clean` uses the existing embeddings and cluster memberships. It removes unsafe
members, refreshes counts and representatives, and writes an exclusion ledger. The
scores refer to the original centres; the filtered revision uses size order until
shape neighbours are recomputed. A changed source unit or page table requires
fresh clustering so that old embeddings cannot be attached to changed crops.

The measured cleanup of revision `e24a2ac8ef6b00d7` excludes 40,330 of 825,840
clustered glyphs and retains 785,510. It preserves the original revision and review
journals. No inference or model training is involved. The broader candidate audit
excludes 43,785 records, including candidates without cached pixels.

Publication uses the normal form export after code is merged. Export with
`ATLAS_FORM_CLUSTERS=work/forms-quality/current` to select the filtered revision.
The original clustering remains available for recovery; no source records are
deleted. Excluded crops require re-extraction or an appropriate source correction
before a later clustering can admit them.

Hosted `form_units.clustered` distinguishes visible membership from saved identity
decisions. Publication replays decisions for excluded glyphs too, so corpus details
and subsequent corpus imports retain their corrected characters and crop reports.
Inherited cluster revisions retain existing mixed marks. Apply migration 0026 and
deploy the merged Worker before publishing a filtered revision.
