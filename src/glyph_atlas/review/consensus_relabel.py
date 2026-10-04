"""Relabel crops that look like another character's crops and not like their own.

The similar-crop index (`glyph_atlas.similar`) holds every published crop as the classifier's
penultimate features. A crop filed under the wrong character sits among crops of the character it
shows: its nearest neighbours carry another label, with high similarity, and the classifier reads it
as that label too. A crop that is no character at all (a stroke, a dot, two characters in one box,
blank paper) also sits among crops of some other label, since its nearest whole shape is still a
character: a stroke's is し. The neighbours cannot tell the two apart, so a box test decides first
whether the crop holds one whole character.

For each crop of the dataset that the index holds:

- **neighbours.** Its `K` nearest crops by cosine similarity, leaving out crops of its own page (the
  Ainu records and the detector cut the same glyph twice). A neighbour agrees with the crop's label
  when the two are one character, one kana (katakana ナ and hiragana な), variants of one character,
  or a kana and a kanji it is written with (者 and は, 川 and つ). The best other label is the one
  most neighbours carry; its share, its mean similarity and the documents its crops come from are
  kept;
- **classifier.** The probability the classifier gives the crop's label and the other label, read
  from the display crop the dataset publishes, with the families and kana readings
  `quiz_suspects.Labels` accepts for each;
- **one whole character.** The box against the median box of its line (of its page when the line
  has fewer than `PEERS` boxes): a box `JOINED` times the median along one side and `ELONGATION`
  times longer that way than across, or `MAX_AREA` times the median area, holds two characters; one
  under `MIN_AREA` of the median area or `MIN_SIDE` of the median along a side is a part of one. A
  crop whose dark pixels are under `BLANK_INK` of the classifier's square is blank paper.

A crop is relabelled to the other label when at least `share` of its neighbours carry it with mean
similarity `similarity` or more, at most `own` carry its label, the neighbours come from at least
`documents` documents, the classifier gives the other label `p_target` or more and `margin` more
than the crop's own, and the box holds one whole character. `SETTINGS` names three bars.

Measured on 2026-10-04 (docs/reports/consensus-relabel.md) against the 1,105 crops reviewed on the
site: at the `default` bar the rule relabels 50 reviewed crops, 43 to the reviewer's correction, 1 to
another character, 1 that the reviewer confirmed, 2 the reviewer reported as bad crops and 3 the
reviewer reported as a wrong reading; it finds 43 of the 136 wrong-character reports in the index.
Of 48 random relabels among unreviewed crops, the 34 of the collection (ar:, hk:, ex:) were 30 plainly
right and 4 uncertain by eye; the 14 HI Lab glyphs were 1 right, 11 uncertain and 2 wrong.

A crop a person reviewed (any event of a role other than `model`), one named in `protect` (the ids
the site holds reviews for, or subjects of observed or editorial ledger claims), and one whose review
state is anything but `machine` is never relabelled. The relabel is a model event in the dataset's
journal with the evidence that chose it, and `undo` restores what each relabel replaced.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from functools import cache, lru_cache
from pathlib import Path
from statistics import median
from typing import Any

import numpy as np

METHOD = "neighbour-consensus-v1"
#: Neighbours that vote.
K = 10
#: Neighbours named in a relabel's evidence.
NAMED = 5
#: Neighbours fetched before the crops of the same page are left out.
FETCH = 64
#: A line with fewer boxes than this is measured against its page.
PEERS = 4
#: A box this many times the median along one side,
JOINED = 1.4
#: and this many times longer along that side than across it, holds two characters,
ELONGATION = 1.4
#: as does a box of this many times the median area.
MAX_AREA = 2.2
#: A box under this share of the median area, or of the median along either side, is part of one.
MIN_AREA = 0.35
MIN_SIDE = 0.35
#: Under this share of dark pixels in the classifier's square, a crop is blank paper.
BLANK_INK = 0.02
#: A grey value under this is ink.
INK_LEVEL = 128


@dataclass(frozen=True)
class Setting:
    share: float
    similarity: float
    own: int
    documents: int
    p_target: float
    margin: float


SETTINGS = {
    "strict": Setting(share=0.9, similarity=0.9, own=0, documents=2, p_target=0.9, margin=0.5),
    "default": Setting(share=0.8, similarity=0.85, own=1, documents=2, p_target=0.8, margin=0.5),
    "loose": Setting(share=0.7, similarity=0.75, own=2, documents=2, p_target=0.5, margin=0.3),
}


@dataclass(frozen=True)
class Vote:
    """What a crop's neighbours say: how many carry its label and how many the best other one."""

    own: int
    own_similarity: float
    target: str | None
    count: int
    similarity: float
    documents: int


