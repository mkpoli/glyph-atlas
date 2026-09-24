"""Write, read, validate and merge the dataset tables as Parquet.

A dataset is one file per model of `glyph_atlas.schema`: `documents.parquet` and the optional
`pages`, `page_texts`, `lines`, `units` and `groups`. Every file carries the full schema of its
model, so an empty table still has every column. `Character` is a model too but not a dataset table:
the character layer is one generated vocabulary under `data/vocab/`, read through `refs`, and a
release refers to it rather than copying it. `units` and `lines` may instead be a directory of
shards named after the first two hex digits of `sha1(document_id)`; a row without a `document_id`
lands in shard `00`. Rows inside a file are sorted by `document_id`, `page_id`, `seq` and `id`, using
the fields the model has, with nulls first.

Dictionaries and other free-form values live in string columns as JSON text, and are decoded again
on the way back into a model. Dates, datetimes and enums use the Arrow types `date32`,
`timestamp[us, UTC]` and `string`.

A directory that `write` was given as a whole, and the directory `Dataset.merge` writes, carry a
`MANIFEST.json` with the schema version, the row count of every table, the sha256 of every file, the
writer version, the command that produced it and the time of writing.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, date, datetime
from enum import Enum, IntEnum
from functools import cache
from pathlib import Path
from types import UnionType
from typing import Any, Literal, Self, Union, get_args, get_origin

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from pydantic import BaseModel, ValidationError

from . import __version__
from .schema import Box, Document, Group, Line, Page, PageText, Unit

#: The version of the dataset tables' schema, written into every `MANIFEST.json`. Version 2 removed
#: `Unit.jibo`: the 字母 is metadata on a character now, and `data/vocab/characters.tsv` states it.
SCHEMA_VERSION = 2
BATCH_SIZE = 65_536
MANIFEST_NAME = "MANIFEST.json"

#: The dataset tables. A table only refers to tables listed before it, which validation relies on.
TABLES: dict[str, type[BaseModel]] = {
    "documents": Document,
    "pages": Page,
    "page_texts": PageText,
    "lines": Line,
    "units": Unit,
    "groups": Group,
}

#: The tables that may be a directory of shards.
SHARDED_TABLES = frozenset({"lines", "units"})

#: The fields that order the rows of a file, most significant first.
SORT_FIELDS = ("document_id", "page_id", "seq", "id")

_BUCKET_NAME = re.compile(r"^[0-9a-f]{2}\.parquet$")


def schema_for(model: type[BaseModel]) -> pa.Schema:
    """The Arrow schema of a Pydantic model.

    Enums become strings, nested models structs, `dict[str, Any]` fields JSON text in a string
    column, dates `date32` and datetimes `timestamp[us, UTC]`.
    """
    return pa.schema([(name, _arrow_type(field.annotation)) for name, field in model.model_fields.items()])


def _unwrap(annotation: Any) -> Any:
    """An optional annotation without its `None`."""
    if get_origin(annotation) in (Union, UnionType):
        arguments = [argument for argument in get_args(annotation) if argument is not type(None)]
        if len(arguments) == 1:
            return _unwrap(arguments[0])
    return annotation


def _arrow_type(annotation: Any) -> pa.DataType:
    annotation = _unwrap(annotation)
    if annotation is Any:
        return pa.string()
    if isinstance(annotation, type):
        if issubclass(annotation, BaseModel):
            return pa.struct(schema_for(annotation))
        if issubclass(annotation, Enum):
            return pa.int64() if issubclass(annotation, IntEnum) else pa.string()
        if annotation is bool:
            return pa.bool_()
        if annotation is int:
            return pa.int64()
        if annotation is float:
            return pa.float64()
        if annotation is datetime:
            return pa.timestamp("us", tz="UTC")
        if annotation is date:
            return pa.date32()
        if annotation is str:
            return pa.string()
    origin = get_origin(annotation)
    if origin is Literal:
        values = get_args(annotation)
        if all(isinstance(value, int) and not isinstance(value, bool) for value in values):
            return pa.int64()
        return pa.string()
    if origin in (list, set, frozenset, tuple):
        arguments = get_args(annotation)
        return pa.list_(_arrow_type(arguments[0])) if arguments else pa.list_(pa.string())
    return pa.string()


def _identity(value: Any) -> Any:
    return value


def _as_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _from_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except ValueError:
        return value


def _as_date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return date.fromisoformat(value)


def _as_datetime(value: Any) -> datetime:
    moment = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _codec(annotation: Any) -> tuple[Callable[[Any], Any], Callable[[Any], Any]] | None:
    """How a field's value is written to its column and read back, or `None` when Arrow handles it."""
    annotation = _unwrap(annotation)
    if annotation is Any:
        return _as_json, _from_json
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        inner = {name: codec for name, field in annotation.model_fields.items() if (codec := _codec(field.annotation))}
        if not inner:
            return None

        def encode_struct(value: dict[str, Any]) -> dict[str, Any]:
            return {key: inner[key][0](item) if key in inner and item is not None else item for key, item in value.items()}

        def decode_struct(value: dict[str, Any]) -> dict[str, Any]:
            return {key: inner[key][1](item) if key in inner and item is not None else item for key, item in value.items()}

        return encode_struct, decode_struct
    if annotation is datetime:
        return _as_datetime, _identity
    if annotation is date:
        return _as_date, _identity
    origin = get_origin(annotation)
    if origin in (list, set, frozenset, tuple):
        arguments = get_args(annotation)
        inner = _codec(arguments[0]) if arguments else None
        if inner is None:
            return None

        def encode_list(value: list[Any]) -> list[Any]:
            return [inner[0](item) if item is not None else None for item in value]

        def decode_list(value: list[Any]) -> list[Any]:
            return [inner[1](item) if item is not None else None for item in value]

        return encode_list, decode_list
    if origin is dict:
        return _as_json, _from_json
    return None


