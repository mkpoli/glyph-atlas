"""Build the table of parts no code point names, from ids-input's custom-components file.

BabelStone IDS writes such a part as a number (`{88}`, the top of 合), CHISE IDS as an entity
(`&CDP-8BD0;`) and zi.tools as a stroke chain (`#(-𠃌㇉)`). ids-input gives each shape one identifier,
`X-00087`, written `&X-00087;` inside a description, and lists every name each is known by, with what a
composer draws in its place. The file is fetched at a pinned commit and checked against its digest.

    uv run scripts/build_custom_parts.py

Writes data/vocab/custom-parts.tsv, which `glyph_atlas.custom_parts` reads, and
apps/cloudflare/src/customPartsData.ts, which the Worker reads.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "6b790780628b7d7d2a0bbc2f69d51ef0061ed58f"
URL = f"https://raw.githubusercontent.com/mkpoli/ids-input/{COMMIT}/data/custom-components.json.gz"
SHA256 = "22c430de0326d61d05cd868341acecadd320fa2528dd2e1f7f734cc099d50d3f"
CACHE = ROOT / "cache" / "ids-input" / f"custom-components-{COMMIT[:12]}.json.gz"
TABLE = ROOT / "data" / "vocab" / "custom-parts.tsv"
MODULE = ROOT / "apps" / "cloudflare" / "src" / "customPartsData.ts"
#: Numbered parts whose fragment is ids-input's reading, not BabelStone's note 6 (its build_open_ids.mjs).
EDITORIAL = {"{12}", "{82}"}


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
        "# retired: for an identifier taken out of use, the one it was merged into, or withdrawn.",
        "# columns: id, names, draws, approximate, editorial, description, retired",
    ]
    for key, names, draws, approximate, editorial, description in rows(data):
        lines.append("\t".join((key, " ".join(names), " ".join(draws), " ".join(approximate), "1" if editorial else "", description, "")))
    for key, into in retired(data):
        lines.append("\t".join((key, "", "", "", "", "", into or "withdrawn")))
    return "\n".join(lines) + "\n"


def module(data: dict) -> str:
    """Each name's identifier and each identifier's drawings, as the Worker reads them."""
    named: dict[str, str] = {}
    for key, names, *_ in rows(data):
        for name in names:
            named.setdefault(name, key)
    drawn = {key: draws for key, _, draws, *_ in rows(data) if draws}
    return (
        "// Written by scripts/build_custom_parts.py with data/vocab/custom-parts.tsv, from ids-input; edit neither here.\n"
        "/** Every spelling of a part no code point names, with the identifier ids-input gives the part. */\n"
        f"export const CUSTOM_PART_NAMES: Record<string, string> = {json.dumps(named, ensure_ascii=False, separators=(',', ':'))};\n"
        "/** The identifiers taken out of use, each with the one it was merged into, '' for one withdrawn. */\n"
        f"export const CUSTOM_PART_RETIRED: Record<string, string> = {json.dumps(dict(retired(data)), separators=(',', ':'))};\n"
        "/** Every identifier in use. */\n"
        f"export const CUSTOM_PARTS: readonly string[] = {json.dumps([key for key, *_ in rows(data)], separators=(',', ':'))};\n"
        "/** What a composer may draw in a part's place, in ids-input's order: descriptions, some with the private-use\n"
        " *  glyph of BabelStone Han PUA that alone draws 69 numbered parts. */\n"
        f"export const CUSTOM_PART_DRAWN: Record<string, string[]> = {json.dumps(drawn, ensure_ascii=False, separators=(',', ':'))};\n"
    )


def main() -> int:
    data = fetched()
    TABLE.write_text(table(data), encoding="utf-8")
    MODULE.write_text(module(data), encoding="utf-8")
    from glyph_atlas import custom_parts

    found = rows(data)
    encoded = sum(1 for row in found if custom_parts.drawn(f"&{row[0]};") is not None)
    print(f"{len(found)} parts, {encoded} drawn in encoded characters, {sum(1 for row in found if row[2]) - encoded} only with a "
          f"private-use glyph, {sum(len(row[1]) for row in found)} names, {len(retired(data))} retired")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