@dataclass(frozen=True)
class Read:
    """The classifier's reading of a crop: its own label, the other label, and the share of ink."""

    p_label: float | None
    p_target: float | None
    ink: float


@cache
def _kana(label: str) -> str:
    from .atlas import kana_of

    try:
        return kana_of(label) or label
    except (KeyError, ValueError):
        return label


@cache
def _variants(label: str) -> frozenset[str]:
    from .. import refs

    try:
        return frozenset(variant for variant, _ in refs.variants(label))
    except (KeyError, ValueError):
        return frozenset()


@lru_cache(maxsize=1)
def _written_with() -> dict[str, frozenset[str]]:
    """The kana each kanji is written for: the hentaigana it is the 字母 of, and its cursive kana."""
    from .. import refs

    found: dict[str, set[str]] = defaultdict(set)
    for row in refs.characters():
        for letter in row.jibo or ():
            found[letter].update(refs.to_hiragana(reading) for reading in row.readings or ())
    for kanji, kana in refs.kana_origins().items():
        found[kanji].update(kana)
    return {kanji: frozenset(kana) for kanji, kana in found.items()}


def agree(label: str, other: str) -> bool:
    """Whether a neighbour filed under `other` agrees with a crop filed under `label`."""
    if label == other:
        return True
    a, b = _kana(label), _kana(other)
    if a == b or other in _variants(label) or label in _variants(other):
        return True
    written = _written_with()
    return a in written.get(other, ()) or b in written.get(label, ())


def vote(label: str, neighbours: Iterable[tuple[str | None, float, Any]], k: int = K) -> Vote:
    """The vote of a crop's first `k` neighbours, each (label, similarity, document).

    A neighbour without a label does not vote. The other labels are counted by the kana they read
    as, so ナ and な count as one; the target is the form most of them carry.
    """
    own, own_total = 0, 0.0
    others: dict[str, list] = {}
    for other, similarity, document in list(neighbours)[:k]:
        if not other:
            continue
        if agree(label, other):
            own += 1
            own_total += similarity
            continue
        entry = others.setdefault(_kana(other), [0, 0.0, Counter(), set()])
        entry[0] += 1
        entry[1] += similarity
        entry[2][other] += 1
        entry[3].add(document)
    if not others:
        return Vote(own, own_total / own if own else 0.0, None, 0, 0.0, 0)
    count, total, forms, documents = max(others.values(), key=lambda entry: (entry[0], entry[1]))
    return Vote(own, own_total / own if own else 0.0, forms.most_common(1)[0][0], count, total / count,
                len(documents))


def gate(box: tuple[float, float] | None, peers: Sequence[tuple[float, float]], ink: float) -> str | None:
    """Why a crop is not one whole character (`blank`, `joined` or `fragment`), or None.

    `box` is its width and height, `peers` the boxes of its line or page, itself included. Without
    enough peers only the ink is judged.
    """
    if ink < BLANK_INK:
        return "blank"
    if box is None or len(peers) < PEERS:
        return None
    width = median(w for w, _ in peers) or 1.0
    height = median(h for _, h in peers) or 1.0
    across, along = box[0] / width, box[1] / height
    area = across * along
    if ((along >= JOINED and along / max(across, 1e-6) >= ELONGATION)
            or (across >= JOINED and across / max(along, 1e-6) >= ELONGATION) or area > MAX_AREA):
        return "joined"
    if area < MIN_AREA or min(across, along) < MIN_SIDE:
        return "fragment"
    return None


