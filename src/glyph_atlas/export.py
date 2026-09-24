"""Build a release directory: the records that may be redistributed, with their attribution.

A release is a filtered copy of one or more dataset directories, not a new dataset. Every input
record goes through three separate questions, because the rights of an annotation and of the page it
sits on are different rights:

- may the annotations themselves be redistributed under the dataset licence (they are CC BY-SA 4.0,
  and a record the build keeps states that);
- may the text of the document be redistributed;
- may the image of the document be redistributed.

A document whose text may go out but whose images may not keeps its records: the coordinates still
describe a rectangle on a page the user can reach at the holder's address, and the release carries
no crop for it. A document whose text may not go out is absent entirely, because the transcriber's
string is the record.

Referential closure is kept: a unit's line and page and document are in the release, a line is there
because a unit of it is, a group because every unit of it is. The derived normalisation columns are
computed here and nowhere else, from the policy in `data/vocab/normalisation-policies.yaml`, so the
record fields keep the values they were given and the policy that produced the derived columns is
recorded in `MANIFEST.json`.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import yaml
from pydantic import BaseModel

from . import reconcile, refs, registry, tables
from . import rights as rights_module
from .schema import Document, Group, Licence, Line, Page, PageText, ReviewState, Rights, Script, Unit

ROOT = Path(__file__).resolve().parents[2]
VOCAB = ROOT / "data" / "vocab"
SOURCES = ROOT / "data" / "sources"
POLICIES = VOCAB / "normalisation-policies.yaml"
TEMPLATE = ROOT / "docs" / "datasheet-template.md"
TARGET = Licence.CC_BY_SA_4
DEFAULT_REVIEW = (
    ReviewState.REVIEWED.value,
    ReviewState.DOUBLE_REVIEWED.value,
    ReviewState.ADJUDICATED.value,
)
WORK = ".release-work"
PLACEHOLDER = re.compile(r"\{\{([^{}]+)\}\}")
# The columns the units table carries before the derived ones are added, in the store's order.
DERIVED_KEYS = ("modern_kana", "shinji")
#: The scripts whose forms have a modern kana. A kanji's grapheme is its family's representative
#: rather than a reading — 國 and 国 are one family, and the shinji column is the one that says
#: so — so a han character is never rewritten by the modern_kana policy.
KANA_FORMS = frozenset({Script.HIRAGANA, Script.HENTAIGANA, Script.KATAKANA})


class ExportError(RuntimeError):
    """A release that cannot be built as asked."""


# The normalisation policy ----------------------------------------------------------------------


@dataclass
class Policy:
    """One named normalisation policy: the derived columns it adds and how each is computed."""

    name: str
    version: int
    description: str
    columns: list[dict[str, Any]] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.name} v{self.version}"

    @property
    def names(self) -> list[str]:
        return [str(column["column"]) for column in self.columns]


def policy(name: str = "export-v1", path: Path | None = None) -> Policy:
    """Read a normalisation policy; every column names the relation that computes it."""
    source = Path(path) if path is not None else POLICIES
    if not source.exists():
        raise ExportError(f"{source} is missing; it is a hand-written table")
    document = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    if name not in document:
        raise ExportError(f"unknown normalisation policy {name!r}; {source} has {sorted(document)}")
    raw = document[name]
    columns = []
    for column in raw.get("columns") or []:
        relation = str(column.get("relation", ""))
        if relation not in {"hentaigana-to-modern-kana", "kanji-to-shinji"}:
            raise ExportError(f"policy {name!r} names an unknown relation {relation!r}")
        columns.append({"column": str(column["column"]), "relation": relation})
    return Policy(
        name=name,
        version=int(raw.get("version", 1)),
        description=str(raw.get("description", "")).strip(),
        columns=columns,
    )


@dataclass
class Normaliser:
    """The tables a policy's columns are computed from, read once.

    The modern kana of a form is not read here: the character layer states it as the grapheme of
    the form, and `refs.grapheme` is the one place that answers which kana a form is a form of.
    """

    shinji: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls) -> Normaliser:
        """Each 旧字 to its 新字."""
        shinji: dict[str, str] = {}
        for row in refs._read_tsv("kanji-equivalents.tsv"):
            if row.get("kind") == "shinji-kyuji":
                shinji[row["a"]] = row["b"]
        return cls(shinji=shinji)

    def value(self, column: str, unit: Unit) -> str | None:
        """One derived cell, or None when the policy has nothing to say about this unit."""
        if column == "modern_kana":
            return self._modern_kana(unit)
        if column == "shinji":
            return self._shinji(unit)
        raise ExportError(f"no implementation for the derived column {column!r}")

    def _modern_kana(self, unit: Unit) -> str | None:
        """The modern kana a unit's form reads as, where the form is not already the modern kana.

        A unit whose code point is a hentaigana is written in the kana of its first 音価 - か for
        U+1B019 - and one whose code point is the alternate katakana of Unicode 18.0 is written as
        ネ. The grapheme is what says so: it is the modern kana a form is a form of, which the
        character layer states for every kana, so this asks it rather than keeping a second table.
        Only a kana form is rewritten: a kanji's grapheme names its old/new family, which is the
        shinji column's business, and a symbol answers as itself. A unit with several code points,
        as a kana with a combining voicing mark, is rewritten per code point and left alone where a
        code point has no grapheme. A unit that is already the modern kana of its grapheme gets
        nothing: the column says what the form reads as.
        """
        if not unit.unicode:
            return None
        points = [point.strip().upper() for point in unit.unicode.split() if point.strip()]
        if not points:
            return None
        changed = False
        words = []
        for point in points:
            char = refs.to_char(point)
            # U+306D is ね, the grapheme of the ね-forms; a code point the character layer does not
            # hold, or that is not a kana form, is written as itself.
            grapheme = refs.grapheme(point) if refs.script_of(char) in KANA_FORMS else None
            words.append(refs.to_char(grapheme) if grapheme else char)
            changed = changed or (grapheme is not None and grapheme != point)
        return "".join(words) if changed else None

    def _shinji(self, unit: Unit) -> str | None:
        """The 新字 of a 旧字 the record carries, where the equivalence table names one."""
        if not unit.unicode:
            return None
        points = [point.strip().upper() for point in unit.unicode.split() if point.strip()]
        changed = False
        words = []
        for point in points:
            char = refs.to_char(point)
            replacement = self.shinji.get(char)
            if replacement is None:
                words.append(char)
            else:
                words.append(replacement)
                changed = True
        return "".join(words) if changed else None


# The release -----------------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(tz=UTC)


def input_manifests(datasets: Sequence[Path]) -> list[dict[str, Any]]:
    """What each input dataset directory says about itself, for the release manifest."""
    found = []
    for directory in datasets:
        directory = Path(directory)
        path = directory / tables.MANIFEST_NAME
        entry: dict[str, Any] = {"directory": str(directory), "manifest": None}
        if path.is_file():
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
            except ValueError as exc:
                raise ExportError(f"{path} is not readable JSON: {exc}") from exc
            entry["manifest"] = str(path)
            entry["tables"] = manifest.get("tables", {})
            entry["command"] = manifest.get("command")
            entry["written_at"] = manifest.get("written_at")
            entry["writer"] = manifest.get("writer")
            entry["files"] = manifest.get("files", {})
        found.append(entry)
    return found


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def release(
    datasets: Sequence[Path],
    out: Path,
    *,
    licence: Licence | str = TARGET,
    review: Sequence[str] = DEFAULT_REVIEW,
    include_machine: bool = False,
    crops: bool = False,
    normalisation: str = "export-v1",
    command: str | None = None,
    version: str | None = None,
    limit: int | None = None,
    policy_path: Path | None = None,
) -> dict[str, int]:
    """Write a release directory and return its counts.

    `datasets` are the dataset directories to merge; `licence` is the licence the release claims,
    `review` the review states it admits (machine units join them under `include_machine`),
    `normalisation` the policy that adds the derived columns. `limit` caps the units of the release
    and is meant for a scratch build: the closure then holds over the capped set.
    """
    if not datasets:
        raise ExportError("a release needs at least one input dataset directory")
    target = Licence(licence)
    chosen = policy(normalisation, policy_path)
    normaliser = Normaliser.load()
    wanted_review = {str(state) for state in review}
    if include_machine:
        wanted_review.add(ReviewState.MACHINE.value)
    sources = [Path(directory) for directory in datasets]
    for directory in sources:
        if not (directory / tables.MANIFEST_NAME).is_file() and not (directory / "documents.parquet").is_file():
            raise ExportError(f"{directory} holds no dataset tables")
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    inputs = input_manifests(sources)
    counts = tables.Dataset(sources[0]).merge(
        [tables.Dataset(directory) for directory in sources[1:]], out, command=command
    )
    _apply_filters(
        out,
        target=target,
        review=wanted_review,
        normaliser=normaliser,
        columns=chosen.names,
        limit=limit,
    )
    # The filtered tables are read into memory and the working directory goes before the merge, so
    # the merge cannot see them twice: the release directory then holds exactly what the filters
    # kept, in the layout any dataset directory has.
    work = out / WORK
    kept = {name: tables.read(path, tables.TABLES[name]) for name, path in tables.Dataset(work).tables.items() if path}
    derived = _derived_values(tables.Dataset(work), chosen.names)
    shutil.rmtree(work, ignore_errors=True)
    for name in tables.TABLES:
        tables._clear_table(out, name)
    for name, records in kept.items():
        if records:
            tables.write(out / f"{name}.parquet", records, tables.TABLES[name])
    _write_derived(out / "units.parquet", chosen.names, derived)
    errors = tables.Dataset(out).validate()
    if errors:
        raise ExportError(f"the release is not referentially closed: {errors[0]}")

    if crops:
        _write_crops(out, crops=crops)
    counts = {name: len(records) for name, records in kept.items()}
    counts["crops"] = _crop_count(out)
    written = _write_documents(
        out,
        inputs=inputs,
        policy=chosen,
        normalisation=chosen.label,
        filters=_filters(target, wanted_review, crops, limit),
        command=command,
        version=version,
        counts=counts,
    )
    counts["files"] = written
    return counts


def _derived_values(dataset: tables.Dataset, columns: Sequence[str]) -> dict[str, dict[str, Any]]:
    """The policy's cells of one dataset's units, by unit id."""
    if not columns or dataset.tables["units"] is None:
        return {}
    rows = pq.read_table(dataset.tables["units"], columns=["id", *columns]).to_pylist()
    return {column: {row["id"]: row[column] for row in rows} for column in columns}


def _write_derived(path: Path, columns: Sequence[str], values: dict[str, dict[str, Any]]) -> None:
    """Put the policy's columns back on a units table the store wrote, as its last columns.

    The record model is `docs/schema.md`'s and has no derived field, so a table written through the
    store cannot carry one; the columns are appended to the file the store wrote instead.
    """
    if not columns or not values or not path.is_file():
        return
    table = pq.read_table(path)
    ids = table.column("id").to_pylist()
    for column in columns:
        table = table.append_column(
            column, pa.array([values.get(column, {}).get(unit_id) for unit_id in ids], type=pa.string())
        )
    pq.write_table(table, path)


def _filters(target: Licence, review: set[str], crops: bool, limit: int | None) -> dict[str, Any]:
    return {
        "licence": target.value,
        "review": sorted(review),
        "crops": bool(crops),
        "limit": limit,
    }


def _apply_filters(
    out: Path,
    *,
    target: Licence,
    review: set[str],
    normaliser: Normaliser,
    columns: Sequence[str],
    limit: int | None,
) -> dict[str, int]:
    """Filter the merged tables in place, keeping the closure, and add the derived columns."""
    dataset = tables.Dataset(out)
    documents = dataset.read("documents")
    kept_documents = [document for document in documents if _document_ok(document, target)]
    if not kept_documents:
        raise ExportError(
            f"no document of {out} may be redistributed under {target.value}; "
            "the release would be empty"
        )
    kept_document_ids = {document.id for document in kept_documents}
    documents = kept_documents
    pages = (
        [page for page in dataset.read("pages") if page.document_id in kept_document_ids]
        if dataset.tables["pages"] is not None
        else []
    )
    kept_page_ids = {page.id for page in pages}
    # Every line any unit names, admitted or not. A line here waits for a unit of it to be admitted;
    # a line that is nowhere in this set has no unit at all, and is a record of the page on its own.
    # A dataset that mixes resolved and unresolved pages — the Ainu records, where an alignment has
    # run over 53 of 658 — must keep the unresolved ones, so this is decided per line rather than by
    # whether the dataset holds a units table at all.
    lines_with_units = {
        str(unit.line_id)
        for batch in dataset.scan("units", columns=["line_id"]) if dataset.tables["units"] is not None
        for unit in batch
        if unit.line_id
    } if dataset.tables["units"] is not None else set()
    # The lines that no unit names, which are records of their pages on their own.
    all_line_ids = {
        str(line.id)
        for batch in dataset.scan("lines", columns=["id"]) if dataset.tables["lines"] is not None
        for line in batch
    } if dataset.tables["lines"] is not None else set()
    lines_without_units = all_line_ids - lines_with_units
    groups_all = dataset.read("groups") if dataset.tables["groups"] is not None else []
    page_texts_all = dataset.read("page_texts") if dataset.tables["page_texts"] is not None else []
    unit_rows: list[dict[str, Any]] = []
    units_seen = 0
    # A dataset may hold lines and text without units, as the platform transcriptions do before an
    # alignment has run over their pages; such a release carries the lines and no unit rows.
    for batch in dataset.scan("units") if dataset.tables["units"] is not None else ():
        for unit in batch:
            # A unit is a record of its document; the page is where its rectangle points, and a
            # unit without a page, as a standalone crop, still belongs to its document.
            if unit.document_id is not None and unit.document_id not in kept_document_ids:
                continue
            if unit.page_id is not None and unit.page_id not in kept_page_ids:
                continue
            if str(unit.review) not in review:
                continue
            if not unit.active:
                # A unit a later run retired is not a record of the page any more: withdrawing a
                # derived line box retires the machine units that were placed in it, and a release
                # must not resurrect them.
                continue
            units_seen += 1
            if limit is not None and units_seen > limit:
                continue
            unit_rows.append(_unit_row(unit, normaliser, columns))
    dataset_holds_units = dataset.tables["units"] is not None
    if not unit_rows and not lines_without_units:
        # A dataset whose units are all filtered out can still have records to publish — the lines
        # that carry no unit at all — and that is the case the Ainu records reach when the review
        # filter admits nothing. The failure is only when the release would hold no record of any
        # kind: no unit and no unit-less line.
        raise ExportError(
            "no unit of this dataset is admitted by the filters and it holds no line without one, "
            "so the release would hold no record; widen --review, ask for machine units, or check "
            "the document rights"
        )
    kept_units = {str(row["id"]) for row in unit_rows}
    kept_lines = {str(row["line_id"]) for row in unit_rows if row.get("line_id")}
    # A dataset with no units at all, such as a platform transcription before an alignment has run
    # over its pages, releases its lines whole: there are no units to say which line is used, and a
    # line without a unit is still a record of the page it sits on. A line that carries no units
    # anywhere still has no unit to be admitted by, so it is kept too — that is what keeps the
    # unresolved pages of a mixed dataset in the release. A line that was aligned keeps only the
    # units the filters admitted, so the release stays the closure of those units.
    line_rows = (
        [
            line
            for batch in dataset.scan("lines")
            for line in batch
            if line.page_id in kept_page_ids
            and (line.id in kept_lines or line.id not in lines_with_units)
        ]
        if dataset.tables["lines"] is not None
        else []
    )
    # A page is in the release because a record of it is: a unit on it, a line, or a page text. A
    # transcription-only page has no line to admit it — the Ainu records have 138 pages whose only
    # record is a `page_texts` row — and a row of that table is as much a record of its page as a
    # unit-less line is. Whether the page may be redistributed is a separate question, asked per
    # document above, so this is where the record's own existence decides and nothing else.
    used_pages = (
        {str(row["page_id"]) for row in unit_rows if row.get("page_id")}
        | {line.page_id for line in line_rows}
        | {str(row.page_id) for row in page_texts_all if row.page_id in kept_page_ids}
    )
    groups = [group for group in groups_all if group.unit_ids and set(group.unit_ids) <= kept_units]
    page_texts = [row for row in page_texts_all if row.page_id in used_pages]
    pages = [page for page in pages if page.id in used_pages]
    # Every read of the merged directory is done, so its unfiltered tables are dropped here. The
    # release is what the filtered tables hold: an unfiltered table left in `out` would be counted
    # as if the filters admitted it, and the layout of the release would be whatever the inputs had.
    for name in tables.TABLES:
        tables._clear_table(out, name)
    work = out / WORK
    work.mkdir(parents=True, exist_ok=True)
    tables.write(work / "documents.parquet", documents, Document)
    if pages:
        tables.write(work / "pages.parquet", pages, Page)
    if line_rows:
        tables.write(work / "lines.parquet", line_rows, Line)
    if dataset_holds_units:
        _write_unit_rows(work / "units.parquet", unit_rows, columns)
    tables.write(work / "groups.parquet", groups, Group)
    tables.write(work / "page_texts.parquet", page_texts, PageText)
    return {
        "units": len(unit_rows),
        "lines": len(line_rows),
        "pages": len(pages),
        "documents": len(documents),
    }


def _document_ok(document: Document, target: Licence) -> bool:
    """Whether the document's text may be redistributed into a release licensed as `target`.

    The transcription and the record metadata are what a unit is; the image is addressed rather than
    copied, so a document whose images may not be redistributed still keeps its records, with the
    rectangle on the holder's page and no crop. `images_ok` answers the image question separately.
    """
    return rights_ok(document.text_rights, target)


def rights_ok(rights: Rights | None, target: Licence) -> bool:
    """Whether material with these rights may go into a release licensed as `target`."""
    return rights_module.eligible(rights, target) if rights is not None else False


def images_ok(document: Document, target: Licence) -> bool:
    """Whether crops of this document's pages may be redistributed."""
    return rights_ok(document.image_rights, target)


