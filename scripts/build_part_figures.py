"""Write the variation sequences the IDS composer draws as the Worker and the review app read them.

The export the composer draws sequences from (`VARIANTS` in apps/review/src/lib/composer/variants.js)
lists in `variants.json` each character with the selectors its font, Noto Sans CJK JP, has a glyph of
its own for. The ideographic ones are kept: a standardized sequence (U+FE00) is no registered figure.
The second table is the descriptions with such a sequence that a Moji_Joho figure already has in
apps/review/static/fonts/mj/ (遇 U+9047 U+E0103 is ⿺辶禺󠄁), so that a form has one name.

    uv run scripts/build_part_figures.py

Writes apps/cloudflare/src/partFiguresData.ts.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
EXPORT = ROOT / "apps" / "review" / "src" / "lib" / "composer" / "variants.js"
FIGURES = ROOT / "apps" / "review" / "static" / "fonts" / "mj"
MODULE = ROOT / "apps" / "cloudflare" / "src" / "partFiguresData.ts"
FIRST, LAST = 0xE0100, 0xE01EF


def export_url() -> str:
    found = re.search(r"VARIANTS = '([^']+)'", EXPORT.read_text(encoding="utf-8"))
    assert found, f"{EXPORT} names no export"
    return found[1]


def selected(text: str) -> list[str]:
    return re.findall(f"(.[{chr(FIRST)}-{chr(LAST)}])", text)


def named(rows: dict[str, str]) -> list[str]:
    """The descriptions of Moji_Joho figures with a sequence of `rows` in them, and no other sequence."""
    described = {row[3] for file in FIGURES.glob("mj-figures-*.json") for figures in json.loads(file.read_text(encoding="utf-8")).values()
                 for row in figures if row[3]}
    return sorted(text for text in described if (parts := selected(text)) and all(part[1] in rows.get(part[0], "") for part in parts))


def escaped(text: str) -> str:
    return "".join(f"\\u{{{ord(c):X}}}" if FIRST <= ord(c) <= LAST else c for c in text)


def module(listed: dict[str, str], version: str) -> str:
    rows = {char: "".join(kept) for char, selectors in sorted(listed.items())
            if (kept := sorted(s for s in selectors if FIRST <= ord(s) <= LAST))}
    body = "\n".join(f"  {json.dumps(char, ensure_ascii=False)}: '{escaped(kept)}',"
                     for char, kept in rows.items())
    return (
        f"// Written by scripts/build_part_figures.py from the composer's export {version}; do not edit.\n"
        "/** The ideographic variation selectors each character has a glyph of its own for in the font the\n"
        " *  composer draws with, Noto Sans CJK JP. */\n"
        f"export const PART_FIGURES: Record<string, string> = {{\n{body}\n}};\n"
        "/** The descriptions with such a part that a registered figure of a whole character already has. */\n"
        f"export const FIGURE_DESCRIPTIONS = new Set([\n{''.join(f"  '{escaped(text)}',{chr(10)}" for text in named(rows))}]);\n"
    )


def main() -> None:
    url = export_url()
    listed = httpx.get(url + "variants.json", timeout=60).raise_for_status().json()
    MODULE.write_text(module(listed, url.rstrip("/").rsplit("/", 1)[-1]), encoding="utf-8")
    print(f"{MODULE.relative_to(ROOT)} written")


if __name__ == "__main__":
    main()
