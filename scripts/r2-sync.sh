#!/usr/bin/env bash
# Move the collection store between local `work/` and the object-storage archive.
#
# The collection is bulk images and tables kept out of version control, so a
# cloud run has to fetch the store before it can advance it and put it back
# afterwards. Reads and writes are addressed by prefix and are idempotent, so a
# run that dies halfway leaves no worse state than a run that never started.
#
#   r2-sync.sh restore collections   # archive -> work/
#   r2-sync.sh mirror collections    # archive -> work/, dropping what the archive no longer holds
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
action="${1:?usage: r2-sync.sh restore|mirror|save <group>}"
group="${2:?usage: r2-sync.sh restore|mirror|save <group>}"

# Each group is a local directory and its key under the bucket.
case "$group" in
  collections)
    pairs=("work/honkoku-collection:collections/honkoku"
           "work/wikisource-collection:collections/wikisource")
    # A collector deletes a dataset it rebuilds (a Wikisource work that gained pages), so the
    # archive mirrors the store: a copy would bring the stale dataset back on the next restore.
    save=sync
    # R2 bills each request. Comparing modification times costs one HEAD per object, and listing
    # directory by directory one LIST per book; checksums come with a single recursive listing.
    compare=(--checksum --fast-list)
    # About 155k files of 18 KB on average: a transfer waits on its round trip far longer than on
    # bandwidth, so many run at once (at 8, a runner was still restoring the store after 35 minutes).
    parallel=(--transfers 64 --checkers 64)
    ;;
  images)
    pairs=("cache/images:images")
    # A local image cache holds only what this machine fetched; it adds to the archive.
    save=copy
    # The image cache is 166 GiB on a network drive: a checksum compare would read all of it on
    # every sync, where a modification-time compare reads only metadata.
    compare=(--fast-list)
    parallel=(--transfers 8 --checkers 16)
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
sys.exit(f"{sys.argv[1]} is in use" if busy else 0)' "$db"
  done
}

for pair in "${pairs[@]}"; do
  local="${pair%%:*}"
  remote="${pair#*:}"
  mkdir -p "$local"
  # A collector holds an flock on worker.lock while it runs; moving its store under it would
  # mix two states. Hold the same lock for the transfer.
  exec {lock}>"$local/worker.lock"
  if ! flock -n "$lock"; then
    echo "$local has a running worker; stop it first" >&2
    exit 1
  fi
  case "$action" in
    restore)
      echo "restoring r2:$bucket/$remote -> $local"
      checkpoint "$local"
      rclone copy "r2:$bucket/$remote" "$local" \
        "${parallel[@]}" "${compare[@]}" "${exclude[@]}"
      ;;
    mirror)
      # For a store carried over from an earlier run: a dataset the archive dropped since then would
      # otherwise ride back into it on the next save.
      echo "mirroring r2:$bucket/$remote -> $local"
      checkpoint "$local"
      rclone sync "r2:$bucket/$remote" "$local" \
        "${parallel[@]}" "${compare[@]}" "${exclude[@]}"
      ;;
    save)
      echo "saving $local -> r2:$bucket/$remote"
      checkpoint "$local"
      rclone "$save" "$local" "r2:$bucket/$remote" \
        "${parallel[@]}" "${compare[@]}" "${exclude[@]}"
      ;;
    *)
      echo "unknown action: $action" >&2
      exit 2
      ;;
  esac
  exec {lock}>&-
done