def _unit_row(unit: Unit, normaliser: Normaliser, columns: Sequence[str]) -> dict[str, Any]:
    """One unit as a table row, with the policy's derived columns added."""
    row = unit.model_dump(mode="json")
    for column in columns:
        row[column] = normaliser.value(column, unit)
    return row


def _unit_fields() -> list[tuple[str, pa.DataType, str]]:
    """Every field of `Unit`, with its column type and the kind of value that column takes.

    `model_dump(mode="json")` gives text for a date, a datetime and a nested object, while the
    column wants a date, a timestamp and a struct; the kind says which conversion to do.
    """
    fields: list[tuple[str, pa.DataType, str]] = []
    for name, definition in Unit.model_fields.items():
        annotation = tables._unwrap(definition.annotation)
        data_type = tables.schema_for(Unit).field(name).type
        if _numeric(annotation):
            kind = "number"
        elif isinstance(annotation, type) and issubclass(annotation, BaseModel):
            kind = "struct"
        elif annotation is date:
            kind = "date"
        elif annotation is datetime:
            kind = "datetime"
        elif tables._codec(definition.annotation) is not None:
            kind = "json"
        else:
            kind = "text"
        fields.append((name, data_type, kind))
    return fields


def _numeric(annotation: Any) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, (int, float, bool))


