# Interfaces

This page fixes the names and signatures shared across modules, so that code written in parallel
meets. Adding a function changes nothing here without saying so in its pull request.

## Layout

- Python package `src/glyph_atlas/`, tests `tests/`, scripts `scripts/`, model code
  `models/<name>/`.
- Generated data: upstream downloads under `cache/`, intermediate tables under `work/<source>/`,
  releases under `out/`, trained artefacts under `models/<name>/artifacts/`. None of it is
  committed.

## `glyph_atlas.tables`

```python
schema_for(model: type[BaseModel]) -> pa.Schema
write(path: Path, records: Iterable[BaseModel] | Iterable[dict], model: type[BaseModel], *,
      shard: bool = False, command: str | None = None) -> int
read(path: Path, model: type[BaseModel]) -> list[BaseModel]
scan(path: Path, model: type[BaseModel], columns: list[str] | None = None,
     batch_size: int = 65_536) -> Iterator[list[BaseModel]]
```

- `write` accepts models or already-dumped dicts. `path` may be a file or a directory; a directory
  is required when `shard=True`, and `shard=True` writes `path/<bucket>.parquet` with `bucket` the
  first two hex digits of `sha1(document_id)` (units and lines only).
- Both `write` and `read` take a file or a directory of shards.
- A file has every column of the model's schema, so an empty table still has the columns.

```python
class Dataset:
    def __init__(self, directory: Path) -> None: ...
    tables: dict[str, ...]      # documents, pages, page_texts, lines, units, groups
    def read(self, name: str) -> list[BaseModel]: ...
    def scan(self, name: str, columns: list[str] | None = None) -> Iterator[list[BaseModel]]: ...
    def validate(self) -> list[str]: ...        # human-readable errors, empty when valid
    def merge(self, others: Iterable[Dataset], out: Path, *, command: str | None = None) -> dict[str, int]: ...
```

`Dataset` takes the directory that holds `documents.parquet` and the other optional tables, with
`units` and `lines` either a file or a directory of shards. `documents` is the only required table.
`merge` writes a new dataset directory with a `MANIFEST.json`. `MANIFEST.json` is written by
`write` when it is given a directory and by `merge`, and holds `schema_version` (1), `tables` with
row counts, `files` with sha256, `writer`, `command`, `written_at`.

## `glyph_atlas.net`

```python
class DownloadError(RuntimeError): ...

def download(url: str, dest: Path, *, pause: float | None = None, retries: int = 5,
             expected: str | None = None, client: httpx.Client | None = None) -> Path
def host_pause(url: str) -> float
```

`pause` is the minimum interval between two requests to the same host, enforced across calls in one
process; the default is 3.0 s, and 1.0 s for `codh.rois.ac.jp` and any `*.huggingface.co` host.
`expected` names the content kind (`"json"`, `"image"`, `"zip"`, `"text"`) and a response that
does not look like it fails. A destination that already exists is returned untouched unless the
caller asks for a refresh.

## `glyph_atlas.images`

```python
class ImageRecord(BaseModel):  # url, service, sha256, width, height, bytes, fetched_at, etag, last_modified
    ...

def service_of(url: str) -> str | None
def info(service: str, *, client: httpx.Client | None = None) -> dict   # width, height, version, tiles
def full_url(service: str, version: int | str) -> str
def fetch(url: str, *, box: Box | None = None, client: httpx.Client | None = None) -> ImageRecord
def register(path: Path, url: str, **fields) -> ImageRecord
def path_for(url: str) -> Path | None
def crop(url: str, box: Box) -> Image.Image
def index_path() -> Path
```

- The cache is `cache/images/<sha256[:2]>/<sha256>.<ext>` with `cache/images/index.parquet`.
- `fetch` downloads the full-size image at `url` and adds a row to the index. `crop` reads the
  cached full image and cuts `box`; it never writes a file.
- The CLI (`atlas images info|fetch|crop`) is the only place that fills a pages table; the module
  itself does not read or write `pages.parquet`.

## `glyph_atlas.koji`

```python
class Node: ...                       # Text(start, end) | Element(kind, start, end, children, attrs)
class Char(BaseModel):                # text, start, end, path, role
class Parsed(BaseModel):              # plain: str, nodes: list[Node], chars: list[Char], malformed: bool
def parse(text: str) -> Parsed
def plain(text: str) -> str
```

`role` is one of `main`, `ruby`, `ruby-left`, `warigaki`, `note`, `okurigana`, `kaeriten`, `gap`,
`unreadable`, `cancelled`, `inserted`. Offsets are half-open and index code points of the raw line.

## `glyph_atlas.refs`

```python
def hentaigana() -> list[dict]                      # hentaigana.tsv joined with mj-hentaigana.tsv
def candidates(reading: str) -> list[str]           # U+XXXX strings, ordinary kana first, then code point order
def jibo(code_point: str) -> str | None
def readings(code_point: str) -> list[str]
def equivalents(char: str, policy: str) -> set[str]
def same(a: str, b: str, policy: str) -> bool
def policy(name: str) -> dict                       # from data/vocab/equivalence-policies.yaml
def to_code_points(text: str) -> list[str]          # "か" -> ["U+304B"]
def from_code_points(code_points: Iterable[str]) -> str
```

Policies live in `data/vocab/equivalence-policies.yaml`, one row per policy with `version`,
`relations` (the names it composes) and `description`.

## `glyph_atlas.remotezip`

```python
@dataclass
class Entry:  # name, method, compress_size, file_size, crc32, header_offset
    ...

class RemoteZip:
    def __init__(self, url: str, *, client: httpx.Client | None = None) -> None: ...
    entries: list[Entry]
    def read(self, name: str) -> bytes: ...
    def extract(self, names: Iterable[str], dest: Path) -> list[Path]: ...
    def iter_prefix(self, prefix: str) -> Iterator[Entry]: ...
    def requests_made(self) -> int: ...
```

## `glyph_atlas.rights`

```python
def resolve(*, licence: str | None = None, url: str | None = None, holder: str | None = None,
            checked: date | None = None) -> Rights
def manifest_rights(manifest: dict) -> Rights | None
def eligible(rights: Rights | None, target: Licence | str = Licence.CC_BY_SA_4) -> bool
def vocabulary() -> list[dict]                       # data/vocab/licences.yaml
def holder_entry(name: str | None) -> dict | None    # data/vocab/holders.yaml
```

## Command line

`atlas` is one Typer application in `src/glyph_atlas/cli.py` with sub-applications `tables`,
`images`, `rights`, `import`, `pilot`, `eval`, `review`, `audit`, `align`, `jibo`, `export` and the
top-level commands `sources` and `coverage`. A new command adds a module under
`src/glyph_atlas/` and registers it in `cli.py`; logic does not go in `cli.py`.

## Records and ids

Ids and field meanings are in `docs/schema.md`. Imported records take deterministic ids; a table
written twice from the same inputs is byte-identical apart from `MANIFEST.json`'s timestamp.
