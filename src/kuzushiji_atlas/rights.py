"""Rights vocabulary and licence resolution.

Every licence string, deed URL and institutional terms page that the upstreams use is recorded once
in `data/vocab/licences.yaml`; `resolve` turns the fields of an upstream record into a `Rights`
record, `manifest_rights` reads the rights fields of a IIIF manifest, and `eligible` answers whether
material may go into a release under a given licence. Input that matches no entry is kept as
`Licence.UNKNOWN` with the raw strings in `attribution`, so that nothing is silently granted and
nothing raises mid-import.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import yaml

from .schema import Licence, Rights

ROOT = Path(__file__).resolve().parents[2]
LICENCES = ROOT / "data" / "vocab" / "licences.yaml"
HOLDERS = ROOT / "data" / "vocab" / "holders.yaml"

PER_ITEM = "per-item"
REQUIRED = ("match", "licence", "eligible", "obligations", "checked")
HOLDER_LICENCE_FIELDS = ("licence", "licensing", "rights", "terms", "url")
LANGUAGE_ORDER = ("en", "ja", "@none", "none")

# A share-alike source can only be released under a share-alike target; a target that grants nothing
# cannot carry a release at all.
SHARE_ALIKE = frozenset({Licence.CC_BY_SA_2_1_JP, Licence.CC_BY_SA_3, Licence.CC_BY_SA_4})
NOT_A_TARGET = frozenset({Licence.UNKNOWN, Licence.RESTRICTED, Licence.RS_NOC_CR})

_HREF = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]*>")
_SPACE = re.compile(r"\s+")
_DEED = re.compile(r"/deed(?:\.[a-z]{2}(?:-[a-z]{2})?)?$", re.IGNORECASE)
_TRAILING = re.compile(r"/+$")

# The vocabulary is read once per process: the files are small, immutable during a run, and read on
# every record by the importers. `vocabulary` and `holder_entry` pass the module constant in at call
# time, so a test can point either path at a fixture.
_ENTRY_CACHE: dict[Path, tuple[dict, ...]] = {}
_INDEX_CACHE: dict[Path, dict[str, dict]] = {}
_LICENCE_CACHE: dict[Path, dict[str, dict]] = {}
_HOLDER_CACHE: dict[Path, tuple[dict, ...]] = {}


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _clean(value: str) -> str:
    """Collapse HTML markup and whitespace in a statement."""
    return _SPACE.sub(" ", _TAG.sub(" ", value)).strip()


def _href(value: str) -> str:
    """The target of an anchor, or the value itself."""
    found = _HREF.search(value)
    return found.group(1) if found else value


def _label(value: str) -> str:
    """The first line of a value: holding institution fields put a note after a `<br>`."""
    return _clean(_BR.split(value, maxsplit=1)[0])


def key(raw: str) -> str:
    """A comparison key for a licence statement: a folded id or label, or a canonical URL.

    `http` and `https` name the same page, a Creative Commons `deed.<language>` is the deed of the
    licence above it, and `rightsstatements.org` serves the same statement under `page/` and
    `vocab/`; all of those collapse to one key. The path of other hosts is case-folded as well,
    which the host names in this vocabulary tolerate.
    """
    text = _clean(_href(raw))
    if not text:
        return ""
    if "://" not in text:
        return text.casefold()
    parts = urlsplit(text)
    host = (parts.hostname or "").removeprefix("www.").casefold()
    path = _TRAILING.sub("", parts.path)
    if host.endswith("creativecommons.org"):
        path = _DEED.sub("", path)
    elif host.endswith("rightsstatements.org"):
        path = path.replace("/page/", "/vocab/", 1)
    return urlunsplit(("https", host, path.casefold(), parts.query, ""))


def _entries(path: Path) -> tuple[dict, ...]:
    if path not in _ENTRY_CACHE:
        _ENTRY_CACHE[path] = tuple(_parse_entries(path))
    return _ENTRY_CACHE[path]


def _parse_entries(path: Path) -> list[dict]:
    raw = _load(path)
    if not isinstance(raw, list):
        raise TypeError(f"{path}: the vocabulary must be a list of entries")
    entries = []
    for position, item in enumerate(raw):
        if not isinstance(item, dict):
            raise TypeError(f"{path}: entry {position} is not a mapping")
        missing = [field for field in REQUIRED if field not in item]
        if missing:
            raise ValueError(f"{path}: entry {position} has no {', '.join(missing)}")
        match = item["match"] if isinstance(item["match"], list) else [item["match"]]
        try:
            licence = Licence(item["licence"])
        except ValueError as error:
            raise ValueError(f"{path}: entry {position} states {item['licence']!r}, not a licence") from error
        eligible = item["eligible"]
        if eligible is not True and eligible is not False and eligible != PER_ITEM:
            raise ValueError(f"{path}: entry {position} has eligible {eligible!r}")
        checked = item["checked"]
        if isinstance(checked, str):
            checked = date.fromisoformat(checked)
        entries.append(
            {
                "match": [str(value) for value in match],
                "licence": licence.value,
                "label": str(item.get("label") or licence.value),
                "evidence": item.get("evidence"),
                "eligible": eligible,
                "obligations": str(item["obligations"]),
                "checked": checked,
            }
        )
    return entries


def vocabulary(path: Path | None = None) -> list[dict]:
    """`data/vocab/licences.yaml`: one entry per known licence statement."""
    return [dict(entry) for entry in _entries(path or LICENCES)]


def _index(path: Path) -> dict[str, dict]:
    if path not in _INDEX_CACHE:
        index: dict[str, dict] = {}
        for entry in _entries(path):
            for raw in entry["match"]:
                folded = key(raw)
                if folded:
                    index.setdefault(folded, entry)
        _INDEX_CACHE[path] = index
    return _INDEX_CACHE[path]


def _by_licence(path: Path) -> dict[str, dict]:
    if path not in _LICENCE_CACHE:
        found: dict[str, dict] = {}
        for entry in _entries(path):
            found.setdefault(entry["licence"], entry)
        _LICENCE_CACHE[path] = found
    return _LICENCE_CACHE[path]


def _lookup(raw: Any) -> dict | None:
    """The vocabulary entry a statement names, or None."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    entry = _index(LICENCES).get(key(raw))
    if entry is not None:
        return entry
    try:
        licence = Licence(_clean(_href(raw)))
    except ValueError:
        return None
    return _by_licence(LICENCES).get(licence.value)


