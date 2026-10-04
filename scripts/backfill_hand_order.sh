#!/usr/bin/env bash
# Place the runs written before migration 0062 by how their first crop's letterforms were made.
#
#   scripts/backfill_hand_order.sh        run from anywhere; it works in apps/cloudflare
#
# Each step rewrites `hand_order` for the next SLICE runs in key order after the cursor in
# `hand_order_backfill`, then moves the cursor to the last of them; the runs are found by the primary
# key, so a step reads only its slice. A step that stops halfway is applied whole or not at all, so the
# script may be stopped and run again. It ends when a step finds no run past the cursor. Runs written
# after the migration are placed by its triggers and are rewritten here to the same value.
# D1_TARGET (default --remote) can be set, e.g. D1_TARGET="--local --persist-to state".
set -euo pipefail
cd "$(git -C "$(dirname "$0")" rev-parse --show-toplevel)/apps/cloudflare"
read -r -a target <<< "${D1_TARGET:---remote}"
slice="${SLICE:-20000}"
last="(SELECT max(first) FROM (SELECT first FROM unit_ngrams WHERE first>(SELECT after FROM hand_order_backfill) ORDER BY first LIMIT $slice))"
step="UPDATE unit_ngrams SET hand_order=coalesce((SELECT hand_order FROM units WHERE id=unit_ngrams.first),
  (SELECT hand_order FROM corpus_units WHERE id=unit_ngrams.first),1)
  WHERE first>(SELECT after FROM hand_order_backfill) AND first<=$last;
UPDATE hand_order_backfill SET after=coalesce($last,after);
SELECT after FROM hand_order_backfill;"
previous=""
while :; do
  after="$(bunx wrangler d1 execute glyph-atlas "${target[@]}" --json --command "$step" | jq -r '.[-1].results[0].after')"
  echo "placed through ${after:-(start)}"
  [ "$after" = "$previous" ] && break
  previous="$after"
done
