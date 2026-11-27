"""Build the table of parts no code point names, from ids-input's custom-components file.

BabelStone IDS writes such a part as a number (`{88}`, the top of 合), CHISE IDS as an entity
(`&CDP-8BD0;`) and zi.tools as a stroke chain (`#(-𠃌㇉)`). ids-input gives each shape one identifier,
`X-00087`, written `&X-00087;` inside a description, and lists every name each is known by, with what a
composer draws in its place. The file is fetched at a pinned commit and checked against its digest.

    uv run scripts/build_custom_parts.py

Reads data/vocab/custom-part-donors.tsv, the drawing chosen for each part. Writes
data/vocab/custom-parts.tsv, which `glyph_atlas.custom_parts` reads,
apps/cloudflare/src/customPartsData.ts, which the Worker reads, and
apps/review/src/lib/customPartDrawings.js, which the site's composer reads.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "6b790780628b7d7d2a0bbc2f69d51ef0061ed58f"
URL = f"https://raw.githubusercontent.com/mkpoli/ids-input/{COMMIT}/data/custom-components.json.gz"
SHA256 = "22c430de0326d61d05cd868341acecadd320fa2528dd2e1f7f734cc099d50d3f"
CACHE = ROOT / "cache" / "ids-input" / f"custom-components-{COMMIT[:12]}.json.gz"
TABLE = ROOT / "data" / "vocab" / "custom-parts.tsv"
MODULE = ROOT / "apps" / "cloudflare" / "src" / "customPartsData.ts"
DRAWINGS = ROOT / "apps" / "review" / "src" / "lib" / "customPartDrawings.js"
#: What the composer draws a part with, where a choice was made (scripts/part_donor_candidates.py).
DONORS = ROOT / "data" / "vocab" / "custom-part-donors.tsv"
#: The parts the site's composer holds cuts of, in the han-compose package the review app pins.
COMPOSER_CUTS = ROOT / "apps" / "review" / "node_modules" / "han-compose" / "data" / "custom-forms.json.gz"
COMPOSER_NUMBERED = COMPOSER_CUTS.with_name("numbered-sans-outlines.json.gz")
#: A Han character or radical: a confirmed part drawn as one is drawn as that glyph.
HAN = re.compile(r"[\u2E80-\u2FD5\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF\U00020000-\U0003FFFF]")
#: Numbered parts whose fragment is ids-input's reading, not BabelStone's note 6 (its build_open_ids.mjs).
EDITORIAL = {"{12}", "{82}"}
_SETTLED: dict[int, tuple[dict[str, str], dict[str, list[str]]]] = {}


def fetched() -> dict:
    """The upstream file, from the cache when its digest matches."""
    if not CACHE.exists():
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(URL, timeout=60) as answer:
            CACHE.write_bytes(answer.read())
    raw = CACHE.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != SHA256:
        raise SystemExit(f"{CACHE}: sha256 {digest}, expected {SHA256}")
    return json.loads(gzip.decompress(raw))


def rows(data: dict) -> list[tuple[str, list[str], list[str], list[str], bool, str]]:
    """One row per identifier in use: its names; what draws it, in ids-input's order (the first of its
    components with a drawing, in the file's order, then the others, then the first of its names that
    no source writes as a component, its expression); which of those drawings are approximate; whether
    its fragment is editorial; and BabelStone's description of the numbered part, if any."""
    parts = {form["id"]: {"names": list(form["names"]), "draws": [], "approximate": [], "editorial": False, "description": ""}
             for form in data["forms"]}
    components = {component["text"] for component in data["components"]}
    for component in data["components"]:
        part = parts.get(component["id"])
        if part is None:
            continue
        if component.get("description") and not part["description"]:
            part["description"] = component["description"]
        part["editorial"] |= component["text"] in EDITORIAL
        draw = component.get("draw") or ""
        if draw and draw not in part["draws"]:
            part["draws"].append(draw)
            if component.get("drawApproximate"):
                part["approximate"].append(draw)
    for part in parts.values():
        expression = next((name for name in part["names"] if name not in components), None)
        if expression and expression not in part["draws"]:
            part["draws"].append(expression)
    return [(key, part["names"], part["draws"], part["approximate"], part["editorial"], part["description"])
            for key, part in sorted(parts.items())]


