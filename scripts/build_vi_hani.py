"""Write the chữ Hán-Nôm interface catalogue and its webfont from the Vietnamese one.

`apps/review/src/locales/vi-Hani.json` is `vi.json` spelled in chữ Hán-Nôm: every Vietnamese word,
longest match first, is replaced by its spelling in `data/vocab/vi-hani.tsv`, the spaces between
Han characters are dropped and the punctuation turns full-width. Latin names (CODH, JSON, Ctrl…),
placeholders and symbols stay as they are. A word the table lacks stops the run and is listed.

    uv run python scripts/build_vi_hani.py            # write vi-Hani.json
    uv run python scripts/build_vi_hani.py --check    # fail if vi-Hani.json is out of date
    uv run python scripts/build_vi_hani.py --propose  # print table rows for the missing words
    uv run --extra fonts python scripts/build_vi_hani.py --font

`--propose` reads the Wiktionary extract and Unihan into `cache/vi-hani/` and suggests, for each
missing word, the spellings they attest, most attested first; the row still has to be checked and
added by hand. `--font` subsets Plangothic (SIL OFL 1.1) to the catalogue's characters beyond the
Basic Multilingual Plane, which few installed fonts draw, and writes
`apps/review/static/fonts/Plangothic-vi-Hani.woff2`.
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
FONT_CACHE = ROOT / "cache/fonts"
FONT_OUT = ROOT / "apps/review/static/fonts/Plangothic-vi-Hani.woff2"

LOCALE = {"name": "㗂越（𡨸漢喃）", "base": "vi", "matches": ["vi-hani"], "numerals": "hanzi"}
# Messages whose Vietnamese abbreviates or spells a unit the word table cannot carry.
FIXED = {"tile.page": "張{page}", "progress.source.pauseSeconds": "{seconds}秒𡧲各作品"}
# Latin words that are names, keys or file formats and stay in Latin letters.
KEEP = {"CODH", "HI", "Lab", "Minna", "de", "Honkoku", "JSON", "ID", "Ctrl", "Home", "Enter", "Shift",
        "click", "qwerty", "zi", "tools", "roneo", "scan", "C", "J", "K", "X", "n", "s"}

KAIKKI = "https://kaikki.org/dictionary/Vietnamese/kaikki.org-dictionary-Vietnamese.jsonl"
UNIHAN = "https://www.unicode.org/Public/18.0.0/ucd/Unihan.zip"
PLANGOTHIC = "https://github.com/Fitzgerald-Porthmouth-Koenigsegg/Plangothic-Project/releases/download/V2.9.5795"
FONTS = {
    "PlangothicP1-Regular.ttf": "550b5d0775b15405946b18f4843df439a51e69508d7e6778d94c1f7a53dc5ad6",
    "PlangothicP2-Regular.ttf": "681933370adfe0fc7253f77735275a82fea09fe4f8adba907bdeb46c110daf8f",
}

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


def font(catalogue: dict) -> None:
    """Subset Plangothic P1 and P2 to the catalogue's supplementary-plane characters, as one woff2."""
    from fontTools import subset
    from fontTools.merge import Merger
    from fontTools.ttLib import TTFont

    text = "".join(v for v in catalogue.values() if isinstance(v, str)) + LOCALE["name"]
    wanted = sorted({ord(c) for c in text if ord(c) >= 0x20000})
    parts, covered = [], set()
    for name, sha256 in FONTS.items():
        source = download(f"{PLANGOTHIC}/{name}", FONT_CACHE / name, sha256)
        cmap = TTFont(source).getBestCmap()
        codes = [c for c in wanted if c in cmap and c not in covered]
        if not codes:
            continue
        covered.update(codes)
        options = subset.Options()
        options.layout_features, options.name_IDs, options.notdef_outline = [], ["*"], True
        part = subset.load_font(str(source), options)
        subsetter = subset.Subsetter(options)
        subsetter.populate(unicodes=codes)
        subsetter.subset(part)
        path = CACHE / f"part-{name}"
        path.parent.mkdir(parents=True, exist_ok=True)
        part.save(path)
        parts.append(str(path))
    if lacking := [chr(c) for c in wanted if c not in covered]:
        raise SystemExit(f"Plangothic lacks {''.join(lacking)}")
    merged = Merger().merge(parts) if len(parts) > 1 else TTFont(parts[0])
    # The source's own timestamp, so the same characters always give the same file.
    merged["head"].modified = TTFont(FONT_CACHE / next(iter(FONTS)))["head"].modified
    merged.recalcTimestamp = False
    merged.flavor = "woff2"
    merged.save(FONT_OUT)
    print(f"{FONT_OUT.relative_to(ROOT)}: {len(wanted)} characters, {FONT_OUT.stat().st_size} bytes")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="fail if vi-Hani.json is out of date")
    mode.add_argument("--propose", action="store_true", help="suggest table rows for missing words")
    mode.add_argument("--font", action="store_true", help="write the subset webfont")
    args = parser.parse_args()

    catalogue, missing = build()
    if args.propose:
        propose(missing) if missing else print("Every word is in the table.")
        return
    if missing:
        sys.exit(f"Not in {TABLE.relative_to(ROOT)}: {', '.join(sorted(missing))} (run with --propose)")
    if args.font:
        font(catalogue)
        return
    written = json.dumps(catalogue, ensure_ascii=False, indent=2) + "\n"
    target = LOCALES / "vi-Hani.json"
    if args.check:
        if target.read_text(encoding="utf-8") != written:
            sys.exit(f"{target.relative_to(ROOT)} is out of date; run scripts/build_vi_hani.py")
        return
    target.write_text(written, encoding="utf-8")


if __name__ == "__main__":
    main()
