"""Drawn forms: private-use code points for ways of writing that nothing else names.

`data/vocab/drawn-forms.yaml` gives each form a code point of Supplementary Private Use Area-A, the
grapheme it writes and a drawing in each of two styles (docs/decisions/0014-drawn-forms.md). A form picked by that code point
is a `pua` representation in the table's namespace and version; any other private-use character names
nothing. `refs` holds each form as a character of its grapheme's family. The Worker reads the same
table as `drawnFormsData.ts`, which `scripts/build_drawn_forms.py` writes with the fonts that draw
the forms.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
TABLE = ROOT / "data" / "vocab" / "drawn-forms.yaml"
DRAWINGS = ROOT / "data" / "vocab" / "drawn-forms"
#: The styles a form is drawn in: sans for text set in a sans-serif face, serif for the rest.
STYLES = ("sans", "serif")
#: Supplementary Private Use Area-A, where no font of the site and no 구결자 convention has a glyph.
AREA = (0xF0000, 0xFFFFD)


@dataclass(frozen=True)
class DrawnForm:
    """One drawn form: its character, the head of the grapheme it writes, its script and what it looks like."""

    char: str
    written_for: str
    script: str
    name: str
    basis: str

    @property
    def code_point(self) -> str:
        return f"U+{ord(self.char):X}"

    def drawing(self, style: str) -> Path:
        """The form's drawing in one of `STYLES`."""
        return DRAWINGS / f"{ord(self.char):X}.{style}.svg"


@dataclass(frozen=True)
class Table:
    namespace: str
    version: str
    forms: dict[str, DrawnForm]


def _char(code_point: str) -> str:
    digits = code_point.removeprefix("U+")
    if digits == code_point:
        raise ValueError(f"{code_point!r} is not a code point")
    return chr(int(digits, 16))


@cache
def table() -> Table:
    """The table as written, checked: every form in the private-use area, with its drawings, a grapheme
    that is a character, and the fields a reader needs."""
    data = yaml.safe_load(TABLE.read_text(encoding="utf-8"))
    namespace, version = data["namespace"], data["version"]
    if not isinstance(namespace, str) or not isinstance(version, str) or not namespace or not version:
        raise ValueError("drawn-forms.yaml names its namespace and version as text")
    forms: dict[str, DrawnForm] = {}
    for code_point, row in data["forms"].items():
        char = _char(code_point)
        if not AREA[0] <= ord(char) <= AREA[1] or code_point != f"U+{ord(char):X}":
            raise ValueError(f"{code_point} is not a code point of Supplementary Private Use Area-A, written U+F0000")
        form = DrawnForm(char, _char(row["written_for"]), row["script"], " ".join(row["name"].split()), " ".join(row["basis"].split()))
        if not form.name or not form.basis or not form.script:
            raise ValueError(f"{code_point} lacks a name, a basis or a script")
        for style in STYLES:
            if not form.drawing(style).exists():
                raise ValueError(f"{code_point} has no drawing at {form.drawing(style).relative_to(ROOT)}")
        forms[char] = form
    return Table(namespace, version, forms)


def drawn(char: str) -> DrawnForm | None:
    """The drawn form a character names, or None for any other character."""
    return table().forms.get(char)


def written_for(grapheme: str) -> list[DrawnForm]:
    """The drawn forms of a grapheme, named by its head, in code point order."""
    return sorted((form for form in table().forms.values() if form.written_for == grapheme), key=lambda form: form.char)
