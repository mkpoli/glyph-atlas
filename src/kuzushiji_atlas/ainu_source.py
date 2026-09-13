"""The bridge between the atlas's Ainu import and the source project that publishes it.

The アイヌ関連資料 records come from みんなで翻刻 and are published by `aynumosir/ainu-records`. Both
projects hold the same pages, with different jobs: the source owns the diplomatic transcription and
its editorial corrections, and the atlas owns the images, the detector's boxes, the alignment and the
review work done here. This module is the correspondence between them, and it is deliberately the only
place that knows both vocabularies.

**The join is an id, not a position.** Every curated part in `data/sources.yaml` carries the
みんなで翻刻 `entry` id, and the atlas records the same string in a document's
`source_refs["honkoku-data"]`. So a document maps to a source unit by looking that id up, never by
matching titles, shelfmarks or page counts — those differ between the two projects, and a mapping
built on them would be a guess that silently attaches feedback to the wrong witness.

**Pages are one-based and lines are the parser's own.** A correction lives at
`data/editorial/corrections/<work>/<witness>/p<page>.json`, where `page` is the source's one-based
reader page, and each record names a one-based line *as the source's parser counts them*: blank lines
and the 右丁/左丁/丁 markers are skipped, so the count is not the atlas's `page.seq` plus one and not
the atlas's own markup segmentation. `ReviewStanding` therefore carries the source page and line
numbers when they can be established, and reports what it could not establish instead of guessing.

**A correction is validated the way the source validates it.** An `original` must match exactly one
place in the named line, because that is what makes a changed upstream transcription fail the build
rather than silently move the correction to another character. This module checks the same conditions
before writing anything, so a proposal that passes here passes there, and one that cannot be placed is
returned as `unmappable` with its reason for a person to look at.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

#: Where the corrections live, relative to the source repository root.
CORRECTIONS = Path("data") / "editorial" / "corrections"
#: The source's own index of works, witnesses and the platform entries that make each part.
SOURCES = Path("data") / "sources.yaml"

#: A unit slug as the source writes it: lowercase words joined by `-`.
UNIT = re.compile(r"^[a-z0-9-]+/[a-z0-9-]+$")
#: A correction file, whose name is the one-based reader page.
PAGE_FILE = re.compile(r"^p([1-9][0-9]*)\.json$")
#: A structural line the source's parser skips when it counts transcription lines.
STRUCTURAL = frozenset({"右丁", "左丁", "丁"})
_PHYSICAL = re.compile(r"^［(.+?)］\s*$")


class AinuSourceError(RuntimeError):
    """The source tree cannot be read, or a proposal would not survive its validator."""


@dataclass
class Part:
    """One platform entry: the transcription of one part of one witness."""

    entry: str
    label: str | None = None

    @property
    def id(self) -> str:
        return self.entry


@dataclass
class Witness:
    """One physical copy, with the platform entries that reproduce it."""

    slug: str
    work: str
    holder: str | None = None
    shelfmark: str | None = None
    title: str | None = None
    catalogue: str | None = None
    parts: list[Part] = field(default_factory=list)

    @property
    def unit(self) -> str:
        """The slug the source uses in paths and character keys: `<work>/<witness>`."""
        return f"{self.work}/{self.slug}"

    def part_of(self, entry: str) -> Part | None:
        return next((part for part in self.parts if part.entry == entry), None)


@dataclass
class Work:
    """One work, with the witnesses the source publishes."""

    slug: str
    title: str | None = None
    title_reading: str | None = None
    title_latin: str | None = None
    kind: str | None = None
    genre: str | None = None
    date: str | None = None
    witnesses: list[Witness] = field(default_factory=list)


@dataclass
class Mapping:
    """One atlas document's place in the source project."""

    document_id: str
    unit: str
    work: str
    witness: str
    part: Part | None
    entry: str
    holder: str | None = None
    title: str | None = None
    catalogue: str | None = None

    @property
    def corrections_dir(self) -> Path:
        return CORRECTIONS / self.work / self.witness


