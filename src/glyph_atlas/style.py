"""The style of letterforms in a document, a page or one unit, a value of `data/vocab/style.yaml`."""
from __future__ import annotations

import copy
import datetime
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


#: What an item of a confirmed style's evidence may rest on.
EVIDENCE_SOURCES = frozenset({"reviewer", "style-teacher", "reference"})


class InvalidDocumentStyles(ValueError):
    """`document-styles.yaml` holds something that is not a reviewed style."""


class _UniqueKeys(yaml.SafeLoader):
    """A YAML loader that refuses a mapping with the same key twice."""


def _mapping(loader, node, deep=False):
    keys = [loader.construct_object(key, deep=deep) for key, _ in node.value]
    repeated = {key for key in keys if keys.count(key) > 1}
    if repeated:
        raise InvalidDocumentStyles(f"{node.start_mark}: {', '.join(map(str, sorted(repeated, key=str)))} listed twice")
    return loader.construct_mapping(node, deep=deep)


_UniqueKeys.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def _entry(path: str, key, entry) -> None:
    """Refuse an entry that is not a person's confirmation of a real style."""
    where = f"{path}: {key!r}"
    if not isinstance(key, str):
        raise InvalidDocumentStyles(f"{where}: a document id must be a string")
    if not isinstance(entry, dict):
        raise InvalidDocumentStyles(f"{where}: an entry must be a mapping")
    value = entry.get("style", "")
    check(value)
    if value == UNASSESSED:
        raise InvalidDocumentStyles(f"{where}: {UNASSESSED} is not a style to confirm")
    evidence = entry.get("evidence")
    if not isinstance(evidence, list) or not all(isinstance(item, dict) for item in evidence):
        raise InvalidDocumentStyles(f"{where}: evidence must be a list of mappings")
    unknown = {item.get("source") for item in evidence} - EVIDENCE_SOURCES
    if unknown:
        raise InvalidDocumentStyles(f"{where}: unknown evidence source {sorted(map(str, unknown))}")
    reviewers = [item for item in evidence if item.get("source") == "reviewer"]
    if len(reviewers) != 1 or not isinstance(reviewers[0].get("reviewed"), datetime.date):
        raise InvalidDocumentStyles(f"{where}: evidence needs one reviewer item with a reviewed date")


@lru_cache(maxsize=4)
def _confirmed(path: str, stamp: int, size: int) -> dict[str, dict]:
    data = yaml.load(Path(path).read_text(encoding="utf-8"), Loader=_UniqueKeys) or {}
    entries = data.get("documents") if isinstance(data, dict) else None
    if entries is None:
        entries = {}
    if not isinstance(entries, dict):
        raise InvalidDocumentStyles(f"{path}: documents must be a mapping of document id to entry")
    for key, entry in entries.items():
        _entry(path, key, entry)
    return entries


def confirmed() -> dict[str, dict]:
    """The confirmed document styles by document id, with their evidence."""
    if not DOCUMENTS.exists():
        return {}
    stat = DOCUMENTS.stat()
    return copy.deepcopy(_confirmed(str(DOCUMENTS), stat.st_mtime_ns, stat.st_size))


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