def decide(found: Vote, read: Read | None, reason: str | None, setting: Setting, k: int = K) -> str:
    """`propose`, or the first test the crop fails: `vote`, `documents`, `classifier` or `gate:<reason>`."""
    if (found.target is None or found.count < setting.share * k or found.own > setting.own
            or found.similarity < setting.similarity):
        return "vote"
    if found.documents < setting.documents:
        return "documents"
    if (read is None or read.p_target is None or read.p_target < setting.p_target
            or read.p_target - (read.p_label or 0.0) < setting.margin):
        return "classifier"
    if reason:
        return f"gate:{reason}"
    return "propose"


def nearest(vectors: np.ndarray, rows: Sequence[int], pages: Sequence[str | None], k: int = K,
            fetch: int = FETCH) -> tuple[np.ndarray, np.ndarray]:
    """The `k` nearest rows of `vectors` to each of `rows` by cosine, leaving out the row itself and
    rows of its page. Rows short of `k` such neighbours are padded with -1."""
    fetch = min(fetch, len(vectors) - 1)
    found = np.full((len(rows), k), -1, dtype=np.int64)
    scores = np.zeros((len(rows), k), dtype=np.float32)
    for start, (top, values) in _top(vectors, rows, fetch):
        for offset, (indices, sims) in enumerate(zip(top, values, strict=True)):
            row = rows[start + offset]
            kept = 0
            for index, sim in zip(indices, sims, strict=True):
                if index == row or (pages[row] is not None and pages[index] == pages[row]):
                    continue
                found[start + offset, kept], scores[start + offset, kept] = index, sim
                kept += 1
                if kept == k:
                    break
    return found, scores


