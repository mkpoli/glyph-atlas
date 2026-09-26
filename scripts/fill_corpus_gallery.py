# /// script
# requires-python = ">=3.11"
# dependencies = ["boto3"]
# ///
"""Write the SQL that brings D1's `corpus_gallery` up to date with the published corpus.

The homepage gallery deals copies of the records of the corpus glyphs whose `shuffle` falls below
2^22 (migration 0019). This reads which of those D1 lacks a current copy of, reads each record's bytes
from its R2 pack with a ranged S3 request, and writes one SQL file that removes copies whose record
has since been republished and inserts the missing ones. Run it after every publication that writes
`corpus_units`:

    uv run scripts/fill_corpus_gallery.py gallery.sql
    (cd apps/cloudflare && bunx wrangler d1 execute glyph-atlas --remote --file ../../gallery.sql)

The S3 endpoint and keys are those of an rclone remote (`r2` by default) that reads the site's bucket.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import boto3
from botocore.config import Config

CLOUDFLARE = Path(__file__).resolve().parents[1] / "apps/cloudflare"
# Matches `SAMPLE_RANGE` in the Worker and the cut migration 0019 describes.
SAMPLE_RANGE = 2**22
MISSING = f"""SELECT c.id,c.shuffle,c.object,c.offset,c.size FROM corpus_units c WHERE c.shuffle<{SAMPLE_RANGE}
  AND NOT EXISTS (SELECT 1 FROM corpus_gallery g WHERE g.id=c.id AND g.object=c.object AND g.offset=c.offset)"""
STALE = """DELETE FROM corpus_gallery WHERE NOT EXISTS (SELECT 1 FROM corpus_units c
  WHERE c.id=corpus_gallery.id AND c.object=corpus_gallery.object AND c.offset=corpus_gallery.offset);"""


def missing() -> list[dict]:
    out = subprocess.run(["bunx", "wrangler", "d1", "execute", "glyph-atlas", "--remote", "--json", "--command", MISSING],
                         cwd=CLOUDFLARE, check=True, capture_output=True, text=True).stdout
    return json.loads(out)[0]["results"]


def bucket_client(remote: str, jobs: int):
    """An S3 client with the endpoint and keys the rclone remote `remote` holds."""
    dump = subprocess.run(["rclone", "config", "dump"], check=True, capture_output=True, text=True).stdout
    config = json.loads(dump)[remote]
    return boto3.client("s3", endpoint_url=config["endpoint"], aws_access_key_id=config["access_key_id"],
                        aws_secret_access_key=config["secret_access_key"], region_name="auto",
                        config=Config(max_pool_connections=jobs))


def record(s3, bucket: str, row: dict) -> str:
    end = row["offset"] + row["size"] - 1
    data = s3.get_object(Bucket=bucket, Key=row["object"], Range=f"bytes={row['offset']}-{end}")["Body"].read()
    if len(data) != row["size"]:
        raise ValueError(f"{row['id']}: read {len(data)} of {row['size']} bytes from {row['object']}")
    text = data.decode()
    if json.loads(text).get("id") != row["id"]:
        raise ValueError(f"{row['id']}: the bytes at {row['object']}:{row['offset']} are another record")
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output", type=Path, help="the SQL file to write")
    parser.add_argument("--remote", default="r2", help="rclone remote whose S3 endpoint and keys read the bucket")
    parser.add_argument("--bucket", default="glyph-atlas")
    parser.add_argument("--jobs", type=int, default=64, help="simultaneous R2 reads")
    args = parser.parse_args()
    rows = missing()
    s3 = bucket_client(args.remote, args.jobs)
    with ThreadPoolExecutor(args.jobs) as pool:
        records = list(pool.map(lambda row: record(s3, args.bucket, row), rows))
    quote = sqlite3.connect(":memory:")
    with args.output.open("w") as sql:
        sql.write(STALE + "\n")
        for row, text in zip(rows, records):
            values = quote.execute("SELECT quote(?),quote(?),quote(?)", (row["id"], row["object"], text)).fetchone()
            sql.write(f"INSERT OR REPLACE INTO corpus_gallery VALUES({values[0]},{row['shuffle']},{values[1]},"
                      f"{row['offset']},{values[2]});\n")
    print(f"{len(rows)} records written to {args.output}")


if __name__ == "__main__":
    main()
