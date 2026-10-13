"""Large hanja and post-circle 언해 on ruled half-leaves of 老乞大諺解.

A printed page is one half-leaf of ten ruled columns, read right to left. Large hanja run down a
column, with a row of two small readings under each. After each Chinese phrase, a ○ is followed by
its 언해 in two half-width sub-columns, right then left, continuing down columns and across pages.
The readings under hanja are never cut.

The column grid is fitted from large-hanja x-centres. Detector boxes are classed by their size,
shape and classifier top label as large hanja, circle or small text; small text under a large hanja
is its reading, and small text after a circle is ordered as 언해 by half-column. Large hanja are
aligned to the pinned Chinese Wikisource stream near a cursor and checked by the atlas classifier. A
Chinese page is accepted only when enough matched hanja are actually judged by the classifier and
agree; an unjudged hanja is emitted only inside a no-gap run bracketed by agreeing matched anchors.

Each 언해 run is the ordered boxes after one circle up to the next large hanja or circle. Runs may be
assigned across a page break, but output lines never span pages: a continued phrase is split into
page-local lines with shared phrase metadata. A skipped page ends pending Chinese and Korean state.
A Hangul classifier chooses among Korean candidate phrases whose character count matches the run.
Its scoring is restricted to the candidates' Hangul shape keys, then gated by margin, agreement, an
evidence floor, and geometry. Unknown Hangul and non-Hangul phrase positions carry the same neutral
score. A box agrees with a phrase when the phrase's character is among the model's five best class
slots, counted before dropping `other`, so that a lone candidate cannot agree with itself. The
Korean cursor advances after an accepted run, advances by one expected phrase after a dropped run,
and searches the whole volume after `--reacquire` consecutive drops without going behind the last
accepted phrase.

Geometry gates refuse overlapping boxes within one half-column, boxes too small for the page's small
text and half-column scale, and gaps too large for one half-column run. A unit is kept only when the
source text supplies the character and the relevant classifier either agrees or cannot judge it:
every label comes from the transcription, none from a classifier.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import Literal

from . import clusters, glossary, net, wikitext
from .schema import Box

Volume = Literal["sang", "ha"]

#: Classifier labels read as the circle that starts a Korean 언해 run.
CIRCLES = glossary.CIRCLES
#: Dataset segmentation note written to every unit this cutter emits.
SEGMENTATION = (
    "eonhae-v3: detector boxes ordered by fitted ten-column grid; large hanja aligned to zh.wikisource, "
    "small post-circle characters aligned exactly to slash-delimited ko.wikisource phrases"
)
#: Least accepted Chinese alignment agreement among atlas-classifier-judged matched hanja.
ZH_MIN_AGREEMENT = 0.70
#: Least number of classifier-agreeing large hanja required before a Chinese page is trusted.
ZH_MIN_AGREEING_MATCHES = 3
#: Chinese stream positions searched on each side of the current cursor before global voting.
ZH_WINDOW = 60
#: Greatest vertical overlap allowed within one 언해 half-column, as a share of the smaller box.
EONHAE_OVERLAP_SMALLER_SHARE = 0.25
#: Least 언해 box height, as a share of the page's median small-box height.
EONHAE_MIN_HEIGHT_MEDIAN_SHARE = 0.50
#: Least 언해 box width, as a share of the fitted half-column width.
EONHAE_MIN_WIDTH_HALF_COLUMN_SHARE = 0.40
#: Greatest vertical gap inside one 언해 half-column, in median small-box heights.
EONHAE_MAX_GAP_MEDIAN_HEIGHTS = 1.60
#: Dynamic-programming penalty for matching a weak lower hanja when its upper reading-row neighbour fits.
ZH_WEAK_LOWER_NEIGHBOUR_PENALTY = 2.0
#: Maximum vertical gap, in local hanja heights, for treating two hanja as reading-row neighbours.
ZH_READING_ROW_GAP_HEIGHTS = 0.75
#: Per-volume scan ids, source filenames, pinned Chinese revisions and public source URLs.
VOLUMES: dict[Volume, dict[str, str]] = {
    "sang": {
        "label": "上",
        "scan": "CNTS-00132358209",
        "zh_file": "zh-sang-2551540.json",
        "zh_host": "zh.wikisource.org",
        "zh_revid": "2551540",
        "zh_url": "https://zh.wikisource.org/wiki/老乞大/卷上?oldid=2551540",
    },
    "ha": {
        "label": "下",
        "scan": "CNTS-00132358210",
        "zh_file": "zh-ha-2551541.json",
        "zh_host": "zh.wikisource.org",
        "zh_revid": "2551541",
        "zh_url": "https://zh.wikisource.org/wiki/老乞大/卷下?oldid=2551541",
    },
}
#: Pinned Korean Wikisource JSON filename under an upstream directory.
KO_FILE = "ko-nogeoldae-431023.json"
#: Korean Wikisource host used to fetch `KO_FILE` when it is absent.
KO_HOST = "ko.wikisource.org"
#: Pinned Korean Wikisource revision id.
KO_REVID = "431023"
#: Public Korean source URL kept in unit upstream metadata.
KO_URL = "https://ko.wikisource.org/wiki/노걸대언해?oldid=431023"

#: Wikitext comments, which do not carry printed text.
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
#: Wikitext or HTML tags that do not carry printed text here.
_TAG = re.compile(r"<[^>]*>")
#: Korean volume headings in the Wikisource transcription.
_SECTION = re.compile(r"^==\s*老乞大諺解([上下])\s*==\s*$", re.MULTILINE)
#: Numbered Korean transcription lines.
_HASH_LINE = re.compile(r"^#\s*(.*)$", re.MULTILINE)
#: A no-argument template whose name is the printed character.
_NOARG_PRINTED = re.compile(r"\{\{\s*([\w\u3400-\u9fff\U00020000-\U0002EBEF])\s*\}\}")
#: Templates whose contents are page furniture or notes rather than printed text.
_DROP_TEMPLATE = frozenset({
    "header2",
    "머리말",
    "스캔으로 이동",
    "옛한글 알림",
    "위키백과",
    "옛한글/시작",
    "옛한글/끝",
    "pd-old-100",
})

#: MediaWiki API format used to fetch one pinned revision as JSON.
MEDIAWIKI_REVISION_API = (
    "https://{host}/w/api.php?"
    "action=query&prop=revisions&revids={revid}&rvprop=content|ids&rvslots=main&format=json&formatversion=2"
)


@dataclass(frozen=True)
class WikiSource:
    title: str
    revid: str
    url: str
    content: str

    @property
    def upstream(self) -> dict[str, str]:
        return {"source": "wikisource", "title": self.title, "revid": self.revid, "url": self.url}


@dataclass(frozen=True)
class KoLine:
    index: int
    raw: str
    text: str
    chars: tuple[str, ...]


@dataclass(frozen=True)
class KoPhrase:
    index: int
    line_index: int
    phrase_index: int
    raw: str
    text: str
    chars: tuple[str, ...]


@dataclass(frozen=True)
class LayoutEvent:
    kind: Literal["hanja", "circle", "eonhae", "reading"]
    box: Box
    top5: tuple[str, ...]
    column: int
    half: int | None = None


@dataclass(frozen=True)
class EonhaePageGeometry:
    median_small_height: float
    half_column_width: float


@dataclass(frozen=True)
class EonhaeRunGeometryProblem:
    reason: Literal["overlap", "small box", "gap"]
    detail: str


@dataclass(frozen=True)
class PageGrid:
    centers: tuple[float, ...]
    pitch: float
    spans: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class PageLayout:
    events: tuple[LayoutEvent, ...]
    reading_boxes: int
    end_in_eonhae: bool
    grid: PageGrid | None = None
    reading_events: tuple[LayoutEvent, ...] = ()


@dataclass(frozen=True)
class HanjaAlignment:
    offset: int | None
    chars: tuple[str | None, ...]
    verdicts: tuple[bool | None, ...]
    matched: tuple[bool, ...]
    agreement: float
    refused: int
    accepted: bool
    reason: str
    consumed: int = 0
    stream_indices: tuple[int | None, ...] = ()
    safe_unjudged: tuple[bool, ...] = ()


@dataclass(frozen=True)
class HanjaUnitDecision:
    event: LayoutEvent
    char: str | None
    verdict: bool | None
    matched: bool
    emitted: bool
    stream_index: int | None = None


@dataclass
class PendingUnit:
    page_id: str
    box: Box
    char: str
    top5: tuple[str, ...]
    verdict: bool | None
    column: int
    meta: dict[str, object] = field(default_factory=dict)


@dataclass
class PendingRun:
    events: list[tuple[str, LayoutEvent, tuple[float, ...]]] = field(default_factory=list)
    pages: set[str] = field(default_factory=set)
    circle_ordinal: int | None = None


@dataclass(frozen=True)
class PageLocalLineSegment:
    page_id: str
    line_id: str
    units: tuple[PendingUnit, ...]
    text: str
    text_raw: str
    meta: dict[str, object]


@dataclass(frozen=True)
class PageSkipDecision:
    ko_cursor: EonhaeCursorState
    dropped_eonhae_units: int
    dropped_chinese_units: int
    dropped_circles: int
    in_eonhae: bool = False


@dataclass(frozen=True)
class HangulShapeIndex:
    class_keys: tuple[str | None, ...]
    key_indices: Mapping[str, tuple[int, ...]]
    model_name: str


@dataclass(frozen=True)
class EonhaePhraseScore:
    phrase: KoPhrase
    score: float
    agree_fraction: float
    num_unknown: int
    known_positions: int


@dataclass(frozen=True)
class EonhaeRunAssignment:
    accepted: bool
    reason: str | None
    phrase: KoPhrase | None
    score: float | None
    margin: float | None
    agree_fraction: float | None
    candidates: int
    num_unknown: int
    runner_up_score: float | None = None
    center: int = 0
    global_search: bool = False
    evidence: int = 0


@dataclass(frozen=True)
class EonhaeCursorState:
    anchor: int = 0
    dropped_since_accept: int = 0
    accepted_once: bool = False
    consecutive_drops: int = 0

    @property
    def center(self) -> int:
        """Expected phrase index for the next circle-delimited run."""
        return self.anchor + self.dropped_since_accept

    def uses_global_search(self, reacquire: int) -> bool:
        return not self.accepted_once or self.consecutive_drops >= reacquire

    def accept(self, index: int) -> EonhaeCursorState:
        return EonhaeCursorState(anchor=index + 1, accepted_once=True)

    def drop(self) -> EonhaeCursorState:
        return EonhaeCursorState(
            anchor=self.anchor,
            dropped_since_accept=self.dropped_since_accept + 1,
            accepted_once=self.accepted_once,
            consecutive_drops=self.consecutive_drops + 1,
        )


@dataclass(frozen=True)
class EonhaeHangulUnitDecision:
    char: str
    classifier_agrees: bool | None
    hangul_p: float | None
    argmax_key: str | None
    label_key: str | None


def box_key(box: Box) -> str:
    return f"{box.x},{box.y},{box.w},{box.h}"


def unit_id(page_id: str, role: str, box: Box) -> str:
    """Stable unit id from the page, role and final page-space box."""
    return "eo:" + hashlib.sha1(f"{page_id}|{role}|{box_key(box)}".encode()).hexdigest()[:20]


def line_id(page_id: str, role: str, phrase_key: str) -> str:
    """Stable line id from the page and phrase/chunk key."""
    return "eo:l:" + hashlib.sha1(f"{page_id}|{role}|{phrase_key}".encode()).hexdigest()[:20]


def load_source(path: Path, url: str) -> WikiSource:
    page = json.loads(path.read_text(encoding="utf-8"))["query"]["pages"][0]
    revision = page["revisions"][0]
    return WikiSource(
        title=str(page["title"]),
        revid=str(revision["revid"]),
        url=url,
        content=revision["slots"]["main"]["content"],
    )


def _source_api_url(host: str, revid: str) -> str:
    return MEDIAWIKI_REVISION_API.format(host=host, revid=revid)


def ensure_source(
    root: Path,
    filename: str,
    host: str,
    revid: str,
    *,
    downloader: Callable[..., Path] = net.download,
) -> Path:
    """Fetch a pinned Wikisource API JSON file when the upstream directory lacks it."""
    path = root / filename
    if path.exists():
        return path
    downloader(_source_api_url(host, revid), path, expected="json")
    return path


def load_pinned_source(path: Path, url: str, revid: str) -> WikiSource:
    """Read a Wikisource API JSON file and refuse it unless it holds the pinned revision."""
    source = load_source(path, url)
    if source.revid != revid:
        raise ValueError(f"{path.name} has revid {source.revid}, expected {revid}")
    return source


def load_sources(
    root: Path,
    volume: Volume,
    *,
    downloader: Callable[..., Path] = net.download,
) -> tuple[WikiSource, WikiSource]:
    spec = VOLUMES[volume]
    ko_path = ensure_source(root, KO_FILE, KO_HOST, KO_REVID, downloader=downloader)
    zh_path = ensure_source(root, spec["zh_file"], spec["zh_host"], spec["zh_revid"], downloader=downloader)
    return (
        load_pinned_source(ko_path, KO_URL, KO_REVID),
        load_pinned_source(zh_path, spec["zh_url"], spec["zh_revid"]),
    )


def _plain_wikitext(text: str) -> str:
    text = _COMMENT.sub("", text)
    text = _NOARG_PRINTED.sub(lambda m: m.group(1), text)
    out: list[str] = []
    index = 0
    while index < len(text):
        if text.startswith("{{", index):
            end = wikitext._closing(text, index, "{{", "}}")
            body = text[index + 2:end]
            parts = wikitext._split(body)
            name = parts[0].strip().lower()
            if name not in _DROP_TEMPLATE:
                out.append(_plain_wikitext("".join(wikitext._positional(parts[1:]))))
            index = min(end + 2, len(text))
        elif text.startswith("[[", index):
            end = wikitext._closing(text, index, "[[", "]]")
            target, _, label = text[index + 2:end].partition("|")
            out.append(_plain_wikitext(label or target.lstrip(":")))
            index = min(end + 2, len(text))
        elif text[index] == "<":
            tag = _TAG.match(text, index)
            index = tag.end() if tag else index + 1
        else:
            out.append(text[index])
            index += 1
    return "".join(out)


def characters(text: str) -> list[str]:
    """Printed characters after dropping spaces, slashes and punctuation, with old Hangul clusters."""
    out: list[str] = []
    for char in _plain_wikitext(text):
        category = unicodedata.category(char)
        if char == "/" or codepoints(char) in CIRCLES or category[0] in "PZ" or category == "Cc":
            continue
        code = ord(char)
        joins = category in ("Mn", "Mc") or 0x1160 <= code <= 0x11FF or 0xD7B0 <= code <= 0xD7FF
        if joins and out:
            out[-1] += char
        else:
            out.append(char)
    return out


def ko_lines(source: WikiSource, volume: Volume) -> list[KoLine]:
    label = VOLUMES[volume]["label"]
    sections = list(_SECTION.finditer(source.content))
    body = ""
    for index, match in enumerate(sections):
        if match.group(1) != label:
            continue
        start = match.end()
        end = sections[index + 1].start() if index + 1 < len(sections) else len(source.content)
        body = source.content[start:end]
        break
    lines: list[KoLine] = []
    title = f"老乞大諺解{label}"
    for raw in _HASH_LINE.findall(body):
        raw = raw.strip()
        if raw == title:
            continue
        chars = tuple(characters(raw))
        if chars:
            lines.append(KoLine(len(lines), raw, "".join(chars), chars))
    return lines


def ko_phrases(source: WikiSource, volume: Volume) -> list[KoPhrase]:
    phrases: list[KoPhrase] = []
    for line in ko_lines(source, volume):
        pieces = re.split(r"[/○〇]", line.raw)
        for phrase_index, raw in enumerate(pieces):
            raw = raw.strip()
            chars = tuple(characters(raw))
            if not chars:
                continue
            phrases.append(KoPhrase(
                index=len(phrases),
                line_index=line.index,
                phrase_index=phrase_index,
                raw=raw,
                text="".join(chars),
                chars=chars,
            ))
    return phrases


def zh_stream(source: WikiSource) -> list[str]:
    return characters(source.content)


def is_han(char: str) -> bool:
    return len(char) == 1 and unicodedata.name(char, "").startswith("CJK ")


def label_char(label: str) -> str | None:
    if not label.startswith("U+"):
        return None
    try:
        return chr(int(f"0x{label[2:]}", 0))
    except ValueError:
        return None


def is_han_label(label: str) -> bool:
    char = label_char(label)
    return char is not None and is_han(char)


def script_of(char: str) -> str:
    if is_han(char):
        return "han"
    if all(_is_hangul_code(ord(c)) for c in char):
        return "hangul"
    return "unknown"


def codepoints(char: str) -> str:
    return " ".join(f"U+{ord(c):04X}" for c in char)


def _is_hangul_code(code: int) -> bool:
    return (
        0x1100 <= code <= 0x11FF
        or 0x3130 <= code <= 0x318F
        or 0xA960 <= code <= 0xA97F
        or 0xAC00 <= code <= 0xD7AF
        or 0xD7B0 <= code <= 0xD7FF
    )


def wide_box(boxes: Sequence[Box]) -> float:
    return glossary.wide_box(boxes)


def kind(box: Box, top_label: str, pitch: float) -> str:
    """`hanja`, `circle` or `small`, using the fitted column pitch as scale."""
    aspect = box.w / box.h if box.h else 0.0
    if top_label in CIRCLES and box.h >= 0.45 * pitch and box.w >= 0.5 * pitch and 0.65 <= aspect <= 1.45:
        return "circle"
    if is_han_label(top_label) and box.h >= 0.48 * pitch and box.w >= 0.6 * pitch:
        return "hanja"
    return "small"


def median_small_box_height(boxes: Sequence[Box], top5: Sequence[Sequence[str]], pitch: float) -> float:
    """Median height of boxes classified as small on a page, with a page-wide fallback."""
    labels = _top_labels(top5)
    heights = sorted(
        box.h for box, label in zip(boxes, labels, strict=True)
        if kind(box, label, pitch) == "small"
    )
    if not heights:
        heights = sorted(box.h for box in boxes)
    if not heights:
        return 0.0
    middle = len(heights) // 2
    if len(heights) % 2:
        return float(heights[middle])
    return (heights[middle - 1] + heights[middle]) / 2


def eonhae_page_geometry(boxes: Sequence[Box], top5: Sequence[Sequence[str]], grid: PageGrid) -> EonhaePageGeometry:
    return EonhaePageGeometry(
        median_small_height=median_small_box_height(boxes, top5, grid.pitch),
        half_column_width=grid.pitch / 2,
    )


def eonhae_run_geometry_problems(
    events: Sequence[tuple[str, LayoutEvent]],
    page_geometry: Mapping[str, EonhaePageGeometry],
) -> tuple[EonhaeRunGeometryProblem, ...]:
    """Geometry reasons a post-circle 언해 run should be refused."""
    problems: list[EonhaeRunGeometryProblem] = []
    by_half: dict[tuple[str, int, int | None], list[LayoutEvent]] = {}
    for page_id, event in events:
        geometry = page_geometry.get(page_id)
        if geometry is None:
            continue
        if geometry.median_small_height and event.box.h < EONHAE_MIN_HEIGHT_MEDIAN_SHARE * geometry.median_small_height:
            problems.append(EonhaeRunGeometryProblem(
                "small box",
                (
                    f"{page_id} box {event.box} height {event.box.h} below "
                    f"{EONHAE_MIN_HEIGHT_MEDIAN_SHARE:.2f} of median {geometry.median_small_height:.1f}"
                ),
            ))
        if geometry.half_column_width and event.box.w < EONHAE_MIN_WIDTH_HALF_COLUMN_SHARE * geometry.half_column_width:
            problems.append(EonhaeRunGeometryProblem(
                "small box",
                (
                    f"{page_id} box {event.box} width {event.box.w} below "
                    f"{EONHAE_MIN_WIDTH_HALF_COLUMN_SHARE:.2f} of half-column {geometry.half_column_width:.1f}"
                ),
            ))
        by_half.setdefault((page_id, event.column, event.half), []).append(event)

    for (page_id, column, half), half_events in by_half.items():
        geometry = page_geometry.get(page_id)
        if geometry is None:
            continue
        ordered = sorted(half_events, key=lambda event: event.box.y)
        for previous, current in pairwise(ordered):
            overlap = min(previous.box.y + previous.box.h, current.box.y + current.box.h) - max(previous.box.y, current.box.y)
            if overlap > EONHAE_OVERLAP_SMALLER_SHARE * min(previous.box.h, current.box.h):
                problems.append(EonhaeRunGeometryProblem(
                    "overlap",
                    (
                        f"{page_id} column {column} half {half} boxes {previous.box} and {current.box} "
                        f"overlap vertically by {overlap}px"
                    ),
                ))
            gap = current.box.y - (previous.box.y + previous.box.h)
            if geometry.median_small_height and gap > EONHAE_MAX_GAP_MEDIAN_HEIGHTS * geometry.median_small_height:
                problems.append(EonhaeRunGeometryProblem(
                    "gap",
                    (
                        f"{page_id} column {column} half {half} gap {gap}px after {previous.box} exceeds "
                        f"{EONHAE_MAX_GAP_MEDIAN_HEIGHTS:.2f} medians"
                    ),
                ))
    return tuple(problems)


def _top_labels(top5: Sequence[Sequence[str]]) -> list[str]:
    return [labels[0] if labels else "" for labels in top5]


def _large_hanja_candidates(boxes: Sequence[Box], labels: Sequence[str], page_width: int) -> list[Box]:
    min_h = max(50.0, page_width * 0.045)
    min_w = max(45.0, page_width * 0.055)
    return [
        box for box, label in zip(boxes, labels, strict=True)
        if is_han_label(label) and box.h >= min_h and box.w >= min_w
    ]


def _x_clusters(boxes: Sequence[Box], page_width: int) -> list[tuple[float, int]]:
    if not boxes:
        return []
    tolerance = max(45.0, page_width * 0.04)
    clusters: list[list[float]] = []
    for centre in sorted(box.x + box.w / 2 for box in boxes):
        if not clusters or centre - clusters[-1][-1] > tolerance:
            clusters.append([centre])
        else:
            clusters[-1].append(centre)
    return [(sum(cluster) / len(cluster), len(cluster)) for cluster in clusters]


def _fit_grid_to_clusters(clusters: Sequence[tuple[float, int]], columns: int) -> tuple[float, float, float] | None:
    if not clusters:
        return None
    centres = [centre for centre, _ in clusters]
    weights = [weight for _, weight in clusters]
    gaps = [b - a for a, b in pairwise(centres)]
    regular = [gap for gap in gaps if 80 <= gap <= 280]
    if regular:
        pitch0 = sorted(regular)[len(regular) // 2]
    elif len(centres) >= 2:
        pitch0 = (centres[-1] - centres[0]) / max(1, min(columns - 1, len(centres) - 1))
    else:
        return None

    best: tuple[float, float, float, tuple[int, ...]] | None = None
    for anchor_i, centre in enumerate(centres):
        for anchor_k in range(columns):
            x0 = centre - anchor_k * pitch0
            assignments: list[int] = []
            residuals: list[float] = []
            for other in centres:
                k = round((other - x0) / pitch0)
                k = max(0, min(columns - 1, k))
                assignments.append(k)
                residuals.append(other - (x0 + k * pitch0))
            if len(set(assignments)) < min(len(assignments), max(3, columns // 2)):
                continue
            pitch = pitch0
            for _ in range(3):
                sw = sum(weights)
                sx = sum(w * x for w, x in zip(weights, centres, strict=True))
                sk = sum(w * k for w, k in zip(weights, assignments, strict=True))
                skk = sum(w * k * k for w, k in zip(weights, assignments, strict=True))
                sxk = sum(w * x * k for w, x, k in zip(weights, centres, assignments, strict=True))
                denom = sw * skk - sk * sk
                if abs(denom) < 1e-6:
                    break
                pitch = (sw * sxk - sx * sk) / denom
                x0 = (sx - pitch * sk) / sw
                if pitch <= 0:
                    break
                assignments = [
                    max(0, min(columns - 1, round((other - x0) / pitch))) for other in centres
                ]
            if pitch <= 0:
                continue
            residuals = [abs(other - (x0 + k * pitch)) for other, k in zip(centres, assignments, strict=True)]
            weighted = sum(w * r for w, r in zip(weights, residuals, strict=True)) / sum(weights)
            missing = columns - len(set(assignments))
            score = weighted + missing * pitch * 0.08
            if best is None or score < best[0]:
                best = (score, x0, pitch, tuple(assignments))
    if best is None:
        return None
    score, x0, pitch, _ = best
    if pitch < 80 or pitch > 280 or score > pitch * 0.35:
        return None
    return x0, pitch, score


def fit_column_grid(
    boxes: Sequence[Box],
    top5: Sequence[Sequence[str]],
    page_width: int,
    *,
    columns: int = 10,
) -> PageGrid | None:
    """Fit a ten-column half-leaf grid to confident large-hanja x-centres."""
    if len(boxes) != len(top5):
        raise ValueError("boxes and top5 must have the same length")
    labels = _top_labels(top5)
    candidates = _large_hanja_candidates(boxes, labels, page_width)
    clusters = _x_clusters(candidates, page_width)
    fitted = _fit_grid_to_clusters(clusters, columns)
    if fitted is None:
        return None
    x0, pitch, _ = fitted
    centers_ltr = tuple(x0 + k * pitch for k in range(columns))
    centers = tuple(reversed(centers_ltr))
    spans = tuple((center - pitch / 2, center + pitch / 2) for center in centers)
    return PageGrid(centers, pitch, spans)


def page_layout(
    boxes: Sequence[Box],
    top5: Sequence[Sequence[str]],
    grid: PageGrid,
    *,
    in_eonhae: bool = False,
) -> PageLayout:
    """Order detector boxes through the fitted grid and split text, circles, readings and 언해."""
    if not boxes:
        return PageLayout((), 0, in_eonhae, grid)
    if len(boxes) != len(top5):
        raise ValueError("boxes and top5 must have the same length")
    by_id = {id(box): tuple(labels) for box, labels in zip(boxes, top5, strict=True)}
    columns: list[list[Box]] = [[] for _ in grid.centers]
    for box in boxes:
        centre = box.x + box.w / 2
        distances = [abs(centre - x) for x in grid.centers]
        index = min(range(len(distances)), key=distances.__getitem__)
        if distances[index] <= grid.pitch * 0.6:
            columns[index].append(box)

    events: list[LayoutEvent] = []
    reading_events: list[LayoutEvent] = []
    reading_boxes = 0
    eonhae = in_eonhae
    for column_index, column in enumerate(columns):
        items = sorted(column, key=lambda b: b.y)
        pending_eonhae: list[tuple[Box, tuple[str, ...]]] = []

        def flush_eonhae(column_index: int = column_index) -> None:
            nonlocal pending_eonhae
            if not pending_eonhae:
                return
            mid = grid.centers[column_index]
            split: list[tuple[Box, tuple[str, ...], int]] = []
            for box, labels in pending_eonhae:
                if box.x < mid < box.x + box.w:
                    left_w = round(mid - box.x)
                    right_x = box.x + left_w
                    right_w = box.x + box.w - right_x
                    min_half = max(8, box.w * 0.30)
                    if left_w >= min_half and right_w >= min_half:
                        split.append((Box(x=right_x, y=box.y, w=right_w, h=box.h), labels, 0))
                        split.append((Box(x=box.x, y=box.y, w=left_w, h=box.h), labels, 1))
                    else:
                        side = 0 if box.x + box.w / 2 >= mid else 1
                        split.append((box, labels, side))
                else:
                    side = 0 if box.x + box.w / 2 >= mid else 1
                    split.append((box, labels, side))
            ordered = sorted(split, key=lambda item: (item[2], item[0].y, -(item[0].x + item[0].w / 2)))
            for box, labels, half in ordered:
                events.append(LayoutEvent("eonhae", box, labels, column_index, half))
            pending_eonhae = []

        for box in items:
            labels = by_id[id(box)]
            box_kind = kind(box, labels[0] if labels else "", grid.pitch)
            if box_kind == "circle":
                flush_eonhae()
                events.append(LayoutEvent("circle", box, labels, column_index))
                eonhae = True
            elif box_kind == "hanja":
                flush_eonhae()
                events.append(LayoutEvent("hanja", box, labels, column_index))
                eonhae = False
            elif eonhae:
                pending_eonhae.append((box, labels))
            else:
                reading_boxes += 1
                reading_events.append(LayoutEvent("reading", box, labels, column_index))
        flush_eonhae()
    return PageLayout(tuple(events), reading_boxes, eonhae, grid, tuple(reading_events))


def classifier_agrees(char: str, top5: Sequence[str], known: set[str]) -> bool | None:
    if script_of(char) != "han":
        codes = {codepoints(char)}
        if not codes & known:
            return None
        return bool(codes & set(top5))
    return glossary.agrees(glossary.Glyph(char), top5, known)


def names_a_character(char: str) -> bool:
    """Whether a transcribed character is one anyone can read: a private-use code point stands for a
    glyph only in its transcriber's font, so its box gets no unit, as in `glossary`."""
    return bool(char) and not any(unicodedata.category(c) == "Co" for c in char)


