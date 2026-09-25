"""The style of letterforms in a document, a page or one unit, a value of `data/vocab/style.yaml`."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from .vocab_tree import Tree

if TYPE_CHECKING:
    from .schema import Document, Page, Unit

VOCAB = Path(__file__).resolve().parents[2] / "data/vocab/style.yaml"
#: Styles a person has confirmed for whole documents.
DOCUMENTS = Path(__file__).resolve().parents[2] / "data/vocab/document-styles.yaml"
TREE = Tree(VOCAB)
check, label = TREE.check, TREE.label
UNASSESSED = "unassessed"
MIXED = "mixed"


def vocabulary() -> dict[str, dict]:
    return TREE.nodes


@lru_cache(maxsize=4)
def _confirmed(path: str, stamp: int) -> dict[str, dict]:
    entries = (yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}).get("documents") or {}
    for key, entry in entries.items():
        check(entry.get("style", ""))
        if not entry.get("evidence"):
            raise ValueError(f"{path}: {key} has no evidence")
    return entries


def confirmed() -> dict[str, dict]:
    """The confirmed document styles by document id, with their evidence."""
    return _confirmed(str(DOCUMENTS), DOCUMENTS.stat().st_mtime_ns) if DOCUMENTS.exists() else {}


def document_style(document: Document) -> str:
    """The confirmed style of `document`, else the style it states."""
    entry = confirmed().get(document.id)
    return entry["style"] if entry else document.style


def style_of(unit: Unit, page: Page | None = None, document: Document | None = None) -> str:
    """A unit's own style, else its page's, else its document's, confirmed or stated.

    A `mixed` page or document says its units differ, so it passes nothing down.
    """
    for record, value in ((unit, unit.style), (page, page and page.style),
                          (document, document and document_style(document))):
        if record is None:
            continue
        if value == MIXED and record is not unit:
            return UNASSESSED
        if value != UNASSESSED:
            return value
    return UNASSESSED
