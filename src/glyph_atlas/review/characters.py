"""The character layer as a service: identity, grapheme, 字母, ligature and the ink that carries them.

Three layers, and this module is the only place that answers all of them together:

- **Grapheme** — a curated orthographic family, including 仮 and 假.
- **Character** — a distinct written identity and code point, with readings and 字母 metadata.
- **Form / occurrence** — the exact ink, source position and variant identifiers of one unit.

The routes are read-only except for one write, and that write keeps the layers apart on purpose:

    GET  /layers/summary
    GET  /layers/search?q=&expand=none|grapheme&limit=&offset=
    GET  /layers/characters/{code_point}?expand=&state=&limit=&offset=
    GET  /layers/occurrences?code_point=&expand=&state=&limit=&offset=
    GET  /layers/graphemes?q=&group=&forms=&limit=&offset=
    GET  /layers/graphemes/{code_point}
    GET  /layers/ligatures
    GET  /layers/candidates?code_point=&limit=
    POST /layers/units/{unit_id}

Search is exact by default: a query that names a character answers that character and nothing else,
so searching 𛄧 does not quietly answer ネ or 子. Widening to the other forms of the grapheme is a
second request the reader makes on purpose (`expand=grapheme`), and every added row says which
expansion produced it. A character the corpus never used is still a character: searching 𪜈 answers
its identity, its components ト + モ and its reading even when the occurrence index is empty, and the
occurrence count is reported as zero rather than as an error.

The write, `POST /layers/units/{unit_id}`, records a **character** correction and a **reading**
correction as two separate review events, on `unicode` and on `reading`. A phonetic reading never
overwrites the encoded written identity, and a corrected identity is stored where the layer that owns
it lives: `Unit.unicode`, which is what every other view derives the character from. When the new
identity is a character the table knows, the unit's `script` follows the layer, because the layer is
its authority. Each event's evidence names its own layer, so the log says which of the three a
reviewer changed.

The corpus is read, never rebuilt here: `/layers/candidates` and the counts on every candidate row
come from the corpus index through `corpus_source`, which calls the corpus worker's own in-process
`CorpusAPI`. This module never scans a corpus and names no source, so the Ainu records, the Honkoku
lines and the next corpus all arrive the same way.
"""

from __future__ import annotations

import json
from functools import lru_cache
from threading import RLock
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from .. import images, refs, visual_families
from ..production import production_info
from ..schema import Box, Character, Document, Page, Unit
from . import corpus_source, status
from .atlas import (
    canonical_identity,
    identity_text,
    label,
    review_state,
    script_of_identity,
    single_character,
    stored_identity,
    written_identity,
)
from .request_cache import file_stamp, lookup_scope, memoize
from .store import BadRequest, ReviewRequest, Store, StoreError


def character_corpus_counts(char: str) -> dict[str, Any] | None:
    """One character's corpus counts, or `None`; a fault is recorded on the summary, not raised."""
    counts, fault = corpus_source.safe(corpus_source.counts, [char])
    row = (counts or {}).get(char)
    return {**(row or {}), "fault": fault} if fault else row


def fault_of(live: dict[str, Any] | None) -> str | None:
    """What the corpus read said, when it did not answer: `error`, `not-loaded`, or nothing."""
    if live is None:
        return "not-loaded"
    return live.get("fault")


def candidate_summary(char: str, *, live: dict[str, Any] | None = None, local: int = 0) -> dict[str, Any]:
    """What the corpus index holds for one character, with the evidence kinds kept apart.

    `glyphs` is the corpus's own `n_glyphs`: located character units, the only thing that may be drawn
    as a crop. `lines` and `pages` are text matches, shown where they were read and never as the
    character's picture. A count the index does not state is `None` — unknown — and `imported` is how
    many occurrences of the character this dataset already holds.
    """
    from_service = corpus_source.summary(live)
    known = [value for value in (from_service["glyphs"], from_service["lines"], from_service["pages"])
             if value is not None]
    fault = fault_of(live)
    return {
        "status": fault or ("ok" if live is not None else "not-loaded"),
        # A fault means the counts are not known, so nothing here reads as a corpus that holds none.
        "known": live is not None and fault is None,
        "glyphs": from_service["glyphs"],
        "lines": from_service["lines"],
        "pages": from_service["pages"],
        "total": sum(known) if known else None,
        "sources": from_service["corpora"],
        "counts_kind": (live or {}).get("counts_kind"),
        "requires_family_scope": bool((live or {}).get("requires_family_scope")),
        "source_glyphs": from_service["glyphs"],
        "family_glyphs": from_service.get("family_glyphs"),
        "imported": local,
    }


class LayerEdit(BaseModel):
    """A correction to one layer of one occurrence, and to no other layer.

    `character` is the encoded written identity — a character or a `U+XXXX` sequence — and is stored
    in `Unit.unicode`. `reading` is the diplomatic reading and is stored in `Unit.reading`. A request
    that carries both writes two events, and a request that carries one leaves the other alone.
    """

    model_config = ConfigDict(extra="forbid")

    id: UUID
    client_id: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=0)
    image_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    character: str | None = Field(default=None, max_length=64,
                                  description="the character or U+XXXX sequence the source printed")
    reading: str | None = Field(default=None, min_length=1, max_length=32)
    box: Box | None = Field(default=None, description="a corrected crop, in page pixels")
    note: str = Field(default="", max_length=2000)
    verdict: Literal["match", "wrong", "unsure"] = "wrong"
    issue: Literal["character", "reading", "crop", "merged", "blank", "unclear", "other"] = "character"


def _source_digest(store: Store, unit: Unit) -> str | None:
    """The checksum of the file this occurrence's crop is cut from, as the collection reports it."""
    from .server import cached_image

    if unit.crop_sha256:
        path = cached_image(unit.crop_sha256)
        return path.stem if path else None
    page = _page(store, unit.page_id) if unit.page_id else None
    if page is None:
        return None
    if page.sha256 and cached_image(page.sha256):
        return page.sha256
    record = _url_index(_index_stamp()).get(page.image)
    return record.sha256 if record else None