def page_local_line_segments(
    pending: Sequence[PendingUnit],
    *,
    role: str,
    phrase_key: str,
    text_raw: str | None = None,
    meta: Mapping[str, object] | None = None,
) -> tuple[PageLocalLineSegment, ...]:
    """Split one logical phrase/chunk into page-local line segments.

    The input order is preserved. Segment metadata records character offsets inside the logical
    phrase and links adjacent page-local pieces with ``continued_from`` / ``continues_to``.
    """
    by_page: list[tuple[str, list[PendingUnit]]] = []
    for unit in pending:
        if not by_page or by_page[-1][0] != unit.page_id:
            by_page.append((unit.page_id, []))
        by_page[-1][1].append(unit)
    ids = [line_id(page_id, role, phrase_key) for page_id, _units in by_page]
    segments: list[PageLocalLineSegment] = []
    offset = 0
    for index, (page_id, units) in enumerate(by_page):
        text = "".join(unit.char for unit in units)
        end = offset + len(units)
        segment_meta = {
            **(dict(meta) if meta else {}),
            "phrase_key": phrase_key,
            "phrase_start": offset,
            "phrase_end": end,
        }
        if index:
            segment_meta["continued_from"] = ids[index - 1]
        if index + 1 < len(ids):
            segment_meta["continues_to"] = ids[index + 1]
        segments.append(PageLocalLineSegment(
            page_id=page_id,
            line_id=ids[index],
            units=tuple(units),
            text=text,
            text_raw=text_raw if text_raw is not None else text,
            meta=segment_meta,
        ))
        offset = end
    return tuple(segments)


