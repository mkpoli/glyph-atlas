"""Write the registered figure of each CJK compatibility ideograph as the Worker reads it.

A compatibility ideograph stands for a unified ideograph and is no form of its own
(docs/decisions/0013-finer-forms.md). Where the MJ文字情報一覧表 gives the compatibility code point
as a figure's implementation (data/vocab/mj-kanji.tsv: U+FA55 implements MJ030283, 7A81_E0103), that
figure's Moji_Joho sequence names it; `glyph_atlas.representation.figures` reads the same rows.

    uv run scripts/build_han_compatibility.py

Writes apps/cloudflare/src/compatibilityData.ts, which the Worker and the review app read.
"""

from __future__ import annotations

from pathlib import Path

from glyph_atlas import representation

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "apps" / "cloudflare" / "src" / "compatibilityData.ts"


def module() -> str:
    """The figures as hexadecimal code points: a compatibility ideograph typed into a source file does
    not survive a tool that normalises it."""
    figures = " ".join(f"{ord(char):X}:{ord(sequence[1]):X}" for char, sequence in sorted(representation.figures().items()))
    return (
        "// Written by scripts/build_han_compatibility.py from data/vocab/mj-kanji.tsv; edit neither here.\n"
        "/** The compatibility ideographs a Moji_Joho sequence of the IVD names, each with the selector that\n"
        " *  follows its unified ideograph, as hexadecimal code points. */\n"
        f"export const MOJI_JOHO = '{figures}';\n"
    )


def main() -> int:
    MODULE.write_text(module(), encoding="utf-8")
    print(f"{len(representation.figures())} compatibility ideographs with a Moji_Joho sequence")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