def _item(store: Store, unit: Unit, revision: int) -> dict[str, Any]:
    """One occurrence as the collection reports it, for the snapshot a correction has to carry.

    The review export compares a saved review against the record as it now stands, so a correction
    from this module records the same shape the character editor does; two shapes would make one of
    the two look stale forever.
    """
    page = _page(store, unit.page_id) if unit.page_id else None
    document_id = unit.document_id or (page.document_id if page else None)
    document = store.document(document_id) if document_id else None
    standing = status.unit_reviews([unit], store.events())[unit.id]
    digest = _source_digest(store, unit)
    return {
        "id": unit.id,
        "label": label(unit),
        "reading": unit.reading,
        "script": str(unit.script),
        "jibo": refs.jibo_of_unit(unit.unicode),
        "revision": revision,
        "state": review_state(standing.human_review),
        "page_id": unit.page_id,
        "line_id": unit.line_id,
        "box": unit.box.model_dump() if unit.box else None,
        "image_sha256": digest,
        "image": f"/atlas/characters/{unit.id}/image?revision={revision}"
                 + (f"&image_sha256={digest}" if digest else ""),
        "document_id": document_id,
        "document_title": document.title if document else None,
    }


def reading_is_allowed(reading: str, identity: str | None) -> bool:
    """Whether a reading may be written for an occurrence with this identity.

    A unit is one character and reads as one kana, so a reading is one character — unless the
    character is a ligature, whose reading is the two kana it is made of: 𪜈 reads トモ and refusing
    that would make the phonetic layer lose what the shape says. The allowance is not free text: the
    reading has to be the one the character layer states for that ligature, katakana and hiragana
    being one reading, so a reviewer cannot type a phrase into a reading field.
    """
    if single_character(reading):
        return True
    if not identity:
        return False
    ligature = refs.ligature(identity)
    if ligature is None or not ligature.reading:
        return False
    return refs.to_hiragana(reading) == refs.to_hiragana(ligature.reading)


def to_row(character: Character | None) -> dict[str, Any]:
    """A character named inside another row, so a card links without a second request."""
    if character is None:
        return {}
    return {"code_point": character.code_point, "char": character.char, "name": character.name,
            "script": str(character.script)}


def _ligature_row(ligature) -> dict[str, Any]:
    components = []
    for code_point in ligature.components:
        row = refs.character(code_point)
        components.append(to_row(row) or {"code_point": code_point, "char": refs.to_char(code_point)})
    return {"components": components, "reading": ligature.reading, "kind": str(ligature.kind),
            "evidence": ligature.evidence}


def _row(character: Character, counts: dict[str, int], *, reason: str | None = None) -> dict[str, Any]:
    """One character as a compact row: what it is, and how much ink the corpus has of it."""
    row: dict[str, Any] = {
        "code_point": character.code_point,
        "char": character.char,
        "name": character.name,
        "script": str(character.script),
        "age": character.age,
        "block": character.block,
        "readings": list(character.readings),
        # A character that is two kana set as one reads as the two of them: the Unicode name of a
        # digraph states the reading and the character table's `readings` column, which is built from
        # kana names, does not hold it, so the ligature layer is where the reading comes from.
        "reading": (character.readings[0] if character.readings
                    else character.ligature.reading if character.ligature else None),
        "jibo": [to_row(refs.character(refs.to_code_point(letter))) for letter in character.jibo],
        "ligature": _ligature_row(character.ligature) if character.ligature else None,
        "occurrence_count": counts.get(character.code_point, 0),
        "url": f"/layers/characters/{character.code_point}",
        "grapheme": _grapheme_head(character, counts),
        "default_scope": ("grapheme" if _family(character)["relation"]
                          == "shinjitai-kyujitai" else "character"),
    }
    if reason:
        row["reason"] = reason
    return row


def _forms(code_point: str) -> list[str]:
    """Every code point that is a form of the same grapheme, the character itself first."""
    grapheme = refs.grapheme(code_point)
    if grapheme is None:
        return [code_point]
    forms = refs.graphemes().get(grapheme, [])
    return [code_point] + [other for other in forms if other != code_point]


