# /// script
# requires-python = ">=3.11"
# dependencies = ["boto3"]
# ///
"""Write the SQL that brings D1's `corpus_gallery` up to date with the published corpus.

The homepage gallery deals copies of the records of the corpus glyphs whose `shuffle` falls below
2^22 (migration 0020), and of every glyph of a script the corpus holds few of (`WHOLE`), each with
its category (0079), so a script group deals its own. This reads which of those D1 lacks a current
copy of, reads each record's bytes from its R2 pack with a ranged S3 request, and writes SQL parts
under D1's import size that remove copies whose record has since been republished, insert the missing
ones and bring every copy's category to its glyph's current character. `publish_cloudflare.sh`
runs it and applies the parts after every publication; by hand:

    uv run scripts/fill_corpus_gallery.py gallery
    cd apps/cloudflare && for part in ../../gallery/*.sql; do ../../scripts/d1_import.sh "$part" || { echo "stopped at $part" >&2; break; }; done

The S3 endpoint and keys are those of an rclone remote (`r2` by default) that reads the site's bucket.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
from cloudflare_schema import GUGYEOL, HANGUL, category_of

CLOUDFLARE = ROOT / "apps/cloudflare"
# Matches `SAMPLE_RANGE` in the Worker and the cut migration 0020 describes.
SAMPLE_RANGE = 2**22
# D1 refuses a statement over 100 KB; parts stay under the import size `seal_cloudflare_records.py` uses.
STATEMENT_BYTES = 100_000
PART_BYTES = 90 * 1024**2
# A read that fails mid-stream is not retried by botocore itself.
READ_ATTEMPTS = 4
# Scripts whose every corpus glyph has a copy, by the code point ranges their characters start in:
# each range is one stretch of the `corpus_character` index.
WHOLE = {"hangul": HANGUL, "gugyeol": GUGYEOL}
WANTED = " UNION ".join(
    [f"SELECT id,shuffle,object,offset,size,character FROM corpus_units WHERE shuffle<{SAMPLE_RANGE}"]
    + [f"SELECT id,shuffle,object,offset,size,character FROM corpus_units WHERE character>=char({a}) AND character<char({b + 1})"
       for ranges in WHOLE.values() for a, b in ranges])
MISSING = f"""SELECT c.id,c.shuffle,c.object,c.offset,c.size,c.character FROM ({WANTED}) c
  WHERE NOT EXISTS (SELECT 1 FROM corpus_gallery g WHERE g.id=c.id AND g.object=c.object AND g.offset=c.offset)"""
# Every current copy with the character its glyph is now filed under, which a form decision can move
# to another script without republishing the record.
COPIES = """SELECT g.id,g.shuffle,g.category,c.character FROM corpus_gallery g JOIN corpus_units c
  ON c.id=g.id AND c.object=g.object AND c.offset=g.offset"""
STALE = """DELETE FROM corpus_gallery WHERE NOT EXISTS (SELECT 1 FROM corpus_units c
  WHERE c.id=corpus_gallery.id AND c.object=corpus_gallery.object AND c.offset=corpus_gallery.offset);"""


def remote(sql: str) -> list[dict]:
    out = subprocess.run(["bunx", "wrangler", "d1", "execute", "glyph-atlas", "--remote", "--json", "--command", sql],
                         cwd=CLOUDFLARE, check=True, capture_output=True, text=True).stdout
    return json.loads(out)[0]["results"]


def in_lists(head: str, quoted: list[str]) -> list[str]:
    """`head` followed by runs of `quoted` as `IN (…)` lists, each statement under D1's limit."""
    statements, run, size = [], [], len(head) + 2
    for value in quoted:
        if run and size + len(value) + 1 > STATEMENT_BYTES:
            statements.append(head + ",".join(run) + ");")
            run, size = [], len(head) + 2
        run.append(value)
        size += len(value) + 1
    return statements + ([head + ",".join(run) + ");"] if run else [])