def skip_page_decision(
    ko_cursor: EonhaeCursorState,
    *,
    active_run: PendingRun | None = None,
    active_chinese: Sequence[PendingUnit] = (),
    circle_count: int = 0,
) -> PageSkipDecision:
    """State transition when a page is skipped by selection, image/grid failure or refusal."""
    next_cursor = ko_cursor
    if active_run is not None:
        next_cursor = next_cursor.drop()
    for _ in range(max(0, circle_count)):
        next_cursor = next_cursor.drop()
    return PageSkipDecision(
        ko_cursor=next_cursor,
        dropped_eonhae_units=len(active_run.events) if active_run else 0,
        dropped_chinese_units=len(active_chinese),
        dropped_circles=max(0, circle_count),
    )


def emits_unit(verdict: bool | None) -> bool:
    """Whether a matched classifier verdict is written as a dataset unit."""
    return verdict is not False


def hanja_unit_decisions(
    events: Sequence[LayoutEvent],
    alignment: HanjaAlignment,
) -> tuple[HanjaUnitDecision, ...]:
    """Per-event view of the hanja alignment, including the cutter's unit emission rule."""
    decisions: list[HanjaUnitDecision] = []
    for index, event in enumerate(events):
        matched = index < len(alignment.matched) and alignment.matched[index]
        char = alignment.chars[index] if index < len(alignment.chars) else None
        verdict = alignment.verdicts[index] if index < len(alignment.verdicts) else None
        stream_index = alignment.stream_indices[index] if index < len(alignment.stream_indices) else None
        safe_unjudged = alignment.safe_unjudged[index] if index < len(alignment.safe_unjudged) else False
        emitted = alignment.accepted and matched and (emits_unit(verdict) if verdict is not None else safe_unjudged)
        decisions.append(HanjaUnitDecision(
            event=event,
            char=char,
            verdict=verdict,
            matched=matched,
            emitted=emitted,
            stream_index=stream_index,
        ))
    return tuple(decisions)