def _encoded(text: str) -> str | None:
    """The encoded character a description in encoded characters only describes, if any."""
    from glyph_atlas import han_component_variants, refs

    known = refs.descriptions()
    try:
        return known.named.get(known.folded_text(han_component_variants.parse(text)))
    except ValueError:
        return None


def _spellings(data: dict) -> tuple[dict[str, str], dict[str, list[str]]]:
    """The one spelling of each part ids-input knows only as a description, and the descriptions
    written as each named part: a description of a named part (one of its drawings, close and in
    encoded characters) that draws no other part, and no encoded character, inside a larger one."""
    if id(data) in _SETTLED:
        return _SETTLED[id(data)]
    from glyph_atlas import custom_parts

    found = rows(data)
    gone = dict(retired(data))
    named = {key for key, names, *_ in found if any(custom_parts.TOKEN.fullmatch(name) for name in names)}
    expressions = {key: names[0] for key, names, *_ in found
                   if key not in named and names and not custom_parts.PRIVATE.search(names[0]) and _arity(names[0])}
    spelling: dict[str, str] = {}
    folds: dict[str, list[str]] = {}
    for _ in range(16):
        table = custom_parts.Table({key: custom_parts.Part(key, tuple(names), tuple(draws), tuple(approximate), editorial,
                                                           description, spelling.get(key, ""), tuple(folds.get(key, ())))
                                    for key, names, draws, approximate, editorial, description in found}, gone)
        drawings: dict[str, set[str]] = defaultdict(set)
        for key, names, draws, approximate, *_ in found:
            if key in named:
                for draw in draws:
                    if _arity(draw) and draw not in approximate and not custom_parts.PRIVATE.search(draw):
                        drawings[table.spelt(draw)].add(key)
        following = {key: table.spelt(text) for key, text in expressions.items()}
        for key, text in following.items():
            drawings[text].add(key)
        folding: dict[str, list[str]] = defaultdict(list)
        for text, keys in sorted(drawings.items()):
            if len(keys) == 1 and not table.unknown(text) and (key := next(iter(keys))) in named \
                    and (custom_parts.IDENTIFIER.search(text) or _encoded(text) is None):
                folding[key].append(text)
        if following == spelling and folding == folds:
            _SETTLED[id(data)] = spelling, folds
            return spelling, folds
        spelling, folds = following, dict(folding)
    raise SystemExit("the spellings of the parts do not settle")


def _arity(text: str) -> bool:
    from glyph_atlas.han_components import BINARY, TERNARY, UNARY

    return bool(text) and text[0] in BINARY | TERNARY | UNARY


def retired(data: dict) -> list[tuple[str, str]]:
    """Identifiers taken out of use, with the one each was merged into (`''` for one withdrawn)."""
    return [(entry["id"], entry["note"].removeprefix("merged into ") if entry["note"].startswith("merged into ") else "")
            for entry in data["retired"]]


