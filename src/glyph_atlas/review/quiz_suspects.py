"""Suspect crops for Quick review: crops the character classifier reads as something else.

The classifier gives every crop a probability for each class it knows. A crop is a suspect when the
classes consistent with its label hold less than `FLAG_BELOW` of that probability and the classifier
has something definite to say: the label is kana, which most of its training crops are, or one other
character (or merged family) holds at least `READS_AS` of the probability, and the suspect names it.
A kanji the classifier merely finds unfamiliar is not a suspect: on the Quick review dataset those
were nearly all good crops in a hand or a print the training set lacked.

Measured on 2026-09-26 against the hosted reviews of that dataset (149 crops a reviewer marked wrong,
2,151 left unflagged or confirmed), the rule marks 104 of the wrong crops and 9 of the others. Of the
19,291 crops it marks 4,088, and 4,021 of those the alignment-repair pass had already withheld from
rounds. Of the 67 left to be dealt, about one in five is a plain error on inspection (a half
character, a blank page edge, 知 filed as 如); reviewers found 5 errors among some 1,900 dealt crops
of the same dataset. The nearest-neighbour distance within a character was tried beside the
classifier and dropped: among dealable crops it flagged a tenth of them, mostly good ones.

The marks are an aid for a person, never a verdict: a suspect is still judged in its round like any
other crop. They are computed ahead of time, like the shape order, and kept with what they describe:
`<dataset>/quiz-suspects.json` for a review dataset, and one file for the published corpus glyphs.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import numpy as np

METHOD = "classifier-label-probability-v1"
FILE = "quiz-suspects.json"
#: Below this much probability on the label's classes, a crop is a suspect.
FLAG_BELOW = 0.05
#: A suspect names the character the classifier reads it as when that character holds this much.
READS_AS = 0.5


def load(dataset: Path) -> dict[str, dict]:
    """Each suspect crop's mark by unit id, or nothing when no marks were computed for `dataset`."""
    path = Path(dataset) / FILE
    try:
        stat = path.stat()
    except OSError:
        return {}
    return _load(str(path.resolve()), stat.st_mtime_ns, stat.st_size)


_CACHE: dict[tuple, dict[str, dict]] = {}


def _load(path: str, stamp: int, size: int) -> dict[str, dict]:
    key = (path, stamp, size)
    if key not in _CACHE:
        _CACHE.clear()
        _CACHE[key] = json.loads(Path(path).read_text())["suspects"]
    return _CACHE[key]


def kana(label: str) -> bool:
    """Whether a label is a kana, the script nearly all of the classifier's training crops are in."""
    name = unicodedata.name(label[0], "") if label else ""
    return "HIRAGANA" in name or "KATAKANA" in name or name.startswith("HENTAIGANA")


class Labels:
    """Which classes a label accepts, and which family each class belongs to.

    A label accepts a class that is the label itself, a class whose family merges it with the label
    (仮 and 假 are one family to the classifier, which saw them under one reading), and a kana class
    that reads as the label. Families are what the suggestions list merges, so a crop is never read
    "as" a character the classifier cannot tell from its label.
    """

    def __init__(self, classes: list[str]):
        from .. import refs
        from .atlas import reading_of
        from .suggestions import _class_family

        self.classes = classes
        self.chars = [refs.to_char(name) if name.startswith("U+") else None for name in classes]
        families = [_class_family(name) for name in classes]
        keys = sorted({family for family, _ in families})
        index = {key: i for i, key in enumerate(keys)}
        self.family = np.array([index[family] for family, _ in families])
        self.members = [set(members) for _, members in families]
        # The character a family is read as: its own first member, or none for `other`.
        names: dict[int, str | None] = {}
        for (family, members), char in zip(families, self.chars, strict=True):
            names.setdefault(index[family], members[0] if members else char)
        self.names = [names[i] for i in range(len(keys))]
        self.readings = [reading_of(char) if char else None for char in self.chars]
        self._masks: dict[str, np.ndarray] = {}

    def mask(self, label: str) -> np.ndarray:
        """The classes `label` accepts: itself, its merged family, and the kana that read as it does.

        Reading is compared both ways, so katakana ヘ accepts hiragana へ as へ accepts ヘ: the two are
        one shape, and a classifier trained mostly on hiragana is not doubting the label when it
        names the other.
        """
        if label not in self._masks:
            from .atlas import reading_of

            readings = {label, reading_of(label)} - {None}
            self._masks[label] = np.array([char == label or label in members or reading in readings
                                           for char, members, reading in zip(self.chars, self.members, self.readings,
                                                                             strict=True)])
        return self._masks[label]

    def judge(self, probabilities: np.ndarray, labels: list[str]) -> list[dict | None]:
        """A mark for each crop that is a suspect, `None` for each that is not.

        `probabilities` is one softmax row per crop, in class order.
        """
        names = sorted(set(labels))
        accepts = np.stack([self.mask(label) for label in names]).astype(probabilities.dtype)
        index = {label: i for i, label in enumerate(names)}
        which = np.array([index[label] for label in labels])
        known = accepts.any(1)[which]
        p = (probabilities @ accepts.T)[np.arange(len(labels)), which]
        marks: list[dict | None] = [None] * len(labels)
        for row in np.flatnonzero(known & (p < FLAG_BELOW)):
            totals = np.bincount(self.family, weights=probabilities[row], minlength=len(self.names))
            top = int(totals.argmax())
            name, label = self.names[top], labels[row]
            reads = name if name and name != label and totals[top] >= READS_AS else None
            if reads or kana(label):
                marks[row] = {"p": round(float(p[row]), 4), "reads_as": reads}
        return marks


