#!/usr/bin/env bash
# Move the collection store between local `work/` and the object-storage archive.
#
# The collection is bulk images and tables kept out of version control, so a
# cloud run has to fetch the store before it can advance it and put it back
# afterwards. Reads and writes are addressed by prefix and are idempotent, so a
# run that dies halfway leaves no worse state than a run that never started.
#
#   r2-sync.sh restore collections   # archive -> work/
#   r2-sync.sh save collections      # work/ -> archive
#
# Credentials come from R2_ACCOUNT_ID, R2_ACCESS_KEY_ID and
# R2_SECRET_ACCESS_KEY. R2_BUCKET names the archive bucket, `glyph-atlas-harvest` by default.
# The archive stays out of `glyph-atlas`, the bucket the site serves its media from.
set -euo pipefail

export RCLONE_CONFIG_R2_TYPE=s3
export RCLONE_CONFIG_R2_PROVIDER=Cloudflare
export RCLONE_CONFIG_R2_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID:?R2_ACCESS_KEY_ID is required}"
export RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY:?R2_SECRET_ACCESS_KEY is required}"
export RCLONE_CONFIG_R2_ENDPOINT="https://${R2_ACCOUNT_ID:?R2_ACCOUNT_ID is required}.r2.cloudflarestorage.com"
# The token is limited to one bucket's objects, so rclone must not try to check or create the
# bucket itself; these are the settings Cloudflare documents for such a token.
export RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true
export RCLONE_CONFIG_R2_ACL=private

bucket="${R2_BUCKET:-glyph-atlas-harvest}"
action="${1:?usage: r2-sync.sh restore|save <group>}"
group="${2:?usage: r2-sync.sh restore|save <group>}"

# Each group is a local directory and its key under the bucket.
case "$group" in
  collections)
    pairs=("work/honkoku-collection:collections/honkoku"
           "work/wikisource-collection:collections/wikisource")
    # A collector deletes a dataset it rebuilds (a Wikisource work that gained pages), so the
    # archive mirrors the store: a copy would bring the stale dataset back on the next restore.
    save=sync
    ;;
  images)
    pairs=("cache/images:images")
    # A local image cache holds only what this machine fetched; it adds to the archive.
    save=copy
    ;;
  *)
    echo "unknown group: $group" >&2
    exit 2
    ;;
esac

# Publication state stays on the machine that publishes: its generations, the `current` link and
# its receipt. Cloud runs never publish, so their copies would only overwrite the publisher's.
# SQLite sidecars never travel: a stale `-wal` beside a newer database is replayed onto it.
exclude=(--exclude '*.lock' --exclude '*.tmp' --exclude '*.sqlite-wal' --exclude '*.sqlite-shm'
         --exclude '/generations/**' --exclude /current --exclude /.current-next
         --exclude /published.json --exclude /published.json.tmp)

# Fold each queue's write-ahead log into its database, so the database file alone is complete.
checkpoint() {
  find "$1" -maxdepth 1 -name '*.sqlite' -print0 | while IFS= read -r -d '' db; do
    python3 -c 'import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
busy = db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]
db.close()
sys.exit(f"{sys.argv[1]} is in use; stop its worker first" if busy else 0)' "$db"
  done
}

for pair in "${pairs[@]}"; do
  local="${pair%%:*}"
  remote="${pair#*:}"
  mkdir -p "$local"
  case "$action" in
    restore)
      echo "restoring r2:$bucket/$remote -> $local"
      checkpoint "$local"
      rclone copy "r2:$bucket/$remote" "$local" \
        --transfers 8 --checkers 16 "${exclude[@]}"
      ;;
    save)
      echo "saving $local -> r2:$bucket/$remote"
      checkpoint "$local"
      rclone "$save" "$local" "r2:$bucket/$remote" \
        --transfers 8 --checkers 16 "${exclude[@]}"
      ;;
    *)
      echo "unknown action: $action" >&2
      exit 2
      ;;
  esac
done