def table(data: dict) -> str:
    sources = "; ".join(f"{s['title']} ({s.get('licence') or 'no licence stated'}, {s['url']})" for s in data["sources"])
    lines = [
        "# Parts no code point names, one row per identifier; see scripts/build_custom_parts.py.",
        f"# source ids-input: mkpoli/ids-input data/custom-components.json.gz at {COMMIT}, sha256:{SHA256}",
        f"#   drawing on {sources}",
        "# names: space-separated, every spelling the sources give the part: BabelStone {n}, CHISE &…;, zi.tools #(…) and",
        "#   descriptions of encoded characters that draw it; written &X-NNNNN; inside a description.",
        "# draws: space-separated, what a composer may draw in its place, in ids-input's order: descriptions, some with",
        "#   the private-use glyph of BabelStone Han PUA that alone draws 69 numbered parts; approximate lists the ones",
        "#   that are close only; editorial marks a fragment ({12}, {82}) that is no reading of BabelStone's note 6.",
        "# spelling: for a part ids-input knows only as a description of encoded characters, that description, which",
        "#   is how a form holding the part is written.",
        "# folds: space-separated, the descriptions written as this part inside a larger one: its drawings that draw no",
        "#   other part and no encoded character.",
        "# retired: for an identifier taken out of use, the one it was merged into, or withdrawn.",
        "# columns: id, names, draws, approximate, editorial, description, spelling, folds, retired",
    ]
    spelling, folds = _spellings(data)
    for key, names, draws, approximate, editorial, description in rows(data):
        lines.append("\t".join((key, " ".join(names), " ".join(draws), " ".join(approximate), "1" if editorial else "", description,
                                spelling.get(key, ""), " ".join(folds.get(key, ())), "")))
    for key, into in retired(data):
        lines.append("\t".join((key, "", "", "", "", "", "", "", into or "withdrawn")))
    return "\n".join(lines) + "\n"


def module(data: dict) -> str:
    """Each name's identifier and each identifier's drawings, as the Worker reads them."""
    named: dict[str, str] = {}
    for key, names, *_ in rows(data):
        for name in names:
            named.setdefault(name, key)
    drawn = {key: kept for key, _, draws, approximate, *_ in rows(data) if (kept := [draw for draw in draws if draw not in approximate])}
    spelling, folds = _spellings(data)
    folded = {text: key for key, texts in folds.items() for text in texts}
    return (
        "// Written by scripts/build_custom_parts.py with data/vocab/custom-parts.tsv, from ids-input; edit neither here.\n"
        "/** Every spelling of a part no code point names, with the identifier ids-input gives the part. */\n"
        f"export const CUSTOM_PART_NAMES: Record<string, string> = {json.dumps(named, ensure_ascii=False, separators=(',', ':'))};\n"
        "/** The identifiers taken out of use, each with the one it was merged into, '' for one withdrawn. */\n"
        f"export const CUSTOM_PART_RETIRED: Record<string, string> = {json.dumps(dict(retired(data)), separators=(',', ':'))};\n"
        "/** Every identifier in use. */\n"
        f"export const CUSTOM_PARTS: readonly string[] = {json.dumps([key for key, *_ in rows(data)], separators=(',', ':'))};\n"
        "/** What a composer may draw in a part's place, in ids-input's order, less those that are close only: descriptions,\n"
        " *  some with the private-use glyph of BabelStone Han PUA that alone draws 69 numbered parts. */\n"
        f"export const CUSTOM_PART_DRAWN: Record<string, string[]> = {json.dumps(drawn, ensure_ascii=False, separators=(',', ':'))};\n"
        "/** The description a part ids-input knows only as one is written as. */\n"
        f"export const CUSTOM_PART_SPELLING: Record<string, string> = {json.dumps(spelling, ensure_ascii=False, separators=(',', ':'))};\n"
        "/** The descriptions written as a named part inside a larger one, each with that part. */\n"
        f"export const CUSTOM_PART_FOLDS: Record<string, string> = {json.dumps(folded, ensure_ascii=False, separators=(',', ':'))};\n"
    )


def donors() -> dict[str, str]:
    """The drawing chosen for each part that has one, empty where every candidate drew badly."""
    chosen: dict[str, str] = {}
    for line in DONORS.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            key, drawing, *_ = line.split("\t")
            chosen[key] = drawing
    return chosen