def recategorised(rows: list[dict], quote) -> list[str]:
    """Statements that bring each copy in `rows` to its character's category: a copy without one or
    of another script is given its own, and one only its old script was copied whole for is removed."""
    moved: dict[str, list[str]] = {}
    unwanted: list[str] = []
    for row in rows:
        category = category_of(row["character"])
        if category == row["category"]:
            continue
        quoted = quote.execute("SELECT quote(?)", (row["id"],)).fetchone()[0]
        if row["shuffle"] >= SAMPLE_RANGE and category not in WHOLE:
            unwanted.append(quoted)
        else:
            moved.setdefault(category, []).append(quoted)
    return ([s for category, quoted in moved.items()
             for s in in_lists(f"UPDATE corpus_gallery SET category='{category}' WHERE id IN (", quoted)]
            + in_lists("DELETE FROM corpus_gallery WHERE id IN (", unwanted))


def bucket_client(remote: str, jobs: int):
    """An S3 client with the endpoint and keys the rclone remote `remote` holds."""
    dump = subprocess.run(["rclone", "config", "dump"], check=True, capture_output=True, text=True).stdout
    config = json.loads(dump)[remote]
    return boto3.client("s3", endpoint_url=config["endpoint"], aws_access_key_id=config["access_key_id"],
                        aws_secret_access_key=config["secret_access_key"], region_name="auto",
                        config=Config(max_pool_connections=jobs, retries={"mode": "standard", "max_attempts": 5}))


def record(s3, bucket: str, row: dict) -> str:
    end = row["offset"] + row["size"] - 1
    for attempt in range(READ_ATTEMPTS):
        try:
            data = s3.get_object(Bucket=bucket, Key=row["object"], Range=f"bytes={row['offset']}-{end}")["Body"].read()
            break
        except (BotoCoreError, ClientError):
            if attempt == READ_ATTEMPTS - 1:
                raise
            time.sleep(2 ** attempt)
    if len(data) != row["size"]:
        raise ValueError(f"{row['id']}: read {len(data)} of {row['size']} bytes from {row['object']}")
    text = data.decode()
    if json.loads(text).get("id") != row["id"]:
        raise ValueError(f"{row['id']}: the bytes at {row['object']}:{row['offset']} are another record")
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output", type=Path, help="a new directory for the SQL parts")
    parser.add_argument("--remote", default="r2", help="rclone remote whose S3 endpoint and keys read the bucket")
    parser.add_argument("--bucket", default="glyph-atlas")
    parser.add_argument("--jobs", type=int, default=64, help="simultaneous R2 reads")
    args = parser.parse_args()
    rows, copies = remote(MISSING), remote(COPIES)
    s3 = bucket_client(args.remote, args.jobs)
    with ThreadPoolExecutor(args.jobs) as pool:
        records = list(pool.map(lambda row: record(s3, args.bucket, row), rows))
    quote = sqlite3.connect(":memory:")
    statements = [STALE, *recategorised(copies, quote)]
    for row, text in zip(rows, records):
        values = quote.execute("SELECT quote(?),quote(?),quote(?)", (row["id"], row["object"], text)).fetchone()
        statement = (f"INSERT OR REPLACE INTO corpus_gallery(id,shuffle,object,offset,data,category) VALUES({values[0]},"
                     f"{row['shuffle']},{values[1]},{row['offset']},{values[2]},'{category_of(row['character'])}');")
        if len(statement.encode()) > STATEMENT_BYTES:
            raise ValueError(f"{row['id']}: its record makes a statement over D1's 100 KB limit")
        statements.append(statement)
    args.output.mkdir(parents=True, exist_ok=False)
    parts, size = [[]], 0
    for statement in statements:
        if size + len(statement.encode()) + 1 > PART_BYTES:
            parts.append([])
            size = 0
        parts[-1].append(statement)
        size += len(statement.encode()) + 1
    for index, part in enumerate(parts, 1):
        (args.output / f"{index:03}.sql").write_text("\n".join(part) + "\n")
    print(f"{len(rows)} records written to {len(parts)} parts in {args.output}")


if __name__ == "__main__":
    main()
