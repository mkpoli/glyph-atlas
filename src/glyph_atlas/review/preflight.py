"""Find joined crops before they reach single-character review, and say what to do with them.

A detector box that holds two or three characters set as one blob cannot be reviewed as one
character: the reviewer is asked which character a crop holds when the honest answer is "three", and
the answer they give then has to be undone by a split. The existing `scan_joined` samples boxes that
are merely tall, shuffles them, and drops whatever it cannot assess, so the same failures come back
every round. This is the pass that runs *before* review instead: it measures every active
single-character crop for the geometry of a join, asks the image-only recognizer about the ones that
look suspicious, and returns one explicit record a crop.

Three rules keep it honest.

* **The vote is the image's.** The verdict comes from the crop's own ink and from the recognizer's
  reading of that crop. A unit's transcription, its neighbours and its line are recorded for the
  report and never vote: a guess from context mixed into a visual vote is a guess that cannot be
  audited. The one exception is stated as a guard rather than a vote — a character the layer says is
  one encoded ligature (𪜈, ゟ, ヿ, the Unicode 18 digraphs) is never joined, whatever the recognizer
  reads, because that reading is the ligature's two components and not two characters.
* **Length is not evidence.** A crop is never suspicious because its text is long, and two characters
  are never assumed to sit at equal heights: an equal division is a shape a join may take, not a
  measurement of one.
* **Failure is not a verdict.** A recognizer that is not installed, that raises, or that answers
  weakly is `unavailable` or `uncertain`, kept apart from `joined` and from `single`, so a caller can
  retry a failure without treating it as a finding.

Everything is read-only: no unit, no box, no journal event and no table is written. A joined record
carries `withhold=True` so a caller can hold that crop out of single-character review until a split
has been proposed and accepted; whether to do that, and whether to split at all, is the caller's
decision. Nothing here is applied.

The pass is bounded and resumable. `limit` is the number of recognizer calls one invocation may make
(256 by default); units are visited page by page so the page image is decoded once; and a `ScanState`
remembers the fingerprint of every crop it has decided — image, box, revision and thresholds — so a
second run continues instead of rescanning what has not changed.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, Protocol

from PIL import Image
from pydantic import BaseModel, Field, computed_field

from .. import refs
from ..schema import Box, ReviewState, Unit
from ..split_proposals import ink_profile
from . import atlas

#: The policy the records carry; part of every fingerprint, so a change to how a verdict is reached
#: invalidates a scan rather than silently mixing two rules in one state file.
POLICY = "preflight-joined-v2"
#: The states a person wrote. A unit carrying one is not this pass's to inspect.
HUMAN_REVIEW = (
    ReviewState.REVIEWED,
    ReviewState.DOUBLE_REVIEWED,
    ReviewState.ADJUDICATED,
    ReviewState.DISPUTED,
    ReviewState.TRANSCRIBER,
)

Verdict = Literal["single", "joined", "uncertain", "unavailable"]

#: The engine whose vote is a *sequence*. The classifier beside it answers one character at a time and
#: its probabilities are not on the same scale, so the verdict reads this engine's score alone.
SEQUENCE_ENGINE = "NDLkotenOCR"


class Thresholds(BaseModel):
    """Every number the suspicion test and the vote use, conservatively set.

    The geometry is what makes a crop worth a recognizer call, so it is deliberately hard to trip:
    a box has to be either half again as tall as the characters of its own line, or long and narrow
    with a blank run across it. The recognizer then has to read a sequence clearly to be believed.
    """

    #: Crop height over the typical character height of its line. 1.45 is a join of two for the
    #: moderate cases and leaves a single tall character below the line.
    min_height_ratio: float = 1.45
    #: Crop height over its width, for a box that is narrow as well as tall.
    min_aspect: float = 1.9
    #: The height ratio at which stacked ink is plausible even with no blank run in the profile.
    strong_height_ratio: float = 1.8
    #: The depth of a blank run, as a share of the crop's reference row ink, for it to count.
    min_valley_depth: float = 0.35
    #: The rise away from a blank run, as a share of the reference, for it to count.
    min_valley_prominence: float = 0.15
    #: Boxes shorter than this do not vote on what their line's typical character height is.
    typical_height_floor: int = 8
    #: How many characters the recognizer has to read for a sequence to be a join at all.
    min_sequence_characters: int = 2
    #: More than this and the reading is not a join this pass will name.
    max_sequence_characters: int = 4
    #: The whole-crop sequence score below which the reading is called weak rather than believed. A
    #: joined verdict withholds a crop from single-character review, so it is held to a sequence the
    #: recognizer is nearly sure of; 0.92 leaves the 0.85-to-0.9 answers that mixed models produce on
    #: a confusable crop as `uncertain`, which is a question for a person and not a withheld crop.
    min_sequence_score: float = 0.92
    #: Rows averaged before the profile is read, as in `split_proposals.ink_profile`.
    smooth: int = 3

    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(self.model_dump(mode="json"), sort_keys=True).encode("utf-8")
        ).hexdigest()[:12]


class InkMeasure(BaseModel):
    """What the crop's ink says about a stack, measured without a model."""

    width: int
    height: int
    aspect: float
    height_ratio: float = Field(description="crop height over the typical height of its line")
    reference_row_ink: float = Field(default=0.0, description="a row with ink in it, for the shares below")
    valley_depth: float = Field(default=0.0, description="deepest blank run over the reference ink")
    valley_prominence: float = Field(default=0.0)
    valley_y: int | None = None
    ink_rows: int = 0
    stacked: bool = Field(default=False, description="a blank run, or ink tall enough to hold two")


