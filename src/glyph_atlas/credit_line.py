"""The credit a reader sees under a crop or a page, from the attribution its rights record.

An attribution may carry words of the pipeline's own: that a licence was not resolved, that no holder is
stated, what a licence allows. A credit names people, works and collections, so those words are left out
where it is shown. The record keeps its attribution, which `reconcile` reads.
"""
from __future__ import annotations

import ast
import re

UNRESOLVED = "unresolved for review: "
#: The holder a note of `rights._raw_note` states, as the string literal it wrote.
_HOLDER = re.compile(r"\bholder=('(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\")")
_BREAK = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]*>")
#: What the pipeline wrote, and what stays.
_WORDS = (
    (re.compile(r"^unstated holder, (?:via )?"), ""),
    (re.compile(r", via (?=Wikimedia Commons\b)"), ", "),
    (re.compile(r" \(free licence with attribution\)$"), ""),
    (re.compile(r"; scans supplied by "), "; "),
)


def credit_line(attribution: str | None) -> str | None:
    """The credit of `attribution`, or None where it names no one."""
    if not attribution:
        return None
    if attribution.startswith(UNRESOLVED):
        stated = _HOLDER.search(attribution)
        # A holder's name ends at its first line; what follows is its terms, or its name again.
        name = _BREAK.split(ast.literal_eval(stated[1]))[0] if stated else ""
        return _TAG.sub("", name).strip() or None
    for words, kept in _WORDS:
        attribution = words.sub(kept, attribution)
    return attribution.strip() or None
