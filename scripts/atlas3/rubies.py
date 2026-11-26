"""The 振り仮名 the transcriptions give, as R2 objects the site reads beside a crop's line.

`atlas text build` keeps every line of the みんなで翻刻 clone in `text.sqlite`. This writes, for every
entry, the rubies of its lines in packs of 32 pages under `v3/rubies/<entry>/<n>.json.gz`:

    {"<page>": {"<line key>": [[start, length, side, reading], ...]}}

- A line is named by its key: its document characters that are letters or numbers, so punctuation and
  spacing a dataset changed do not part a published line from its transcription. Lines of a page with
  one key and different rubies, or one with rubies and one with none, are left out. The line is read in
  NFC, as the site reads a published line.
- `start` and `length` count characters of the key. `side` is 0 for the right (振り仮名) and 1 for the
  left (左訓). A ruby whose base is not one stretch of the key is left out.
- `reading` is the ruby as typed, without spaces: one string over the whole base, or a list with one
  string for each character where the kanji readings table (`data/vocab/kanji-readings.json`) splits it
  in exactly one way.

Nothing is written to D1. `--send` uploads the packs whose bytes differ from the manifest R2 holds
(`v3/rubies/manifest.json.gz`, {path: hash}) and then the manifest; without it the run only counts.

    uv run python scripts/atlas3/rubies.py                 # count, write nothing to R2
    uv run python scripts/atlas3/rubies.py --send          # upload what changed
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sqlite3
import subprocess
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from publish import BUCKET, OUT, R2_REMOTE, ROOT, upload

from glyph_atlas import rubies

PREFIX = "v3/rubies/"
MANIFEST = "manifest.json.gz"
PACK_PAGES = 32
READINGS = ROOT / "data/vocab/kanji-readings.json"
TEXT = ROOT / "work/text/text.sqlite"
SMALL = str.maketrans("ぁぃぅぇぉっゃゅょゎ", "あいうえおつやゆよわ")
SOUND = str.maketrans("ゐゑをぢづ", "いえおじず")


def keyed(char: str) -> bool:
    """Whether a character counts in a line's key: a letter or a number."""
    return unicodedata.category(char)[0] in "LN"


def fold(kana: str) -> str:
    """A reading as the table and a transcriber may both spell it: katakana as hiragana, small kana full
    size, and the kana of one sound alike."""
    text = "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in unicodedata.normalize("NFKC", kana))
    return text.translate(SMALL).translate(SOUND)


def readings_of() -> dict[str, frozenset[str]]:
    table = json.loads(READINGS.read_text(encoding="utf-8"))["readings"]
    found: dict[str, set[str]] = defaultdict(set)
    for reading, chars in table.items():
        for char in chars:
            found[char].add(fold(reading))
    return {char: frozenset(values) for char, values in found.items()}


def split(base: list[str], reading: str, known: dict[str, frozenset[str]]) -> list[str] | None:
    """The reading cut over each character of the base, when the table allows exactly one cut."""
    if len(base) < 2 or len(reading) < len(base):
        return None
    found: list[list[str]] = []

    def walk(at: int, rest: str, taken: list[str]) -> None:
        if len(found) > 1:
            return
        if at == len(base):
            if not rest:
                found.append(taken)
            return
        char, last = base[at], at == len(base) - 1
        for end in range(1, len(rest) - (len(base) - at - 1) + 1):
            if last and end != len(rest):
                continue
            part = rest[:end]
            own = fold(part)
            if own == fold(char) or own in known.get(char, ()):
                walk(at + 1, rest[end:], [*taken, part])

    walk(0, reading, [])
    return found[0] if len(found) == 1 else None


def line_rubies(raw: str, known: dict[str, frozenset[str]], counts: Counter) -> tuple[str, list[list]]:
    """A raw line's key and its rubies over the key."""
    raw = unicodedata.normalize("NFC", raw)
    chars = [(offset, char) for offset, char in rubies.document(raw) if keyed(char)]
    place = {offset: i for i, (offset, _) in enumerate(chars)}
    found = []
    for mark in rubies.marks(raw):
        at = sorted(place[offset] for offset in mark.base_offsets if offset in place)
        for side, typed in mark.readings.items():
            reading = "".join(unicodedata.normalize("NFC", typed).split())
            if not at or at != list(range(at[0], at[-1] + 1)) or not reading:
                counts["rubies left out: no base on the line, or a parted one"] += 1
                continue
            base = [chars[i][1] for i in at]
            cut = split(base, reading, known) if side == "right" else None
            counts["rubies"] += 1
            counts["rubies over one character"] += len(base) == 1
            counts["rubies cut over their characters"] += cut is not None
            found.append([at[0], len(at), 0 if side == "right" else 1, cut or reading])
    return "".join(char for _, char in chars), found


