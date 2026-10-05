#!/usr/bin/env bash
# Count the runs written before migration 0074 by their graphemes: each written text under its graphemes
# on the whole site (`ngram_forms`, whose triggers sum them into the site's `ngram_counts`), and each
# book's count of them (`ngram_counts`), as the migration's triggers count every run past it. Apply 0074, deploy the Worker that reads the new counts, then run it:
# the old Worker reads columns 0074 removes, and until the script is done Explore's counts hold only the
# runs it has reached.
#
#   scripts/backfill_ngram_counts.sh        run from anywhere; it works in apps/cloudflare
#
# It starts by emptying the counts and moving the cursor in `ngram_counts_backfill` back to the start, so
# the triggers leave every run to it (RESUME=1 continues after the cursor instead). Each step counts the
# runs of the next SLICE first crops in key order after the cursor, then moves the cursor to the last of
# them; the runs are found by the primary key, so a step reads only its slice. A step is applied whole or
# not at all; a step D1 refuses is asked again after a growing pause, ten times at most. When a step finds
# no run past the cursor, the runs written since are counted and the cursor moves past every run in one
# batch, so the triggers count each one from then on, and the catalogue version (`units_refreshed_at`)
# moves, so answers the edge kept while the counts were partial are not served.
# D1_TARGET (default --remote) can be set, e.g. D1_TARGET="--local --persist-to state".
set -euo pipefail
cd "$(git -C "$(dirname "$0")" rev-parse --show-toplevel)/apps/cloudflare"
read -r -a target <<< "${D1_TARGET:---remote}"
slice="${SLICE:-5000}"
d1() { bunx wrangler d1 execute glyph-atlas "${target[@]}" --json --command "$1" 2>&1; }
# One statement batch, asked again while D1 refuses it; prints the last statement's first row as JSON.
ask() {
  local out fails=0
  while :; do
    out="$(d1 "$1")" || true
    if jq -ec 'if type=="array" then .[-1].results[0] else empty end' <<< "$out" 2>/dev/null; then return; fi
    fails=$((fails + 1))
    echo "step refused ($fails): $(grep -oiE '"(text|message)": *"[^"]{0,160}' <<< "$out" | head -1)" >&2
    [ "$fails" -ge 10 ] && { echo "giving up" >&2; exit 1; }
    sleep $((fails * 15))
  done
}
if [ "${RESUME:-0}" != 1 ]; then
  ask "UPDATE ngram_counts_backfill SET after=''; SELECT after FROM ngram_counts_backfill" >/dev/null
  # A form deleted takes its share of the site's count with it (0074), and the last one the row; a book's
  # rows are deleted outright. A plain assignment, so a step that gives up stops the script rather than
  # ending the loop.
  while :; do
    left="$(ask "DELETE FROM ngram_forms WHERE (size,graphemes,text) IN (SELECT size,graphemes,text FROM ngram_forms LIMIT $slice);
      DELETE FROM ngram_counts WHERE (scope,size,graphemes) IN (SELECT scope,size,graphemes FROM ngram_counts WHERE scope<>'' LIMIT $slice);
      SELECT EXISTS(SELECT 1 FROM ngram_counts) AS more")"
    [ "$(jq -r .more <<< "$left")" = 1 ] || break
    echo "emptying the counts"
  done
fi
last="(SELECT max(first) FROM (SELECT first FROM unit_ngrams WHERE first>(SELECT after FROM ngram_counts_backfill) ORDER BY first LIMIT $slice))"
count() { echo "INSERT INTO ngram_forms(size,graphemes,text,n,down)
  SELECT size,graphemes,text,count(*),sum(vertical) FROM unit_ngrams
  WHERE first>(SELECT after FROM ngram_counts_backfill) $1 AND text IS NOT NULL AND graphemes IS NOT NULL
  GROUP BY size,graphemes,text
  ON CONFLICT(size,graphemes,text) DO UPDATE SET n=n+excluded.n,down=down+excluded.down;
INSERT INTO ngram_counts(scope,size,graphemes,n,down)
  SELECT document,size,graphemes,count(*),sum(vertical) FROM unit_ngrams
  WHERE first>(SELECT after FROM ngram_counts_backfill) $1 AND text IS NOT NULL AND graphemes IS NOT NULL AND document<>''
  GROUP BY document,size,graphemes
  ON CONFLICT(scope,size,graphemes) DO UPDATE SET n=n+excluded.n,down=down+excluded.down;"; }
step="$(count "AND first<=$last")
UPDATE ngram_counts_backfill SET after=coalesce($last,after);
SELECT after FROM ngram_counts_backfill;"
previous="__start__"
while :; do
  row="$(ask "$step")"
  after="$(jq -r .after <<< "$row")"
  [ "$after" = "$previous" ] && break
  echo "counted through ${after:-(start)}"
  previous="$after"
done
# The runs written since the last step are counted with the move, so none falls between the cursor and the triggers.
ask "$(count '')
  UPDATE ngram_counts_backfill SET after=char(1114111);
  INSERT OR REPLACE INTO metadata(key,value) VALUES('units_refreshed_at',json_quote(strftime('%Y-%m-%dT%H:%M:%fZ','now')));
  SELECT 1 AS done" >/dev/null
echo "done"