def _safe_unjudged_hanja(
    verdicts: Sequence[bool | None],
    matched: Sequence[bool],
    stream_indices: Sequence[int | None],
) -> tuple[bool, ...]:
    safe = [verdict is not None for verdict in verdicts]
    index = 0
    while index < len(verdicts):
        if not matched[index] or verdicts[index] is not None:
            index += 1
            continue
        start = index
        while index < len(verdicts) and matched[index] and verdicts[index] is None:
            index += 1
        end = index
        before = start - 1
        after = end
        if before < 0 or after >= len(verdicts):
            continue
        before_stream = stream_indices[before]
        after_stream = stream_indices[after]
        anchored = (
            matched[before]
            and matched[after]
            and verdicts[before] is True
            and verdicts[after] is True
            and before_stream is not None
            and after_stream is not None
            and after_stream == before_stream + (end - start) + 1
        )
        if anchored:
            for place in range(start, end):
                safe[place] = True
    return tuple(safe)


def align_hanja(
    events: Sequence[LayoutEvent],
    stream: Sequence[str],
    cursor: int,
    known: set[str],
    *,
    window: int = ZH_WINDOW,
    min_agreement: float = ZH_MIN_AGREEMENT,
    min_agreeing: int = ZH_MIN_AGREEING_MATCHES,
) -> HanjaAlignment:
    """Align one page's large-hanja events to the Chinese stream near the cursor.

    Events are global: every detector event is either matched to a stream character or marked
    unmatched. The stream side is local to a candidate offset and may skip characters for missed
    printed hanja.
    """
    n = len(events)
    if not n:
        return HanjaAlignment(cursor, (), (), (), 1.0, 0, True, "no large hanja", 0)
    verdict_cache: dict[tuple[int, str], bool | None] = {}
    stream_positions: dict[str, list[int]] = {}
    for index, char in enumerate(stream):
        stream_positions.setdefault(char, []).append(index)

    def verdict_score(verdict: bool | None) -> float:
        if verdict is True:
            return 2.0
        if verdict is None:
            return 0.0
        return -1.25

    def verdict_at(event_index: int, char: str) -> bool | None:
        key = (event_index, char)
        if key not in verdict_cache:
            verdict_cache[key] = classifier_agrees(char, events[event_index].top5, known)
        return verdict_cache[key]

    weak_upper_neighbours: list[list[int]] = [[] for _ in events]
    for event_index, event in enumerate(events):
        for other_index, other in enumerate(events):
            if other_index == event_index or other.column != event.column:
                continue
            gap = event.box.y - (other.box.y + other.box.h)
            limit = max(event.box.h, other.box.h) * ZH_READING_ROW_GAP_HEIGHTS
            if 0 <= gap <= limit:
                weak_upper_neighbours[event_index].append(other_index)

    def label_chars(event: LayoutEvent) -> set[str]:
        chars: set[str] = set()
        for label in event.top5:
            char = label_char(label)
            if char is not None and is_han(char):
                chars.add(char)
        return chars

    def weak_lower_neighbour_penalty(event_index: int, char: str, verdict: bool | None) -> float:
        if verdict is True:
            return 0.0
        for other_index in weak_upper_neighbours[event_index]:
            if verdict_at(other_index, char) is not False:
                return -ZH_WEAK_LOWER_NEIGHBOUR_PENALTY
        return 0.0

    def voted_offsets(limit: int | None = None) -> list[tuple[int, float]]:
        votes: dict[int, float] = {}
        event_chars = [label_chars(event) for event in events]
        for i, chars in enumerate(event_chars):
            if not chars:
                continue
            for char in chars:
                for j in stream_positions.get(char, ()):
                    offset = j - i
                    if 0 <= offset < len(stream):
                        votes[offset] = votes.get(offset, 0.0) + 1.0
        ranked = sorted(votes.items(), key=lambda item: (-item[1], abs(item[0] - cursor), item[0]))
        return ranked if limit is None else ranked[:limit]

    votes = voted_offsets()
    vote_by_offset = dict(votes)

    def candidate_offsets(include_global: bool) -> list[int]:
        start = max(0, cursor - window)
        stop = min(len(stream), cursor + window + 1)
        near = set(range(max(0, cursor - 16), min(len(stream), cursor + 17)))
        local = [
            offset for offset in range(start, stop)
            if offset not in near
        ]
        local = sorted(
            local,
            key=lambda offset: (-vote_by_offset.get(offset, 0.0), abs(offset - cursor), offset),
        )[:56]
        offsets = sorted(near | set(local))
        if include_global:
            seen = set(offsets)
            for offset, _count in votes[:48]:
                if offset not in seen:
                    offsets.append(offset)
                    seen.add(offset)
        return offsets

    best: tuple[
        float,
        float,
        int,
        int,
        int,
        tuple[str | None, ...],
        tuple[bool | None, ...],
        tuple[bool, ...],
        tuple[int | None, ...],
    ] | None = None

    def evaluate(offsets: Sequence[int]) -> None:
        nonlocal best
        event_gap_penalty = -1.0
        stream_skip_penalty = -0.35
        max_span = max(n + 24, int(n * 1.8))
        for offset in offsets:
            if offset >= len(stream):
                continue
            chunk = stream[offset:min(len(stream), offset + max_span)]
            m = len(chunk)
            if not m:
                continue
            dp = [[-1e9] * (m + 1) for _ in range(n + 1)]
            back: list[list[tuple[int, int, str] | None]] = [[None] * (m + 1) for _ in range(n + 1)]
            dp[0][0] = 0.0
            for j in range(1, m + 1):
                dp[0][j] = dp[0][j - 1] + stream_skip_penalty
                back[0][j] = (0, j - 1, "skip")
            for i in range(1, n + 1):
                dp[i][0] = dp[i - 1][0] + event_gap_penalty
                back[i][0] = (i - 1, 0, "event")
                for j in range(1, m + 1):
                    event_gap = dp[i - 1][j] + event_gap_penalty
                    stream_skip = dp[i][j - 1] + stream_skip_penalty
                    verdict = verdict_at(i - 1, chunk[j - 1])
                    match = (
                        dp[i - 1][j - 1]
                        + verdict_score(verdict)
                        + weak_lower_neighbour_penalty(i - 1, chunk[j - 1], verdict)
                    )
                    if match >= event_gap and match >= stream_skip:
                        dp[i][j] = match
                        back[i][j] = (i - 1, j - 1, "match")
                    elif event_gap >= stream_skip:
                        dp[i][j] = event_gap
                        back[i][j] = (i - 1, j, "event")
                    else:
                        dp[i][j] = stream_skip
                        back[i][j] = (i, j - 1, "skip")
            end = max(range(m + 1), key=lambda j: dp[n][j] - 0.03 * abs(j - n))
            i, j = n, end
            chars_rev: list[str | None] = []
            verdicts_rev: list[bool | None] = []
            matched_rev: list[bool] = []
            stream_indices_rev: list[int | None] = []
            while i > 0 or j > 0:
                step = back[i][j]
                if step is None:
                    break
                pi, pj, op = step
                if op == "match":
                    char = chunk[j - 1]
                    chars_rev.append(char)
                    verdicts_rev.append(verdict_at(i - 1, char))
                    matched_rev.append(True)
                    stream_indices_rev.append(offset + j - 1)
                elif op == "event":
                    chars_rev.append(None)
                    verdicts_rev.append(None)
                    matched_rev.append(False)
                    stream_indices_rev.append(None)
                i, j = pi, pj
            if len(chars_rev) != n:
                continue
            chars = tuple(reversed(chars_rev))
            verdicts = tuple(reversed(verdicts_rev))
            matched = tuple(reversed(matched_rev))
            stream_indices = tuple(reversed(stream_indices_rev))
            matched_count = matched.count(True)
            judged = [v for v, is_matched in zip(verdicts, matched, strict=True) if is_matched and v is not None]
            agreement = judged.count(True) / len(judged) if judged else 1.0
            refused = judged.count(False)
            matched_share = matched_count / n
            distance = abs(offset - cursor)
            consumed = end
            score = (
                dp[n][end]
                + agreement * 12
                + matched_share * 8
                - refused * 1.5
                - distance * 0.015
                - abs(consumed - matched_count) * 0.05
            )
            if best is None or score > best[0]:
                best = (score, agreement, matched_count, offset, refused, chars, verdicts, matched, stream_indices)

    evaluate(candidate_offsets(include_global=False))
    if best is None or best[1] < min_agreement or best[2] < int(n * 0.7):
        evaluate(candidate_offsets(include_global=True))

    if best is None:
        return HanjaAlignment(None, (), (), (), 0.0, 0, False, "no stream window", 0)
    _, agreement, matched_count, offset, refused, chars, verdicts, matched, stream_indices = best
    consumed = 0
    last_stream_index = -1
    for char in chars:
        if char is None:
            continue
        for stream_index in range(max(offset, last_stream_index + 1), len(stream)):
            if stream[stream_index] == char:
                last_stream_index = stream_index
                break
    if last_stream_index >= offset:
        consumed = last_stream_index - offset + 1
    refused_share = refused / matched_count if matched_count else 1.0
    judged_count = sum(1 for verdict, is_matched in zip(verdicts, matched, strict=True) if is_matched and verdict is not None)
    agreeing = sum(1 for verdict, is_matched in zip(verdicts, matched, strict=True) if is_matched and verdict is True)
    accepted = (
        agreement >= min_agreement
        and matched_count >= n * 0.70
        and judged_count > 0
        and agreeing >= min_agreeing
        and agreeing >= math.ceil(judged_count / 2)
        and refused_share <= glossary.REFUSED_SHARE
    )
    safe_unjudged = _safe_unjudged_hanja(verdicts, matched, stream_indices)
    reason = (
        f"zh offset {offset}, consumed {consumed}, agreement {agreement:.2f}, "
        f"matched {matched_count} of {n}, judged {judged_count}, agreeing {agreeing}, "
        f"refused {refused} of {matched_count}"
    )
    if not accepted:
        reason += "; page left out as misaligned"
    return HanjaAlignment(
        offset,
        chars,
        verdicts,
        matched,
        agreement,
        refused,
        accepted,
        reason,
        consumed,
        stream_indices,
        safe_unjudged,
    )


