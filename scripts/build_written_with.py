"""Write the table of characters a grapheme is also written with as the Worker reads it.

`data/vocab/written-with.yaml` lists characters of another script that write a grapheme (ヿ for 事) with
what states it; `glyph_atlas.written_with` reads the same rows.

    uv run scripts/build_written_with.py

Writes apps/cloudflare/src/writtenWithData.ts.
"""

from __future__ import annotations

import json
from pathlib import Path

from glyph_atlas import written_with

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "apps" / "cloudflare" / "src" / "writtenWithData.ts"


def module() -> str:
    sources, writers = written_with._table()
    rows = [{"char": writer.char, "grapheme": writer.grapheme, "tier": writer.tier, "relations": writer.relations}
            for writer in sorted(writers.values(), key=lambda writer: writer.char)]
    return (
        "// Written by scripts/build_written_with.py from data/vocab/written-with.yaml; edit neither here.\n"
        "/** The characters of another script a grapheme is also written with: each with the head of that\n"
        " *  grapheme, whether a source states the link or the table makes it, and the statements, as rows\n"
        " *  of the variant graph. */\n"
        "export const WRITERS: { char: string; grapheme: string; tier: string; relations: { relation: string; source: string; detail: string }[] }[] = "
        f"{json.dumps(rows, ensure_ascii=True, indent=2)};\n"
        "/** The citation of each source those statements name. */\n"
        f"export const WRITER_SOURCES: Record<string, string> = {json.dumps(sources, ensure_ascii=True, indent=2)};\n"
    )


def main() -> int:
    MODULE.write_text(module(), encoding="utf-8")
    print(f"{len(written_with._table()[1])} characters that also write a grapheme")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