class OcrEvidence(BaseModel):
    """What the recognizer said about the whole crop, and what ran.

    `text` and `score` are the sequence model's own answer, which is the only vote the verdict uses.
    `votes` keeps every engine's answer as it came back, unmerged: a classifier's probability and a
    sequence model's are not on one scale, so taking the larger of the two would let a confident
    single-character guess outrank the reading that actually saw a sequence.
    """

    engine: str | None = None
    text: str | None = None
    characters: int = 0
    score: float | None = None
    engines: list[dict[str, Any]] = Field(default_factory=list)
    votes: list[dict[str, Any]] = Field(default_factory=list)
    symbol_partition: dict[str, Any] | None = None
    error: str | None = None


class CandidateRecord(BaseModel):
    """One active single-character crop, its measurements, its vote and what to do with it.

    `image_sha256` is the hash of the decoded crop and `fingerprint` is the identity of the decision:
    the image, the box, the unit's revision and the thresholds. A later run that sees the same
    fingerprint skips the crop; a box that moved, a revision that changed or a threshold that was
    retuned is a different fingerprint and is scanned again.
    """

    unit_id: str
    revision: int
    page_id: str | None = None
    line_id: str | None = None
    box: Box
    image_sha256: str
    fingerprint: str
    label: str | None = Field(default=None, description="what the reviewer would see; report only")
    text_source: str | None = Field(default=None, description="recorded, never a vote")
    written: str | None = Field(default=None, description="the written identity")
    ligature: str | None = Field(default=None, description="the code point, when it is a ligature")
    verdict: Verdict
    withhold: bool = Field(default=False, description="hold out of single-character review")
    reason: str = ""
    ink: InkMeasure
    ocr: OcrEvidence | None = None
    split: dict[str, Any] | None = Field(
        default=None, description="the split engine's answer, for a confident join"
    )
    split_state: Literal["not-attempted", "proposed", "partial", "refused"] = Field(
        default="not-attempted",
        description="what came back for a join: all children, some of them, or nothing",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def status(self) -> str:
        """The one string a caller writes into metadata, so a refused split is still a join.

        A crop whose split was refused is not a crop to hand a reviewer as one character: the reading
        and the ink agree that it holds a sequence, and only where the boundaries are is unsettled.
        This keeps the two apart — `joined:proposed`, `joined:partial`, `joined:refused` — beside the
        verdicts that are not joins at all.
        """
        if self.verdict != "joined":
            return self.verdict
        return f"joined:{self.split_state}"


class ScanReport(BaseModel):
    """One invocation: what it decided, what it spent, and why it stopped."""

    policy: str = POLICY
    thresholds: dict[str, Any] = Field(default_factory=dict)
    considered: int = Field(default=0, description="active single-character crops seen")
    suspicious: int = Field(default=0, description="crops the geometry sent to the recognizer")
    scanned: int = Field(default=0, description="records produced by this invocation")
    skipped_done: int = Field(default=0, description="crops a previous run had already decided")
    skipped_human: int = Field(default=0, description="crops a person has touched")
    whole_crop_reads: int = Field(default=0, description="recognizer reads of a whole crop")
    split_attempts: int = Field(default=0, description="assessments asked of the split engine")
    stopped: str | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    records: list[CandidateRecord] = Field(default_factory=list)
    seconds: float = 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ocr_calls(self) -> int:
        """The whole-crop reads under the name earlier callers used for the budget."""
        return self.whole_crop_reads

    @property
    def withheld(self) -> list[str]:
        """The units a caller may hold out of single-character review until they are split."""
        return [record.unit_id for record in self.records if record.withhold]

    def by_verdict(self, verdict: Verdict) -> list[CandidateRecord]:
        return [record for record in self.records if record.verdict == verdict]


class ScanState:
    """The ledger that makes a scan resumable: one line a decision, keyed by fingerprint.

    The file is the caller's to place and it is never inside the dataset: this pass does not write
    to the thing it inspects. Without a path the state lives for one invocation, which is what a
    test or a one-off scan wants.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self.decisions: dict[str, dict[str, Any]] = {}
        self._handle = None
        if self.path is not None and self.path.is_file():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    self.decisions[row["fingerprint"]] = row

    def seen(self, fingerprint: str) -> dict[str, Any] | None:
        return self.decisions.get(fingerprint)

    def remember(self, record: CandidateRecord) -> None:
        row = {
            "fingerprint": record.fingerprint,
            "unit_id": record.unit_id,
            "revision": record.revision,
            "image_sha256": record.image_sha256,
            "verdict": record.verdict,
            "policy": POLICY,
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self.decisions[record.fingerprint] = row
        if self.path is not None:
            if self._handle is None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._handle = self.path.open("a", encoding="utf-8")
            self._handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            self._handle.flush()


class CropProvider(Protocol):
    """What the scan needs to turn a unit into pixels: the crop and its source digest."""

    def __call__(self, store: Any, unit: Unit) -> tuple[Image.Image, str]: ...


def _typical_heights(store: Any) -> tuple[dict[str, list[tuple[str, int]]], dict[str, list[tuple[str, int]]]]:
    """Every line's and page's placed character heights, by unit id.

    A join is judged against the hand it sits in, not against a page: one line's characters may be
    twice another's, and a page's median would call the small hand's ordinary characters joins. The
    unit under test is left out when its own line's height is taken — a 94-pixel box that is half of
    a two-character line would otherwise raise the line's own typical height and hide itself, which is
    exactly the failure this pass exists to find.
    """
    by_line: dict[str, list[tuple[str, int]]] = {}
    by_page: dict[str, list[tuple[str, int]]] = {}
    for unit, _ in store.unit_snapshot():
        if not unit.active or unit.box is None or unit.box.h <= 0:
            continue
        if not _readable_single(unit):
            continue
        if unit.line_id:
            by_line.setdefault(unit.line_id, []).append((unit.id, unit.box.h))
        if unit.page_id:
            by_page.setdefault(unit.page_id, []).append((unit.id, unit.box.h))
    return by_line, by_page


def _typical_for(rows: Sequence[tuple[str, int]] | None, exclude: str) -> float:
    """The typical height of the boxes around `exclude`, or zero when there are none to ask."""
    values = [height for unit_id, height in (rows or ()) if unit_id != exclude]
    if not values:
        return 0.0
    return _median(values)


def _median(values: Sequence[float]) -> float:
    ordered = sorted(value for value in values if value > 0)
    if not ordered:
        return 0.0
    middle = len(ordered) // 2
    return float(ordered[middle]) if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def _blank_run(profile: Sequence[float], *, reference: float, thresholds: Thresholds,
               margin: int = 2) -> tuple[float, float, int | None]:
    """The deepest run of rows the strokes do not cross: depth, prominence and where it is.

    Depth and prominence are shares of one row that holds ink, so the same thresholds hold on a
    brown scan as on a white one. A run touching either end of the crop is not a boundary between
    two characters and is passed over.
    """
    if not profile or reference <= 0:
        return 0.0, 0.0, None
    bound = reference * (1.0 - thresholds.min_valley_depth)
    runs: list[list[int]] = []
    for y in range(margin, len(profile) - margin):
        here = profile[y]
        left = profile[max(0, y - margin):y]
        right = profile[y + 1:min(len(profile), y + margin + 1)]
        if not left or not right or here > bound:
            continue
        if here > max(left) or here > max(right):
            continue
        if runs and y == runs[-1][-1] + 1:
            runs[-1].append(y)
        else:
            runs.append([y])
    best = (0.0, 0.0, None)
    for run in runs:
        lowest = min(profile[y] for y in run)
        left = profile[max(0, run[0] - margin):run[0]]
        right = profile[run[-1] + 1:min(len(profile), run[-1] + 1 + margin)]
        if not left or not right:
            continue
        depth = 1.0 - lowest / reference
        prominence = (min(max(left), max(right)) - lowest) / reference
        if depth > best[0]:
            emptiest = [y for y in run if profile[y] == lowest]
            best = (depth, prominence, emptiest[len(emptiest) // 2])
    return best


def measure(crop: Image.Image, *, height_ratio: float, thresholds: Thresholds) -> InkMeasure:
    """The ink measurements of one crop: its shape, and whether a stack is visible in it."""
    profile = ink_profile(crop, smooth=thresholds.smooth)
    raw = ink_profile(crop, smooth=1)
    inked = [value for value in profile if value > 0]
    reference = _median(profile) if _median(profile) > 0 else (
        sum(inked) / len(inked) if inked else 0.0
    )
    depth, prominence, y = _blank_run(profile, reference=reference, thresholds=thresholds)
    width, height = crop.size
    measure_ = InkMeasure(
        width=width,
        height=height,
        aspect=round(height / width, 3) if width else 0.0,
        height_ratio=round(height_ratio, 3),
        reference_row_ink=round(reference, 2),
        valley_depth=round(depth, 3),
        valley_prominence=round(prominence, 3),
        valley_y=y,
        ink_rows=sum(1 for value in raw if value > 0),
    )
    measure_.stacked = bool(
        (depth >= thresholds.min_valley_depth and prominence >= thresholds.min_valley_prominence)
        or height_ratio >= thresholds.strong_height_ratio
    )
    return measure_


def _suspicious(measure_: InkMeasure, thresholds: Thresholds) -> bool:
    """Whether a crop is worth a recognizer call: tall against its line, or long and narrow."""
    if measure_.height_ratio >= thresholds.min_height_ratio:
        return True
    return measure_.aspect >= thresholds.min_aspect and measure_.valley_depth > 0.0


def _ligature_of(unit: Unit, guard: Callable[[str], Any] | None) -> str | None:
    """The code point of the one encoded ligature this unit is, when the layer says it is one.

    A ligature is one character made of two (𪜈 is ト + モ) and its crop is one character's ink: no
    reading of it, however many characters it spells, makes it a join. This is an identity guard and
    not part of the vote; a caller that passes no guard is told the guard was unavailable rather
    than being given a verdict the identity cannot support.
    """
    points = [point for point in (unit.unicode or "").split() if point]
    if len(points) != 1:
        return None
    checker = guard if guard is not None else getattr(refs, "ligature", None)
    if checker is None:
        return None
    found = checker(points[0])
    return points[0] if found else None


def _guard_available(guard: Callable[[str], Any] | None) -> bool:
    return guard is not None or getattr(refs, "ligature", None) is not None


def _verdict(ocr: OcrEvidence, measure_: InkMeasure, *, thresholds: Thresholds,
             ligature: str | None, guard_available: bool) -> tuple[Verdict, bool, str]:
    """The vote: the recognizer's reading of the crop against the crop's own ink.

    A sequence the recognizer reads clearly in ink that shows a stack is a join, and it is a join
    even when nothing can say where the boundary is — a caller that must withhold the crop does not
    need the split to succeed first. A sequence read in ink that shows no stack is not a join this
    pass will name, and neither is a single reading in ink that looks like a stack: those are
    `uncertain`, which is a question for a person and not a finding.
    """
    if ligature is not None:
        return "single", False, (
            f"{ligature} is one encoded ligature in the character layer: its crop is one character, "
            f"not a join"
        )
    if ocr.error is not None:
        return "unavailable", False, f"the recognizer could not answer: {ocr.error}"
    if ocr.symbol_partition and guard_available:
        return "joined", True, "a separate printed triangle and neighbouring ink occupy the same crop"
    count = ocr.characters
    score = ocr.score
    if count == 0 or score is None:
        return "unavailable", False, "the recognizer returned no reading for this crop"
    if score < thresholds.min_sequence_score:
        return "uncertain", False, (
            f"the recognizer's reading {ocr.text!r} scores {score:.2f}, under the "
            f"{thresholds.min_sequence_score:g} this pass believes"
        )
    if count == 1:
        return "uncertain", False, (
            "one character recognized in ink tall enough for a join; a person decides whether the "
            "box holds one character or more"
        )
    if count > thresholds.max_sequence_characters:
        return "uncertain", False, (
            f"the recognizer reads {count} characters, more than the {thresholds.max_sequence_characters} "
            f"a join of this pass is named for"
        )
    if not guard_available:
        return "uncertain", False, (
            "a multi-character reading, but the ligature layer is unavailable here, so one encoded "
            "character cannot be ruled out"
        )
    if not measure_.stacked:
        return "uncertain", False, (
            f"the recognizer reads {ocr.text!r}, but the ink shows no blank run and the box is not "
            f"tall enough for a stack"
        )
    return "joined", True, (
        f"the recognizer reads {ocr.text!r} at {score:.2f} in ink that shows a stack "
        f"(height {measure_.height_ratio:.2f}x the line, blank run {measure_.valley_depth:.2f})"
    )


class PageCropReader:
    """The default crop provider: one page decoded at a time, the checks `refine.crop_for` makes.

    Grouping the scan by page makes this worth having: a page holds hundreds of crops and decoding
    its image once per crop is the difference between a pass taking minutes and taking an hour. The
    checks are the viewer's own — a reduced cached image and a box outside it are refused rather
    than cut in the wrong coordinate system.
    """

    def __init__(self) -> None:
        self.pages: dict[str, list[Unit]] = {}
        self._page_id: str | None = None
        self._image: Image.Image | None = None
        self.decoded_pages = 0

    def __call__(self, store: Any, unit: Unit) -> tuple[Image.Image, str]:
        from .characters import _source_digest
        from .server import cached_image

        if unit.crop_sha256:
            raise ValueError("a standalone crop cannot be split into page coordinates")
        page = store.page(unit.page_id) if unit.page_id else None
        if page is None or unit.box is None or not page.width or not page.height:
            raise ValueError("page geometry unavailable")
        digest = _source_digest(store, unit)
        path = cached_image(digest) if digest else None
        if path is None:
            raise ValueError("source image unavailable")
        if self._page_id != unit.page_id or self._image is None:
            with Image.open(path) as opened:
                opened.load()
                self._image = opened.convert("RGB")
            self._page_id = unit.page_id
            self.decoded_pages += 1
        source = self._image
        if source.size != (page.width, page.height):
            raise ValueError("cached image and page use different coordinate scales")
        box = unit.box
        if box.x < 0 or box.y < 0 or box.x + box.w > source.width or box.y + box.h > source.height:
            raise ValueError("crop extends beyond the source image")
        return source.crop((box.x, box.y, box.x + box.w, box.y + box.h)).convert("RGB"), digest


def _human_targets(store: Any) -> set[str]:
    events = store.events()
    return {event.target_id for event in events if event.role != "model"}


def _identity(unit: Unit) -> tuple[str, str | None]:
    """The label a reviewer would see and the written identity, both for the report alone."""
    return atlas.label(unit), atlas.written_identity(unit) or None


def _readable_single(unit: Unit) -> bool:
    """Whether this is one character's crop, as a *written* identity rather than as a reading.

    The viewer's own test asks the label first and falls back to the written identity only when the
    label is empty. A ligature is the case that needs the wider test: its reading spells two
    characters (𪜈 reads トモ) while its ink is one character, so a pass that judged by the reading
    would send it to review and then try to split it. Judging by the written identity reaches it and
    the ligature guard names it as what it is.
    """
    if unit.granularity != "char":
        return False
    label, written = _identity(unit)
    if atlas.single_character(label):
        return True
    return bool(written) and atlas.single_character(written) and not atlas.is_space_identity(written)


def scan(
    store: Any,
    *,
    limit: int = 256,
    thresholds: Thresholds | None = None,
    engine: Any = None,
    state: ScanState | None = None,
    crop_of: CropProvider | None = None,
    on_progress: Callable[[int, int, CandidateRecord], None] | None = None,
    units: Iterable[tuple[Unit, int]] | None = None,
    guard: Callable[[str], Any] | None = None,
    propose_splits: bool = False,
) -> ScanReport:
    """Measure every active single-character crop and name the joins among them.

    `limit` is the number of **whole-crop reads** this invocation may make, not the number of units it
    may look at: a crop whose geometry is ordinary costs no read and is recorded as `single` so it is
    not measured again. `propose_splits` is off by default, so a scan over a whole collection spends
    one cheap read a suspicious crop and asks the split engine nothing; a caller that wants the
    boundaries too turns it on for the crops the first pass called joined, and pays an assessment for
    each of those alone.

    `engine` is the recognizer seam (`review.refine.SplitEngine` by default, built only when a
    suspicious crop is found); `crop_of` is the pixel seam; `state` is the resume ledger. Nothing is
    written to the store, the tables or the journal.
    """
    thresholds = thresholds or Thresholds()
    started = time.monotonic()
    state = state if state is not None else ScanState()
    crop_of = crop_of or PageCropReader()
    human = _human_targets(store)
    rows = list(units) if units is not None else list(store.unit_snapshot())
    line_heights, page_heights = _typical_heights(store)
    guard_available = _guard_available(guard)

    report = ScanReport(thresholds=thresholds.model_dump(mode="json"))
    ordered: list[tuple[str, Unit, int]] = []
    for unit, revision in rows:
        if not unit.active or unit.box is None:
            continue
        if not _readable_single(unit):
            continue
        if unit.meta.get("segmentation_scan", {}).get("verdict") == "joined":
            report.skipped_done += 1
            continue
        if unit.review in HUMAN_REVIEW or unit.id in human:
            report.skipped_human += 1
            continue
        report.considered += 1
        ordered.append((unit.page_id or "", unit, revision))
    # A page at a time, so the decoded image survives every crop of that page.
    ordered.sort(key=lambda row: (row[0], row[1].box.y, row[1].box.x, row[1].id))

    for page_id, unit, revision in ordered:
        try:
            crop, _digest = crop_of(store, unit)
        except (ValueError, OSError) as error:
            category = type(error).__name__
            report.records.append(
                _unavailable(store, unit, revision, thresholds, str(error), category)
            )
            report.scanned += 1
            continue
        image_sha256 = hashlib.sha256(crop.tobytes()).hexdigest()
        fingerprint = _fingerprint(image_sha256, unit, revision, thresholds)
        if state.seen(fingerprint) is not None:
            report.skipped_done += 1
            continue
        typical = _typical_for(line_heights.get(unit.line_id or ""), unit.id) or _typical_for(
            page_heights.get(page_id), unit.id
        )
        height_ratio = unit.box.h / typical if typical > 0 else 0.0
        ink = measure(crop, height_ratio=height_ratio, thresholds=thresholds)
        from ..symbol_parts import triangle_regions
        suspected = _suspicious(ink, thresholds) or bool(triangle_regions(crop))
        ligature = _ligature_of(unit, guard)
        label, written = _identity(unit)
        record = CandidateRecord(
            unit_id=unit.id,
            revision=revision,
            page_id=unit.page_id,
            line_id=unit.line_id,
            box=unit.box,
            image_sha256=image_sha256,
            fingerprint=fingerprint,
            label=label or None,
            text_source=unit.text_source,
            written=written,
            ligature=ligature,
            verdict="single",
            ink=ink,
        )
        if ligature is not None:
            # One encoded character: nothing to read, nothing to split, no call spent.
            record.verdict = "single"
            record.reason = (
                f"{ligature} is one encoded ligature in the character layer: one character's ink, "
                f"whatever it spells"
            )
        elif not suspected:
            record.verdict = "single"
            record.reason = (
                f"geometry is ordinary for its line (height {height_ratio:.2f}x, aspect "
                f"{ink.aspect:.2f}, blank run {ink.valley_depth:.2f})"
            )
        else:
            report.suspicious += 1
            if report.whole_crop_reads >= max(0, limit):
                report.stopped = "limit"
                break
            report.whole_crop_reads += 1
            ocr = _read(engine, unit, crop, thresholds)
            record.ocr = ocr
            verdict, withhold, reason = _verdict(
                ocr, ink, thresholds=thresholds, ligature=ligature,
                guard_available=guard_available,
            )
            record.verdict = verdict
            record.withhold = withhold
            record.reason = reason
            if verdict == "joined" and propose_splits:
                report.split_attempts += 1
                expected = ("".join(ocr.symbol_partition["text"]) or None) if ocr.symbol_partition else ocr.text
                record.split = _assess(engine, unit, crop, expected)
                record.split_state = _split_state(record.split, expected=len(expected) if expected else ocr.characters)
                if record.split_state in ("partial", "refused"):
                    record.reason += (
                        f"; the split engine proposed {record.split_state} boundaries, and the crop "
                        f"is still a join"
                    )
        report.records.append(record)
        state.remember(record)
        report.scanned += 1
        if on_progress is not None:
            on_progress(report.scanned, report.considered, record)

    report.counts = {}
    for record in report.records:
        report.counts[record.verdict] = report.counts.get(record.verdict, 0) + 1
    report.seconds = round(time.monotonic() - started, 2)
    return report


def _fingerprint(image_sha256: str, unit: Unit, revision: int, thresholds: Thresholds) -> str:
    """The identity of one decision: the pixels, the box, the revision and the thresholds."""
    box = unit.box
    payload = "|".join([
        POLICY,
        unit.id,
        unit.unicode or "",
        unit.text_source or "",
        thresholds.fingerprint(),
        image_sha256,
        f"{box.x},{box.y},{box.w},{box.h}" if box else "-",
        str(revision),
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _unavailable(store: Any, unit: Unit, revision: int, thresholds: Thresholds, error: str,
                 category: str) -> CandidateRecord:
    """A crop whose pixels could not be reached at all: a failure, not a verdict."""
    return CandidateRecord(
        unit_id=unit.id,
        revision=revision,
        page_id=unit.page_id,
        line_id=unit.line_id,
        box=unit.box,
        image_sha256="",
        fingerprint=hashlib.sha256(f"{POLICY}|{unit.id}|{revision}|unavailable".encode()).hexdigest()[:32],
        text_source=unit.text_source,
        verdict="unavailable",
        ink=InkMeasure(width=unit.box.w, height=unit.box.h, aspect=0.0, height_ratio=0.0),
        ocr=OcrEvidence(error=f"{category}: {error}"),
        reason=f"the crop could not be read from the page ({category}: {error})",
    )


def _engine_call(engine: Any, unit: Unit, crop: Image.Image, expected: str | None) -> Any:
    """Ask the engine about a crop, preferring the call that knows which character it is.

    `SplitEngine.assess_unit(unit, crop, expected)` hands the character layer the unit's own encoded
    identity, so a crop whose parent is キ is not read as the digraph because the transcription above
    it spells トキ: a reading that matches a ligature's components is not evidence of one glyph, and an
    engine given only the pixels cannot tell the two apart. An engine that exposes only `assess` — an
    older engine, or a stand-in in a test — is called with the crop alone.
    """
    aware = getattr(engine, "assess_unit", None)
    if callable(aware):
        return aware(unit, crop, expected)
    return engine.assess(crop, expected)


def _sequence_of(votes: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The sequence model's own vote out of everything the recognizer answered.

    Named rather than taken as the best score: `NDLkotenOCR` is the engine that reads a sequence and
    the classifier beside it answers one character at a time, so choosing between them by score would
    be choosing between two models with different calibration.
    """
    return next((vote for vote in votes if vote.get("engine") == SEQUENCE_ENGINE), None)


def _read(engine: Any, unit: Unit, crop: Image.Image, thresholds: Thresholds) -> OcrEvidence:
    """One cheap reading of the whole crop: what the recognizer sees in it, as evidence only.

    The recognizer is asked for its *reading*, not for an assessment. Assessment is the expensive
    path — it proposes children for every sequence it can name — and a scan that ran it on every
    suspicious crop would spend a split's work on crops that turn out to hold one character, which is
    what would make a budget of 256 crops mean something else entirely. The read is under the same
    lock the server serializes inference with, so a scan and a reviewer never hold a session at once.

    An engine without a `.model` — a stand-in in a test — is asked through `assess`, which is the seam
    those fakes implement. The real `SplitEngine` takes this path: one read, no assessment.
    """
    try:
        if engine is None:
            from .refine import SplitEngine

            engine = SplitEngine()
        model = getattr(engine, "model", None)
        if model is not None and hasattr(model, "read"):
            from .suggestions import LOCK

            with LOCK:
                answer = model.read(crop)
        else:
            answer = _engine_call(engine, unit, crop, None)
    except Exception as error:  # noqa: BLE001 — an unavailable model is a state, not a crash
        return OcrEvidence(error=f"{type(error).__name__}: {error}")
    answer = answer or {}
    votes = [dict(vote) for vote in (answer.get("votes") or answer.get("candidates") or [])
             if isinstance(vote, Mapping)]
    sequence = _sequence_of(votes)
    if sequence is None and isinstance(answer.get("sequence"), Mapping):
        sequence = answer["sequence"]
    text = sequence.get("text") if sequence else None
    score = sequence.get("score") if sequence else None
    return OcrEvidence(
        engine=sequence.get("engine") if sequence else None,
        text=text if isinstance(text, str) and text else None,
        characters=len(text) if isinstance(text, str) else 0,
        score=float(score) if isinstance(score, (int, float)) else None,
        engines=list(answer.get("engines") or []),
        votes=votes,
        symbol_partition=answer.get("symbol_partition"),
    )


def _split_state(answer: Mapping[str, Any], *, expected: int) -> str:
    """Whether the engine placed every character, some of them, or none.

    A partial answer is the useful one for a long join: two children of three, with the third left
    where the ink does not justify a cut, is a proposal a reviewer can finish. It is recorded as
    `partial` rather than being forced to the reading's length or thrown away, and the verdict stays
    `joined` whatever happens here.
    """
    if not answer.get("accepted"):
        return "refused"
    children = len(answer.get("boxes") or [])
    if children == 0:
        return "refused"
    return "partial" if children < expected else "proposed"


def _assess(engine: Any, unit: Unit, crop: Image.Image, expected: str | None) -> dict[str, Any]:
    """Ask the split engine where the join divides, for a candidate the vote already called joined.

    The assessment is evidence: a join a caller has to withhold does not stop being a join because no
    boundary could be justified, so a refusal here is recorded and the verdict stands. The
    identity-aware call is preferred here too — the split of a crop is about the characters the ink
    holds, and the character layer is what says which character the parent is.
    """
    try:
        answer = _engine_call(engine, unit, crop, expected)
    except Exception as error:  # noqa: BLE001 — a split that fails is a proposal, never a verdict
        return {"accepted": False, "reason": f"{type(error).__name__}: {error}"}
    return dict(answer or {})


def apply_records(store, records, *, engine=None, split_limit=64) -> dict:
    """Apply checked scan results; separate only independently read blank-gap partitions.

    Revisions and decoded pixels must still match. Every confirmed join is withheld
    even if no safe split is available. Existing human decisions are protected.
    """
    from collections import Counter

    from ..partial_split import propose_partial
    from .refine import SplitEngine, _changes, split_unit
    from .suggestions import LOCK

    outcomes = []
    reader = PageCropReader()
    human = _human_targets(store)
    attempted = 0
    for record in records:
        if not record.withhold or record.verdict != "joined":
            continue
        unit = store.unit(record.unit_id)
        if (unit is None or not unit.active or unit.id in human
                or store.revision(unit.id) != record.revision):
            outcomes.append({"unit_id": record.unit_id, "status": "stale"})
            continue
        crop, _ = reader(store, unit)
        if (unit.box != record.box or hashlib.sha256(crop.tobytes()).hexdigest() != record.image_sha256):
            outcomes.append({"unit_id": unit.id, "status": "stale-pixels"})
            continue
        evidence = record.model_dump(mode="json")
        repair = {**unit.meta.get("alignment_repair", {}), "status": "joined",
                  "withheld": True, "quiz": False, "reliable": False, "reason": record.reason}
        meta = {**unit.meta, "alignment_repair": repair, "segmentation_scan": evidence}
        _changes(store, unit, {"meta": meta}, {"kind": POLICY, "scan": evidence},
                 base_revision=record.revision)
        outcome = {"unit_id": unit.id, "status": "withheld"}
        partition = record.ocr.symbol_partition if record.ocr else None
        can_split = bool(partition and partition.get("accepted")) if partition else bool(
            record.ocr and record.ocr.score is not None and record.ocr.score >= .98)
        if can_split and attempted < split_limit:
            attempted += 1
            if partition:
                proposal = {**partition, "engines": record.ocr.engines}
            else:
                engine = engine or SplitEngine()
                with LOCK:
                    proposal = propose_partial(crop, record.ocr.text, engine.model.read)
            outcome["assessment"] = proposal
            if proposal.get("accepted"):
                outcome.update(split_unit(store, store.unit(unit.id), proposal,
                                          base_revision=store.revision(unit.id)))
        outcomes.append(outcome)
    return {"counts": dict(Counter(row["status"] for row in outcomes)),
            "split_attempts": attempted, "items": outcomes}