def holder_entry(name: str | None) -> dict | None:
    """The `data/vocab/holders.yaml` row for a holder, matched by id, name or containment.

    A holder may be listed as `per-item`, in which case its licence is stated on each item and the
    row itself names no licence the resolver can use.
    """
    if not isinstance(name, str) or not _label(name):
        return None
    query = _label(name).casefold()
    rows = _holder_rows(HOLDERS)
    pairs = [(row, str(value)) for row in rows for value in _holder_names(row) if str(value).strip()]
    for row, value in pairs:
        if _label(value).casefold() == query:
            return dict(row)
    best: dict | None = None
    length = 0
    for row, value in pairs:
        candidate = _label(value).casefold()
        if candidate and candidate in query and len(candidate) > length:
            best, length = row, len(candidate)
    return dict(best) if best is not None else None


def _holder_rows(path: Path) -> tuple[dict, ...]:
    if path not in _HOLDER_CACHE:
        raw = _load(path)
        if not isinstance(raw, list):
            raise ValueError(f"{path}: the holder table must be a list of rows")
        _HOLDER_CACHE[path] = tuple(row for row in raw if isinstance(row, dict))
    return _HOLDER_CACHE[path]


def _holder_names(row: dict) -> list[Any]:
    names = [row.get(field) for field in ("id", "ja", "en", "name")]
    aliases = row.get("aliases")
    if isinstance(aliases, list):
        names.extend(aliases)
    return names


def _per_item(row: dict) -> bool:
    return any(str(row.get(field, "")).strip() == PER_ITEM for field in HOLDER_LICENCE_FIELDS)


def _credit(holder: Any, row: dict | None) -> str | None:
    if isinstance(holder, str) and _label(holder):
        return _label(holder)
    if row is not None:
        for field in ("ja", "en", "name", "id"):
            if isinstance(row.get(field), str) and row[field].strip():
                return row[field].strip()
    return None


def _evidence(url: Any, row: dict | None) -> str | None:
    if isinstance(url, str) and "://" in _href(url):
        return _href(url).strip()
    if row is not None and isinstance(row.get("terms"), str) and row["terms"].strip():
        return row["terms"].strip()
    return None


def _raw_note(licence: Any, url: Any, holder: Any) -> str:
    """The unresolved input, kept verbatim for later review."""
    parts = [
        f"{field}={value!r}"
        for field, value in (("licence", licence), ("url", url), ("holder", holder))
        if isinstance(value, str) and value.strip()
    ]
    if not parts:
        return "unresolved for review: no licence statement"
    return "unresolved for review: " + ", ".join(parts)


def resolve(
    *,
    licence: str | None = None,
    url: str | None = None,
    holder: str | None = None,
    checked: date | None = None,
) -> Rights:
    """The rights of one upstream record from its licence field, licence URL and holder.

    The licence field decides first, then the URL, then the holder's own terms page; a statement
    that only points at an institutional page which grants nothing resolves to `restricted`, and a
    holder that states its licences per item lends only its terms page, never a licence. Input that
    matches no statement stays `Licence.UNKNOWN` with the raw strings in `attribution`. Raises
    nothing.
    """
    row = holder_entry(holder)
    matches: list[dict] = []
    for value in (licence, url):
        entry = _lookup(value)
        if entry is not None:
            matches.append(entry)
    if row is not None:
        fields = ("terms", "url") if _per_item(row) else HOLDER_LICENCE_FIELDS
        for field in fields:
            value = row.get(field)
            if not isinstance(value, str) or not value.strip() or value.strip() == PER_ITEM:
                continue
            entry = _lookup(value)
            if entry is not None:
                matches.append(entry)
                break
    granted = next((entry for entry in matches if entry["licence"] != Licence.UNKNOWN.value), None)
    if granted is None and matches:
        granted = matches[0]
    holder_name = _credit(holder, row)
    if granted is None:
        return Rights(
            licence=Licence.UNKNOWN,
            holder=holder_name,
            attribution=_raw_note(licence, url, holder),
            evidence=_evidence(url, row),
            checked=checked,
        )
    entry = granted
    if entry["licence"] == Licence.UNKNOWN.value:
        return Rights(
            licence=Licence.UNKNOWN,
            holder=holder_name,
            attribution=_raw_note(licence, url, holder),
            evidence=entry["evidence"] or _evidence(url, row),
            checked=checked,
        )
    evidence = entry["evidence"]
    if not evidence and isinstance(url, str) and _lookup(url) is entry:
        evidence = _href(url).strip()
    return Rights(
        licence=Licence(entry["licence"]),
        holder=holder_name,
        attribution=holder_name or entry["label"],
        evidence=evidence,
        checked=checked or entry["checked"],
    )


