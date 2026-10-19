"""Crop geometry flags that ainu-records measured for the crops it cut.

ainu-records measures every crop it cuts (`src/lib/character-quality.ts`) and writes the result per
copy to `data/characters/<work>--<copy>/quality.json`: the crop's box, its margins, ink and blank band,
and the flags those measurements raise: `tall-crop`, `wide-crop`, `blank-band`, `large-margin` and
`sparse-ink`. An `ar:` unit is one of those crops, `ar:<work>--<copy>:<id>`, so the atlas can carry the
same flags.

A flag describes one box. It is kept with the box it was measured on, and holds only while the unit
still has that box: a crop recut since then, here or by a reviewer, has no flags until ainu-records
measures it again. The flags are measurements, not verdicts. On 60 flagged live crops sampled on
19 Oct 2026 a blank band, with or without a tall crop, mostly marked a real fault (a box over bare
paper, a frame rule, or two characters), while a tall crop alone was often a sound long stroke such
as ー or し. Only `sparse-ink` keeps a crop out of the quiz: ainu-records treats such a crop as empty.

`run` writes the flags to `<dataset>/crop-geometry.json`; the export puts them in each crop's `data`
as `geometry: {flags, box}`.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

METHOD = "ainu-records-character-quality-v1"
FILE = "crop-geometry.json"
FLAGS = ("tall-crop", "wide-crop", "blank-band", "large-margin", "sparse-ink")
#: Flags that keep a crop out of the quiz.
OUT_OF_QUIZ = frozenset({"sparse-ink"})


def load(dataset: Path) -> dict[str, dict]:
    """Each flagged unit's flags and the box they were measured on, by unit id."""
    path = Path(dataset) / FILE
    try:
        stat = path.stat()
    except OSError:
        return {}
    key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    if key not in _CACHE:
        _CACHE.clear()
        _CACHE[key] = json.loads(path.read_text())["units"]
    return _CACHE[key]


_CACHE: dict[tuple, dict[str, dict]] = {}


def current(mark: dict | None, box: dict | None) -> dict | None:
    """`mark` while `box` is still the box it was measured on, else None."""
    return mark if mark and box and mark["box"] == {k: box[k] for k in ("x", "y", "w", "h")} else None


def out_of_quiz(mark: dict | None) -> bool:
    return bool(mark and OUT_OF_QUIZ & set(mark["flags"]))


def records(root: Path) -> dict[str, dict]:
    """The flagged crops of an ainu-records checkout (or of a copy of its `data/characters`)."""
    base = root / "data" / "characters" if (root / "data" / "characters").is_dir() else root
    found: dict[str, dict] = {}
    for path in sorted(base.glob("*/quality.json")):
        report = json.loads(path.read_text())
        for record in report["records"]:
            flags = [flag for flag in record["flags"] if flag in FLAGS]
            if flags:
                x, y, w, h = record["box"]
                found[f"ar:{path.parent.name}:{record['id']}"] = {"flags": flags, "box": {"x": x, "y": y, "w": w, "h": h}}
    return found


def run(dataset: Path, root: Path, *, apply: bool = False) -> dict[str, Any]:
    """Match `root`'s flagged crops to the dataset's `ar:` units, and with `apply` write `FILE`.

    Only a unit whose box is the measured box takes its flags; the file replaces any earlier one."""
    from .review.store import Store

    store = Store(dataset)
    units = {unit.id: unit for unit, _ in store.unit_snapshot() if unit.id.startswith("ar:")}
    counts: Counter[str] = Counter()
    kept: dict[str, dict] = {}
    for identity, mark in records(root).items():
        unit = units.get(identity)
        if unit is None:
            counts["no unit"] += 1
        elif current(mark, unit.box.model_dump() if unit.box else None) is None:
            counts["box changed since"] += 1
        else:
            kept[identity] = mark
            counts["flagged"] += 1
            counts.update(mark["flags"])
    if apply:
        (Path(dataset) / FILE).write_text(json.dumps({"method": METHOD, "units": kept}, ensure_ascii=False, sort_keys=True) + "\n")
    return {"method": METHOD, "counts": dict(counts)}