class Layers:
    """The occurrence side of the layer: the units of one dataset, indexed once per request.

    An occurrence is a record and not a glyph: a unit whose written identity is the character, with a
    crop that can be drawn. `refs` answers what a character *is*; this answers where it was printed.

    The router builds this index before serving requests, then rebuilds it when the journal changes.
    """

    def __init__(self, store: Store) -> None:
        self.store = store
        # Startup also builds outside HTTP middleware. Share page and image lookups while
        # indexing thousands of occurrences from the same small set of scans.
        with lookup_scope():
            self._records = [(unit, revision) for unit, revision in store.unit_snapshot()
                             if unit.active and _showable(store, unit)]
        standing = status.unit_reviews([unit for unit, _ in self._records], store.events())
        self._states = {key: review_state(value.human_review) for key, value in standing.items()}
        self._by_identity: dict[str, list[int]] = {}
        self._by_code_point: dict[str, list[int]] = {}
        for index, (unit, _) in enumerate(self._records):
            identity = written_identity(unit)
            if not identity or not single_character(identity):
                continue
            self._by_identity.setdefault(identity, []).append(index)
            self._by_code_point.setdefault(" ".join(refs.to_code_points(identity)), []).append(index)
        self._counts: dict[str, int] | None = None

    @property
    def per_character(self) -> dict[str, int]:
        """Occurrences per code point, the exact written identity and nothing widened."""
        if self._counts is None:
            self._counts = {" ".join(refs.to_code_points(identity)): len(indexes)
                            for identity, indexes in self._by_identity.items()}
        return self._counts

    def state(self, unit_id: str) -> str:
        return self._states.get(unit_id, "pending")

    def rows(self, code_point: str, *, expand: str = "none", state: str = "all"
             ) -> list[tuple[Unit, int, str, bool]]:
        """The units written with a character: `(unit, revision, form, exact)`.

        `exact` is a row of the character the reader asked for; a row with `exact` false is a form of
        the same grapheme and appears only when the reader asked for the expansion.
        """
        wanted = _forms(code_point) if "grapheme" in split_expansions(expand) else [code_point]
        rows: list[tuple[Unit, int, str, bool]] = []
        for form in wanted:
            for index in self._by_code_point.get(form, ()):
                unit, revision = self._records[index]
                if state != "all" and self.state(unit.id) != state:
                    continue
                rows.append((unit, revision, form, form == code_point))
        rows.sort(key=lambda row: (row[0].document_id or "", row[0].page_id or "", row[0].seq or 0, row[0].id))
        return rows

    def counts(self, code_point: str, *, expand: str = "none") -> dict[str, Any]:
        """How much ink one character has, by review state, by document and by form."""
        rows = self.rows(code_point, expand=expand)
        states = [self.state(unit.id) for unit, _, _, _ in rows]
        sources: dict[str, dict[str, Any]] = {}
        forms: dict[str, int] = {}
        for unit, _, form, _ in rows:
            document = _document(self.store, unit.document_id) if unit.document_id else None
            entry = sources.setdefault(unit.document_id or "", {
                "document_id": unit.document_id,
                "title": document.title if document else None,
                "holder": document.holder if document else None,
                "count": 0,
            })
            entry["count"] += 1
            forms[form] = forms.get(form, 0) + 1
        exact = len(rows) if "grapheme" not in split_expansions(expand) else len(self.rows(code_point))
        return {
            "total": len(rows),
            "exact_total": exact,
            "checked": states.count("checked"),
            "pending": states.count("pending"),
            "flagged": states.count("flagged"),
            "by_source": sorted(sources.values(), key=lambda entry: (-entry["count"], entry["document_id"] or "")),
            "by_character": [{"code_point": form, "count": count} for form, count in sorted(forms.items())],
            "expanded": "grapheme" in split_expansions(expand),
        }

    def item(self, unit: Unit, revision: int, form: str, exact: bool) -> dict[str, Any]:
        """One occurrence as the collection lists it: the crop, and where the crop was printed.

        `machine` is true for a record no person has confirmed — an alignment or a classifier wrote
        it — so a view can say "machine proposal" instead of showing it as a verified example. The
        distinction is the review state's, not this module's: `state` is checked, pending or flagged.
        """
        document = _document(self.store, unit.document_id) if unit.document_id else None
        page = _page(self.store, unit.page_id) if unit.page_id else None
        path = f"/atlas/characters/{unit.id}/image?revision={revision}"
        state = self.state(unit.id)
        return {
            "id": unit.id,
            "label": written_identity(unit) or label(unit) or form,
            "written_character": written_identity(unit) or None,
            "code_point": form,
            "grapheme": _grapheme_head(refs.character(form), self.per_character) if refs.character(form) else None,
            "variants": [variant.model_dump() for variant in unit.variants],
            "exact": exact,
            "reading": unit.reading,
            "kind": str(unit.kind),
            "granularity": unit.granularity,
            "classification": str(unit.classification),
            "method": unit.method,
            "review": str(unit.review),
            "machine": state != "checked",
            "state": state,
            "revision": revision,
            "image": path,
            "context_image": path + "&context=true",
            "document_id": unit.document_id,
            "document_title": document.title if document else None,
            **production_info(document),
            "holder": document.holder if document else None,
            "source_refs": dict(document.source_refs) if document else {},
            "page_id": unit.page_id,
            "page_number": page.seq + 1 if page else None,
            "line_id": unit.line_id,
            "box": unit.box.model_dump() if unit.box else None,
        }


@lru_cache(maxsize=2)
def _url_index(stamp: int) -> dict:
    return {record.url: record for record in images.index(images.images_root()) if not record.superseded_by}


@memoize
def _index_stamp() -> int:
    return file_stamp(images.images_root() / "index.parquet") or 0


@memoize
def _page(store: Store, page_id: str) -> Page | None:
    return store.page(page_id)


@memoize
def _document(store: Store, document_id: str) -> Document | None:
    return store.document(document_id)


def _showable(store: Store, unit: Unit) -> bool:
    """Whether a unit has an image that can be drawn: a cached crop, or a cached page behind a box.

    The rule the collection uses, stated once here so that a count in the layer view and a tile in
    the collection cannot disagree about whether an occurrence exists.
    """
    from .server import cached_image

    if unit.crop_sha256:
        return cached_image(unit.crop_sha256) is not None
    if not (unit.page_id and unit.box and unit.box.w > 0 and unit.box.h > 0):
        return False
    page = _page(store, unit.page_id)
    if page is None:
        return False
    if page.sha256 and cached_image(page.sha256):
        return True
    record = _url_index(_index_stamp()).get(page.image)
    return bool(record and cached_image(record.sha256))


def _grapheme_head(character: Character, counts: dict[str, int]) -> dict[str, Any]:
    info = _family(character)
    return {
        **info,
        "is_self": info["code_point"] == character.code_point,
        "occurrence_count": sum(counts.get(member["code_point"], 0) for member in info["members"]),
    }


def _family(character: Character) -> dict[str, Any]:
    """The grapheme family of a character, or a family of itself when no grapheme names it."""
    return refs.grapheme_info(character.code_point) or _own_family(character)


def _own_family(character: Character) -> dict[str, Any]:
    """The family of a character no grapheme names, such as ツ + U+309A: itself alone."""
    return {"code_point": character.code_point, "char": character.char, "name": character.name,
            "script": str(character.script), "label": character.char,
            "members": [{"code_point": character.code_point, "char": character.char}],
            "character_count": 1, "relation": "self", "evidence": [], "url": None}


def character_view(character: Character, layer: Layers, *, expand: str = "none", state: str = "all",
                   limit: int = 24, offset: int = 0) -> dict[str, Any]:
    """One character in full: what it is, the shape it belongs to, and the ink that carries it.

    The dictionary side and the ink side are separate keys, never one list: `layers` are the facts of
    the character — identity, reading, 字母, grapheme, ligature, confusables — while `occurrences` and
    `samples` are where it was printed. A font glyph is not an occurrence and never enters `samples`.
    """
    counts = layer.per_character
    rows = layer.rows(character.code_point, expand=expand, state=state)
    window = rows[offset:offset + limit] if limit else rows
    forms = [refs.character(code_point) for code_point in _forms(character.code_point)]
    written_as = [refs.character(code_point) for code_point in refs.derived(character.char)]
    candidates = candidate_summary(character.char, live=character_corpus_counts(character.char),
                                   local=counts.get(character.code_point, 0))
    row = _row(character, counts)
    return {
        **row,
        "default_scope": "grapheme" if candidates["requires_family_scope"] else row["default_scope"],
        "alias": character.alias,
        "category": character.category,
        "confusables": [to_row(refs.character(code_point)) for code_point in character.confusables],
        "grapheme": _grapheme_head(character, counts),
        "characters": [_row(row, counts) for row in forms if row is not None],
        "visual_analysis": visual_families.family_analysis(character.code_point),
        # The widenings this character offers, with the one the request already applied marked
        # enabled: a card is what a reader navigates the three layers from, so it carries the same
        # choices the search answer does.
        "expansions": _expansions(character, counts, expand=expand),
        # The other direction of the 字母 relation: the kana a reader writes as this kanji. 子 is not
        # a form of them and they are not forms of 子, which is why they are a list of their own.
        "derived": [_row(row, counts) for row in written_as if row is not None],
        "occurrences": {**layer.counts(character.code_point, expand=expand), "filtered": len(rows)},
        "samples": [layer.item(unit, revision, form, exact) for unit, revision, form, exact in window],
        "candidates": candidates,
    }


