"""The style of letterforms in a document, a page or one unit, a value of `data/vocab/style.yaml`."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .vocab_tree import Tree

if TYPE_CHECKING:
    from .schema import Document, Page, Unit

VOCAB = Path(__file__).resolve().parents[2] / "data/vocab/style.yaml"
TREE = Tree(VOCAB)
check, label = TREE.check, TREE.label
UNASSESSED = "unassessed"
MIXED = "mixed"


def vocabulary() -> dict[str, dict]:
    return TREE.nodes


def style_of(unit: Unit, page: Page | None = None, document: Document | None = None) -> str:
    """A unit's own style, else its page's, else its document's.

    A `mixed` page or document says its units differ, so it passes nothing down.
    """
    for record in (unit, page, document):
        if record is None:
            continue
        if record.style == MIXED and record is not unit:
            return UNASSESSED
        if record.style != UNASSESSED:
            return record.style
    return UNASSESSED