def _class_label_text(label: str) -> str | None:
    if label == "other":
        return None
    chars: list[str] = []
    for piece in label.split():
        if not piece.startswith("U+"):
            return None
        try:
            chars.append(chr(int(f"0x{piece[2:]}", 0)))
        except ValueError:
            return None
    return "".join(chars) or None


def hangul_shape_index(classes_: Sequence[str], model_name: str = "") -> HangulShapeIndex:
    """Map a Hangul classifier's class columns to comparable shape keys."""
    class_keys: list[str | None] = []
    grouped: dict[str, list[int]] = {}
    for index, label in enumerate(classes_):
        text = _class_label_text(label)
        key = clusters.shape_key(text) if text and script_of(text) == "hangul" else None
        class_keys.append(key)
        if key is not None:
            grouped.setdefault(key, []).append(index)
    return HangulShapeIndex(
        class_keys=tuple(class_keys),
        key_indices={key: tuple(indices) for key, indices in grouped.items()},
        model_name=model_name,
    )


def hangul_model_name(path: Path) -> str:
    """Compact run name for unit metadata, normally ``run/e3`` from ``.../run/e3/classifier.onnx``."""
    if path.parent.parent.name:
        return f"{path.parent.parent.name}/{path.parent.name}"
    return path.parent.name