def _column(value: Any, kind: str) -> Any:
    """One cell as its column takes it, or None when the record has no value there."""
    if value is None:
        return None
    if kind == "date":
        return tables._as_date(value)
    if kind == "datetime":
        return tables._as_datetime(value)
    if kind == "struct":
        return value if isinstance(value, dict) else json.loads(value)
    if kind == "json":
        return value if isinstance(value, str) else tables._as_json(value)
    return value


def _unit_schema(columns: Sequence[str]) -> pa.Schema:
    """The units schema of the store with the derived columns appended as strings."""
    schema = tables.schema_for(Unit)
    for column in columns:
        if column in schema.names:
            raise ExportError(f"the derived column {column!r} is already a field of Unit")
        schema = schema.append(pa.field(column, pa.string()))
    return schema


def _write_unit_rows(path: Path, rows: Sequence[dict[str, Any]], columns: Sequence[str]) -> None:
    """Write the units table with its derived columns, in the store's own layout.

    The table is written here rather than through `tables.write` because a derived column is not a
    field of `Unit`: the record model stays as `docs/schema.md` describes it, and the policy's
    columns are the last columns of the file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    _unit_schema(columns)  # the derived names are checked against the model's fields
    fields = _unit_fields()
    arrays = {
        name: pa.array([_column(row.get(name), kind) for row in rows], type=data_type)
        for name, data_type, kind in fields
    }
    table = pa.table(arrays, schema=tables.schema_for(Unit))
    for column in columns:
        table = table.append_column(column, pa.array([row.get(column) for row in rows], type=pa.string()))
    pq.write_table(table, path)


# Crops -----------------------------------------------------------------------------------------


def _crop_count(out: Path) -> int:
    """How many crops the release directory holds, counted where they are."""
    root = Path(out) / "crops"
    if not root.is_dir():
        return 0
    return sum(1 for path in root.rglob("*.jpg") if path.is_file())


def _write_crops(out: Path, *, crops: bool) -> list[Path]:
    """Write one JPEG per unit whose document allows redistribution, into `crops/<bucket>/`."""

    dataset = tables.Dataset(out)
    documents = {document.id: document for document in dataset.read("documents")}
    pages = {page.id: page for page in dataset.read("pages")} if dataset.tables["pages"] else {}
    written: list[Path] = []
    seen: set[str] = set()
    for batch in dataset.scan("units"):
        for unit in batch:
            if unit.id in seen:
                continue
            document = documents.get(unit.document_id or "")
            if document is None or not images_ok(document, TARGET):
                continue
            target = out / "crops" / unit.id[:2] / f"{unit.id.replace(':', '_')}.jpg"
            if target.exists():
                continue
            source = _crop_source(unit, pages)
            if source is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                source.convert("RGB").save(target, format="JPEG", quality=95)
            except OSError:
                continue
            seen.add(unit.id)
            written.append(target)
    return written


def _crop_source(unit: Unit, pages: dict[str, Page]) -> Any:
    """The image of one unit: a cached page cut to its box, or the crop a source ships."""
    from . import images

    if unit.crop:
        path = _standalone_crop(unit)
        if path is not None:
            from PIL import Image

            return Image.open(path)
        return None
    page = pages.get(unit.page_id or "")
    if page is None or unit.box is None:
        return None
    path = images.path_for(page.image)
    if path is None:
        return None
    from PIL import Image

    with Image.open(path) as image:
        box = unit.box
        left, top = max(0, box.x), max(0, box.y)
        right, bottom = min(image.width, box.x + box.w), min(image.height, box.y + box.h)
        if right <= left or bottom <= top:
            return None
        return image.crop((left, top, right, bottom)).copy()


def _standalone_crop(unit: Unit) -> Path | None:
    """The file of a unit that is a standalone crop: a file of the archive, or a cached copy.

    A unit whose `crop` names a member of an archive (the HI Lab form `all.zip!all/characters/...`)
    is found either in the directory the importer extracted it to or in a cache of that member's
    name; a unit whose `crop` is a URL is looked up in the image cache. A crop that is nowhere on
    disk is skipped rather than fetched: the export command does not reach the network.
    """
    from . import images

    _, _, member = (unit.crop or "").partition("!")
    if member:
        relative = member.split("all/characters/", 1)[-1]
        for base in (ROOT / "cache" / "hilab", ROOT / "cache" / "crops", ROOT / "work" / "hilab" / "crops"):
            for candidate in (base / member, base / relative, base / Path(member).name):
                if candidate.is_file():
                    return candidate
        return None
    cached = images.path_for(unit.crop or "") if unit.crop else None
    return cached if cached is not None and cached.is_file() else None


# The release documents -------------------------------------------------------------------------


def _write_documents(
    out: Path,
    *,
    inputs: list[dict[str, Any]],
    policy: Policy,
    normalisation: str,
    filters: dict[str, Any],
    command: str | None,
    version: str | None,
    counts: dict[str, int],
) -> int:
    """Write the release's own files and return how many were written."""
    figures = figures_of(out, policy=policy, filters=filters, command=command, version=version, inputs=inputs)
    written = 0
    (out / "COUNTS.md").write_text(counts_markdown(figures), encoding="utf-8")
    written += 1
    (out / "ATTRIBUTION.md").write_text(reconcile.attribution(out), encoding="utf-8")
    written += 1
    (out / "datasheet.md").write_text(datasheet(figures), encoding="utf-8")
    written += 1
    (out / "README.md").write_text(dataset_card(out, figures), encoding="utf-8")
    written += 1
    (out / "zenodo.json").write_text(
        json.dumps(zenodo_metadata(figures), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    written += 1
    _extend_manifest(
        out,
        inputs=inputs,
        policy=policy,
        filters=filters,
        command=command,
        version=version,
        counts=counts,
    )
    written += 1
    (out / "CHECKSUMS.txt").write_text(checksums(out), encoding="utf-8")
    written += 1
    return written


@dataclass
class Figures:
    """Every number and string the release documents need, computed once."""

    out: Path
    version: str
    date: str
    command: str
    normalisation: str
    filters: dict[str, Any]
    inputs: list[dict[str, Any]]
    counts: dict[str, int]
    units_by_source: Counter[str]
    units_by_method: Counter[str]
    units_by_review: Counter[str]
    units_by_script: Counter[str]
    units_by_classification: Counter[str]
    units_by_label_coverage: Counter[str]
    units_by_image_licence: Counter[str]
    units_by_text_licence: Counter[str]
    units_by_split: Counter[str]
    lines_by_split: Counter[str]
    documents_by_production: Counter[str]
    documents_by_genre: Counter[str]
    documents_with_dating: int
    documents_without_dating: int
    crops_by_bucket: Counter[str]
    sources: list[dict[str, Any]]


def figures_of(
    out: Path,
    *,
    policy: Policy,
    filters: dict[str, Any],
    command: str | None,
    version: str | None,
    inputs: list[dict[str, Any]],
) -> Figures:
    """Measure the release directory: every count the documents quote comes from here."""
    dataset = tables.Dataset(out)
    documents = dataset.read("documents")
    licences = {document.id: document for document in documents}
    counts = {name: 0 for name in tables.TABLES}
    for name in tables.TABLES:
        path = dataset.tables[name]
        if path is None:
            continue
        files = sorted(path.glob("*.parquet")) if path.is_dir() else [path]
        counts[name] = sum(pq.ParquetFile(file).metadata.num_rows for file in files)
    units_by_source: Counter[str] = Counter()
    units_by_method: Counter[str] = Counter()
    units_by_review: Counter[str] = Counter()
    units_by_script: Counter[str] = Counter()
    units_by_classification: Counter[str] = Counter()
    units_by_label_coverage: Counter[str] = Counter()
    units_by_image: Counter[str] = Counter()
    units_by_text: Counter[str] = Counter()
    units_by_split: Counter[str] = Counter()
    if dataset.tables["units"] is not None:
        for batch in dataset.scan("units"):
            for unit in batch:
                units_by_source[str((unit.upstream or {}).get("source", "(none)"))] += 1
                units_by_method[str(unit.method)] += 1
                units_by_review[str(unit.review)] += 1
                units_by_script[str(unit.script)] += 1
                units_by_classification[str(unit.classification)] += 1
                units_by_label_coverage["reading" if unit.reading else "no reading"] += 1
                units_by_label_coverage["unicode" if unit.unicode else "no unicode"] += 1
                units_by_label_coverage["jibo" if refs.jibo_of(unit.unicode) else "no jibo"] += 1
                units_by_label_coverage["variants" if unit.variants else "no variants"] += 1
                document = licences.get(unit.document_id or "")
                units_by_image[_licence_of(document.image_rights if document else None)] += 1
                units_by_text[_licence_of(document.text_rights if document else None)] += 1
                units_by_split[str((unit.meta or {}).get("split", "(none)"))] += 1
    lines_by_split: Counter[str] = Counter()
    if dataset.tables["lines"] is not None:
        for batch in dataset.scan("lines"):
            for line in batch:
                lines_by_split[str((line.meta or {}).get("split", "(none)"))] += 1
    crops_by_bucket: Counter[str] = Counter()
    crop_root = out / "crops"
    if crop_root.is_dir():
        for bucket in sorted(crop_root.iterdir()):
            if bucket.is_dir():
                crops_by_bucket[bucket.name] = sum(1 for _ in bucket.glob("*.jpg"))
    counts["crops"] = sum(crops_by_bucket.values())
    known = {source.id: source for source in registry.load(SOURCES)}
    imported = reconcile._imported_source(out)
    present = reconcile.sources_present(documents, known, imported)
    return Figures(
        out=out,
        version=version or out.name,
        date=_now().date().isoformat(),
        command=command or "(not recorded)",
        normalisation=policy.label,
        filters=filters,
        inputs=inputs,
        counts=counts,
        units_by_source=units_by_source,
        units_by_method=units_by_method,
        units_by_review=units_by_review,
        units_by_script=units_by_script,
        units_by_classification=units_by_classification,
        units_by_label_coverage=units_by_label_coverage,
        units_by_image_licence=units_by_image,
        units_by_text_licence=units_by_text,
        units_by_split=units_by_split,
        lines_by_split=lines_by_split,
        documents_by_production=Counter(str(document.production) for document in documents),
        documents_by_genre=Counter(genre for document in documents for genre in document.genre or ["(none)"]),
        documents_with_dating=sum(1 for document in documents if document.dating),
        documents_without_dating=sum(1 for document in documents if not document.dating),
        crops_by_bucket=crops_by_bucket,
        sources=[_source_entry(source_id, known[source_id]) for source_id in present],
    )


def _licence_of(rights: Rights | None) -> str:
    return rights.licence.value if rights is not None else "(none)"


def _source_entry(source_id: str, source: Any) -> dict[str, Any]:
    """One registry source with the pin its file records."""
    raw = _raw_source(source_id)
    pin = raw.get("revision") or raw.get("commit") or raw.get("sha256")
    if pin is None:
        access = raw.get("access") or {}
        pin = access.get("zip_sha256") or access.get("sha256") or raw.get("version")
    return {
        "id": source.id,
        "name": source.name,
        "publisher": source.publisher,
        "version": source.version or "",
        "released": source.released.isoformat() if source.released else "",
        "licence": source.licence.value,
        "doi": source.doi or "",
        "pin": str(pin or ""),
        "attribution": source.attribution,
        "url": source.url,
        "counts": raw.get("counts") or {},
    }


def _raw_source(source_id: str) -> dict[str, Any]:
    path = SOURCES / f"{source_id}.yaml"
    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _table(rows: Sequence[Sequence[Any]], header: Sequence[str]) -> list[str]:
    lines = ["| " + " | ".join(str(cell) for cell in header) + " |"]
    lines.append("| " + " | ".join("---" for _ in header) + " |")
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return lines


def _counter_cells(counter: Counter[str]) -> str:
    if not counter:
        return "(none)"
    return ", ".join(f"{name} {count}" for name, count in sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def counts_markdown(figures: Figures) -> str:
    """`COUNTS.md`: the release by provenance and by source."""
    counts = dict(figures.counts)
    # A release directory counts its own tables; the crops are counted where they are, so a figures
    # record built by reading a finished release takes them from the crop buckets.
    counts.setdefault("crops", sum(figures.crops_by_bucket.values()))
    lines = [
        "# Counts",
        "",
        f"Release {figures.version}, built {figures.date}. Command: `{figures.command}`.",
        (
            f"Filters: licence {figures.filters['licence']}, "
            f"review {', '.join(figures.filters['review'])}, "
            f"crops {'yes' if figures.filters['crops'] else 'no'}, limit {figures.filters['limit']}."
        ),
        f"Normalisation policy {figures.normalisation}.",
        "",
        "## Tables",
        "",
    ]
    lines += _table([[name, counts.get(name, 0)] for name in tables.TABLES], ["Table", "Rows"])
    lines += [
        "",
        "## Units by provenance",
        "",
        "### By `method`",
        "",
        *_table([[name, value] for name, value in figures.units_by_method.most_common()], ["method", "units"]),
        "",
        "### By `review`",
        "",
        *_table([[name, value] for name, value in figures.units_by_review.most_common()], ["review", "units"]),
        "",
        "### By upstream source",
        "",
        *_table(
            [[name, value] for name, value in figures.units_by_source.most_common()],
            ["source", "units"],
        ),
        "",
        "### By script",
        "",
        *_table([[name, value] for name, value in figures.units_by_script.most_common()], ["script", "units"]),
        "",
        "### By classification",
        "",
        *_table(
            [[name, value] for name, value in figures.units_by_classification.most_common()],
            ["classification", "units"],
        ),
        "",
        "### Label coverage",
        "",
        *_table(
            [[name, value] for name, value in sorted(figures.units_by_label_coverage.items())],
            ["field", "units"],
        ),
        "",
        "## Rights carried on a record",
        "",
        "Units by the image licence of their document:",
        "",
        *_table(
            [[name, value] for name, value in figures.units_by_image_licence.most_common()],
            ["image licence", "units"],
        ),
        "",
        "Units by the text licence of their document:",
        "",
        *_table(
            [[name, value] for name, value in figures.units_by_text_licence.most_common()],
            ["text licence", "units"],
        ),
        "",
        "## Documents",
        "",
        "By production type:",
        "",
        *_table(
            [[name, value] for name, value in figures.documents_by_production.most_common()],
            ["production", "documents"],
        ),
        "",
        (
            f"Dating: {figures.documents_with_dating} documents carry a date, "
            f"{figures.documents_without_dating} do not."
        ),
        "",
        "By genre:",
        "",
        *_table(
            [[name, value] for name, value in figures.documents_by_genre.most_common()],
            ["genre", "documents"],
        ),
        "",
        "## Splits",
        "",
        "Units by evaluation split: " + _counter_cells(figures.units_by_split) + ".",
        "",
        "Lines by evaluation split: " + _counter_cells(figures.lines_by_split) + ".",
        "",
        "## Crops",
        "",
        f"{sum(figures.crops_by_bucket.values())} crops in {len(figures.crops_by_bucket)} buckets."
        if figures.crops_by_bucket
        else "No crops are materialised in this release.",
        "",
    ]
    return "\n".join(lines).rstrip("\n") + "\n"


APPENDIX = "## Placeholders"
# The first section of the filled datasheet: everything before it is the template's own explanation.
FIRST_SECTION = "## Motivation"


def datasheet(figures: Figures, template: Path | None = None) -> str:
    """Fill the datasheet template; an unfilled placeholder fails the build.

    The template's opening paragraph, which explains the placeholders to whoever edits it, and its
    appendix, which lists them, are scaffolding for the release: the datasheet itself opens with the
    release it describes and stops after Maintenance.
    """
    source = Path(template) if template is not None else TEMPLATE
    text = source.read_text(encoding="utf-8")
    filled = {key: value for key, value in _placeholders(figures).items()}
    missing = sorted({match.group(1).strip() for match in PLACEHOLDER.finditer(text)} - set(filled))
    if missing:
        raise ExportError(f"{source} needs placeholders this export cannot fill: {', '.join(missing)}")
    filled_text = PLACEHOLDER.sub(lambda match: filled[match.group(1).strip()], text)
    body = filled_text.split(APPENDIX, 1)[0]
    if FIRST_SECTION not in body:
        raise ExportError(f"{source} has no {FIRST_SECTION} section")
    return (
        "# Datasheet\n\n"
        f"Release {figures.version} of Glyph Atlas, built {figures.date} with "
        f"`{figures.command}`. Every number below is also in `COUNTS.md`, and `CHECKSUMS.txt` and "
        "`MANIFEST.json` describe the files it counts. The sections follow the datasheet for "
        "datasets.\n\n"
        + FIRST_SECTION
        + "\n\n"
        + body.split(FIRST_SECTION, 1)[1].lstrip("\n")
    )


def _placeholders(figures: Figures) -> dict[str, str]:
    counts = figures.counts
    inputs = figures.inputs
    return {
        "counts.units": str(counts.get("units", 0)),
        "counts.lines": str(counts.get("lines", 0)),
        "counts.pages": str(counts.get("pages", 0)),
        "counts.documents": str(counts.get("documents", 0)),
        "counts.units_by_source": _counter_cells(figures.units_by_source),
        "counts.units_by_script": _counter_cells(figures.units_by_script),
        "counts.units_by_classification": _counter_cells(figures.units_by_classification),
        "counts.units_by_label_coverage": _counter_cells(figures.units_by_label_coverage),
        "counts.documents_by_metadata": (
            "production " + _counter_cells(figures.documents_by_production)
            + "; genre " + _counter_cells(figures.documents_by_genre)
            + f"; dating {figures.documents_with_dating} with a date, "
            f"{figures.documents_without_dating} without"
        ),
        "counts.units_by_rights": (
            "image " + _counter_cells(figures.units_by_image_licence)
            + "; text " + _counter_cells(figures.units_by_text_licence)
        ),
        "counts.splits": (
            "units " + _counter_cells(figures.units_by_split)
            + "; lines " + _counter_cells(figures.lines_by_split)
        ),
        "counts.units_by_review": _counter_cells(figures.units_by_review),
        "counts.units_by_method": _counter_cells(figures.units_by_method),
        "counts.crops": (
            _counter_cells(figures.crops_by_bucket) if figures.crops_by_bucket else "no crops"
        ),
        "release.version": figures.version,
        "release.date": figures.date,
        "release.command": figures.command,
        "release.filters": (
            f"licence {figures.filters['licence']}; review {', '.join(figures.filters['review'])}; "
            f"crops {'yes' if figures.filters['crops'] else 'no'}; limit {figures.filters['limit']}"
        ),
        "release.normalisation_policy": figures.normalisation,
        "release.inputs": _inputs_cell(inputs),
        "sources.table": _sources_table(figures.sources),
        "sources.licences": _licences_cell(figures),
    }


def _inputs_cell(inputs: Sequence[dict[str, Any]]) -> str:
    if not inputs:
        return "no input dataset records its manifest"
    parts = []
    for entry in inputs:
        tables_cell = ", ".join(f"{name} {count}" for name, count in sorted((entry.get("tables") or {}).items()))
        files = entry.get("files") or {}
        parts.append(
            f"`{entry['directory']}` ({tables_cell or 'no manifest'}; {len(files)} files, checksums in "
            f"`{entry.get('manifest') or 'no manifest'}`)"
        )
    return "; ".join(parts)


def _sources_table(sources: Sequence[dict[str, Any]]) -> str:
    if not sources:
        return "The records name no source of `data/sources/`."
    rows = [
        [
            f"`{source['id']}`",
            source["name"],
            source["publisher"],
            source["version"] or "(none)",
            source["released"] or "(none)",
            source["licence"],
            source["doi"] or "(none)",
            source["pin"] or "(none)",
            source["attribution"],
        ]
        for source in sources
    ]
    return "\n".join(
        _table(rows, ["id", "name", "publisher", "version", "released", "licence", "DOI", "pin", "attribution"])
    )


def _licences_cell(figures: Figures) -> str:
    parts = []
    for name, count in figures.units_by_image_licence.most_common():
        entry = reconcile.vocabulary_index().get(name)
        url = entry["evidence"] if entry and entry.get("evidence") else "(no URL)"
        parts.append(f"images {name} ({count} units) — {url}")
    for name, count in figures.units_by_text_licence.most_common():
        entry = reconcile.vocabulary_index().get(name)
        url = entry["evidence"] if entry and entry.get("evidence") else "(no URL)"
        parts.append(f"text {name} ({count} units) — {url}")
    return "; ".join(parts)


def checksums(out: Path) -> str:
    """`CHECKSUMS.txt`: the sha256 of every file of the release but this one and the manifest."""
    out = Path(out)
    skip = {"CHECKSUMS.txt", "MANIFEST.json"}
    lines = []
    for path in sorted(out.rglob("*")):
        if not path.is_file() or path.name in skip:
            continue
        lines.append(f"{_sha256(path)}  {path.relative_to(out)}")
    return "\n".join(lines) + "\n"


def _extend_manifest(
    out: Path,
    *,
    inputs: list[dict[str, Any]],
    policy: Policy,
    filters: dict[str, Any],
    command: str | None,
    version: str | None,
    counts: dict[str, int],
) -> None:
    """Add what the release documents need to the manifest the merge wrote."""
    path = out / tables.MANIFEST_NAME
    manifest = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    manifest["release"] = {
        "version": version or out.name,
        "built": _now().isoformat(timespec="seconds").replace("+00:00", "Z"),
        "command": command,
        "filters": filters,
    }
    manifest["normalisation"] = {
        "policy": policy.name,
        "version": policy.version,
        "description": policy.description,
        "columns": policy.names,
    }
    manifest["inputs"] = [
        {
            "directory": entry["directory"],
            "manifest": entry.get("manifest"),
            "command": entry.get("command"),
            "writer": entry.get("writer"),
            "written_at": entry.get("written_at"),
            "tables": entry.get("tables", {}),
            "files": entry.get("files", {}),
        }
        for entry in inputs
    ]
    manifest["tables"] = counts
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def dataset_card(out: Path, figures: Figures) -> str:
    """The Hugging Face dataset card, from the same numbers as `COUNTS.md`."""
    counts = figures.counts
    return "\n".join(
        [
            "---",
            "license: cc-by-sa-4.0",
            "language:",
            "- ja",
            "task_categories:",
            "- image-to-text",
            "- object-detection",
            "pretty_name: Glyph Atlas",
            f"version: {figures.version}",
            "---",
            "",
            "# Glyph Atlas",
            "",
            (
                f"{counts.get('units', 0)} units in {counts.get('lines', 0)} lines, "
                f"{counts.get('pages', 0)} pages and {counts.get('documents', 0)} documents of "
                "pre-modern Japanese writing, each unit carrying its rectangle, the transcriber's "
                "string, a diplomatic reading, code points, the 字母 of kana forms and a variant key "
                "for kanji."
            ),
            "",
            "## Files",
            "",
            (
                "- `units.parquet`, `lines.parquet`, `pages.parquet`, `documents.parquet` and the "
                "other tables of `docs/schema.md`."
            ),
            "- `crops/` where the image licence allows redistribution.",
            "- `ATTRIBUTION.md`, `COUNTS.md`, `datasheet.md`, `CHECKSUMS.txt`, `MANIFEST.json`.",
            "",
            "## Licence",
            "",
            (
                "The annotations and the compilation are CC BY-SA 4.0. Each record carries the licence of "
                "its image and of its text; see `ATTRIBUTION.md`."
            ),
            "",
            "## Citation",
            "",
            f"Release {figures.version}, built {figures.date}. See `CITATION.cff` for the concept DOI.",
            "",
        ]
    )


def zenodo_metadata(figures: Figures) -> dict[str, Any]:
    """The Zenodo deposit metadata, from the same numbers."""
    counts = figures.counts
    return {
        "metadata": {
            "title": "Glyph Atlas: A Dataset of Character Forms in Pre-modern Books of the Sinosphere",
            "upload_type": "dataset",
            "version": figures.version,
            "publication_date": figures.date,
            "language": "jpn",
            "license": "cc-by-sa-4.0",
            "description": (
                f"<p>{counts.get('units', 0)} units in {counts.get('lines', 0)} lines, "
                f"{counts.get('pages', 0)} pages and {counts.get('documents', 0)} documents. Each unit "
                "carries the rectangle on the page image, the transcriber's string, a diplomatic "
                "reading, the Unicode code points including hentaigana, the 字母 of kana forms and a "
                "variant key for kanji written in a form other than the transcribed one.</p>"
                "<p>The annotations and the compilation are CC BY-SA 4.0; every record carries the "
                "licence of its image and of its text. See ATTRIBUTION.md.</p>"
            ),
            "creators": [{"name": "mkpoli"}],
            "keywords": ["kuzushiji", "hentaigana", "Japanese", "pre-modern", "character recognition"],
            "related_identifiers": [
                {"identifier": "https://github.com/mkpoli/glyph-atlas", "relation": "isSupplementTo",
                 "resource_type": "software"},
            ],
            "notes": f"Built by `{figures.command}`. Normalisation policy {figures.normalisation}.",
        }
    }
