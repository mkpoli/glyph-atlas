"""Write the chữ Hán-Nôm interface catalogue from the Vietnamese one.

`apps/review/src/locales/vi-Hani.json` is `vi.json` spelled in chữ Hán-Nôm: every Vietnamese word,
longest match first, is replaced by its spelling in `data/vocab/vi-hani.tsv`, the spaces between
Han characters are dropped and the punctuation turns full-width. Latin names (CODH, JSON, Ctrl…),
placeholders and symbols stay as they are. A word the table lacks stops the run and is listed.

    uv run python scripts/build_vi_hani.py            # write vi-Hani.json
    uv run python scripts/build_vi_hani.py --check    # fail if vi-Hani.json is out of date
    uv run python scripts/build_vi_hani.py --propose  # print table rows for the missing words

`--propose` reads the Wiktionary extract and Unihan into `cache/vi-hani/` and suggests, for each
missing word, the spellings they attest, most attested first; the row still has to be checked and
added by hand.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
import zipfile
from collections import Counter
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
LOCALES = ROOT / "apps/review/src/locales"
TABLE = ROOT / "data/vocab/vi-hani.tsv"
CACHE = ROOT / "cache/vi-hani"

LOCALE = {"name": "㗂越（𡨸漢喃）", "base": "vi", "matches": ["vi-hani"], "numerals": "hanzi"}
# Messages whose Vietnamese abbreviates or spells a unit the word table cannot carry.
FIXED = {"tile.page": "張{page}", "progress.source.pauseSeconds": "{seconds}秒𡧲各作品"}
# Latin words that are names, keys or file formats and stay in Latin letters.
KEEP = {"CODH", "HI", "Lab", "Minna", "de", "Honkoku", "JSON", "ID", "Ctrl", "Home", "Enter", "Shift",
        "click", "qwerty", "zi", "tools", "roneo", "scan", "C", "J", "K", "X", "n", "s"}

KAIKKI = "https://kaikki.org/dictionary/Vietnamese/kaikki.org-dictionary-Vietnamese.jsonl"
UNIHAN = "https://www.unicode.org/Public/18.0.0/ucd/Unihan.zip"

HAN = r"㐀-䶿一-鿿豈-﫿\U00020000-\U0003ffff"
WORD = re.compile(r"\{\w+\}|[A-Za-zÀ-ỹĐđ]+")


def load_table() -> dict[str, str]:
    rows = (line.rstrip("\n").split("\t") for line in TABLE.open(encoding="utf-8") if not line.startswith("#"))
    next(rows)  # header
    return {word: hannom for word, hannom, *_ in rows}


def spell(text: str, table: dict[str, str], missing: set[str]) -> str:
    """`text` with each run of Vietnamese words replaced by its chữ Hán-Nôm spelling."""
    text = unicodedata.normalize("NFC", text)
    pieces: list[str | list[str]] = []  # plain text, or a run of words separated by single spaces
    at = 0
    for match in WORD.finditer(text):
        token = match.group()
        between = text[at:match.start()]
        convertible = not token.startswith("{") and token not in KEEP
        if convertible and between == " " and pieces and isinstance(pieces[-1], list):
            pieces[-1].append(token)
        else:
            if between:
                pieces.append(between)
            pieces.append([token] if convertible else token)
        at = match.end()
    pieces.append(text[at:])

    out = []
    for piece in pieces:
        if isinstance(piece, str):
            out.append(piece)
            continue
        i = 0
        while i < len(piece):
            for n in range(min(5, len(piece) - i), 0, -1):
                key = " ".join(piece[i:i + n]).lower()
                if key in table:
                    out.append(table[key])
                    i += n
                    break
            else:
                missing.add(piece[i].lower())
                out.append(piece[i])
                i += 1
    result = "".join(out)
    if re.search(f"[{HAN}]", result):
        result = result.replace("&", "吧")
        result = re.sub(r" ?\((.*?)\)", r"（\1）", result)
        for ascii_mark, wide in ((",", "，"), ("?", "？"), (":", "："), (";", "；"), (".", "。")):
            result = re.sub(rf"(?<=[{HAN}）\w}}]) ?{re.escape(ascii_mark)}( |$)", wide, result)
        # No space where Han characters meet each other, Latin letters, digits or placeholders.
        result = re.sub(rf"(?<=[{HAN}]) (?=[{HAN}{{A-Za-z0-9])|(?<=[{HAN}}}A-Za-z0-9%）]) (?=[{HAN}])", "", result)
    return result.strip()


def build() -> tuple[dict, set[str]]:
    vi = json.loads((LOCALES / "vi.json").read_text(encoding="utf-8"))
    table, missing = load_table(), set()
    catalogue = {"@locale": LOCALE}
    for key, text in vi.items():
        if key != "@locale":
            catalogue[key] = FIXED.get(key) or spell(text, table, missing)
    return catalogue, missing


def download(url: str, dest: Path, sha256: str | None = None) -> Path:
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        with httpx.stream("GET", url, follow_redirects=True, timeout=120) as response:
            response.raise_for_status()
            with dest.open("wb") as file:
                for chunk in response.iter_bytes():
                    file.write(chunk)
    if sha256 and hashlib.sha256(dest.read_bytes()).hexdigest() != sha256:
        raise SystemExit(f"{dest} does not match its pinned SHA-256; delete it and run again")
    return dest


def propose(missing: set[str]) -> None:
    """Print a table row for each missing word, with the spellings Wiktionary and Unihan attest."""
    norm = lambda s: unicodedata.normalize("NFC", s.lower())
    han_word = re.compile(f"^[{HAN}]+$")
    attested: dict[str, Counter] = {word: Counter() for word in missing}
    for line in download(KAIKKI, CACHE / "kaikki-vi.jsonl").open(encoding="utf-8"):
        entry = json.loads(line)
        if han_word.match(entry["word"]):
            for sense in entry.get("senses", []):
                for gloss in sense.get("glosses") or []:
                    if m := re.match(r"(?:chữ Nôm|chữ Hán|Hán Nôm) form of ([^(“]+)", gloss):
                        for word in m.group(1).split(","):
                            if norm(word.strip()) in attested:
                                attested[norm(word.strip())][entry["word"]] += 1
        elif norm(entry["word"]) in attested:
            for form in entry.get("forms") or []:
                if "CJK" in (form.get("tags") or []) and han_word.match(form["form"]):
                    attested[norm(entry["word"])][form["form"]] += 1
    with zipfile.ZipFile(download(UNIHAN, CACHE / "Unihan-18.0.0.zip")) as archive:
        for line in archive.read("Unihan_Readings.txt").decode("utf-8").splitlines():
            if "\tkVietnamese\t" in line:
                code, _, readings = line.split("\t")
                for reading in readings.split():
                    if norm(reading) in attested:
                        attested[norm(reading)][chr(int(code.removeprefix("U+"), 16))] += 1
    for word in sorted(missing):
        forms = " ".join(form for form, _ in attested[word].most_common())
        print(f"{word}\t\t\t{forms or 'no attested spelling'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="fail if vi-Hani.json is out of date")
    mode.add_argument("--propose", action="store_true", help="suggest table rows for missing words")
    args = parser.parse_args()

    catalogue, missing = build()
    if args.propose:
        propose(missing) if missing else print("Every word is in the table.")
        return
    if missing:
        sys.exit(f"Not in {TABLE.relative_to(ROOT)}: {', '.join(sorted(missing))} (run with --propose)")
    written = json.dumps(catalogue, ensure_ascii=False, indent=2) + "\n"
    target = LOCALES / "vi-Hani.json"
    if args.check:
        if target.read_text(encoding="utf-8") != written:
            sys.exit(f"{target.relative_to(ROOT)} is out of date; run scripts/build_vi_hani.py")
        return
    target.write_text(written, encoding="utf-8")


if __name__ == "__main__":
    main()