def _hangul_key(char: str) -> str | None:
    return clusters.shape_key(char) if script_of(char) == "hangul" else None


def _known_hangul_keys(chars: Sequence[str], index: HangulShapeIndex) -> set[str]:
    keys = {_hangul_key(char) for char in chars}
    return {key for key in keys if key is not None and key in index.key_indices}


#: How many of the hangul model's best readings, over all its classes, a label must be among for its
#: box to agree with it. Counted without the candidate phrases, so one candidate cannot agree with
#: itself.
OPEN_TOP_K = 5


def open_top_keys(row: Sequence[float], index: HangulShapeIndex, k: int = OPEN_TOP_K) -> set[str]:
    """The shape keys of the model's `k` best readings of one box, over all its classes."""
    keys: list[str] = []
    for class_index in sorted(range(len(row)), key=lambda i: -float(row[i]))[:k]:
        key = index.class_keys[class_index] if class_index < len(index.class_keys) else None
        if key is not None and key not in keys:
            keys.append(key)
    return set(keys)


def top_class_labels(row: Sequence[float], classes_: Sequence[str], k: int = OPEN_TOP_K) -> tuple[str, ...]:
    """The model's top class labels, with `other` kept if it is one of the top slots."""
    return tuple(classes_[class_index] for class_index in sorted(range(len(row)), key=lambda i: -float(row[i]))[:k])