def _expansions(character: Character, counts: dict[str, int], *, expand: str) -> list[dict[str, Any]]:
    """The widenings a reader may choose, with what each would add. Never applied silently.

    Two relations widen an exact search, and neither is the same as the other:

    - `grapheme` — the other characters written with this shape. ね adds ネ and the hentaigana of the
      same 音価; 仮 adds 假 through its cited orthographic family.
    - `jibo` — the kana written as this kanji. 子 adds 𛂘 and 𛄧, which are kana and not forms of 子.
    """
    chosen = split_expansions(expand)
    options = []
    grapheme = refs.grapheme(character.code_point)
    others = [code_point for code_point in _forms(character.code_point) if code_point != character.code_point]
    if others and grapheme is not None:
        row = refs.character(grapheme)
        options.append({
            "key": "grapheme",
            "label": f"Other characters in the {row.char if row else grapheme} family",
            "code_point": grapheme,
            "count": len(others),
            "occurrence_count": sum(counts.get(code_point, 0) for code_point in others),
            "enabled": "grapheme" in chosen,
        })
    written_as = refs.derived(character.char)
    if written_as:
        options.append({
            "key": "jibo",
            "label": f"Also the kana written as {character.char}",
            "code_point": None,
            "count": len(written_as),
            "occurrence_count": sum(counts.get(code_point, 0) for code_point in written_as),
            "enabled": "jibo" in chosen,
        })
    return options


def split_expansions(expand: str) -> set[str]:
    """`"grapheme,jibo"` as its parts; `"none"` and the empty string as none of them.

    The expansion is a list rather than a mode so that a reader can ask for both relations at once
    without a third name for the pair, and an unknown part is refused rather than ignored: a typo
    would otherwise look like a relation the layer does not have.
    """
    parts = {part.strip() for part in expand.split(",") if part.strip()}
    unknown = parts - {"none", "grapheme", "jibo"}
    if unknown:
        raise BadRequest(f"unknown expansion {sorted(unknown)}; the layer has grapheme and jibo")
    return parts - {"none"}


def _reason(character: Character, term: str) -> str:
    """Why a row is in the answer to a query that named no single character."""
    literal = identity_text(term)
    if literal and refs.to_hiragana(literal) in [refs.to_hiragana(reading) for reading in character.readings]:
        return f"reads {literal}"
    if literal in character.jibo:
        return f"derives from {literal}"
    if len(literal) == 1 and refs.grapheme(character.code_point) == refs.to_code_point(literal):
        return f"a form of {literal}"
    if character.ligature:
        spelled = "".join(refs.to_char(point) for point in character.ligature.components)
        if literal in (character.ligature.reading, spelled):
            return f"the ligature {character.ligature.reading}"
    if literal and literal.lower() in (character.name or "").lower():
        return "name"
    return "in the character layer"


def search(term: str, layer: Layers, *, expand: str = "none") -> dict[str, Any]:
    """What a query names, exact first, and the expansion the reader asked for.

    A single character or a `U+XXXX` code point names a character: the answer is that character and
    no other, whatever the corpus holds. Only `expand="grapheme"` widens it, and then every added row
    says which form it is. Any other query is answered by `refs.search`, which knows names, readings,
    字母 and ligatures, and each row says why it is there.
    """
    term = term.strip()
    if not term:
        return {"query": None, "expand": expand, "terms": None, "match": None, "results": [],
                "total": 0, "available": 0, "expansions": [],
                "hint": "Type a character, a code point, a reading, a 字母 or a name."}

    literal = identity_text(term)
    points = literal.split()
    kind = "term"
    exact: Character | None = None
    if points and all(point[:2].upper() == "U+" for point in points):
        kind = "code_point"
    elif len(literal) == 1:
        kind = "character"
    if kind in ("code_point", "character") and len(literal) == 1:
        exact = refs.character(refs.to_code_point(literal))
    if exact is None and kind in ("code_point", "character"):
        return {"query": term, "expand": expand,
                "terms": {"text": literal, "kind": kind, "code_points": [refs.to_code_point(c) for c in literal]},
                "match": None, "results": [], "total": 0, "available": 0, "expansions": [],
                "hint": f"{literal} is not in the character table. The layer holds every kana of the kana "
                        f"blocks and every CJK unified ideograph."}

    counts = layer.per_character
    if exact is not None:
        grapheme = refs.grapheme(exact.code_point) or exact.code_point
        chosen = split_expansions(expand)
        rows = []
        for code_point in (_forms(exact.code_point) if "grapheme" in chosen else [exact.code_point]):
            row = refs.character(code_point)
            if row is None:
                continue
            reason = ("the character searched for" if code_point == exact.code_point
                      else f"a form of {refs.to_char(grapheme)} ({grapheme})")
            rows.append(_row(row, counts, reason=reason))
        if "jibo" in chosen:
            for code_point in refs.derived(exact.char):
                row = refs.character(code_point)
                if row is not None:
                    rows.append(_row(row, counts, reason=f"written as {exact.char}"))
        return {"query": term, "expand": expand,
                "terms": {"text": literal, "kind": kind, "code_points": [exact.code_point]},
                "match": character_view(exact, layer,
                                        expand="grapheme" if "grapheme" in chosen else "none"),
                "results": rows, "total": len(rows), "available": len(rows),
                "expansions": _expansions(exact, counts, expand=expand), "hint": None}

    found = refs.search(term, limit=96)
    rows = [_row(row, counts, reason=_reason(row, term)) for row in found]
    return {"query": term, "expand": expand,
            "terms": {"text": literal, "kind": kind,
                      "code_points": [refs.to_code_point(char) for char in literal]},
            "match": None, "results": rows, "total": len(rows), "available": len(rows), "expansions": [],
            "hint": None if rows else f"Nothing in the character layer answers {term!r}."}