def current(mark: dict | None, label: str, box: dict | None) -> dict | None:
    """A stored mark as a listing shows it, while the crop still has the label and box it was made for.

    A crop re-cut or relabelled since the marks were computed is no longer what the classifier saw.
    """
    if not mark or mark.get("label") != label or not same_box(mark.get("box"), box):
        return None
    return {"p": mark["p"], "reads_as": mark["reads_as"]}


def same_box(a: dict | None, b: dict | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return all(abs(float(a[k]) - float(b[k])) < 1e-6 for k in "xywh")


def _write(target: Path, suspects: dict[str, dict], scored: int, inputs: dict) -> dict[str, Any]:
    revision = hashlib.sha256(json.dumps({"method": METHOD, "inputs": inputs, "suspects": suspects},
                                         sort_keys=True).encode()).hexdigest()[:16]
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(json.dumps({"revision": revision, "method": METHOD, "flag_below": FLAG_BELOW,
                                     "reads_as": READS_AS, "scored": scored, "suspects": suspects},
                                    ensure_ascii=False, sort_keys=True))
    os.replace(temporary, target)
    return {"scored": scored, "suspects": len(suspects), "revision": revision}


def _digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def compute(dataset: Path, *, checkpoint: Path, classes: Path) -> dict[str, Any]:
    """Mark the suspects among every crop of `dataset`, and write the marks into the dataset."""
    from PIL import Image

    from ..classify import preprocess, read_classes
    from ..form_clusters import Encoder
    from .quiz_shapes import _crops

    groups, images, boxes = _crops(dataset)
    if not images:
        # Every crop is cut from a cached page image; a cache that holds none leaves nothing to mark.
        raise RuntimeError(f"{dataset} shows no crops; is $GLYPH_ATLAS_CACHE the image cache it was built with?")
    encoder = Encoder(checkpoint, classes)
    labels = Labels(read_classes(classes))
    ids = [identity for members in groups.values() for identity in members]
    label_of = {identity: label for label, members in groups.items() for identity in members}
    suspects: dict[str, dict] = {}
    for start in range(0, len(ids), 512):
        batch_ids = ids[start:start + 512]
        batch = []
        for identity in batch_ids:
            with Image.open(io.BytesIO(images[identity])) as picture:
                batch.append(np.asarray(preprocess(picture.convert("L")), dtype=np.uint8))
        _features, probabilities = encoder.classify(np.stack(batch))
        for identity, mark in zip(batch_ids, labels.judge(probabilities, [label_of[i] for i in batch_ids]),
                                  strict=True):
            if mark:
                suspects[identity] = {**mark, "label": label_of[identity], "box": boxes[identity]}
    inputs = {"crops": hashlib.sha256("\n".join(sorted(ids)).encode()).hexdigest(),
              "checkpoint": _digest(checkpoint), "classes": _digest(classes)}
    return _write(Path(dataset) / FILE, suspects, len(ids), inputs)


def corpus_glyphs(root: Path, corpora: Iterable[str]) -> Iterator[dict]:
    """Every active character glyph of `corpora` with a label, as `form_clusters.glyphs` gives them.

    The label is the one a published corpus glyph is dealt under: the character its code point names,
    or the source's own transcription when it has none.
    """
    import pyarrow.compute as pc
    import pyarrow.dataset as ds

    from ..corpus import sources
    from ..corpus.index import _char_of_codepoint

    found = {corpus.name: corpus for corpus in sources.discover(root)}
    for name in corpora:
        corpus = found.get(name)
        if corpus is None or not corpus.parquet_files("units"):
            continue
        pages = {}
        if corpus.parquet_files("pages"):
            table = ds.dataset([str(p) for p in corpus.parquet_files("pages")], format="parquet")
            pages = dict(zip(*table.to_table(columns=["id", "image"]).to_pydict().values(), strict=True))
        units = ds.dataset([str(p) for p in corpus.parquet_files("units")], format="parquet")
        where = (pc.field("active") & (pc.field("kind") == "char")
                 & (pc.field("box").is_valid() | pc.field("crop").is_valid()))
        columns = ["id", "page_id", "box", "crop", "unicode"] + (["text_source"] if "text_source" in units.schema.names else [])
        table = units.to_table(columns=columns, filter=where)
        box = table.column("box").combine_chunks()
        corners = [box.field(k).to_pylist() for k in "xywh"]
        valid = box.is_valid().to_pylist()
        values = {column: table.column(column).to_pylist() for column in columns if column != "box"}
        for row, identity in enumerate(values["id"]):
            label = _char_of_codepoint(values["unicode"][row]) or (values.get("text_source") or [None] * len(valid))[row]
            if not label:
                continue
            page = pages.get(values["page_id"][row]) if valid[row] else None
            yield {"id": identity, "corpus": name, "label": label, "page": page,
                   "box": tuple(c[row] for c in corners) if page else None, "crop": None if page else values["crop"][row]}


def compute_corpus(root: Path, target: Path, *, checkpoint: Path, classes: Path,
                   corpora: Iterable[str] | None = None, workers: int = 8) -> dict[str, Any]:
    """Mark the suspects among the corpus glyphs whose pixels are on disk, into the file `target`."""
    from ..classify import read_classes
    from ..corpus.index import UNIT_CORPORA
    from ..form_clusters import Encoder, Pixels, _embed, _file_jobs

    corpora = tuple(corpora or UNIT_CORPORA)
    pixels = Pixels(root)
    located: dict[str, tuple[Path, tuple | None]] = {}
    label_of: dict[str, str] = {}
    unheld: dict[str, int] = defaultdict(int)
    for glyph in corpus_glyphs(root, corpora):
        found = pixels(glyph)
        if found is None:
            unheld[glyph["corpus"]] += 1
            continue
        located[glyph["id"]] = found
        label_of[glyph["id"]] = glyph["label"]
    if not located:
        raise RuntimeError(f"no glyph of {', '.join(corpora)} has its pixels under {root}")
    encoder = Encoder(checkpoint, classes)
    labels = Labels(read_classes(classes))
    suspects: dict[str, dict] = {}
    scored = 0
    print(f"{len(located)} glyphs located, {sum(unheld.values())} without pixels", file=sys.stderr, flush=True)
    for batch_ids, (_features, probabilities) in _embed(_file_jobs(located), encoder.classify, workers=workers):
        scored += len(batch_ids)
        if scored // 50000 != (scored - len(batch_ids)) // 50000:
            print(f"{scored} scored, {len(suspects)} suspects", file=sys.stderr, flush=True)
        for identity, mark in zip(batch_ids, labels.judge(probabilities, [label_of[i] for i in batch_ids]),
                                  strict=True):
            if mark:
                box = located[identity][1]
                suspects[identity] = {**mark, "label": label_of[identity],
                                      "box": dict(zip("xywh", box, strict=True)) if box else None}
    inputs = {"corpora": list(corpora), "located": hashlib.sha256("\n".join(sorted(located)).encode()).hexdigest(),
              "checkpoint": _digest(checkpoint), "classes": _digest(classes)}
    target.parent.mkdir(parents=True, exist_ok=True)
    return {**_write(target, suspects, scored, inputs), "unheld": dict(unheld)}