def packs(text: Path) -> tuple[dict[str, bytes], Counter]:
    known, counts = readings_of(), Counter()
    db = sqlite3.connect(f"file:{text}?mode=ro", uri=True)
    pages: dict[tuple[str, int], dict[str, list | None]] = defaultdict(dict)
    rows = db.execute("SELECT p.entry, p.page, l.raw FROM lines l JOIN pages p ON p.id = l.page "
                      "WHERE l.page IN (SELECT DISTINCT page FROM rubies) ORDER BY p.entry, p.page, l.line")
    for entry, page, raw in rows:
        key, found = line_rubies(raw, known, counts)
        if not key:
            continue
        lines = pages[entry.lower(), page]
        if key in lines and lines[key] != found:
            counts["lines left out: one key, different rubies"] += bool(found) + bool(lines[key])
            lines[key] = None
        else:
            lines[key] = found
    made: dict[str, dict[str, dict]] = defaultdict(dict)
    for (entry, page), lines in pages.items():
        kept = {key: found for key, found in lines.items() if found}
        if kept:
            made[f"{entry}/{(page - 1) // PACK_PAGES}.json.gz"][str(page)] = kept
            counts["pages"] += 1
            counts["lines"] += len(kept)
    objects = {path: gzip.compress(json.dumps(body, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(), mtime=0)
               for path, body in made.items()}
    counts["objects"] = len(objects)
    counts["bytes"] = sum(map(len, objects.values()))
    counts["largest object, bytes"] = max(map(len, objects.values()), default=0)
    return objects, counts


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def held() -> dict[str, str]:
    """The manifest R2 holds, or none before the first load."""
    read = subprocess.run(["rclone", "cat", f"{R2_REMOTE}:{BUCKET}/{PREFIX}{MANIFEST}"], capture_output=True, check=False)
    if read.returncode != 0:
        if b"not found" in read.stderr.lower() or b"directory not found" in read.stderr.lower():
            return {}
        sys.exit(f"reading {PREFIX}{MANIFEST} failed: {read.stderr.decode().strip()}")
    return json.loads(gzip.decompress(read.stdout))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--text", type=Path, default=TEXT, help="the text build to read")
    parser.add_argument("--send", action="store_true", help="upload the packs that changed, then the manifest")
    args = parser.parse_args()
    objects, counts = packs(args.text)
    for name, n in sorted(counts.items()):
        print(f"{name}: {n:,}")
    manifest = {path: digest(data) for path, data in objects.items()}
    if not args.send:
        return
    before = held()
    changed = sorted(path for path, h in manifest.items() if before.get(path) != h)
    gone = sorted(set(before) - set(manifest))
    print(f"to upload: {len(changed):,} objects and the manifest; no longer made: {len(gone):,}")
    stage = OUT / "rubies"
    for path in changed:
        (stage / path).parent.mkdir(parents=True, exist_ok=True)
        (stage / path).write_bytes(objects[path])
    by_folder: dict[Path, list[Path]] = defaultdict(list)
    for path in changed:
        by_folder[(stage / path).parent].append(stage / path)
    for folder, files in sorted(by_folder.items()):
        upload(files, PREFIX + folder.name + "/")
    if gone:
        done = subprocess.run(["rclone", "delete", f"{R2_REMOTE}:{BUCKET}/{PREFIX}", "--files-from-raw", "-"],
                              input="\n".join(gone) + "\n", text=True, check=False)
        if done.returncode != 0:
            sys.exit(f"deleting from {PREFIX} failed; rerun to finish")
    (stage / MANIFEST).write_bytes(gzip.compress(json.dumps(manifest, sort_keys=True).encode(), mtime=0))
    upload([stage / MANIFEST], PREFIX)


if __name__ == "__main__":
    main()