def _text(value: Any) -> str | None:
    """A IIIF text value: a string, a language map, or a `{label, value}` statement."""
    if isinstance(value, str):
        return _clean(value) or None
    if isinstance(value, list):
        parts = [text for text in (_text(item) for item in value) if text]
        return "; ".join(parts) or None
    if isinstance(value, dict):
        for field in ("value", "@value"):
            if field in value:
                return _text(value[field])
        for field in LANGUAGE_ORDER:
            if field in value:
                return _text(value[field])
        for field in sorted(value):
            text = _text(value[field])
            if text:
                return text
    return None


def _uri(value: Any) -> str | None:
    """A IIIF identifier value: a string, the first of a list, or an `@id`."""
    if isinstance(value, str):
        return _clean(_href(value)) or None
    if isinstance(value, list):
        for item in value:
            found = _uri(item)
            if found:
                return found
        return None
    if isinstance(value, dict):
        for field in ("@id", "id", "value"):
            if field in value:
                found = _uri(value[field])
                if found:
                    return found
    return None


def manifest_rights(manifest: dict) -> Rights | None:
    """The rights a IIIF manifest states, or None when it states none.

    Reads Presentation 2 `license` and `attribution` and Presentation 3 `rights` and
    `requiredStatement`; the credit line from the manifest replaces the one the vocabulary would
    supply, and a rights URI that matches no statement leaves the licence unknown with the URI kept
    as evidence.
    """
    if not isinstance(manifest, dict):
        return None
    url = _uri(manifest.get("rights")) or _uri(manifest.get("license"))
    credit = _text(manifest.get("attribution")) or _text(manifest.get("requiredStatement"))
    if url is None and credit is None:
        return None
    resolved = resolve(url=url) if url else None
    if resolved is None or resolved.licence is Licence.UNKNOWN:
        attribution = credit or (resolved.attribution if resolved is not None else _raw_note(None, url, None))
        return Rights(licence=Licence.UNKNOWN, attribution=attribution, evidence=url)
    return Rights(
        licence=resolved.licence,
        attribution=credit or resolved.attribution,
        evidence=resolved.evidence or url,
        checked=resolved.checked,
    )


def eligible(rights: Rights | None, target: Licence | str = Licence.CC_BY_SA_4) -> bool:
    """Whether material with these rights may go into a release licensed as `target`.

    PD, PDM, CC0, CC BY 4.0, CC BY-SA 3.0, 4.0 and 2.1 JP, the Unicode licence and `bespoke-free`
    are eligible; NC, ND, RS-NOC-CR, restricted, unknown and a per-item statement are not, and
    neither is a record with no rights at all. A share-alike source needs a share-alike target.
    """
    if rights is None:
        return False
    entry = _lookup(rights.evidence) if rights.evidence else None
    if entry is None or entry["licence"] != rights.licence.value:
        entry = _by_licence(LICENCES).get(rights.licence.value)
    if entry is None or entry["eligible"] is not True:
        return False
    wanted = Licence(target)
    if wanted in NOT_A_TARGET:
        return False
    if wanted in SHARE_ALIKE:
        return True
    return rights.licence not in SHARE_ALIKE


def _cell(value: Any) -> str:
    return _SPACE.sub(" ", str(value)).replace("|", "\\|").strip()


def markdown_table() -> str:
    """The vocabulary as Markdown tables: the statements, then their match strings."""
    rows = vocabulary()
    lines = [
        "| Statement | Licence | Eligible | Evidence | Obligations | Checked |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for entry in rows:
        eligible_text = "per-item" if entry["eligible"] == PER_ITEM else str(bool(entry["eligible"])).lower()
        lines.append(
            "| "
            + " | ".join(
                _cell(value)
                for value in (
                    entry["label"],
                    entry["licence"],
                    eligible_text,
                    entry["evidence"] or "",
                    entry["obligations"],
                    entry["checked"] or "",
                )
            )
            + " |"
        )
    lines += ["", "| Match | Licence |", "| --- | --- |"]
    for entry in rows:
        for match in entry["match"]:
            lines.append(f"| {_cell(match)} | {_cell(entry['licence'])} |")
    return "\n".join(lines)