def _top(vectors: np.ndarray, rows: Sequence[int], fetch: int, block: int = 2048):
    """Each block of `rows` with its `fetch` most similar rows, on the GPU when there is one."""
    try:
        import torch

        device = "cuda" if torch.cuda.is_available() else None
    except ImportError:
        device = None
    if device:
        gallery = torch.from_numpy(np.array(vectors, dtype=np.float16)).to(device)
        for start in range(0, len(rows), block):
            index = torch.as_tensor(np.asarray(rows[start:start + block]), device=device)
            values, top = (gallery[index] @ gallery.T).float().topk(fetch + 1, dim=1)
            yield start, (top.cpu().numpy(), values.cpu().numpy())
        return
    gallery = np.asarray(vectors, dtype=np.float32)
    for start in range(0, len(rows), block // 8):
        scores = gallery[np.asarray(rows[start:start + block // 8])] @ gallery.T
        top = np.argpartition(-scores, fetch + 1, axis=1)[:, :fetch + 1]
        order = np.take_along_axis(scores, top, 1).argsort(1)[:, ::-1]
        top = np.take_along_axis(top, order, 1)
        yield start, (top, np.take_along_axis(scores, top, 1))


def document_of(page: str | None, fallback: str) -> str:
    """The document a page belongs to: its id without the last `:` part. A crop without a page is
    its own document."""
    if not page:
        return fallback
    return page.rsplit(":", 1)[0] if page.count(":") >= 2 else page


def _index(similar: Path, exports: Iterable[Path]) -> tuple[list[str], list[str | None], list[str | None], np.ndarray]:
    """The index's ids, labels and pages, with the labels and pages the named catalogues give, and
    its vectors."""
    import sqlite3
    import unicodedata

    import pyarrow.parquet as pq

    table = pq.read_table(similar / "units.parquet", columns=["id", "label"]).to_pydict()
    ids, labels = table["id"], [unicodedata.normalize("NFC", label) if label else None for label in table["label"]]
    pages: list[str | None] = [None] * len(ids)
    row = {identity: n for n, identity in enumerate(ids)}
    for export in exports:
        with sqlite3.connect(f"file:{export}?mode=ro", uri=True) as db:
            for identity, label, page in db.execute(
                    "SELECT id, json_extract(data,'$.label'), json_extract(data,'$.page_id') FROM units"):
                n = row.get(identity)
                if n is not None:
                    labels[n] = unicodedata.normalize("NFC", label) if label else None
                    pages[n] = page
    return ids, labels, pages, np.load(similar / "vectors.npy", mmap_mode="r")


def _reads(store, wanted: dict[str, tuple[str, str]], checkpoint: Path) -> dict[str, Read]:
    """Classify the display crop the dataset publishes for each of `wanted` ({id: (label, target)})."""
    import re

    from PIL import Image

    from ..classify import preprocess
    from ..form_clusters import Encoder
    from .atlas import router
    from .media import MediaCache
    from .quiz_suspects import Labels
    from .request_cache import lookup_scope

    encoder = Encoder(checkpoint)
    labels = Labels(encoder.classes)
    media = MediaCache()
    listing = next(r.endpoint for r in router(store, media=media).routes if r.path == "/atlas" and "GET" in r.methods)
    # Called as a function, the endpoint takes every parameter given here or its plain default.
    with lookup_scope():
        images = {item["id"]: found[1] for item in listing(reviewer=None, limit=10**7)["items"]
                  if (found := re.fullmatch(r"/atlas/media/([0-9a-f]{64})\.webp", item["image"] or ""))}
    queue = [identity for identity in wanted if identity in images]
    reads: dict[str, Read] = {}
    for start in range(0, len(queue), 512):
        batch, pixels = [], []
        for identity in queue[start:start + 512]:
            try:
                with Image.open(media.materialize(images[identity])) as picture:
                    pixels.append(np.asarray(preprocess(picture.convert("L"), size=encoder.size), dtype=np.uint8))
            except (OSError, ValueError):
                continue
            batch.append(identity)
        if not batch:
            continue
        square = np.stack(pixels)
        _features, probabilities = encoder.classify(square)
        ink = (square < INK_LEVEL).mean(axis=(1, 2))
        for n, identity in enumerate(batch):
            label, target = wanted[identity]
            own, other = labels.mask(label), labels.mask(target)
            reads[identity] = Read(float(probabilities[n][own].sum()) if own.any() else None,
                                   float(probabilities[n][other].sum()) if other.any() else None, float(ink[n]))
    return reads


def run(dataset: Path, *, similar: Path, checkpoint: Path, setting: str = "default", apply: bool = False,
        protect: Iterable[str] = (), exports: Iterable[Path] = (), seen: dict[str, str] | None = None) -> dict:
    """Propose, and with `apply` record, the relabels of every crop of `dataset` the index holds.

    Every crop is judged; a protected crop is reported with what the rule would do (`protected`) so
    that the rule can be measured against the reviews, and is never changed. `seen` gives the label a
    reviewer saw for a crop whose review has since changed it; such a crop is judged by that label,
    as it stood when the reviewer judged it.
    """
    from .atlas import written_identity
    from .store import SEEN, Store

    bar = SETTINGS[setting]
    store = Store(dataset)
    reviewed = {event.target_id for event in store.events() if event.role != "model" and event.field != SEEN}
    protected = reviewed | set(protect)
    ids, labels, pages, vectors = _index(similar, exports)
    row = {identity: n for n, identity in enumerate(ids)}
    units, peers = {}, defaultdict(list)
    for unit, revision in store.unit_snapshot():
        if not unit.active or str(unit.kind) != "char" or unit.box is None:
            continue
        label = written_identity(unit)
        if unit.line_id:
            peers[("line", unit.line_id)].append((unit.box.w, unit.box.h))
        peers[("page", unit.page_id)].append((unit.box.w, unit.box.h))
        if not label or unit.id not in row:
            continue
        if str(unit.review) != "machine":
            protected.add(unit.id)
        labels[row[unit.id]], pages[row[unit.id]] = label, unit.page_id
        if seen and unit.id in seen and unit.id in protected:
            label = seen[unit.id]
        units[unit.id] = (unit, revision, label)
    order = sorted(units)
    found, scores = nearest(vectors, [row[i] for i in order], pages)
    votes = {}
    for n, identity in enumerate(order):
        label = units[identity][2]
        votes[identity] = vote(label, [(labels[j], float(s), document_of(pages[j], ids[j]))
                                       for j, s in zip(found[n], scores[n], strict=True) if j >= 0])
    # Only a crop the neighbours carry past their tests is read by the classifier.
    wanted = {i: (units[i][2], v.target) for i, v in votes.items()
              if decide(v, Read(None, 1.0, 1.0), None, bar) not in ("vote", "documents")}
    reads = _reads(store, wanted, checkpoint) if wanted else {}
    items, counts = [], Counter()
    position = {identity: n for n, identity in enumerate(order)}
    for identity in order:
        unit, revision, label = units[identity]
        found_vote, read = votes[identity], reads.get(identity)
        line = peers.get(("line", unit.line_id), []) if unit.line_id else []
        group = line if len(line) >= PEERS else peers[("page", unit.page_id)]
        reason = gate((unit.box.w, unit.box.h), group, read.ink) if read else None
        verdict = decide(found_vote, read, reason, bar)
        counts[verdict] += 1
        if verdict != "propose":
            continue
        item = {"unit_id": identity, "before": label, "character": found_vote.target,
                "vote": asdict(found_vote), "read": asdict(read), "setting": setting,
                "neighbours": [[ids[j], labels[j], round(float(sim), 4)]
                               for j, sim in zip(found[position[identity]], scores[position[identity]], strict=True)
                               if j >= 0][:NAMED],
                "status": "protected" if identity in protected else "proposed"}
        items.append(item)
    by_prefix = Counter(item["unit_id"].split(":", 1)[0] for item in items if item["status"] == "proposed")
    if apply:
        record(store, items, {i: units[i][:2] for i in units})
    return {"method": METHOD, "setting": asdict(bar) | {"name": setting}, "crops": len(units),
            "protected": len(protected & set(units)), "read": len(reads), "decisions": dict(counts),
            "by_prefix": dict(by_prefix), "counts": dict(Counter(item["status"] for item in items)), "items": items}


def record(store, items: list[dict], units: dict[str, tuple[Any, int]]) -> None:
    """Record each `proposed` item as a model event on its unit; one changed since is `stale`."""
    from .atlas import script_of_identity
    from .refine import _changes, encoded
    from .store import Conflict

    for item in items:
        if item["status"] != "proposed":
            continue
        unit, revision = units[item["unit_id"]]
        evidence = {"kind": "consensus-relabel", "method": METHOD, "automated": True,
                    **{k: v for k, v in item.items() if k not in ("unit_id", "status")}}
        values = {"unicode": encoded(item["character"]), "script": script_of_identity(item["character"]),
                  "review": "machine", "meta": {**(unit.meta or {}), "feedback_identity": evidence}}
        try:
            _changes(store, unit, values, evidence, base_revision=revision)
        except Conflict:
            # Reviewed or changed since it was read; the next run judges it as it stands.
            item["status"] = "stale"
            continue
        item["status"] = "relabelled"


def undo(dataset: Path) -> dict:
    """Restore what every relabel of this method replaced, where the unit still holds the relabel.

    A unit a person reviewed since, or whose fields another pass changed since, keeps what it holds
    and is reported.
    """
    from .refine import _changes
    from .store import SEEN, Conflict, Store

    store = Store(dataset)
    events = store.events()
    later_review = defaultdict(int)
    for n, event in enumerate(events):
        if event.role != "model" and event.field != SEEN:
            later_review[event.target_id] = n
    # Per unit and field: what the first relabel replaced and what the last one wrote.
    changes: dict[str, dict[str, list]] = defaultdict(dict)
    first: dict[str, int] = {}
    for n, event in enumerate(events):
        if event.role != "model" or not event.evidence:
            continue
        try:
            evidence = json.loads(event.evidence)
        except ValueError:
            continue
        if not isinstance(evidence, dict) or evidence.get("method") != METHOD:
            continue
        if evidence.get("kind") == "consensus-relabel-undo":
            # Undone already: a later relabel starts afresh.
            changes.pop(event.target_id, None)
            first.pop(event.target_id, None)
            continue
        changes[event.target_id].setdefault(event.field, [event.old, None])[1] = event.new
        first.setdefault(event.target_id, n)
    counts, items = Counter(), []
    current = {unit.id: (unit, revision) for unit, revision in store.unit_snapshot() if unit.id in changes}
    for identity, fields in sorted(changes.items()):
        unit, revision = current.get(identity, (None, 0))
        status = "restored"
        if unit is None:
            status = "gone"
        elif later_review.get(identity, -1) > first[identity]:
            status = "reviewed since"
        elif any(_value(getattr(unit, field, None)) != new for field, (_old, new) in fields.items() if field != "meta"):
            status = "changed since"
        else:
            evidence = {"kind": "consensus-relabel-undo", "method": METHOD, "automated": True}
            try:
                _changes(store, unit, {field: old for field, (old, _new) in fields.items()}, evidence,
                         base_revision=revision)
            except Conflict:
                status = "stale"
        counts[status] += 1
        items.append({"unit_id": identity, "status": status})
    return {"method": METHOD, "counts": dict(counts), "items": items}


def _value(value: Any) -> Any:
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def evaluate(result: dict, reviews: dict) -> dict:
    """The rule against the site's reviews (`/atlas/reviews.json`): every `propose` decision on a
    reviewed crop (`protected` items) against what the reviewer said.

    A crop's last review counts. A wrong-character review names the reviewer's character; a relabel
    `right` when it agrees with it (`agree`), `other` when not. A relabel of a crop the reviewer
    confirmed is a false fix; one the reviewer reported as a bad crop, joined characters or blank
    paper is counted by that issue.
    """
    verdicts = last_reviews(reviews)
    judged = Counter()
    for verdict in verdicts.values():
        judged[_kind(verdict)] += 1
    found = Counter()
    for item in result["items"]:
        verdict = verdicts.get(item["unit_id"])
        if verdict is None:
            continue
        kind = _kind(verdict)
        if kind == "character":
            kind = "right" if verdict["character"] and agree(verdict["character"], item["character"]) else "other"
        found[kind] += 1
    return {"reviewed": dict(judged), "relabelled": dict(found)}


def _kind(verdict: dict) -> str:
    return "confirmed" if verdict["verdict"] == "match" else verdict.get("issue") or "other"


def last_reviews(reviews: dict) -> dict[str, dict]:
    """Each crop's last verdict in a site review export: verdict, issue and the reviewer's character."""
    from .. import refs

    def character(value: Any) -> str | None:
        if isinstance(value, dict):
            value = value.get("unicode")
        if not isinstance(value, str) or not value:
            return None
        return refs.from_code_points(value.split()) if value.startswith("U+") else value

    found: dict[str, dict] = {}
    for review in reviews.get("reviews", ()):
        event = review["event"]
        if review.get("origin") == "corpus":
            new = event.get("new") or {}
            found[event["target_id"]] = {"verdict": new.get("verdict"), "issue": new.get("issue"),
                                         "character": character(new.get("character")),
                                         "label": (review.get("reviewed") or {}).get("source_label")}
            continue
        evidence = json.loads(event.get("evidence") or "{}")
        for answer in (evidence.get("request") or {}).get("answers", ()) if evidence.get("kind") == "visual-quiz" else ():
            if answer.get("id") != event["target_id"]:
                found[answer["id"]] = {"verdict": answer.get("verdict"), "issue": answer.get("issue"),
                                       "character": character(answer.get("character")) or character(answer.get("correction")),
                                       "label": evidence.get("label")}
        shown = ((review.get("reviewed") or {}).get("character") or {}).get("label")
        found[event["target_id"]] = {"verdict": evidence.get("verdict"), "issue": evidence.get("issue"),
                                     "character": character(evidence.get("suggested_character"))
                                     or character(evidence.get("correction")),
                                     "label": evidence.get("label") or shown}
    return found


def write(result: dict, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n")