@cache
def _codecs(model: type[BaseModel]) -> tuple[dict[str, Callable[[Any], Any]], dict[str, Callable[[Any], Any]]]:
    """The field codecs of a model: how to write its rows and how to read them back."""
    encoders: dict[str, Callable[[Any], Any]] = {}
    decoders: dict[str, Callable[[Any], Any]] = {}
    for name, field in model.model_fields.items():
        codec = _codec(field.annotation)
        if codec is None:
            continue
        encoders[name] = codec[0]
        if codec[1] is not _identity:
            decoders[name] = codec[1]
    return encoders, decoders


def _json_row(record: BaseModel, model: type[BaseModel]) -> dict[str, Any]:
    """A record as the column values of its Arrow schema."""
    row = record.model_dump(mode="json")
    encoders, _ = _codecs(model)
    for name, encode in encoders.items():
        if row.get(name) is not None:
            row[name] = encode(row[name])
    return row


def _row_to_model(row: dict[str, Any], model: type[BaseModel]) -> BaseModel:
    """A Parquet row as a validated record, with JSON text columns decoded."""
    _, decoders = _codecs(model)
    for name, decode in decoders.items():
        if row.get(name) is not None:
            row[name] = decode(row[name])
    return model.model_validate(row)


def write(
    path: Path,
    records: Iterable[BaseModel] | Iterable[dict],
    model: type[BaseModel],
    *,
    shard: bool = False,
    command: str | None = None,
) -> int:
    """Write one table and return its number of rows.

    `records` are models of `model` or dictionaries that validate as one. `path` is a file, or a
    directory when `shard=True`; only `units` and `lines` are sharded. A directory write replaces
    the shards it holds and writes `MANIFEST.json`.

    The write takes the table's lock for the whole call, so two processes writing the same table
    serialize instead of one losing the other's rows. A read-modify-write that spans several
    operations still has to hold the lock itself, which is what `locked` is for.
    """
    path = Path(path)
    with locked(path):
        return _write_unlocked(path, records, model, shard=shard, command=command)


