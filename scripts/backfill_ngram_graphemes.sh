#!/usr/bin/env bash
# Fold the runs written before migration 0071: give each its graphemes, its members folded to their
# graphemes' head characters, as the migration's triggers fold every run written since.
#
#   scripts/backfill_ngram_graphemes.sh        run from anywhere; it works in apps/cloudflare
#
# Each step folds the next SLICE runs in key order after the cursor in `graphemes_backfill`, then moves the
# cursor to the last of them; the runs are found by the primary key, so a step reads only its slice. A
# step is applied whole or not at all, so the script may be stopped and run again; a step D1 refuses is
# asked again after a growing pause, ten times at most. It ends when a step finds no run past the cursor.
# D1_TARGET (default --remote) can be set, e.g. D1_TARGET="--local --persist-to state".
set -euo pipefail
cd "$(git -C "$(dirname "$0")" rev-parse --show-toplevel)/apps/cloudflare"
read -r -a target <<< "${D1_TARGET:---remote}"
slice="${SLICE:-20000}"
last="(SELECT max(first) FROM (SELECT first FROM unit_ngrams WHERE first>(SELECT after FROM graphemes_backfill) ORDER BY first LIMIT $slice))"
step="UPDATE unit_ngrams SET graphemes=coalesce((SELECT h.character FROM characters c JOIN characters h ON h.code_point=json_extract(c.data,'\$.grapheme.code_point') WHERE c.character=coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.first),(SELECT character FROM units WHERE id=unit_ngrams.first),(SELECT character FROM corpus_units WHERE id=unit_ngrams.first)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.first))),coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.first),(SELECT character FROM units WHERE id=unit_ngrams.first),(SELECT character FROM corpus_units WHERE id=unit_ngrams.first)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.first)))
  ||coalesce((SELECT h.character FROM characters c JOIN characters h ON h.code_point=json_extract(c.data,'\$.grapheme.code_point') WHERE c.character=coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.second),(SELECT character FROM units WHERE id=unit_ngrams.second),(SELECT character FROM corpus_units WHERE id=unit_ngrams.second)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.second))),coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.second),(SELECT character FROM units WHERE id=unit_ngrams.second),(SELECT character FROM corpus_units WHERE id=unit_ngrams.second)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.second)))
  ||iif(third IS NULL,'',coalesce((SELECT h.character FROM characters c JOIN characters h ON h.code_point=json_extract(c.data,'\$.grapheme.code_point') WHERE c.character=coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.third),(SELECT character FROM units WHERE id=unit_ngrams.third),(SELECT character FROM corpus_units WHERE id=unit_ngrams.third)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.third))),coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.third),(SELECT character FROM units WHERE id=unit_ngrams.third),(SELECT character FROM corpus_units WHERE id=unit_ngrams.third)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.third))))
  WHERE first>(SELECT after FROM graphemes_backfill) AND first<=$last;
UPDATE graphemes_backfill SET after=coalesce($last,after);
SELECT after FROM graphemes_backfill;"
previous=""; fails=0
while :; do
  out="$(bunx wrangler d1 execute glyph-atlas "${target[@]}" --json --command "$step" 2>&1)" || true
  if ! after="$(jq -er 'if type=="array" then .[-1].results[0].after else empty end' <<< "$out" 2>/dev/null)"; then
    fails=$((fails + 1))
    echo "step refused ($fails): $(grep -oiE '"(text|message)": *"[^"]{0,160}' <<< "$out" | head -1)" >&2
    [ "$fails" -ge 10 ] && { echo "giving up" >&2; exit 1; }
    sleep $((fails * 15)); continue
  fi
  fails=0
  echo "folded through ${after:-(start)}"
  [ "$after" = "$previous" ] && break
  previous="$after"
done