@dataclass
class Proposal:
    """A correction this side proposes, either in the source's format or with a reason it cannot be."""

    unit: str
    page: int
    id: str
    line: int | None = None
    original: str | None = None
    corrected: str | None = None
    note: str | None = None
    kind: str | None = None
    entry: dict[str, Any] | None = None
    ruby_field: str | None = None
    ruby_base: str | None = None
    unmappable: str | None = None

    def record(self) -> dict[str, Any]:
        """The JSON object the source's `corrections.ts` reads, without its location fields.

        The source refuses a record that carries `unit` or `page`, because the path supplies both.
        """
        if self.unmappable:
            raise AinuSourceError(self.unmappable)
        record: dict[str, Any] = {
            "id": self.id,
            "line": self.line,
            "original": self.original,
            "corrected": self.corrected,
            "note": self.note,
        }
        if self.kind and self.kind != "transcription":
            raise AinuSourceError(
                f"corrections.ts accepts kind 'transcription' only, not {self.kind!r}"
            )
        if self.ruby_field is not None:
            record["rubyField"] = self.ruby_field
        if self.ruby_base is not None:
            record["rubyBase"] = self.ruby_base
        if self.entry is not None:
            record["entry"] = self.entry
        return record

    def path(self) -> Path:
        return CORRECTIONS / Path(self.unit).parts[0] / Path(self.unit).parts[1] / f"p{self.page}.json"


def transcription_lines(text: str) -> list[str]:
    """The lines the source's parser counts, in its own order.

    `parsePage` drops blank lines, drops a ［…］ marker whose label is structural (右丁, 左丁, 丁) and
    keeps any other marker in `physical`, and then numbers what is left from one. This reproduces that
    count for corrections, which are placed by it, and it is the reason a correction cannot be placed
    with `page.seq + 1`: the atlas's lines come from its own reading of the markup.
    """
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        marker = _PHYSICAL.match(line.strip())
        if marker:
            label = marker.group(1).strip()
            if label in STRUCTURAL or label == "ママ":
                continue
        lines.append(line)
    return lines


def place(lines: Iterable[str], *, line: int, original: str) -> str | None:
    """Why `original` cannot be placed on `line`, or None when it can.

    The source requires an exact, unique match inside the named line, and the reason matters to a
    reviewer: a line number that does not exist, text that is not there, and text that appears twice
    are three different problems.
    """
    lines = list(lines)
    if line < 1:
        return f"line {line} is not a one-based transcription line"
    if line > len(lines):
        return f"line {line} does not exist; the page has {len(lines)} transcription lines"
    if not original:
        return "a correction needs the original text it replaces"
    found = lines[line - 1].count(original)
    if found == 0:
        return f"line {line} does not contain {original!r}"
    if found > 1:
        return f"{original!r} appears {found} times on line {line}; the source needs one unique match"
    return None


