#!/usr/bin/env bash
# Fold the runs written before migration 0071: give each its graphemes, its members folded to their
# graphemes' head characters, as the migration's triggers fold every run written since.
#
#   scripts/backfill_ngram_graphemes.sh        run from anywhere; it works in apps/cloudflare
#
# Run it again after a grapheme publication (`export_graphemes.py`): one that moves a character to another
# grapheme leaves the runs folded as they were. It starts from the first run (RESUME=1 continues after
# the cursor in `graphemes_backfill` instead). Each step folds the next SLICE runs in key order after the
# cursor, then moves the cursor to the last of them; the runs are found by the primary key, so a step
# reads only its slice. A step is applied whole or not at all; a step D1 refuses is asked again after a
# growing pause, ten times at most. When a step finds no run past the cursor, it moves the catalogue
# version (`units_refreshed_at`), so answers the edge kept while runs were still unfolded are not served.
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
[ "${RESUME:-0}" = 1 ] || bunx wrangler d1 execute glyph-atlas "${target[@]}" --command "UPDATE graphemes_backfill SET after=''" >/dev/null
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
bunx wrangler d1 execute glyph-atlas "${target[@]}" --command \
  "INSERT OR REPLACE INTO metadata(key,value) VALUES('units_refreshed_at',json_quote(strftime('%Y-%m-%dT%H:%M:%fZ','now')))" >/dev/null
echo "done"
