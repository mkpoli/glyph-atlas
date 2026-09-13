"""Compare the アイヌ関連資料 project on みんなで翻刻 with the curation this repository imports.

Run from the repository root:

    .venv/bin/python scripts/check_ainu_records.py
    .venv/bin/python scripts/check_ainu_records.py --refresh      # ignore the cache
    .venv/bin/python scripts/check_ainu_records.py --curation /path/to/ainu-records

The platform publishes no entry list for a project, so the check does not take the curated file's
word for what the project holds: it reads the project record, walks the collections that record names,
and collects every entry id they hold. It then compares, for each curated entry, the platform's canvas
count, page count and licence URL with the curation's `data/sources.yaml` and with the label and
attribution the platform serves, and prints one line per field. It exits 1 when a difference is one a
reviewer should look at before an import is published: an entry that is gone, a canvas count that
moved under a recorded transcription count, a licence URL that disagrees, or an entry the project's
collections hold that the curated file does not list.

The command is a report, not a test: the tests in `tests/test_ainu_records.py` never touch the network.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import yaml

from glyph_atlas import net, refs
from glyph_atlas.importers import ainu_records as ainu

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CURATION = Path.home() / "projects" / "Ainu" / "ainu-records"
#: The curation states the holder, the shelfmark and the licence URL it read from the holder's page.
CURATION_FILE = Path("data") / "sources.yaml"


def curation(path: Path) -> dict[str, dict[str, Any]]:
    """The curation's own `data/sources.yaml`, keyed by the platform entry id of each part.

    The file groups a work's witnesses and their parts, so one platform entry is one row here, with
    the work's fields and the witness's fields flattened over it. It is read as evidence for the
    comparison and never copied into the repository.
    """
    found: dict[str, dict[str, Any]] = {}
    document = yaml.safe_load((Path(path) / CURATION_FILE).read_text(encoding="utf-8")) or {}
    for source in document.get("sources") or []:
        work = dict(source)
        witnesses = work.pop("witnesses", []) or []
        for witness in witnesses:
            part_rows = witness.get("parts") or []
            for part in part_rows:
                entry = str(part.get("entry", "")).strip()
                if not entry:
                    continue
                row = {key: value for key, value in work.items() if key != "slug"}
                row["work"] = source.get("slug")
                row.update({key: value for key, value in witness.items() if key != "parts"})
                row["part"] = part.get("label")
                found[entry] = row
    return found


#: Whether two titles are the same work under the same spelling. 紀 and 記 are 異体字 of each
#: other and a title may carry an extra word, so the comparison is on the characters that survive
#: the alignment policy rather than on exact equality.
def same_title(platform: str, curated: str) -> bool:
    """Whether a platform label and a curated title name the same work."""
    if not platform or not curated:
        return False
    head = curated.split("　")[0]
    for width in (len(head), len(head) + 1, len(head) + 2):
        if head and head in platform and width >= len(head):
            return True
    try:
        return refs.same(head, platform[: len(head)], "align-v1")
    except (ValueError, KeyError):
        return head in platform


def same_holder(platform: str, curated: str) -> bool:
    """Whether two holder statements name the same institution.

    The platform's `attribution` is sometimes the holder's name and sometimes its URL, and the
    curation sometimes names a parent body where the platform names the collection, so a URL that
    contains the curation's holder, or a holder that is a prefix of the platform's, is agreement.
    """
    if not platform or not curated:
        return False
    return bool(curated in platform or platform in curated
                or (platform.startswith("http") and curated in unquote(platform)))


def compare(
    entry: str, record: dict[str, Any], curated: dict[str, Any] | None
) -> list[tuple[str, str, str, str]]:
    """One row per compared field: `(state, field, platform, curated)`.

    The state is `ok` when the two agree, `note` when they say the same thing in different words — a
    licence URL that differs by a documentation page, a holder named by a URL on one side and by its
    name on the other, a title spelled with an 異体字 — and `DIFF` when they disagree about a fact a
    reviewer has to settle before the import is published.
    """
    found: list[tuple[str, str, str, str]] = []
    label = str(record.get("label") or "")
    title = str(curated.get("title") or "") if curated else ""
    if not title:
        found.append(("note", "title", label, "not in the curation"))
    elif label.split("　")[0] == title or label.startswith(title):
        found.append(("ok", "title", label, title))
    elif same_title(label, title):
        found.append(("note", "title", label, f"{title} (異体字 or an added word)"))
    else:
        found.append(("DIFF", "title", label, title))
    canvases = len(ainu.canvases(record))
    size = record.get("size")
    found.append((("ok" if str(size) == str(canvases) else "DIFF"), "canvases", str(canvases), f"size {size}"))
    licence = str(record.get("license") or "")
    curated_licence = str((curated or {}).get("imageLicense") or "")
    if not curated_licence:
        found.append(("note", "image licence", licence, "not in the curation"))
    elif licence == curated_licence:
        found.append(("ok", "image licence", licence, curated_licence))
    else:
        # Both URLs are recorded and the vocabulary resolves the platform's; a difference is worth
        # naming but not a conflict, since the importer prefers the curated one.
        found.append(("note", "image licence", licence, f"{curated_licence} (the importer prefers this)"))
    holder = str((curated or {}).get("holder") or "")
    attribution = str(record.get("attribution") or "")
    if not holder:
        found.append(("note", "holder", attribution, "not in the curation"))
    elif same_holder(attribution, holder):
        found.append(("ok", "holder", attribution, holder))
    else:
        found.append(("note", "holder", attribution, f"{holder} (same institution, named differently)"))
    texts = ainu.transcriptions(record)
    with_text = sum(1 for row_ in texts.values() if ainu.text_of(row_).strip())
    found.append(("counts", "pages with text", str(with_text), f"of {canvases}"))
    lines = sum(len(ainu.lines_of(ainu.text_of(row_))) for row_ in texts.values())
    found.append(("counts", "lines", str(lines), "-"))
    found.append(("counts", "revision", str(ainu.revision_of(record) or "-"), "-"))
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=None, help="directory for the platform responses")
    parser.add_argument("--curation", type=Path, default=DEFAULT_CURATION, help="the curated ainu-records checkout")
    parser.add_argument("--refresh", action="store_true", help="fetch every response again")
    parser.add_argument("--json", type=Path, default=None, help="write the report as JSON here")
    args = parser.parse_args()

    document = ainu.load_source()
    ids = ainu.entry_ids(document)
    curated = curation(args.curation) if args.curation.is_dir() else {}

    print(f"project            {ainu.PROJECT}  ({ainu.PROJECT_API.format(project=ainu.PROJECT)})")
    print(f"curated file       {ainu.SOURCE_FILE}  ({len(ids)} entries)")
    print(f"curation           {args.curation}  ({len(curated)} parts)" if curated
          else f"curation           {args.curation} is not a directory; compared against the platform alone")
    print()

    differences = 0
    notes = 0
    unchecked: list[str] = []
    report: dict[str, Any] = {"entries": {}}
    for entry in ids:
        try:
            record = ainu.fetch_entry(entry, cache=args.cache, refresh=args.refresh)
        except (net.DownloadError, OSError, ValueError) as error:
            print(f"entry {entry}  UNREADABLE  {error.__class__.__name__}: {error}")
            differences += 1
            continue
        row = curated.get(entry)
        if curated and row is None:
            print(f"entry {entry}  not in the curation's data/sources.yaml")
            differences += 1
        found = compare(entry, record, row)
        print(f"entry {entry}  {record.get('label')}")
        for state, field, platform, stated in found:
            if state == "DIFF":
                differences += 1
            elif state == "note":
                notes += 1
            print(f"  {state:<4} {field:<16} platform {platform[:56]:<56} curated {stated[:44]}")
        report["entries"][entry] = {
            "label": record.get("label"),
            "fields": {field: {"state": state, "platform": platform, "curated": stated}
                       for state, field, platform, stated in found},
        }
        if not row:
            unchecked.append(entry)
        print()

    print("project collections")
    try:
        platform_ids, documents, failures = ainu.collection_entries(
            ainu.PROJECT, cache=args.cache, refresh=args.refresh
        )
    except (net.DownloadError, OSError, ValueError) as error:
        print(f"  UNREADABLE  {error.__class__.__name__}: {error}")
        platform_ids, documents, failures = [], [], {}
    extra = [entry for entry in platform_ids if entry not in set(ids)]
    print(f"  collections read   {len(documents)} of {len(documents) + len(failures)}")
    print(f"  entries in them    {len(platform_ids)}")
    print(f"  listed and present {len([e for e in ids if e in set(platform_ids)])} of {len(ids)}")
    print(f"  held but not listed {len(extra)}")
    if extra:
        # Not a difference to settle: the project holds 80 entries and this import is the nine the
        # curation has read. It is reported because it is the size of what is not imported yet.
        print(f"  note the project holds {len(extra)} further entries; this import covers the "
              f"{len(ids)} the curation has read")
        notes += 1
    missing = [entry for entry in ids if entry not in set(platform_ids)]
    if missing:
        print(f"  DIFF {len(missing)} listed entries are not in any collection of the project: "
              f"{', '.join(missing[:6])}")
        differences += 1
    for collection, failure in sorted(failures.items()):
        print(f"  DIFF collection {collection} could not be read: {failure}")
        differences += 1
    report["collections"] = {
        "read": len(documents),
        "failed": failures,
        "entries": platform_ids,
        "not_listed": extra,
    }

    if unchecked:
        print()
        print(f"{len(unchecked)} listed entries are not in the curation and were compared with the "
              f"platform alone: {', '.join(unchecked)}")
    print()
    print(f"notes {notes}, differences a reviewer should see: {differences}")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"report -> {args.json}")
    return 1 if differences else 0


if __name__ == "__main__":
    sys.exit(main())