def restricted_hangul_distribution(
    row: Sequence[float],
    keys: set[str] | frozenset[str],
    index: HangulShapeIndex,
) -> dict[str, float]:
    """Probability mass over selected Hangul shape keys, renormalized inside that set.

    Probability assigned to classifier classes outside ``keys`` is ignored; this avoids treating an
    unrestricted look-alike argmax as evidence for a candidate phrase.
    """
    masses: dict[str, float] = {}
    for key in keys:
        probability = 0.0
        for class_index in index.key_indices.get(key, ()):
            if class_index < len(row):
                probability += float(row[class_index])
        masses[key] = probability
    total = sum(masses.values())
    if total <= 0.0:
        uniform = 1.0 / len(keys) if keys else 0.0
        return {key: uniform for key in keys}
    return {key: probability / total for key, probability in masses.items()}


def _score_eonhae_phrase(
    phrase: KoPhrase,
    rows: Sequence[Sequence[float]],
    union_keys: set[str],
    index: HangulShapeIndex,
) -> EonhaePhraseScore:
    known_union = {key for key in union_keys if key in index.key_indices}
    neutral = math.log(1.0 / len(known_union)) if known_union else 0.0
    total = 0.0
    agree = 0
    known_positions = 0
    unknown = 0
    for char, row in zip(phrase.chars, rows, strict=True):
        key = _hangul_key(char)
        if key is None:
            total += neutral
            unknown += 1
            continue
        if key not in index.key_indices:
            total += neutral
            unknown += 1
            continue
        distribution = restricted_hangul_distribution(row, known_union, index) if known_union else {}
        probability = max(distribution.get(key, 0.0), 1e-12)
        total += math.log(probability)
        known_positions += 1
        agree += key in open_top_keys(row, index)
    score = total / len(rows) if rows else 0.0
    agree_fraction = agree / known_positions if known_positions else 1.0
    return EonhaePhraseScore(phrase, score, agree_fraction, unknown, known_positions)


