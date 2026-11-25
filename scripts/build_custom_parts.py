"""Build the table of parts no code point names, from ids-input's custom-components file.

BabelStone IDS writes such a part as a number (`{88}`, the top of 合), CHISE IDS as an entity
(`&CDP-8BD0;`) and zi.tools as a stroke chain (`#(-𠃌㇉)`). ids-input gives each shape one identifier,
`X-00087`, written `&X-00087;` inside a description, and lists every name each is known by, with what a
composer draws in its place. The file is fetched at a pinned commit and checked against its digest.

    uv run scripts/build_custom_parts.py

Writes data/vocab/custom-parts.tsv, which `glyph_atlas.custom_parts` reads, and
apps/cloudflare/src/customPartsData.ts, which the Worker and the review app read.
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


def rows(data: dict) -> list[tuple[str, list[str], str, bool, str]]:
    """One row per identifier in use: its names, the description a composer draws in its place (a
    numbered part's fragment from BabelStone's note 6 before any equivalent), whether that drawing is
    approximate, and BabelStone's description of the numbered part, if any."""
    parts = {}
    for form in data["forms"]:
        parts[form["id"]] = {"names": list(form["names"]), "draw": "", "approximate": False, "description": ""}
    for component in sorted(data["components"], key=lambda c: c["kind"] != "numbered"):
        part = parts.get(component["id"])
        if part is None:
            continue
        if component.get("description") and not part["description"]:
            part["description"] = component["description"]
        drawn = component.get("fragment") or component.get("draw") or ""
        if drawn and not part["draw"]:
            part["draw"], part["approximate"] = drawn, bool(component.get("drawApproximate"))
    return [(key, part["names"], part["draw"], part["approximate"], part["description"]) for key, part in sorted(parts.items())]


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
        "# draw: what a composer draws in its place: a description in encoded characters, or the private-use",
        "#   character of BabelStone Han PUA that draws a numbered part no description writes out; empty where neither does;",
        "#   approximate marks one that is close only.",
        "# retired: for an identifier taken out of use, the one it was merged into, or withdrawn.",
        "# columns: id, names, draw, approximate, description, retired",
    ]
    for key, names, draw, approximate, description in rows(data):
        lines.append("\t".join((key, " ".join(names), draw, "1" if approximate else "", description, "")))
    for key, into in retired(data):
        lines.append("\t".join((key, "", "", "", "", into or "withdrawn")))
    return "\n".join(lines) + "\n"


def module(data: dict) -> str:
    """Each name's identifier and each identifier's drawing, as the Worker and the review app read them."""
    named: dict[str, str] = {}
    for key, names, *_ in rows(data):
        for name in names:
            named.setdefault(name, key)
    drawn = {key: draw for key, _, draw, _, _ in rows(data) if draw}
    return (
        "// Written by scripts/build_custom_parts.py from data/vocab/custom-parts.tsv; edit neither here.\n"
        "/** Every spelling of a part no code point names, with the identifier ids-input gives the part. */\n"
        f"export const CUSTOM_PART_NAMES: Record<string, string> = {json.dumps(named, ensure_ascii=False, separators=(',', ':'))};\n"
        "/** The identifiers taken out of use, each with the one it was merged into, '' for one withdrawn. */\n"
        f"export const CUSTOM_PART_RETIRED: Record<string, string> = {json.dumps(dict(retired(data)), separators=(',', ':'))};\n"
        "/** Every identifier in use. */\n"
        f"export const CUSTOM_PARTS: readonly string[] = {json.dumps([key for key, *_ in rows(data)], separators=(',', ':'))};\n"
        "/** What a composer draws in a part's place, where something does: a description in encoded characters, or the\n"
        " *  private-use character of BabelStone Han PUA that draws a numbered part. */\n"
        f"export const CUSTOM_PART_DRAWN: Record<string, string> = {json.dumps(drawn, ensure_ascii=False, separators=(',', ':'))};\n"
    )


def main() -> int:
    data = fetched()
    TABLE.write_text(table(data), encoding="utf-8")
    MODULE.write_text(module(data), encoding="utf-8")
    found = rows(data)
    private = sum(1 for row in found if any(0xE000 <= ord(c) <= 0xF8FF for c in row[2]))
    print(f"{len(found)} parts, {sum(1 for row in found if row[2]) - private} drawn with encoded characters only, {private} with a private-use glyph, "
          f"{sum(len(row[1]) for row in found)} names, {len(retired(data))} retired")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