def suggest(term: str, layer: Layers, *, limit: int = 8) -> dict[str, Any]:
    """The characters a query could name, ranked, each with its own counts. The search box's list.

    A reader types what they see and does not always know what it is. トモ is two characters and the
    text is written with one, 𪜈; ネ is a character and the Meiji page used 𛄧; 子 is a kanji and a
    kana derives from it. This answers all of them in one list, and every row says why it is there:

    0. the character the query literally is, or the code point it names;
    1. a ligature the query spells or reads as (トモ -> 𪜈, ヨリ -> ゟ and 𛄦, コト -> ヿ and 𛄣);
    2. the kana written as the kanji the query names (子 -> 𛂘, 𛄧);
    3. the layer's own search: readings, names, 字母, and the word a multi-character query spells;
    4. the other forms of the shape, so ネ leads to 𛄧 and ね to its 変体仮名.

    The counts on a row belong to that row's character and to nothing else: a ト followed by a モ
    counts under ト and under モ, and it is not 𪜈, so it counts under no ligature. Located crops and
    text matches stay apart — a row reads *2 crops · 212 line matches*, never *214 characters* — and a
    count the corpus does not state stays unknown instead of becoming a zero.
    """
    term = term.strip()
    corpus = {"ready": corpus_source.ready()}
    if not term:
        return {"query": None, "items": [], "total": 0, "status": "idle", "corpus": corpus,
                "hint": "Type a character, a reading, a ligature, a 字母 or a name."}
    literal = identity_text(term)
    counts = layer.per_character
    ranked: dict[str, tuple[int, str]] = {}

    def keep(code_point: str | None, rank: int, reason: str) -> None:
        if not code_point or refs.character(code_point) is None:
            return
        if code_point not in ranked or rank < ranked[code_point][0]:
            ranked[code_point] = (rank, reason)

    if len(literal) == 1:
        keep(refs.to_code_point(literal), 0, "the character itself")
    spelled = refs.to_hiragana(literal)
    for code_point, ligature in refs.ligatures().items():
        keys = {ligature.reading or "", refs.from_code_points(ligature.components)}
        if literal in keys or spelled in {refs.to_hiragana(key) for key in keys}:
            keep(code_point, 1, f"ligature, read {ligature.reading}")
    if len(literal) == 1:
        for code_point in refs.derived(literal):
            keep(code_point, 2, f"written as {literal}")
    name_like = " " in literal and literal.isascii()
    for row in refs.search(term, limit=48):
        if len(literal) > 1 and not name_like:
            # A multi-character kana query names what it spells, not each of its characters: トモ is
            # 𪜈 — the ligature above — and is not と, も, ト and モ with their forms and counts.
            readings = {refs.to_hiragana(reading) for reading in row.readings}
            if spelled not in readings:
                continue
        keep(row.code_point, 4, _reason(row, term))
    if len(literal) == 1:
        # The other forms of the shape: the alternate katakana of a kana come first, because a reader
        # looking at a Meiji page is looking for 𛄧 when they type ネ and not for a hentaigana, and a
        # katakana query is answered with katakana before the hiragana forms of the same 音価.
        asked = refs.character(refs.to_code_point(literal))
        asked_script = str(asked.script) if asked else ""
        forms = _forms(refs.to_code_point(literal))[1:]
        alternates = [point for point in forms
                      if (row := refs.character(point)) and str(row.script) == asked_script
                      and (row.name or "").startswith("KATAKANA LETTER ALTERNATE ")]
        same_script = [point for point in forms
                       if point not in alternates and (row := refs.character(point))
                       and str(row.script) == asked_script]
        rest = [point for point in forms if point not in alternates and point not in same_script]
        for code_point in alternates:
            age = refs.character(code_point).age
            keep(code_point, 2, f"the alternate {literal}" + (f" of Unicode {age}" if age else ""))
        for code_point in same_script:
            keep(code_point, 5, f"a form of {literal}")
        for code_point in rest:
            keep(code_point, 6, f"a form of {literal}")

    ordered = sorted(ranked.items(), key=lambda item: (item[1][0], -counts.get(item[0], 0), item[0]))
    window = ordered[:max(limit, 1)]
    live, fault = corpus_source.safe(corpus_source.counts,
                                     [refs.to_char(code_point) for code_point, _ in window])
    live = live or {}
    items = []
    for code_point, (rank, reason) in window:
        row = refs.character(code_point)
        item = _row(row, counts, reason=reason)
        item["rank"] = rank
        item["kind"] = ("ligature" if row.ligature
                        else "han" if str(row.script) == "han" else "kana")
        item["candidates"] = {**candidate_summary(
            row.char, live=live.get(row.char), local=counts.get(code_point, 0)),
            **({"status": fault} if fault else {})}
        items.append(item)
    return {"query": term, "items": items, "total": len(ordered), "status": "ok", "corpus": corpus,
            "more": max(0, len(ordered) - len(window)),
            "hint": None if items else f"Nothing in the character layer answers {term!r}."}


@lru_cache(maxsize=8)
def _grapheme_rows(group: str, forms: str, wanted: str) -> tuple[dict[str, Any], ...]:
    """The shapes of the layer, without their occurrence counts: the table does not change per request."""
    rows = []
    for code_point, members in refs.graphemes().items():
        row = refs.character(code_point)
        if row is None or (forms == "multiple" and len(members) < 2):
            continue
        script = str(row.script)
        if group == "kana" and script not in ("hiragana", "katakana", "hentaigana"):
            continue
        if group == "han" and script != "han":
            continue
        if wanted and not any(wanted in member.char or wanted.lower() in (member.name or "").lower()
                              for point in members if (member := refs.character(point))):
            continue
        rows.append(refs.grapheme_info(code_point))
    return tuple(rows)


def graphemes(group: str = "all", forms: str = "multiple", q: str = "") -> list[dict[str, Any]]:
    """The grapheme rows, without occurrence counts: what the gallery shows above the numbers."""
    return [{**row, "occurrence_count": 0} for row in _grapheme_rows(group, forms, identity_text(q))]