def composer_cuts() -> set[str]:
    """The parts the site's composer holds a cut of, from a real glyph, in its sans face: han-compose's custom
    forms by name (`&CDP-8CB8;`), and its numbered outlines by BabelStone Han PUA character."""
    for path in (COMPOSER_CUTS, COMPOSER_NUMBERED):
        if not path.exists():
            raise SystemExit(f"{path} is missing: run `bun install` in apps/review")
    return set(json.loads(gzip.decompress(COMPOSER_CUTS.read_bytes()))["forms"]["sans"]) \
        | set(json.loads(gzip.decompress(COMPOSER_NUMBERED.read_bytes()))["records"])


def composer_names(data: dict) -> dict[str, str]:
    """What the site's composer (han-compose) is given in place of each part a form may hold, the first that
    applies: a name of the part the composer holds a cut of, from a real glyph (`&CDP-8CB8;`); a single Han
    character the tables confirm the part as (亼 for the top of 合); the drawing chosen for it in
    `custom-part-donors.tsv`, which composed well in the characters tried (some BabelStone's own private-use glyph
    of the part, which the composer holds an outline of). Any other part has none, and a form holding it shows as text."""
    from glyph_atlas import custom_parts

    spelling, _ = _spellings(data)
    live = {key for key, *_ in rows(data)} - set(spelling)
    cuts = composer_cuts()
    chosen = donors()
    names: dict[str, list[str]] = defaultdict(list)
    confirmed: dict[str, list[str]] = defaultdict(list)
    for component in data["components"]:
        key = component.get("id")
        if key not in live:
            continue
        names[key].append(component["text"])
        for item in component.get("equivalents", []):
            text = item["text"]
            if item["status"] == "verified" and not custom_parts.TOKEN.search(text) and not custom_parts.PRIVATE.search(text) \
                    and custom_parts.UNWRITTEN not in text and text not in confirmed[key]:
                confirmed[key].append(text)
    named: dict[str, str] = {}
    for key in sorted(live):
        drawing = chosen.get(key, "")
        if drawing and drawing not in cuts and (custom_parts.TOKEN.search(drawing) or custom_parts.PRIVATE.search(drawing)
                                                or custom_parts.UNWRITTEN in drawing):
            raise SystemExit(f"{DONORS}: {key} is drawn with {drawing}, which is not in encoded characters only")
        single = [text for text in confirmed[key] if len(text) == 1 and HAN.match(text)]
        found = next((name for name in names[key] if name in cuts), None) or next(iter(single), None) or drawing
        if found:
            named[key] = found
    return named


def drawings(data: dict) -> str:
    return (
        "// Written by scripts/build_custom_parts.py from ids-input's table of parts no code point names; do not edit.\n"
        "/** What the composer is given in place of a part a saved form may hold (`&X-00087;`): a name of the part it holds\n"
        " *  a cut of, a Han character the tables confirm the part as, the drawing chosen for it\n"
        " *  (data/vocab/custom-part-donors.tsv), or a confirmed description. A part not listed has none: its form is text. */\n"
        f"export default {json.dumps(composer_names(data), ensure_ascii=False, separators=(',', ':'))}\n"
    )


def main() -> int:
    data = fetched()
    TABLE.write_text(table(data), encoding="utf-8")
    MODULE.write_text(module(data), encoding="utf-8")
    DRAWINGS.write_text(drawings(data), encoding="utf-8")
    from glyph_atlas import custom_parts

    found = rows(data)
    encoded = sum(1 for row in found if custom_parts.drawn(f"&{row[0]};") is not None)
    spelling, folds = _spellings(data)
    print(f"{len(found)} parts, {encoded} drawn in encoded characters, {sum(1 for row in found if row[2]) - encoded} only with a "
          f"private-use glyph, {sum(len(row[1]) for row in found)} names, {len(retired(data))} retired; "
          f"{len(spelling)} written as their description, {sum(map(len, folds.values()))} descriptions written as "
          f"{len(folds)} named parts; the composer is given {len(composer_names(data))} of them")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
