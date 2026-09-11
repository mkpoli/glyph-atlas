# T23 Alignment of transcription lines to detected characters

Goal: unit records with boxes for the characters of a transcribed line, with confidences, an
explicit unresolved state, and no invented certainty.

Read first: `docs/plan.md` sections 5 and 6, `docs/schema.md` (units, groups), T03, T04, T21, T22,
T24.

Interface: `atlas align <dir> [--document ID] [--pages a,b] --run <name>` reads lines with boxes,
reads page images from the cache (fetching with T02 when absent), and writes units and groups
into the directory. `--run` names a configuration file under `models/align/runs/<name>.yaml` (model
paths, costs, thresholds, equivalence policy); unit ids are `{line_id}:{run hash}:{seq}` as
`docs/schema.md` defines, so a rerun with the same configuration overwrites its own units and
never those of another run or of reviewers.

Steps
1. Detection once per page (T21), then assignment of each detection to the line whose box contains
   its centre; a detection inside two line boxes goes to the line with the larger overlap; a
   detection outside every line box is kept in `work/<dir>/unassigned.parquet`.
2. Layout tree per line from T03: main text spans, 割書 containers with their ordered columns,
   ruby, notes. Geometry assigns detections to a container: for a 割書 span the detections inside
   the container's y-range are clustered into columns by x; ambiguous cases leave the span
   unresolved (a group, no units).
3. Tokens: main-text characters in reading order per container, combining marks attached to their
   base, `□` as a gap token, `■` as an unreadable token; each token keeps its raw span.
4. Dynamic programming per container over tokens × ordered detections with transitions: match
   (one token, one detection), skip-token, skip-detection, split (one token, two adjacent
   detections whose union is classified), merge (two tokens, one detection, scored by the
   classifier on candidate cuts at the detection's ink minima; when no cut scores above a floor
   the pair becomes an unresolved group), and gap (a `□` consumes zero or one detection). Match
   cost is `-log score_set(crop, equivalents(token, policy))` with a probability floor of 1e-4;
   the other costs are configuration values tuned on the calibration truth (T25). Ties break by
   preferring match, then skip-detection, then skip-token. Initial and terminal states allow
   leading and trailing skips.
5. Units: `box`, `text_source` (the raw span), `reading` (the token; historical spelling
   alternatives from the source's policy are recorded in `candidates`), `unicode` and
   `classification=identified` for kanji, marks and punctuation; for kana the modern kana code
   point with `classification=unassessed`; `confidence.detection` (detector score),
   `confidence.text` (the match probability), `confidence.segmentation` (1 for a match, the cut
   score for a merge, the union classification for a split), `method="detect-align"`,
   `review=machine`, `active=true`; groups for unresolved spans with `granularity=sequence` on
   their member units when any exist, else a group alone.
6. Acceptance per unit: a unit is `review=machine` when its match probability is at least the
   run's `accept` threshold and its line's path is unambiguous (the best path's cost is lower than
   the second best by the run's `margin`); otherwise `review=rejected`, kept.

Edge cases: far more detections than tokens (illustration in the line box): the DP skips them and
the line is flagged in `meta.excess_detections`; empty `text`; 見せ消ち, where both segments get
units and the cancellation relation goes in `meta.cancels`; ruby tokens get units without boxes.

Tests: fixture detector and classifier returning fixed boxes and scores on lines with a split, a
merge, a gap, a 割書 with two columns, and a leading skip; ids deterministic across two runs.

Acceptance: on the held-out pilot pages (T26), joint precision (IoU ≥ 0.5 and label match) of
accepted units ≥ 0.95 at coverage ≥ 0.80 on printed main text; precision and coverage curves per
document in `models/align/README.md`; costs and thresholds recorded per run.

Size: large. Depends on: T02, T03, T04, T21, T22, T25.