def _write_unlocked(
    path: Path,
    records: Iterable[BaseModel] | Iterable[dict],
    model: type[BaseModel],
    *,
    shard: bool = False,
    command: str | None = None,
) -> int:
    """`write` without taking the lock, for a caller that already holds it."""
    if shard:
        name = _table_name(model)
        if name not in SHARDED_TABLES:
            raise ValueError(f"{name} is not sharded; only {', '.join(sorted(SHARDED_TABLES))} are")
        if path.suffix == ".parquet" or (path.exists() and not path.is_dir()):
            raise ValueError(f"{path} is a file; shard=True writes a directory of shards")
    elif path.is_dir():
        raise ValueError(f"{path} is a directory; a directory of shards needs shard=True")
    table = _table_from_records(records, model)
    files = _write_table(path, table, shard=shard)
    if path.is_dir():
        _write_manifest(path, {_table_name(model): table.num_rows}, files, command)
    return table.num_rows


class locked:
    """Hold a table's lock for a block of work.

    Two processes can read a table, each add its rows and each write the result back; the second
    write then holds only its own contribution, and the first process's rows are gone. The lock is a
    sidecar file beside the table, so it covers a whole dataset directory as well as one file, and it
    is taken with `flock`, which the kernel releases when the process dies — a killed run does not
    leave the table unusable.

    `poll` and `timeout` bound the wait: a run that cannot take the lock within `timeout` seconds
    raises `TimeoutError` rather than hanging behind a stuck process.
    """

    def __init__(self, path: Path, *, poll: float = 0.2, timeout: float | None = 3600.0) -> None:
        self.path = Path(path)
        self.poll = poll
        self.timeout = timeout
        self._handle: Any = None

    @property
    def lock_path(self) -> Path:
        """Where the sidecar lives: beside the table, named for it."""
        name = self.path.name if self.path.suffix else (self.path.name or "root")
        return self.path.with_name(f".{name}.lock")

    def __enter__(self) -> Self:
        import fcntl
        import time

        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.lock_path.open("a+")
        started = time.monotonic()
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._handle = handle
                return self
            except OSError:
                if self.timeout is not None and time.monotonic() - started > self.timeout:
                    handle.close()
                    raise TimeoutError(
                        f"waited {self.timeout:.0f} s for the lock on {self.path}; "
                        f"another process is writing it"
                    ) from None
                time.sleep(self.poll)

    def __exit__(self, *_: object) -> None:
        import fcntl

        if self._handle is None:
            return
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None


def write_table(
    path: Path,
    records: Iterable[BaseModel] | Iterable[dict],
    model: type[BaseModel] | None = None,
    **kwargs: Any,
) -> int:
    """Write a table, taking the model from the first record when the caller does not name it.

    Importers hold records of one model and often do not have the model at hand; the model is still
    needed for an empty table, whose file must carry every column.
    """
    if model is None:
        iterator = iter(records)
        try:
            first = next(iterator)
        except StopIteration:
            raise ValueError("write_table needs a model when there are no records") from None
        model = type(first)
        records = _prepend(first, iterator)
    return write(path, records, model, **kwargs)


def _prepend(record: Any, records: Iterable[Any]) -> Iterator[Any]:
    yield record
    yield from records


def read(path: Path, model: type[BaseModel]) -> list[BaseModel]:
    """Every row of a table, as validated models."""
    return [record for batch in scan(path, model) for record in batch]


def scan(
    path: Path,
    model: type[BaseModel],
    columns: list[str] | None = None,
    batch_size: int = BATCH_SIZE,
    keep: Callable[[dict], bool] | None = None,
) -> Iterator[list[BaseModel]]:
    """Yield batches of a table without loading the file whole.

    `path` is a file or a directory of shards. `columns` reads a subset of the columns; the fields
    left out keep their defaults. A field the model requires is read even when the caller leaves it
    out, because a record without it cannot be validated; the row the caller gets therefore always
    holds every field the model requires, and the fields that have defaults keep them.

    `keep` is applied to the raw row before the model is built, and a row it rejects is never turned
    into one. That is what makes a scan of a narrow slice of a large table cheap: validating a record
    runs Pydantic over every field of it, and a caller that wants the lines of one page out of a
    million should not pay for the million. A row group whose statistics already exclude every value
    of the column is skipped without reading it.
    """
    if columns is not None:
        columns = _projection(model, columns)
    if isinstance(keep, In):
        # A set membership is applied by Arrow, so a scan of one page out of a million rows reads
        # only the row groups whose statistics can hold one of the values; converting every row to a
        # Python object first cost 19 s for two lines of a 1.17M-row table.
        import pyarrow.dataset as ds

        for file in _table_files(Path(path)):
            scanner = ds.dataset(file, format="parquet").scanner(
                columns=columns, filter=pc.field(keep.column).isin(list(keep.values)), batch_size=batch_size
            )
            for batch in scanner.to_batches():
                rows = batch.to_pylist()
                if rows:
                    yield [_row_to_model(row, model) for row in rows]
        return
    for file in _table_files(Path(path)):
        for batch in pq.ParquetFile(file).iter_batches(batch_size=batch_size, columns=columns):
            rows = batch.to_pylist()
            if not rows:
                continue
            if keep is not None:
                rows = [row for row in rows if keep(row)]
            if rows:
                yield [_row_to_model(row, model) for row in rows]


