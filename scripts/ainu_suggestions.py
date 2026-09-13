"""Can the classifier propose a reading for a character crop without being told the transcription?

A reviewer correcting a reading needs suggestions they can click instead of typing one. The obvious
source is the `candidates` already recorded on a unit — and it is the wrong one. The aligner knows the
transcription, so for a unit it identified from the text it can write a candidate at p=1.0 without
looking at the crop at all: 5,037 of the 5,075 units that carry such a candidate are that echo, and
5,069 of them equal the unit's own `unicode` field. A suggestion drawn from it would restate the
reading the reviewer is looking at, and on this corpus the reading is exactly what the alignment's
ordering makes least trustworthy.

What is left is the classifier, which sees only the crop. Measured on a stratified sample of 120 boxed
units it agrees with the assigned code point **18 percent** of the time, and the disagreement is
confident rather than hedged: the median top probability on a wrong answer is 0.97. Split by what the
aligner thought it knew:

| unit classification | units | top-1 agrees | median top probability |
| --- | --- | --- | --- |
| identified | 40 | 0% | 0.97 |
| ambiguous | 40 | 0% | 0.84 |
| unassessed | 40 | 53% | 0.96 |

The split is the diagnosis, and it is not about the crops. Padding a crop should recover a character
whose box is a little tight; over 90 units the agreement **falls** as padding grows — 18 percent at the
detector's own box, 12 at 15 percent pad, 4 at 30, 0 at 60 — while the top probability stays high. So
the boxes are a little tight if anything, and the classifier is confidently wrong on ink outside what
it was trained on. It was trained on printed books of the CODH and Honkoku-Lines corpora; these are
cursive manuscripts at a different scale and hand, which this project's own report already gives as the
reason the accepted share here is 9 percent against the pilot's 17.

So a suggestion control has to be one of two things and not the third. It can offer the line's own
characters as the alternatives — those are the source's text read off the page, and the misordering
above does not touch them — or it can stay empty. What it must not do is present the classifier's top
classes as if they were readings: on 82 percent of these crops the top class is not the character, and
a reviewer clicking the most confident wrong answer is worse off than one who types. Rebuilding the
suggestions after the ordering is fixed is the version worth measuring again, because 53 percent on
units with no text label is the classifier working on ink alone.

It is read-only: the tables, the cached images and the ONNX model are read.

    .venv/bin/python scripts/ainu_suggestions.py [--dataset work/ainu-records] [--sample 120]
"""

from __future__ import annotations

import argparse
import random
import statistics
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from PIL import Image

from glyph_atlas import classify, images, tables
from glyph_atlas.schema import Page, Unit

#: The classifier's abstention class, which is a real answer and not a low score.
OTHER = "other"


def reading_of(unit: Unit) -> str:
    return unicodedata.normalize("NFC", unit.reading or unit.text_source or "").strip()


def codepoint_of(unit: Unit) -> str | None:
    """The unit's own code point, as the class names spell it.

    `Unit.unicode` is already written that way (`U+3057`), so it is used as it stands; a unit that
    carries none is spelled from its reading when that reading is a single code point.
    """
    text = (unit.unicode or "").strip()
    if text.startswith("U+"):
        return text
    reading = reading_of(unit)
    if len(reading) == 1 and not unicodedata.combining(reading):
        return f"U+{ord(reading):04X}"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("work/ainu-records"))
    parser.add_argument("--onnx", type=Path,
                        default=Path("models/classifier/artifacts/classifier.onnx"))
    parser.add_argument("--classes", type=Path, default=Path("models/classifier/classes.json"))
    parser.add_argument("--sample", type=int, default=120)
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    pages = {page.id: page for page in tables.read(args.dataset / "pages.parquet", Page)}
    units = [unit for unit in tables.read(args.dataset / "units.parquet", Unit)
             if unit.active and unit.box is not None]
    # Stratified by what the aligner thought it knew, because that is what decides whether a stored
    # candidate is an echo: an identified unit was labelled from the text, an unassessed one was not.
    by_class: dict[str, list[Unit]] = defaultdict(list)
    for unit in units:
        if codepoint_of(unit):
            by_class[str(unit.classification)].append(unit)
    rng = random.Random(args.seed)
    chosen: list[Unit] = []
    per_class = max(1, args.sample // max(1, len(by_class)))
    for key in sorted(by_class):
        pool = by_class[key]
        rng.shuffle(pool)
        chosen.extend(pool[:per_class])
    print(f"{len(chosen)} units sampled from {len(units)} boxed units, "
          f"by classification {dict(Counter(str(u.classification) for u in chosen))}")

    model = classify.Classifier(args.onnx, classes=classify.read_classes(args.classes))
    print(f"classifier classes {len(model.classes)}, providers {model.providers()}")
    print()

    records = []
    for unit in chosen:
        page = pages.get(unit.page_id or "")
        if page is None:
            continue
        path = images.path_for(page.image)
        if path is None:
            continue
        with Image.open(path) as handle:
            handle.load()
            box = unit.box
            crop = handle.crop((box.x, box.y, box.x + box.w, box.y + box.h)).convert("RGB")
        probabilities = model.probabilities(crop)
        order = probabilities.argsort()[::-1]
        top = [(model.classes[index], float(probabilities[index])) for index in order[: args.top]]
        own = codepoint_of(unit)
        records.append({"unit": unit.id, "reading": reading_of(unit), "own": own,
                        "classification": str(unit.classification), "top": top})

    if not records:
        print("no unit could be cropped; the page images may not be cached")
        return 1

    agree = disagree = 0
    confident_disagree = []
    for record in records:
        best, probability = record["top"][0]
        if best == record["own"]:
            agree += 1
        else:
            disagree += 1
            if best != OTHER and probability >= 0.25:
                confident_disagree.append((probability, record))
    print("the classifier's top class against the unit's own code point")
    print(f"  agrees:    {agree}/{len(records)}  ({agree / len(records):.0%})")
    print(f"  disagrees: {disagree}/{len(records)}")
    abstained = sum(1 for r in records if r["top"][0] == OTHER)
    print(f"  abstains (`other`): {abstained}/{len(records)}  ({abstained / len(records):.0%})")
    print()

    print("by classification of the unit:")
    for key in sorted({r["classification"] for r in records}):
        subset = [r for r in records if r["classification"] == key]
        hits = sum(1 for r in subset if r["top"][0] == r["own"])
        others = sum(1 for r in subset if r["top"][0] == OTHER)
        best = statistics.median([r["top"][0][1] for r in subset])
        print(f"  {key:<12}{len(subset):>5} units, top-1 agrees {hits / len(subset):>5.0%}, "
              f"abstains {others / len(subset):>4.0%}, median top probability {best:.2f}")
    print()

    print(f"a suggestion is only useful when the classifier is sure and the reading differs: "
          f"{len(confident_disagree)} of {len(records)} sampled units")
    for probability, record in sorted(confident_disagree, reverse=True)[:10]:
        proposals = ", ".join(f"{name} {p:.2f}" for name, p in record["top"][:3])
        print(f"  assigned {record['reading'] or '·'!r:>6} ({record['own']}) -> {proposals}")
    print()
    distinct = {r["own"] for r in records}
    print(f"the sample covers {len(distinct)} distinct assigned code points")
    covered = sum(1 for r in records
                  if any(name in {x["own"] for x in records} for name, _ in r["top"][:3]))
    print(f"{covered}/{len(records)} units have at least one of their top three in the sample's own "
          f"inventory, so most suggestions fall outside what the transcription uses")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