def assign_eonhae_run(
    rows: Sequence[Sequence[float]],
    phrases: Sequence[KoPhrase],
    center: int,
    index: HangulShapeIndex,
    *,
    window: int = 12,
    margin: float = 1.0,
    min_agree: float = 0.6,
    min_evidence: int = 3,
    global_search: bool = False,
    min_index: int | None = None,
) -> EonhaeRunAssignment:
    """Choose the Korean phrase for one detected 언해 run from restricted classifier scores.

    Unknown Hangul classes receive the neutral log-probability ``log(1 / known_union_size)`` and are
    reported in ``num_unknown``. Non-Hangul phrase characters do not enter the Hangul union or score.
    The agreement fraction is measured over known Hangul positions. A phrase must carry enough
    known Hangul positions to give the classifier real evidence before margin or agreement can
    accept it.
    """
    n = len(rows)
    if global_search:
        # The book is read forward: a search of the whole volume still starts near the last phrase
        # accepted, or a run the detector cut wrong lands on a like phrase far behind.
        candidates = [
            phrase
            for phrase in phrases
            if len(phrase.chars) == n and (min_index is None or phrase.index >= min_index)
        ]
    else:
        candidates = [
            phrase
            for phrase in phrases
            if abs(phrase.index - center) <= window
            and len(phrase.chars) == n
            and (min_index is None or phrase.index >= min_index)
        ]
    if not candidates:
        return EonhaeRunAssignment(
            False,
            "no candidate phrase",
            None,
            None,
            None,
            None,
            0,
            0,
            center=center,
            global_search=global_search,
        )

    evidence_floor = max(0, min_evidence)
    candidates = [
        phrase
        for phrase in candidates
        if sum(
            1
            for char in phrase.chars
            if (key := _hangul_key(char)) is not None and key in index.key_indices
        ) >= evidence_floor
    ]
    if not candidates:
        return EonhaeRunAssignment(
            False,
            "too little evidence",
            None,
            None,
            None,
            None,
            0,
            0,
            center=center,
            global_search=global_search,
        )

    # A phrase the book repeats (언머 갑슬 밧고져 ᄒᆞᄂᆞᆫ다) is one choice, at its occurrence nearest the
    # cursor: identical shapes score identically, and as rivals they would leave no margin.
    nearest: dict[tuple[str, ...], KoPhrase] = {}
    for phrase in candidates:
        shapes = tuple(_hangul_key(char) or char for char in phrase.chars)
        kept = nearest.get(shapes)
        if kept is None or abs(phrase.index - center) < abs(kept.index - center):
            nearest[shapes] = phrase
    candidates = sorted(nearest.values(), key=lambda phrase: phrase.index)

    union_keys = {
        key
        for phrase in candidates
        for char in phrase.chars
        if (key := _hangul_key(char)) is not None
    }
    scored = sorted(
        (_score_eonhae_phrase(phrase, rows, union_keys, index) for phrase in candidates),
        key=lambda item: (item.score, -abs(item.phrase.index - center), -item.phrase.index),
        reverse=True,
    )
    best = scored[0]
    runner_up_score = scored[1].score if len(scored) > 1 else None
    phrase_margin = None if runner_up_score is None else best.score - runner_up_score
    if phrase_margin is not None and phrase_margin < margin:
        return EonhaeRunAssignment(
            False,
            "ambiguous phrase",
            best.phrase,
            best.score,
            phrase_margin,
            best.agree_fraction,
            len(candidates),
            best.num_unknown,
            runner_up_score,
            center,
            global_search,
            best.known_positions,
        )
    if best.agree_fraction < min_agree:
        return EonhaeRunAssignment(
            False,
            "classifier disagrees",
            best.phrase,
            best.score,
            phrase_margin,
            best.agree_fraction,
            len(candidates),
            best.num_unknown,
            runner_up_score,
            center,
            global_search,
            best.known_positions,
        )
    return EonhaeRunAssignment(
        True,
        None,
        best.phrase,
        best.score,
        phrase_margin,
        best.agree_fraction,
        len(candidates),
        best.num_unknown,
        runner_up_score,
        center,
        global_search,
        best.known_positions,
    )


def eonhae_hangul_unit_decisions(
    phrase: KoPhrase,
    rows: Sequence[Sequence[float]],
    index: HangulShapeIndex,
) -> tuple[EonhaeHangulUnitDecision, ...]:
    """Per-character Hangul keep/drop decisions inside an already accepted phrase."""
    phrase_known = _known_hangul_keys(phrase.chars, index)
    decisions: list[EonhaeHangulUnitDecision] = []
    for char, row in zip(phrase.chars, rows, strict=True):
        key = _hangul_key(char)
        if key is None:
            decisions.append(EonhaeHangulUnitDecision(char, None, None, None, None))
        elif key not in index.key_indices:
            decisions.append(EonhaeHangulUnitDecision(char, None, None, None, key))
        else:
            distribution = restricted_hangul_distribution(row, phrase_known, index)
            argmax = max(distribution, key=lambda candidate: (distribution[candidate], candidate))
            decisions.append(EonhaeHangulUnitDecision(
                char=char,
                classifier_agrees=key in open_top_keys(row, index),
                hangul_p=distribution.get(key, 0.0),
                argmax_key=argmax,
                label_key=key,
            ))
    return tuple(decisions)
