#!/usr/bin/env bash
# Count the runs written before migration 0074 by their graphemes: each written text under its graphemes
# (`ngram_forms`) and their sums (`ngram_counts`), on the whole site and in each book, as the migration's
# triggers count every run past it. Run it after applying 0074 and before deploying the Worker that reads
# them; until it is done, Explore's counts hold only the runs it has reached.
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
  # A form deleted takes its share of its graphemes' count with it (0074), and the last one the row.
  while [ "$(ask "DELETE FROM ngram_forms WHERE (scope,size,graphemes,text) IN (SELECT scope,size,graphemes,text FROM ngram_forms LIMIT $slice);
    SELECT EXISTS(SELECT 1 FROM ngram_forms) AS more" | jq -r .more)" = 1 ]; do echo "emptying the counts"; done
fi
last="(SELECT max(first) FROM (SELECT first FROM unit_ngrams WHERE first>(SELECT after FROM ngram_counts_backfill) ORDER BY first LIMIT $slice))"
count() { echo "INSERT INTO ngram_forms(scope,size,graphemes,text,n,down)
  SELECT s.value,g.size,g.graphemes,g.text,count(*),sum(g.vertical) FROM unit_ngrams g, json_each(json_array('',nullif(g.document,''))) s
  WHERE g.first>(SELECT after FROM ngram_counts_backfill) $1
    AND g.text IS NOT NULL AND g.graphemes IS NOT NULL AND s.value IS NOT NULL
  GROUP BY s.value,g.size,g.graphemes,g.text
  ON CONFLICT(scope,size,graphemes,text) DO UPDATE SET n=n+excluded.n,down=down+excluded.down;"; }
step="$(count "AND g.first<=$last")
UPDATE ngram_counts_backfill SET after=coalesce($last,after);
SELECT after FROM ngram_counts_backfill;"
previous="__start__"
while :; do
  after="$(ask "$step" | jq -r .after)"
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
