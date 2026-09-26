"""Write the chữ Hán-Nôm interface catalogue and its webfont from the Vietnamese one.

`apps/review/src/locales/vi-Hani.json` is `vi.json` spelled in chữ Hán-Nôm: every Vietnamese word,
longest match first, is replaced by its spelling in `data/vocab/vi-hani.tsv`, the spaces between
Han characters are dropped and the punctuation turns full-width. Latin names (CODH, JSON, Ctrl…),
placeholders and symbols stay as they are. A word the table lacks stops the run and is listed.

    uv run python scripts/build_vi_hani.py            # write vi-Hani.json
    uv run python scripts/build_vi_hani.py --check    # fail if vi-Hani.json is out of date
    uv run python scripts/build_vi_hani.py --propose  # print table rows for the missing words
    uv run python scripts/build_vi_hani.py --font     # write the webfont subset

`--propose` reads the Wiktionary extract and Unihan into `cache/vi-hani/` and suggests, for each
missing word, the spellings they attest, most attested first; the row still has to be checked and
added by hand with its source. `--font` subsets Plangothic (SIL OFL 1.1) to the catalogue's
characters beyond the Basic Multilingual Plane, which few installed fonts draw, and writes
`apps/review/static/fonts/Plangothic-vi-Hani.woff2`. Extension A (㐌 㗂 㨂 䀡) is left to the
reader's fonts: it is part of GB 18030, so every system Chinese font draws it, and Plangothic does
not carry it.
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

ROOT = Path(__file__).resolve().parents[1]
LOCALES = ROOT / "apps/review/src/locales"
TABLE = ROOT / "data/vocab/vi-hani.tsv"
CACHE = ROOT / "cache/vi-hani"
FONT_CACHE = ROOT / "cache/fonts"
FONT_OUT = ROOT / "apps/review/static/fonts/Plangothic-vi-Hani.woff2"

LOCALE = {"name": "㗂越（𡨸漢喃）", "base": "vi", "matches": ["vi-hani"], "numerals": "hanzi"}
# Abbreviations in vi.json, written out so the word table spells them: "tr." is trang (page), "s" giây.
EXPAND = (("tr. {page}", "trang {page}"), ("{seconds}s ", "{seconds} giây "))
SOURCES = {"wiktionary", "unihan", "joined", "editorial"}
# Latin words that are names, keys or file formats and stay in Latin letters.
KEEP = {"CODH", "Unicode", "HI", "Lab", "Minna", "de", "Honkoku", "JSON", "ID", "Ctrl", "Home", "Enter", "Shift",
        "click", "qwerty", "zi", "tools", "roneo", "scan", "C", "J", "K", "X", "n", "s"}

KAIKKI = "https://kaikki.org/dictionary/Vietnamese/kaikki.org-dictionary-Vietnamese.jsonl"
UNIHAN = "https://www.unicode.org/Public/18.0.0/ucd/Unihan.zip"
PLANGOTHIC = "https://github.com/Fitzgerald-Porthmouth-Koenigsegg/Plangothic-Project/releases/download/V2.9.5795"
FONTS = {
    "PlangothicP1-Regular.ttf": "550b5d0775b15405946b18f4843df439a51e69508d7e6778d94c1f7a53dc5ad6",
    "PlangothicP2-Regular.ttf": "681933370adfe0fc7253f77735275a82fea09fe4f8adba907bdeb46c110daf8f",
}

HAN = r"㐀-䶿一-鿿豈-﫿\U00020000-\U0003ffff"
VIETNAMESE = "A-Za-zÀÁÂÃÈÉÊÌÍÒÓÔÕÙÚÝàáâãèéêìíòóôõùúýĂăĐđĨĩŨũƠơƯưẠ-ỹ"
WORD = re.compile(rf"\{{\w+\}}|[{VIETNAMESE}]+")


HEADER = ["word", "hannom", "source", "note"]


def load_table() -> dict[str, str]:
    """The word table, refused when a row lacks a spelling or a known source, or repeats a word."""
    lines = [line.rstrip("\n") for line in TABLE.open(encoding="utf-8")]
    header, *rows = [line.split("\t") for line in lines if line and not line.startswith("#")]
    where = TABLE.relative_to(ROOT)
    if header != HEADER:
        raise SystemExit(f"{where}: the first row must be the header {' '.join(HEADER)}")
    table: dict[str, str] = {}
    for row in rows:
        if len(row) != len(HEADER) or not row[1] or row[2] not in SOURCES:
            raise SystemExit(f"{where}: {row[0]!r} needs a spelling, one of {sorted(SOURCES)} and a note column")
        if row[0] in table:
            raise SystemExit(f"{where}: {row[0]!r} appears twice")
        table[row[0]] = row[1]
    return table


def spell(text: str, table: dict[str, str], missing: set[str], used: set[str] | None = None) -> str:
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
                    if used is not None:
                        used.add(key)
                    i += n
                    break
            else:
                missing.add(piece[i].lower())
                out.append(piece[i])
                i += 1
    result = "".join(out)
    if re.search(f"[{HAN}]", result):
        result = result.replace(" & ", " 吧 ")
        result = re.sub(r" ?\((.*?)\)", r"（\1）", result)
        for ascii_mark, wide in ((",", "，"), ("?", "？"), (":", "："), (";", "；"), (".", "。")):
            result = re.sub(rf"(?<=[{HAN}）\w}}]) ?{re.escape(ascii_mark)}( |$)", wide, result)
        # No space where Han characters meet each other, Latin letters, digits or placeholders.
        result = re.sub(rf"(?<=[{HAN}]) (?=[{HAN}{{A-Za-z0-9])|(?<=[{HAN}}}A-Za-z0-9%）]) (?=[{HAN}])", "", result)
        # Key names written as symbols join the same way: ⌫挅, 或⌘/Ctrl, ←→塳割, qwerty…底.
        result = re.sub(rf"(?<=[{HAN}]) (?=[⌫⌘])|(?<=[⌫…]) (?=[{HAN}])|(?<=←→) (?=[{HAN}])", "", result)
    return result.strip()


def build() -> tuple[dict, set[str]]:
    """The catalogue, and the words it lacks a spelling for; a table row no message uses stops the run."""
    vi = json.loads((LOCALES / "vi.json").read_text(encoding="utf-8"))
    table, missing, used = load_table(), set(), set()
    catalogue = {"@locale": LOCALE}
    for key, text in vi.items():
        if key != "@locale":
            for short, full in EXPAND:
                text = text.replace(short, full)
            catalogue[key] = spell(text, table, missing, used)
    if not missing and (unused := sorted(set(table) - used)):
        raise SystemExit(f"{TABLE.relative_to(ROOT)}: no message uses {', '.join(unused)}; remove those rows")
    return catalogue, missing


def download(url: str, dest: Path, sha256: str | None = None) -> Path:
    """`dest`, fetched from `url` unless it is already there; a partial download never takes its name."""
    if not dest.exists():
        import httpx

        dest.parent.mkdir(parents=True, exist_ok=True)
        partial = dest.with_name(dest.name + ".part")
        with httpx.stream("GET", url, follow_redirects=True, timeout=120) as response:
            response.raise_for_status()
            with partial.open("wb") as file:
                for chunk in response.iter_bytes():
                    file.write(chunk)
        partial.rename(dest)
    if sha256 and hashlib.sha256(dest.read_bytes()).hexdigest() != sha256:
        raise SystemExit(f"{dest} does not match its pinned SHA-256; delete it and run again")
    return dest


def propose(missing: set[str]) -> None:
    """Print a draft table row for each missing word: the most attested spelling, a source to fill in,
    and every spelling Wiktionary and Unihan attest. The row does not load until its source is set."""

    def norm(text: str) -> str:
        return unicodedata.normalize("NFC", text.lower())

    han_word = re.compile(f"^[{HAN}]+$")
    attested: dict[str, Counter] = {word: Counter() for word in missing}
    for line in download(KAIKKI, CACHE / "kaikki-vi.jsonl").open(encoding="utf-8"):
        entry = json.loads(line)
        if han_word.match(entry["word"]):
            for sense in entry.get("senses", []):
                for gloss in sense.get("glosses") or []:
                    if m := re.match(r"(?:chữ Nôm|chữ Hán|Hán Nôm) form of ([^(“]+)", gloss, re.IGNORECASE):
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
        forms = [form for form, _ in attested[word].most_common()]
        print(f"{word}\t{forms[0] if forms else ''}\t?\tattested: {' '.join(forms) or 'nothing'}")


def font(catalogue: dict) -> None:
    """Subset Plangothic P1 and P2 to the catalogue's supplementary-plane characters, as one woff2."""
    from fontTools import subset
    from fontTools.merge import Merger
    from fontTools.ttLib import TTFont

    text = "".join(v for v in catalogue.values() if isinstance(v, str)) + LOCALE["name"]
    wanted = sorted({ord(c) for c in text if ord(c) >= 0x20000})
    if not wanted:
        raise SystemExit("The catalogue has no character for the webfont to draw")
    parts, covered = [], set()
    for name, sha256 in FONTS.items():
        source = download(f"{PLANGOTHIC}/{name}", FONT_CACHE / name, sha256)
        cmap = TTFont(source).getBestCmap()
        codes = [c for c in wanted if c in cmap and c not in covered]
        if not codes:
            continue
        covered.update(codes)
        options = subset.Options()
        options.drop_tables += ["FFTM"]
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