class In:
    """A filter for `scan`: the rows whose column holds one of these values.

    It is callable, so it works as a plain predicate, and it carries the column and the values for
    the row-group statistics check.
    """

    __slots__ = ("column", "values")

    def __init__(self, column: str, values: Iterable[Any]) -> None:
        self.column = column
        self.values = set(values)

    def __call__(self, row: dict) -> bool:
        return row.get(self.column) in self.values


def _group_may_hold(group: Any, keep: Callable[[dict], bool] | None) -> bool:
    """Whether a row group can hold a row `keep` accepts, judged by its column statistics.

    Only the interval of the first column is consulted, which is enough for a scan over one page or
    one document: the sort order inside a file puts equal values together, so the row groups that
    hold them are contiguous and the rest are excluded by their minimum and maximum.
    """
    values = getattr(keep, "values", None)
    column = getattr(keep, "column", None)
    if keep is None or values is None or column is None or group.num_columns == 0:
        return True
    position = None
    for index in range(group.num_columns):
        if group.column(index).path_in_schema == column:
            position = index
            break
    if position is None:
        return True
    stats = group.column(position).statistics
    if stats is None or not stats.has_min_max:
        return True
    low, high = stats.min, stats.max
    return any(low <= value <= high for value in values if isinstance(value, type(low)))


def _projection(model: type[BaseModel], columns: list[str]) -> list[str]:
    """The columns to read: what the caller asked for, plus every field the model requires."""
    wanted = list(dict.fromkeys(columns))
    for name, field in model.model_fields.items():
        if field.is_required() and name not in wanted:
            wanted.append(name)
    return wanted


def _table_from_records(records: Iterable[BaseModel] | Iterable[dict], model: type[BaseModel]) -> pa.Table:
    rows: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, model):
            record = model.model_validate(record.model_dump() if isinstance(record, BaseModel) else record)
        rows.append(_json_row(record, model))
    return pa.Table.from_pylist(rows, schema=schema_for(model))


def _write_table(path: Path, table: pa.Table, *, shard: bool) -> list[Path]:
    """Write a sorted table as one file or as a directory of shards; return the files written."""
    table = _sorted(table)
    if not shard:
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, path)
        return [path]
    path.mkdir(parents=True, exist_ok=True)
    for stale in path.glob("*.parquet"):
        if _BUCKET_NAME.match(stale.name):
            stale.unlink()
    files = []
    for bucket, rows in _shards(table).items():
        target = path / f"{bucket}.parquet"
        pq.write_table(rows, target)
        files.append(target)
    return files


def _sorted(table: pa.Table) -> pa.Table:
    """Sort a table by the sort fields it has, nulls first."""
    keys = [name for name in SORT_FIELDS if name in table.column_names and _sortable(table.schema.field(name).type)]
    if not keys:
        return table
    indices = pc.sort_indices(table, sort_keys=[(name, "ascending", "at_start") for name in keys])
    return table.take(indices)