class AinuSource:
    """The source project's editorial data, read from a checkout of it.

    `root` must be a checkout of `aynumosir/ainu-records` at a current `main`. Nothing here writes:
    `drafts` renders the files a submission would add, so a reviewer sees the change before any branch
    exists, and the actual commit and pull request belong to the source project's own workflow.
    """

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        if not (self.root / SOURCES).is_file():
            raise AinuSourceError(f"{self.root} is not an ainu-records checkout: no {SOURCES}")
        self._document: dict[str, Any] | None = None

    # -- works and witnesses ---------------------------------------------------------------------

    @property
    def document(self) -> dict[str, Any]:
        if self._document is None:
            loaded = yaml.safe_load((self.root / SOURCES).read_text(encoding="utf-8")) or {}
            self._document = loaded
        return self._document

    def works(self) -> list[Work]:
        """Every work the source publishes, with its witnesses and their platform entries."""
        works: list[Work] = []
        for row in self.document.get("sources") or []:
            witnesses = []
            for entry in row.get("witnesses") or []:
                witnesses.append(
                    Witness(
                        slug=str(entry["slug"]),
                        work=str(row["slug"]),
                        holder=entry.get("holder"),
                        shelfmark=entry.get("shelfmark"),
                        title=entry.get("title"),
                        catalogue=entry.get("catalogue"),
                        parts=[
                            Part(entry=str(part["entry"]), label=part.get("label"))
                            for part in entry.get("parts") or []
                            if part.get("entry")
                        ],
                    )
                )
            works.append(
                Work(
                    slug=str(row["slug"]),
                    title=row.get("title"),
                    title_reading=row.get("titleReading"),
                    title_latin=row.get("titleLatin"),
                    kind=row.get("kind"),
                    genre=row.get("genre"),
                    date=str(row["date"]) if row.get("date") is not None else None,
                    witnesses=witnesses,
                )
            )
        return works

    def entries(self) -> dict[str, tuple[Work, Witness, Part]]:
        """Every platform entry id the source publishes, and where it belongs."""
        found: dict[str, tuple[Work, Witness, Part]] = {}
        for work in self.works():
            for witness in work.witnesses:
                for part in witness.parts:
                    if part.entry in found:
                        raise AinuSourceError(f"{part.entry} appears under two witnesses")
                    found[part.entry] = (work, witness, part)
        return found

    def map_document(self, document: Any) -> Mapping | None:
        """Where one atlas document sits in the source project, or None when it does not.

        The join is the みんなで翻刻 entry id, which both projects record. A document the source does
        not publish — or one whose entry the curation has not caught up with — comes back as None
        rather than being attached by title, because an attachment by resemblance is not a mapping.
        """
        refs = getattr(document, "source_refs", None) or {}
        entry = refs.get("honkoku-data")
        if not entry:
            return None
        placed = self.entries().get(str(entry))
        if placed is None:
            return None
        work, witness, part = placed
        return Mapping(
            document_id=document.id,
            unit=witness.unit,
            work=work.slug,
            witness=witness.slug,
            part=part,
            entry=str(entry),
            holder=witness.holder,
            title=witness.title or work.title,
            catalogue=witness.catalogue,
        )

    # -- corrections -----------------------------------------------------------------------------

    def corrections_dir(self) -> Path:
        return self.root / CORRECTIONS

    def corrections(self) -> list[dict[str, Any]]:
        """Every correction the source holds, with its unit and page taken from its path."""
        found: list[dict[str, Any]] = []
        root = self.corrections_dir()
        if not root.is_dir():
            return found
        for source in sorted(path for path in root.iterdir() if path.is_dir()):
            for witness in sorted(path for path in source.iterdir() if path.is_dir()):
                unit = f"{source.name}/{witness.name}"
                if not UNIT.match(unit):
                    raise AinuSourceError(f"{unit} is not a unit slug")
                for file in sorted(witness.glob("p*.json")):
                    match = PAGE_FILE.match(file.name)
                    if not match:
                        raise AinuSourceError(f"unexpected corrections file: {unit}/{file.name}")
                    page = int(match.group(1))
                    records = json.loads(file.read_text(encoding="utf-8"))
                    if not isinstance(records, list) or not records:
                        raise AinuSourceError(f"expected a non-empty array: {unit}/{file.name}")
                    for record in records:
                        found.append({**record, "unit": unit, "page": page})
        return found

    def existing_ids(self) -> set[str]:
        """Every correction id the source already uses, so a proposal cannot collide with one."""
        return {str(record.get("id")) for record in self.corrections()}

    def drafts(self, proposals: Iterable[Proposal]) -> dict[Path, list[dict[str, Any]]]:
        """The files a submission would write, keyed by the path in the source repository.

        Existing records for a page are kept: a submission adds to the editorial layer rather than
        replacing it. A proposal that cannot be placed is not written here at all — the caller reports
        it as `unmappable` — because the source's build fails on a correction whose original no longer
        matches, and a review workspace must not be able to produce that.
        """
        files: dict[Path, list[dict[str, Any]]] = {}
        for proposal in proposals:
            if proposal.unmappable:
                continue
            path = proposal.path()
            if path in files:
                files[path].append(proposal.record())
                continue
            existing: list[dict[str, Any]] = []
            if (self.root / path).exists():
                loaded = json.loads((self.root / path).read_text(encoding="utf-8"))
                existing = [dict(record) for record in loaded]
            files[path] = [*existing, proposal.record()]
        return files

    def validate(
        self, proposals: Iterable[Proposal], *, pages: dict[tuple[str, int], str] | None = None
    ) -> list[str]:
        """Every reason these proposals would not survive the source's validator.

        `pages` maps `(unit, page)` to the page's transcription text as the source holds it. Without
        it the text cannot be checked here, and the caller is told so rather than being told the
        proposal is fine: the exact-match rule is the source's, and a proposal that has not been
        checked against it is unverified, not valid.
        """
        problems: list[str] = []
        known = self.existing_ids()
        seen: set[str] = set()
        for proposal in proposals:
            if proposal.unmappable:
                problems.append(f"{proposal.id}: {proposal.unmappable}")
                continue
            if not UNIT.match(proposal.unit):
                problems.append(f"{proposal.id}: {proposal.unit!r} is not a unit slug")
            if proposal.id in known:
                problems.append(f"{proposal.id}: the source already has a correction with this id")
            if proposal.id in seen:
                problems.append(f"{proposal.id}: proposed twice in one submission")
            seen.add(proposal.id)
            try:
                proposal.record()
            except AinuSourceError as exc:
                problems.append(f"{proposal.id}: {exc}")
                continue
            if pages is None:
                problems.append(
                    f"{proposal.id}: not checked against the source text; pass the page transcription"
                )
                continue
            text = pages.get((proposal.unit, proposal.page))
            if text is None:
                problems.append(f"{proposal.id}: no transcription supplied for {proposal.unit} p{proposal.page}")
                continue
            reason = place(transcription_lines(text), line=proposal.line or 0,
                           original=proposal.original or "")
            if reason:
                problems.append(f"{proposal.id}: {reason}")
        return problems
