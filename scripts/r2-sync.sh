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
# R2_SECRET_ACCESS_KEY. R2_BUCKET names the archive bucket.
set -euo pipefail

export RCLONE_CONFIG_R2_TYPE=s3
export RCLONE_CONFIG_R2_PROVIDER=Cloudflare
export RCLONE_CONFIG_R2_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID:?R2_ACCESS_KEY_ID is required}"
export RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY:?R2_SECRET_ACCESS_KEY is required}"
export RCLONE_CONFIG_R2_ENDPOINT="https://${R2_ACCOUNT_ID:?R2_ACCOUNT_ID is required}.r2.cloudflarestorage.com"

bucket="${R2_BUCKET:-kuzushiji-atlas}"
action="${1:?usage: r2-sync.sh restore|save <group>}"
group="${2:?usage: r2-sync.sh restore|save <group>}"

# Each group is a local directory and its key under the bucket.
case "$group" in
  collections)
    pairs=("work/honkoku-collection:collections/honkoku"
           "work/wikisource-collection:collections/wikisource")
    ;;
  images)
    pairs=("cache/images:images")
    ;;
  *)
    echo "unknown group: $group" >&2
    exit 2
    ;;
esac

for pair in "${pairs[@]}"; do
  local="${pair%%:*}"
  remote="${pair#*:}"
  mkdir -p "$local"
  case "$action" in
    restore)
      echo "restoring r2:$bucket/$remote -> $local"
      rclone copy "r2:$bucket/$remote" "$local" \
        --transfers 8 --checkers 16 --exclude '*.lock' --exclude '*.tmp'
      ;;
    save)
      echo "saving $local -> r2:$bucket/$remote"
      rclone copy "$local" "r2:$bucket/$remote" \
        --transfers 8 --checkers 16 --exclude '*.lock' --exclude '*.tmp'
      ;;
    *)
      echo "unknown action: $action" >&2
      exit 2
      ;;
  esac
done