def _sortable(data_type: pa.DataType) -> bool:
    """Whether Arrow can order a column of this type; a column it cannot order counts as null."""
    return (
        pa.types.is_null(data_type)
        or pa.types.is_boolean(data_type)
        or pa.types.is_integer(data_type)
        or pa.types.is_floating(data_type)
        or pa.types.is_decimal(data_type)
        or pa.types.is_date(data_type)
        or pa.types.is_time(data_type)
        or pa.types.is_timestamp(data_type)
        or pa.types.is_string(data_type)
        or pa.types.is_large_string(data_type)
        or pa.types.is_string_view(data_type)
        or pa.types.is_dictionary(data_type)
    )


def _bucket(document_id: Any) -> str:
    """The shard of a row: the first two hex digits of `sha1(document_id)`; `None` goes to `00`."""
    if document_id is None:
        return "00"
    return hashlib.sha1(str(document_id).encode("utf-8")).hexdigest()[:2]


def _shards(table: pa.Table) -> dict[str, pa.Table]:
    """Split a table into one table per shard bucket."""
    if table.num_rows == 0:
        return {}
    if "document_id" not in table.column_names or pa.types.is_null(table.schema.field("document_id").type):
        return {"00": table}
    column = table.column("document_id")
    documents = pc.unique(column).to_pylist()
    if column.null_count and None not in documents:
        documents.append(None)
    codes = {document: int(_bucket(document), 16) for document in documents}
    indices = pc.index_in(column, value_set=pa.array(documents, type=column.type))
    if column.null_count:
        indices = pc.fill_null(indices, documents.index(None))
    code_column = pc.take(pa.array([codes[document] for document in documents], pa.int16()), indices)
    return {f"{code:02x}": table.filter(pc.equal(code_column, code)) for code in sorted(set(codes.values()))}


def _table_files(path: Path) -> list[Path]:
    """The Parquet files of a table: the file itself, or every shard of a directory."""
    if path.is_dir():
        return sorted(entry for entry in path.glob("*.parquet") if entry.is_file())
    if path.is_file():
        return [path]
    raise FileNotFoundError(f"no table at {path}")


def _raw_batches(path: Path) -> Iterator[tuple[str | None, list[dict[str, Any]]]]:
    """Unvalidated rows of a table by batch, with the shard they came from."""
    directory = path.is_dir()
    for file in _table_files(path):
        label = file.name if directory else None
        for batch in pq.ParquetFile(file).iter_batches(batch_size=BATCH_SIZE):
            rows = batch.to_pylist()
            if rows:
                yield label, rows