def router(store: Store) -> APIRouter:
    """The character-layer routes, mounted beside the collection on the same service."""
    api = APIRouter()
    # Counting the ink walks every unit and checks whether its crop can be drawn, which is far too
    # much work per keystroke. The index is built once and reused until the store changes: an event
    # is appended by every correction, so the length of the journal is the stamp that says when the
    # counts a reader sees are out of date.
    recent: dict[str, tuple[tuple[int, int], Layers]] = {}
    index_lock = RLock()

    def stamp() -> tuple[int, int]:
        return len(store.events()), store.import_generation()

    def layer() -> Layers:
        with index_lock:
            key = str(store.directory)
            now = stamp()
            hit = recent.get(key)
            if hit and hit[0] == now:
                return hit[1]
            current = Layers(store)
            recent[key] = (now, current)
            return current

    # Startup pays for the vocabulary and occurrence index, before image requests compete with the
    # first keystroke. Subsequent queries read these small indexes rather than walking the dataset.
    layer()
    refs.graphemes()
    refs.search("トモ")

    def cached_layer() -> Layers | None:
        """Refresh an existing index after reviews or background source publication."""
        hit = recent.get(str(store.directory))
        return layer() if hit else None

    def known(code_point: str) -> Character:
        row = refs.character(code_point)
        if row is not None:
            return row
        # A character written with a mark, such as ツ + U+309A, has no row of its own in the table,
        # but once a unit records it the layer holds it, and it is browsed like any other.
        current = cached_layer()
        key = " ".join(point.upper() for point in code_point.split())
        if current is not None and key in current.per_character:
            text = refs.from_code_points(key.split())
            return Character(code_point=key, char=text, script=script_of_identity(text))
        raise HTTPException(404, f"{refs.normalise(code_point)} is not in the character table.")

    @api.get("/layers/summary")
    def summary() -> dict[str, Any]:
        """What the layer holds, in counts, so a page can say it without loading the table."""
        current = cached_layer()
        counts = current.per_character if current else {}
        characters = refs.characters()
        used = {code_point for code_point, count in counts.items() if count}
        return {
            "characters": len(characters),
            "graphemes": len(refs.graphemes()),
            "ligatures": len(refs.ligatures()),
            "kana": sum(1 for row in characters if str(row.script) in ("hiragana", "katakana", "hentaigana")),
            "han": sum(1 for row in characters if str(row.script) == "han"),
            # Counted only when the occurrence index is already in hand; otherwise the page reports
            # the layer alone rather than making every reader wait for a walk over the units.
            "occurrences": None if current is None else {
                "total": sum(counts.values()), "characters_used": len(used),
                "characters_unused": len(characters) - len(used)},
            "corpus": {"ready": corpus_source.ready()},
            "layers": [
                {"key": "grapheme", "label": "Grapheme", "states": "a curated family of written characters"},
                {"key": "character", "label": "Character", "states": "one written character, with its own code point"},
                {"key": "occurrence", "label": "Form", "states": "the exact ink in a source occurrence"},
            ],
        }

    @api.get("/layers/search")
    def find(
        q: str = "",
        expand: str = Query("none", pattern=r"^(none|grapheme|jibo)(,(grapheme|jibo))*$"),
        limit: Annotated[int, Query(ge=1, le=96)] = 24,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        """The characters a query names; exact unless the reader asks for the grapheme expansion."""
        answer = search(q, layer(), expand=expand)
        answer["results"] = answer["results"][offset:offset + limit]
        answer["available"] = len(answer["results"])
        return answer

    @api.get("/layers/suggest")
    def suggestions(
        q: str = "",
        limit: Annotated[int, Query(ge=1, le=48)] = 8,
    ) -> dict[str, Any]:
        """The candidate list the search box shows while a reader types.

        Exact search is a separate request on purpose: this list is for finding the character, and
        the occurrence gallery is exact once one is chosen. Selecting a row is not a widening of any
        search, it is a change of which character is being looked at.
        """
        return suggest(q, layer(), limit=limit)

    @api.get("/layers/characters/{code_point}")
    def character(
        code_point: str,
        expand: str = Query("none", pattern=r"^(none|grapheme|jibo)(,(grapheme|jibo))*$"),
        state: Literal["all", "pending", "checked", "flagged"] = "all",
        limit: Annotated[int, Query(ge=1, le=96)] = 24,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        """One character: its layers, the forms of its grapheme, and the ink that carries it."""
        row = known(code_point)
        return {"query": row.code_point, "expand": expand, **character_view(
            row, layer(), expand=expand, state=state, limit=limit, offset=offset)}

    @api.get("/layers/occurrences")
    def occurrences(
        code_point: str,
        expand: str = Query("none", pattern=r"^(none|grapheme|jibo)(,(grapheme|jibo))*$"),
        state: Literal["all", "pending", "checked", "flagged"] = "all",
        limit: Annotated[int, Query(ge=1, le=96)] = 48,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        """The ink of one character, paged: the tiles the collection draws, filtered by form."""
        row = known(code_point)
        current = layer()
        rows = current.rows(row.code_point, expand=expand, state=state)
        counts = current.counts(row.code_point, expand=expand)
        counts["filtered"] = len(rows)
        return {"query": row.code_point, "expand": expand, "counts": counts,
                "items": [current.item(unit, revision, form, exact)
                          for unit, revision, form, exact in rows[offset:offset + limit]]}

    @api.get("/layers/graphemes")
    def grapheme_list(
        q: str = "",
        group: Literal["all", "kana", "han"] = "all",
        forms: Literal["multiple", "all"] = "multiple",
        limit: Annotated[int, Query(ge=1, le=512)] = 120,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        """The shapes of the layer: one row per grapheme, with how many forms and occurrences it has.

        The spine of the gallery. A reader who does not know a code point starts from the shape they
        are looking at — ね — and gets the nine other forms of it. `forms=multiple` keeps the shapes
        that have more than one character, which is what a grapheme with forms means; `forms=all`
        adds characters that represent their own families.
        """
        counts = layer().per_character
        rows = [{**row, "occurrence_count": sum(counts.get(member["code_point"], 0) for member in row["members"])}
                for row in _grapheme_rows(group, forms, identity_text(q))]
        rows.sort(key=lambda item: (-item["occurrence_count"], item["code_point"]))
        return {"total": len(rows), "available": len(rows), "items": rows[offset:offset + limit]}

    @api.get("/layers/graphemes/{code_point}")
    def grapheme(code_point: str) -> dict[str, Any]:
        """One grapheme and every form of it, each with what the corpus wrote it as."""
        row = known(code_point)
        head = refs.character(refs.grapheme(row.code_point) or row.code_point) or row
        current = layer()
        counts = current.per_character
        forms = [refs.character(form) for form in _forms(head.code_point)]
        return {**_grapheme_head(head, counts),
                "characters": [_row(form, counts) for form in forms if form is not None]}

    @api.get("/layers/ligatures")
    def ligatures() -> dict[str, Any]:
        """Every character of the layer that is two kana set as one, with the ink that carries it.

        𪜈, ゟ, ヿ and the four digraphs of Unicode 18.0: characters a reader meets in a Meiji-period
        text and cannot look up in a modern dictionary, because the shape is two kana and the code
        point is one character.
        """
        counts = layer().per_character
        live, fault = corpus_source.safe(corpus_source.counts,
                                         [refs.to_char(code_point) for code_point in refs.ligatures()])
        live = live or {}
        del fault
        rows = []
        for code_point, ligature in refs.ligatures().items():
            row = refs.character(code_point)
            if row is None:
                continue
            rows.append({**_row(row, counts),
                         "components": _ligature_row(ligature)["components"],
                         "reading": ligature.reading, "kind": str(ligature.kind),
                         "evidence": ligature.evidence,
                         "candidates": candidate_summary(
                             row.char, live=live.get(row.char), local=counts.get(code_point, 0))})
        rows.sort(key=lambda item: (-item["occurrence_count"], item["code_point"]))
        return {"total": len(rows), "items": rows}

    @api.get("/layers/candidates")
    def candidates(
        code_point: str,
        limit: Annotated[int, Query(ge=1, le=200)] = 24,
        offset: Annotated[int, Query(ge=0)] = 0,
        scope: Literal["character", "grapheme"] = "character",
        visual_group: Annotated[str | None, Query(max_length=160)] = None,
    ) -> dict[str, Any]:
        """What the corpus index holds for one character: located glyphs, and the counts by kind.

        Reads the glyph reading and the cached character summary only. The text matches a corpus
        holds are counted here but not fetched — reading them walks every occurrence in every indexed
        transcription — and a reader who wants to see them asks `/layers/candidates/hits`.
        """
        row = known(code_point)
        current = layer()
        counts, _ = corpus_source.safe(corpus_source.counts, [row.char])
        summary = candidate_summary(row.char, live=(counts or {}).get(row.char),
                                    local=current.per_character.get(row.code_point, 0))
        payload, fault = corpus_source.safe(corpus_source.candidates, row.char, limit, offset,
                                            scope=scope, visual_group=visual_group)
        if fault == "error":
            # Not an empty corpus: the reader is told the index could not be read and can retry,
            # while the counts that are already known stay in the answer.
            raise HTTPException(502, "The corpus index could not be read. Retry, or open the record "
                                     "at its holder.")
        glyphs = (payload or {}).get("items", [])
        payload = payload or {}
        return {"code_point": row.code_point, **summary, "status": fault or summary["status"],
                "scope": scope, "visual_group": visual_group,
                "glyphs": payload.get("total", summary["glyphs"]),
                "total": payload.get("total", summary["glyphs"]),
                "family_total": payload.get("family_total"),
                "assigned_count": payload.get("assigned_count"),
                "unassigned_count": payload.get("unassigned_count"),
                "visual_analysis": visual_families.family_analysis(row.code_point),
                "retry": fault == "not-loaded", "available": len(glyphs),
                "glyph_items": glyphs, "items": glyphs}

    @api.get("/layers/visual-groups/samples/{identity:path}/image")
    def visual_sample(identity: str) -> FileResponse:
        path = visual_families.get_sample_image(identity)
        if path is None:
            raise HTTPException(404, "This visual sample is unavailable.")
        return FileResponse(path)

    @api.get("/layers/gallery")
    def gallery(
        limit: Annotated[int, Query(ge=1, le=60)] = 24,
        seed: Annotated[int, Query(ge=0, le=2**53)] = 0,
    ) -> dict[str, Any]:
        """A bounded corpus glyph sample for the unfiltered homepage, with its attribution.

        No character is named, so this reads the sample the corpus keeps rather than scanning: the
        homepage mixes these rows with the collection's own. A corpus that cannot answer says so and
        the page shows its own rows alone.
        """
        payload, fault = corpus_source.safe(corpus_source.gallery, limit, seed=seed)
        if payload is None:
            return {"status": fault or "not-loaded", "total": None, "items": [], "available": 0}
        items = payload["items"]
        return {"status": "ok", "total": payload["total"], "available": len(items), "items": items}

    @api.get("/layers/candidates/hits")
    def candidate_hits(
        code_point: str,
        limit: Annotated[int, Query(ge=1, le=100)] = 24,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        """The corpus's text matches for one character, paged, and only when asked for.

        A separate route because it is the expensive read: bounded by `limit`, and the counts stay on
        `/layers/candidates` so a gallery never pays for it.
        """
        row = known(code_point)
        payload, fault = corpus_source.safe(corpus_source.source_hits, row.char, limit, offset)
        if fault == "error":
            raise HTTPException(502, "The corpus index could not be read. Retry.")
        if payload is None:
            return {"code_point": row.code_point, "status": "not-loaded", "total": None,
                    "items": [], "available": 0}
        items = payload["items"]
        return {"code_point": row.code_point, "total": payload["total"],
                "total_kind": payload["total_kind"], "available": len(items),
                "items": items}

    @api.post("/layers/units/{unit_id}")
    def correct(unit_id: str, edit: LayerEdit) -> dict[str, Any]:
        """Correct the character, the reading or the crop of one occurrence — each in its own layer.

        The layers are separate events with separate evidence: `unicode` is the encoded written
        identity, `reading` is what the occurrence reads, `box` is the ink the crop is cut from, and
        `script` follows the character layer because the layer is its authority. A reading never
        rewrites a code point, and a code point never rewrites a reading a reviewer typed.

        The evidence carries the same shape the character editor writes (`kind: character-review`,
        `snapshot`, `correction.reading`, `correction.box`), so `/atlas/reviews` reports a correction
        from here exactly as it reports one from there, and adds `layer_correction` for the layer that
        changed.
        """
        records = store.unit_snapshot(unit_id)
        if not records or not records[0][0].active:
            raise HTTPException(404, "This occurrence is no longer available.")
        unit, current = records[0]

        # Idempotency is decided before anything is compared with the record as it stands now. A
        # client that retries a request it never saw the answer to sends the same id with the same
        # body, and that body names the revision it was written against; answering 409 because the
        # occurrence has moved on since would make a lost response unrecoverable, and the first
        # request's answer is still the answer. The same id with a *different* body is a client bug.
        previous = store.submission_results(edit.client_id, f"layer:{edit.id}:")
        if previous:
            evidence = next((json.loads(row["review"]["evidence"]) for row in previous
                             if row["field"] == "review" and row["review"]["evidence"]), {})
            if evidence.get("request") != edit.model_dump(mode="json"):
                raise StoreError("This submission id was already used for a different correction.")
            correction = evidence.get("layer_correction", {})
            return {"results": previous, "duplicate": True, "resolved": bool(evidence.get("resolved")),
                    "changed": correction.get("changed", []),
                    "layers": {key: value for key, value in correction.items() if key != "changed"}}

        if edit.revision != current:
            raise HTTPException(409, "This occurrence changed. Reload it before correcting it.")
        if not _showable(store, unit):
            raise BadRequest("This occurrence has no available crop to correct.")
        digest = _source_digest(store, unit)
        if edit.image_sha256 != digest:
            raise HTTPException(409, "The source image changed. Reload this occurrence.")

        if edit.box is not None:
            page = _page(store, unit.page_id) if unit.page_id else None
            if (not page or edit.box.w <= 0 or edit.box.h <= 0 or edit.box.x < 0 or edit.box.y < 0
                    or edit.box.x + edit.box.w > page.width or edit.box.y + edit.box.h > page.height):
                raise BadRequest("The crop must stay inside the source image.")

        before = {"code_point": stored_identity(unit), "character": written_identity(unit), "reading": label(unit),
                  "script": str(unit.script), "box": unit.box.model_dump() if unit.box else None}
        evidence_base = {"kind": "character-review", "request": edit.model_dump(mode="json"),
                         "issue": edit.issue, "note": edit.note, "before": before,
                         "snapshot": {"character": _item(store, unit, current), "image_sha256": digest},
                         "image_sha256": edit.image_sha256, "revision": current}
        requests: list[ReviewRequest] = []
        revision = edit.revision
        changed: list[str] = []
        identity: str | None = None
        reading: str | None = None

        if edit.box is not None and edit.box.model_dump() != before["box"]:
            requests.append(ReviewRequest(
                target_type="unit", target_id=unit_id, field="box", new=edit.box.model_dump(),
                base_revision=revision, client_id=edit.client_id, idempotency_key=f"layer:{edit.id}:box",
                evidence=json.dumps({**evidence_base, "layer": "crop", "from": before["box"],
                                     "to": edit.box.model_dump()}, ensure_ascii=False),
            ))
            revision += 1
            changed.append("crop")

        if edit.character is not None:
            identity = canonical_identity(edit.character)
            if not single_character(identity_text(identity)):
                raise BadRequest("A character correction is one character; a ligature is one code point too.")
            if identity != stored_identity(unit):
                requests.append(ReviewRequest(
                    target_type="unit", target_id=unit_id, field="unicode", new=identity,
                    base_revision=revision, client_id=edit.client_id,
                    idempotency_key=f"layer:{edit.id}:character",
                    evidence=json.dumps({**evidence_base, "layer": "character", "from": unit.unicode,
                                         "to": identity, "char": identity_text(identity),
                                         "jibo": refs.jibo_of(identity)},
                                        ensure_ascii=False),
                ))
                revision += 1
                changed.append("character")
                # The script of an occurrence is a fact of the character layer, so it is corrected by
                # correcting the character, not by typing a script into a unit.
                script = script_of_identity(identity_text(identity))
                if script != "unknown" and str(unit.script) != script:
                    requests.append(ReviewRequest(
                        target_type="unit", target_id=unit_id, field="script", new=str(script),
                        base_revision=revision, client_id=edit.client_id,
                        idempotency_key=f"layer:{edit.id}:script",
                        evidence=json.dumps({**evidence_base, "layer": "script", "from": str(unit.script),
                                             "to": str(script), "authority": "character layer"},
                                            ensure_ascii=False),
                    ))
                    revision += 1
                    changed.append("script")

        if edit.reading is not None:
            reading = identity_text(edit.reading)
            if not reading_is_allowed(reading, identity or stored_identity(unit)):
                raise BadRequest("A reading is one character, or the two a ligature reads as.")
            if reading != label(unit):
                requests.append(ReviewRequest(
                    target_type="unit", target_id=unit_id, field="reading", new=reading,
                    base_revision=revision, client_id=edit.client_id,
                    idempotency_key=f"layer:{edit.id}:reading",
                    evidence=json.dumps({**evidence_base, "layer": "reading", "from": unit.reading,
                                         "to": reading}, ensure_ascii=False),
                ))
                revision += 1
                changed.append("reading")

        if not requests:
            raise BadRequest("Nothing changed: the character, the reading and the crop already read that way.")

        # An explicit wrong-character correction *with a character that differs from the one on the
        # record* resolves the occurrence: the identity is now what the reviewer says it is, so the
        # record is checked rather than left flagged. Everything else stays disputed — a crop, joined
        # characters, a blank, a reading-only edit, `unclear` — and so does a wrong-character issue
        # whose character is the one already stored, which is a contradiction and not a correction.
        resolved = (edit.verdict == "wrong" and edit.issue == "character" and "character" in changed)

        written = identity if "character" in changed else stored_identity(unit)
        correction = {
            "code_point": written if written and len(identity_text(written)) == 1 else None,
            "character": identity_text(written) if written else None,
            "reading": reading if "reading" in changed else label(unit),
            "box": (edit.box.model_dump() if "crop" in changed else before["box"]),
            "jibo": refs.jibo_of(written),
            "script": str(refs.script_of(identity_text(written))) if written else before["script"],
        }
        requests.append(ReviewRequest(
            target_type="unit", target_id=unit_id, field="review",
            new="reviewed" if edit.verdict == "match" or resolved else "disputed",
            base_revision=revision, client_id=edit.client_id, idempotency_key=f"layer:{edit.id}:review",
            evidence=json.dumps({**evidence_base, "layer": "review", "verdict": edit.verdict,
                                 "resolved": resolved,
                                 "correction": {key: correction[key] for key in ("reading", "box")},
                                 "layer_correction": {"changed": changed, **correction}},
                                ensure_ascii=False),
        ))
        results = store.record_batch(requests)
        # The counts a reader sees next must be the counts after this correction, which the journal
        # stamp already says: nothing else to invalidate.

        return {"results": results, "changed": changed, "resolved": resolved, "layers": correction}

    return api