def _table_name(model: type[BaseModel]) -> str:
    for name, candidate in TABLES.items():
        if candidate is model:
            return name
    raise ValueError(f"{model.__name__} is not one of the dataset tables")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_manifest(directory: Path, counts: dict[str, int], files: Iterable[Path], command: str | None) -> Path:
    """Write `MANIFEST.json` for a dataset or table directory."""
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "tables": counts,
        "files": {str(file.relative_to(directory)): _sha256(file) for file in sorted(files)},
        "writer": f"glyph-atlas {__version__}",
        "command": command,
        "written_at": datetime.now(tz=UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    target = directory / MANIFEST_NAME
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def _clear_table(directory: Path, name: str) -> None:
    """Remove the files of one table from a dataset directory, so that no stale shard survives."""
    file = directory / f"{name}.parquet"
    if file.is_file():
        file.unlink()
    shards = directory / name
    if shards.is_dir():
        for entry in shards.glob("*.parquet"):
            if _BUCKET_NAME.match(entry.name):
                entry.unlink()


def _canonical(row: dict[str, Any]) -> str:
    return json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(row: dict[str, Any]) -> bytes:
    """A stable digest of a row's content, for spotting duplicates."""
    return hashlib.sha256(_canonical(row).encode("utf-8")).digest()


def _pretty(row: dict[str, Any]) -> str:
    return json.dumps(row, ensure_ascii=False, sort_keys=True, indent=2, default=str)


def _merged(name: str, model: type[BaseModel], paths: list[Path]) -> pa.Table:
    """Every row of the given tables, with identical duplicate rows collapsed."""
    schema = schema_for(model)
    has_id = "id" in model.model_fields
    by_id: dict[str, bytes] = {}
    contents: set[bytes] = set()
    chunks: list[pa.Table] = []
    for path in paths:
        for batch in scan(path, model):
            keep: list[dict[str, Any]] = []
            for record in batch:
                row = _json_row(record, model)
                digest = _digest(row)
                if has_id:
                    ident = row["id"]
                    first = by_id.get(ident)
                    if first is None:
                        by_id[ident] = digest
                    elif first != digest:
                        raise DuplicateIdError(name, ident, _earlier(paths, model, ident, first), row)
                    else:
                        continue
                elif digest in contents:
                    continue
                else:
                    contents.add(digest)
                keep.append(row)
            if keep:
                chunks.append(pa.Table.from_pylist(keep, schema=schema))
    return pa.concat_tables(chunks) if chunks else pa.Table.from_pylist([], schema=schema)


def _earlier(paths: list[Path], model: type[BaseModel], ident: str, digest: bytes) -> dict[str, Any] | None:
    """The first row with this id and content, for the message of a `DuplicateIdError`."""
    for path in paths:
        for batch in scan(path, model):
            for record in batch:
                if getattr(record, "id", None) != ident:
                    continue
                row = _json_row(record, model)
                if _digest(row) == digest:
                    return row
    return None


class DuplicateIdError(ValueError):
    """Two rows of one table share an id but hold different content."""

    def __init__(self, table: str, ident: str, first: dict[str, Any] | None, second: dict[str, Any]) -> None:
        self.table = table
        self.id = ident
        self.rows = [row for row in (first, second) if row is not None]
        parts = [f"{table}: duplicate id {ident!r} with different content:"]
        if first is not None:
            parts.append(f"first:\n{_pretty(first)}")
        parts.append(f"second:\n{_pretty(second)}")
        super().__init__("\n".join(parts))


def _where(name: str, label: str | None, index: int, ident: Any) -> str:
    """How an error names the row it is about."""
    prefix = f"{name}/{label}" if label else name
    return f"{prefix}: {ident}" if isinstance(ident, str) else f"{prefix}: row {index}"


def _problems(exc: ValidationError) -> str:
    """A Pydantic error as one line naming the fields it is about."""
    return "; ".join(
        f"{'.'.join(str(part) for part in problem['loc'])}: {problem['msg']}" for problem in exc.errors()[:4]
    )


def _dangling(where: str, field: str, value: Any, known: set[str], table: str) -> list[str]:
    if value in known:
        return []
    return [f"{where}: {field} {value!r} not in {table}"]


def _box_errors(where: str, box: Box | None, page_id: str | None, sizes: dict[str, tuple[int, int]]) -> list[str]:
    if box is None:
        return []
    if box.w <= 0 or box.h <= 0:
        return [f"{where}: box {box.w}x{box.h}; w and h must be positive"]
    size = sizes.get(page_id) if page_id is not None else None
    if size is None or size[0] <= 0 or size[1] <= 0:
        return []
    if box.x < 0 or box.y < 0 or box.x + box.w > size[0] or box.y + box.h > size[1]:
        return [f"{where}: box {box.x},{box.y},{box.w},{box.h} outside page {page_id} {size[0]}x{size[1]}"]
    return []


class Dataset:
    """The tables of one dataset directory.

    `tables` maps `documents`, `pages`, `page_texts`, `lines`, `units` and `groups` to the file or
    shard directory that holds the table, or to `None` when the directory has no such table.
    `documents` is the only table a complete dataset needs.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.tables: dict[str, Path | None] = {}
        for name in TABLES:
            file = self.directory / f"{name}.parquet"
            shards = self.directory / name
            if file.is_file():
                self.tables[name] = file
            elif shards.is_dir():
                self.tables[name] = shards
            else:
                self.tables[name] = None

    def _path(self, name: str) -> Path:
        if name not in TABLES:
            raise ValueError(f"unknown table {name!r}; expected one of {', '.join(TABLES)}")
        path = self.tables[name]
        if path is None:
            raise FileNotFoundError(f"{self.directory} has no {name} table")
        return path

    def read(self, name: str) -> list[BaseModel]:
        """Every row of one table, as validated models."""
        return read(self._path(name), TABLES[name])

    def scan(self, name: str, columns: list[str] | None = None,
             keep: Callable[[dict], bool] | None = None) -> Iterator[list[BaseModel]]:
        """Batches of one table, without loading it whole; `keep` filters raw rows."""
        return scan(self._path(name), TABLES[name], columns=columns, keep=keep)

    def validate(self) -> list[str]:
        """Every rule the tables break, as human-readable lines; empty when the dataset is valid.

        A row that does not parse is reported and skipped. The other rules cover the references
        between tables, positive box sizes, boxes inside pages of known size, a crop for a unit
        with no page, and ids unique per table. A file that is not readable Parquet is reported
        instead of raising.
        """
        errors: list[str] = []
        if self.tables["documents"] is None:
            errors.append("documents: table documents.parquet is missing")
        known: dict[str, set[str]] = {"documents": set(), "pages": set(), "lines": set(), "units": set()}
        sizes: dict[str, tuple[int, int]] = {}
        for name, model in TABLES.items():
            path = self.tables[name]
            if path is None:
                continue
            seen: set[str] = set()
            try:
                for label, rows in _raw_batches(path):
                    for index, row in enumerate(rows):
                        where = _where(name, label, index, row.get("id"))
                        try:
                            record = _row_to_model(row, model)
                        except ValidationError as exc:
                            errors.append(f"{where}: does not parse: {_problems(exc)}")
                            continue
                        ident = getattr(record, "id", None)
                        if isinstance(ident, str):
                            if ident in seen:
                                errors.append(f"{where}: duplicate id")
                            seen.add(ident)
                        if name == "documents":
                            known["documents"].add(record.id)
                        elif name == "pages":
                            known["pages"].add(record.id)
                            sizes[record.id] = (record.width, record.height)
                            errors.extend(
                                _dangling(where, "document_id", record.document_id, known["documents"], "documents")
                            )
                        elif name == "page_texts":
                            errors.extend(_dangling(where, "page_id", record.page_id, known["pages"], "pages"))
                        elif name == "lines":
                            known["lines"].add(record.id)
                            errors.extend(_dangling(where, "page_id", record.page_id, known["pages"], "pages"))
                            errors.extend(_box_errors(where, record.box, record.page_id, sizes))
                        elif name == "units":
                            known["units"].add(record.id)
                            errors.extend(
                                _dangling(where, "document_id", record.document_id, known["documents"], "documents")
                            )
                            if record.page_id is not None:
                                errors.extend(_dangling(where, "page_id", record.page_id, known["pages"], "pages"))
                            if record.line_id is not None:
                                errors.extend(_dangling(where, "line_id", record.line_id, known["lines"], "lines"))
                            if record.page_id is None and record.crop is None:
                                errors.append(f"{where}: page_id is null and crop is not set")
                            errors.extend(_box_errors(where, record.box, record.page_id, sizes))
                        elif name == "groups":
                            errors.extend(_dangling(where, "page_id", record.page_id, known["pages"], "pages"))
                            for unit_id in record.unit_ids:
                                errors.extend(_dangling(where, "unit_ids", unit_id, known["units"], "units"))
                            errors.extend(_box_errors(where, record.box, record.page_id, sizes))
            except pa.ArrowException as exc:
                errors.append(f"{name}: cannot read the table: {exc}")
        return errors

    def merge(self, others: Iterable[Dataset], out: Path, *, command: str | None = None) -> dict[str, int]:
        """Concatenate datasets into `out`, collapse identical rows, sort and write.

        Two rows of one table with the same id and different content raise `DuplicateIdError` with
        both rows printed. A table that is a shard directory in any input is written as a shard
        directory. The returned mapping holds the row count of every table written.
        """
        sources = [self, *(others or ())]
        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        counts: dict[str, int] = {}
        files: list[Path] = []
        for name, model in TABLES.items():
            paths = [source.tables[name] for source in sources if source.tables[name] is not None]
            if not paths:
                continue
            shard = any(path.is_dir() for path in paths)
            table = _merged(name, model, paths)
            _clear_table(out, name)
            files.extend(_write_table(out / name if shard else out / f"{name}.parquet", table, shard=shard))
            counts[name] = table.num_rows
        _write_manifest(out, counts, files, command)
        return counts
