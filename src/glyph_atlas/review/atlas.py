"""Character catalogue, bounded image crops and durable visual review rounds."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import random
import threading
import unicodedata
from collections import Counter
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import Response
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from .. import images, refs
from .. import production as production_metadata
from ..production import production_info
from ..schema import Box, ReviewState, Script, Unit
from . import status
from .request_cache import file_stamp, memoize
from .store import SEEN, BadRequest, ReviewRequest, Store

_IMAGE_SLOTS = threading.BoundedSemaphore(2)
CONFIRMED = {ReviewState.REVIEWED, ReviewState.DOUBLE_REVIEWED, ReviewState.ADJUDICATED}


def label(unit: Unit) -> str:
    return unicodedata.normalize("NFC", unit.reading or unit.text_source or "").strip()


def review_state(decision: str | None) -> str:
    if decision in CONFIRMED:
        return "checked"
    if decision == ReviewState.DISPUTED:
        return "flagged"
    return "pending"


def seen_boxes(events: Iterable[Any]) -> dict[str, dict | None]:
    """The box each unit was last shown in a quiz round without being flagged, oldest event first.

    A later `seen` event with no value is the undo of a round, and the unit is pending again. The box
    is kept because a crop that moved since is a different crop, and it has not been seen.
    """
    boxes: dict[str, dict | None] = {}
    for event in events:
        if event.field != SEEN:
            continue
        if not event.new:
            boxes.pop(event.target_id, None)
            continue
        try:
            boxes[event.target_id] = json.loads(event.evidence or "{}").get("box")
        except ValueError:
            continue
    return boxes


def single_character(text: str) -> bool:
    bases = [c for c in text if not unicodedata.combining(c)
             and not 0xFE00 <= ord(c) <= 0xFE0F and not 0xE0100 <= ord(c) <= 0xE01EF]
    return len(bases) == 1


def review_priority(unit: Unit) -> int:
    """Put measured uncertainty first; a sole transcription candidate is not confidence."""
    confidence = unit.confidence
    scores = [value for value in (confidence.text, confidence.segmentation)
              if value is not None] if confidence else []
    if scores and min(scores) < 0.9:
        return 0
    if scores and min(scores) >= 0.99:
        return 2
    return 1


def character_group(unit: Unit) -> str:
    name = unicodedata.name(label(unit)[0], "") if label(unit) else ""
    if name.startswith(("HIRAGANA", "KATAKANA", "HENTAIGANA")):
        return "kana"
    return "kanji" if name.startswith("CJK") else "other"


#: The key the alignment-repair pass writes into `Unit.meta` (`glyph_atlas.repair.META_KEY`).
REPAIR_KEY = "alignment_repair"


def repair_of(unit: Unit) -> dict[str, Any] | None:
    """What the alignment-repair pass recorded about this occurrence, or `None` when it did not.

    A unit whose alignment was mended carries ``meta["alignment_repair"]``. Its own record says
    whether the pass was sure of the mended box, and a reader has to be able to see that: a crop
    that the pass could not vouch for is worth less than one it never touched, and nothing about it
    is a person's decision.
    """
    recorded = (unit.meta or {}).get(REPAIR_KEY)
    return recorded if isinstance(recorded, dict) else None


def repair_metadata(unit: Unit) -> dict[str, Any] | None:
    """The repair record reduced to what a view needs to mark uncertainty.

    `quiz` false is the pass saying a person should not be shown this crop as a quiz candidate; the
    reason is the point of the flag, because a view that hides a crop has to be able to say what the
    pass could not decide about it. Nothing here is a review state: a mended unit is still a machine
    placement, so it stays out of every human count. `None` means the pass did not touch this unit,
    and then the answer says nothing at all rather than saying "fine".
    """
    recorded = repair_of(unit)
    if recorded is None:
        return None
    return {
        "status": recorded.get("status"),
        "machine": bool(recorded.get("machine", True)),
        "verified": bool(recorded.get("verified", False)),
        "reliable": bool(recorded.get("reliable", False)),
        "withheld": bool(recorded.get("withheld", False)),
        "quiz": bool(recorded.get("quiz", True)),
        "reason": recorded.get("reason"),
    }


def repair_withheld(unit: Unit) -> bool:
    """Whether the repair pass withheld this occurrence from a quiz.

    Only an explicit `quiz: false` withholds. A unit the pass never touched, a unit it mended
    confidently, and a repaired unit a person has since settled are all quiz candidates: the flag is
    the pass's own statement about one crop, not a verdict on the character.
    """
    recorded = repair_of(unit)
    return bool(recorded and recorded.get("quiz") is False)


#: The CJK compatibility ideographs. NFC maps each to its unified twin, but here each is a character
#: of its own with its own row in the character layer, so identity text keeps it as written.
_COMPATIBILITY_IDEOGRAPHS = ((0xF900, 0xFAFF), (0x2F800, 0x2FA1F))


def _compose(text: str) -> str:
    """NFC, except that a compatibility ideograph stays the character it is."""
    parts: list[str] = []
    run: list[str] = []
    for char in text:
        if any(low <= ord(char) <= high for low, high in _COMPATIBILITY_IDEOGRAPHS):
            parts.append(unicodedata.normalize("NFC", "".join(run)))
            parts.append(char)
            run = []
        else:
            run.append(char)
    parts.append(unicodedata.normalize("NFC", "".join(run)))
    return "".join(parts)


def identity_text(value: str) -> str:
    """The characters a written identity names, composed: notation read out, or the input as it stands.

    One helper for both sides of a search, so a query and a recorded identity arrive at the same
    shape by the same route. An explicit `U+XXXX` — or several of them separated by spaces, which is
    how a character written with a combining mark is recorded — is read into characters; anything
    else is kept literally, so `A` is the letter and `1` is the digit rather than U+000A and U+0001.
    NFC then makes one character of either spelling of a voiced kana, so が, U+304C and か + U+3099
    are one value without any of them being re-encoded. A compatibility ideograph is the exception
    NFC would get wrong: U+FA10 is not U+585A, so it is kept.

    The notation is recognised before any case folding: upper-casing first would turn the `u` of
    `u+2a708` into a `U`, which is harmless, but it would also rewrite the hex digits of a literal
    query, so the points are matched case-insensitively as they are written instead.
    """
    term = value.strip()
    points = term.split()
    if points and all(point[:2].upper() == "U+" for point in points):
        try:
            # Base 16 explicitly: `base=0` reads only `0x`-prefixed literals and raises on `2A708`,
            # which would make every `U+XXXX` query match nothing at all.
            text = "".join(chr(int(point[2:], 16)) for point in points)  # noqa: FURB166
            return _compose(text)
        except ValueError:
            return _compose(term)
    return _compose(term)


def search_term(q: str) -> str:
    """What a query asks for, as the characters it names, in NFC."""
    return identity_text(q)


def written_identity(unit: Unit) -> str:
    """The character a unit is recorded as having been written with, as characters, in NFC.

    `Unit.unicode` is the written identity and is the field a search for one character has to reach:
    a reading says how a character is pronounced, and several characters share one. A unit that
    carries no `unicode` falls back to its own character, which is a base and its marks when the
    source wrote a voiced kana as two code points.
    """
    recorded = (unit.unicode or "").strip()
    if recorded:
        return identity_text(recorded)
    return identity_text(one_character(label(unit)))


def one_character(text: str) -> str:
    """`text` when it is one character, marks included; else the empty string.

    One character is a base and the marks that belong to it: か + U+3099 is one character written
    with two code points, so counting code points would refuse it while counting が as one, and the
    two are the same character. The bases are counted, as `single_character` counts them.
    """
    if not text:
        return ""
    bases = [character for character in text
             if not unicodedata.combining(character)
             and not 0xFE00 <= ord(character) <= 0xFE0F
             and not 0xE0100 <= ord(character) <= 0xE01EF]
    return text if len(bases) == 1 else ""


def canonical_identity(value: str) -> str:
    """The stored form of a written identity: `"𪜈"` and `"U+2A708"` both answer `"U+2A708"`.

    The same rule the layers route applies, stated here because a round names a written identity and
    the two must agree: an identity typed as a character and one pasted as a code point are one
    correction, not two. A character written with combining marks keeps its sequence, in the order it
    was written.
    """
    points = identity_text(value).split()
    if not points:
        raise BadRequest("A character correction needs a character or a code point.")
    return " ".join(refs.to_code_points(points[0])) if len(points) == 1 else " ".join(points)


def stored_identity(unit: Unit) -> str | None:
    """The identity already recorded on a unit, canonically spelled, or `None` when it has none.

    A recorded identity is read as the code points it is, not parsed like typed input: U+3000 is
    whitespace, and parsing it would leave nothing to compare a correction with.
    """
    if unit.unicode and unit.unicode.strip():
        return " ".join(point.upper() for point in unit.unicode.split())
    written = written_identity(unit)
    return " ".join(refs.to_code_points(written)) if written else None


def reading_of(identity: str) -> str | None:
    """What a corrected written identity reads as, when the character layer says so in one way.

    A kana with one stated reading reads that (リ reads り), a ligature reads what its components read
    (𪜈 reads とも), and a kanji, which the layer gives no kana reading, reads as itself. A character
    with several stated readings (𛄝: ん, む, も) leaves the choice to a person, so none is derived.
    """
    character = refs.character(" ".join(refs.to_code_points(identity))) if identity else None
    if character is None:
        return None
    if character.ligature and character.ligature.reading:
        return refs.to_hiragana(character.ligature.reading)
    if len(character.readings) == 1:
        return character.readings[0]
    if not character.readings and character.script == Script.HAN:
        return character.char
    return None


def script_of_identity(text: str) -> str:
    """The script of a written identity: that of its base character, whatever marks it carries."""
    base = "".join(char for char in text if not unicodedata.combining(char))
    return str(refs.script_of(base)) if len(base) == 1 else "unknown"


def is_space_identity(text: str) -> bool:
    """Whether a written identity is only whitespace, which has no glyph to review or to find.

    The corpus records 1,220 U+3000 and 5 U+0020 units with no reading. They are real records and
    are kept, but they are not reviewable characters: their crops all show the same blank paper and a
    search for a space is a search for nothing.
    """
    return bool(text) and not text.strip()


def matches(records: list[tuple[Unit, int]], q: str) -> list[tuple[Unit, int]]:
    """The units recorded as having been written with the character the query names.

    The match is exact and on the written identity, which is what makes the answer trustworthy: a
    search for one character does not silently widen to every form that shares its reading or its
    字母. `refs.search` answers a different question — what the character layer knows about a reading
    — and is deliberately not used here.
    """
    wanted = search_term(q)
    if not wanted:
        return []
    return [(unit, revision) for unit, revision in records
            if written_identity(unit) == wanted]



def crop_bounds(image: Image.Image, box: Box | tuple[float, ...] | None,
                context: bool = False) -> tuple[int, int, int, int]:
    """The source pixels a crop is drawn from: the same bounds the thumbnail actually cuts.

    One function for the crop that is drawn and the region that is highlighted, because two
    derivations of the same rectangle is how a highlight drifts off its character: the crop is cut
    from the source image in source pixels, while a box recorded against the page is in page pixels,
    and an image cached at another size than its page makes those two different. Everything that
    needs to agree goes through here.
    """
    if box is None:
        return (0, 0, image.width, image.height)
    if isinstance(box, Box):
        x, y, w, h = box.x, box.y, box.w, box.h
    else:
        x, y, w, h = box
    margin = max(w, h) * (0.65 if context else 0.08)
    bounds = (max(0, int(x - margin)), max(0, int(y - margin)),
              min(image.width, int(x + w + margin)), min(image.height, int(y + h + margin)))
    if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
        raise ValueError("The crop falls outside the image.")
    return bounds


def _pixels(path: Path) -> tuple[bool, tuple[int, int] | None]:
    """Whether a file is a whole image, and its size: header and end marker only, never a decode."""
    try:
        with Image.open(path) as image:
            size = image.size
            # The format decides what "whole" means, and it is read before the check rather than
            # assumed: the cache holds whatever a holder serves, and a PNG, GIF or WebP that is
            # perfectly good does not end with a JPEG's marker.
            kind = image.format
            image.verify()
    except (OSError, ValueError, Image.DecompressionBombError):
        return False, None
    if kind == "JPEG":
        try:
            total = path.stat().st_size
            with open(path, "rb") as handle:
                if total >= 2:
                    handle.seek(total - 2)
                    if handle.read(2) != b"\xff\xd9":
                        # A JPEG that does not end with its end-of-image marker is a download that
                        # stopped early. `verify()` accepts one — it checks the structure it can
                        # reach and reports nothing about the bytes that never arrived — while the
                        # decode that draws the crop raises, which is how a truncated cache file
                        # became a 500 in the reviewer. Two bytes read is the cost of not being
                        # surprised, and it is only read for the format that has such a marker.
                        return False, None
        except OSError:
            return False, None
    return True, size


def _checked_pixels(path: str, stamp: int) -> tuple[bool, tuple[int, int] | None]:
    """Keep completed image checks across restarts, keyed by file modification time."""
    # Hundreds of catalogue pages may live on a mounted Windows disk. Reopening
    # all of them before serving the first crop makes a cold start take minutes.
    key = hashlib.sha256(f"{path}\0{stamp}".encode()).hexdigest()
    target = images.cache_root() / "image-metadata-v1" / key[:2] / f"{key}.json"
    try:
        record = json.loads(target.read_text())
        valid, size = record["valid"], record["size"]
        if isinstance(valid, bool) and (size is None or (
                isinstance(size, list) and len(size) == 2
                and all(isinstance(n, int) and n > 0 for n in size))):
            return valid, tuple(size) if size else None
    except (OSError, ValueError, KeyError, TypeError):
        pass
    result = _pixels(Path(path))
    # A cache failure must never prevent displaying an otherwise valid image.
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        from .media import MediaCache
        MediaCache._write(target, json.dumps({"valid": result[0], "size": result[1]}).encode())
    except OSError:
        pass
    return result


@lru_cache(maxsize=4096)
def readable_image(path: str, stamp: int) -> bool:
    """Whether a cached file is an image a viewer can draw, judged once per content and change.

    A cache can hold a file a download left truncated, or one that is not an image at all, and a
    cache hit is not evidence that the bytes are usable. The check reads the header and the end
    marker and decodes nothing, so it stays cheap next to drawing the crop.

    Bounded by construction. The cache is keyed by the file's own path and modification time, so a
    file is judged once however many occurrences point at it, and a replaced file is judged again
    because its stamp moved.
    """
    return _checked_pixels(path, stamp)[0]


@lru_cache(maxsize=4096)
def image_size(path: str, stamp: int) -> tuple[int, int] | None:
    """The pixel size of a cached image, or `None` when it is not one a viewer can draw.

    The check `readable_image` makes and the header read `page_image` needs are the same read, so
    they are one function and one cache: a caller that asks for the size has already asked whether
    the file is a whole image.
    """
    return _checked_pixels(path, stamp)[1]


@lru_cache(maxsize=3)
def decoded_image(path: str, stamp: int) -> Image.Image:
    """Reuse nearby crops without repeatedly decoding the same manuscript image."""
    with Image.open(path) as image:
        return image.convert("RGB")


@lru_cache(maxsize=256)
def thumbnail(path: str, stamp: int, box: tuple[float, ...] | None, context: bool) -> bytes:
    """Two workers share a cache of three source images and 256 small JPEG crops."""
    with _IMAGE_SLOTS:
        image = decoded_image(path, stamp)
        if box:
            # The same bounds `crop_bounds` reports to the client, so the crop and the outline over
            # it are the same rectangle rather than two derivations that can drift apart.
            image = image.crop(crop_bounds(image, box, context))
        else:
            image = image.copy()
        image.thumbnail((640, 640) if context else (240, 280), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=88)
        return output.getvalue()


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    revision: int = Field(ge=0)
    image_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    verdict: Literal["match", "wrong", "unsure"]
    issue: Literal["reading", "character", "crop", "merged", "blank", "unclear", "other"] | None = None
    correction: str | None = Field(default=None, max_length=32)
    #: The written identity the reviewer says this crop is, as a character or a `U+XXXX` sequence.
    #: Separate from `correction`, which is a reading: the two are different layers, and a correction
    #: to one never rewrites the other.
    character: str | None = Field(default=None, max_length=32)


class Seen(BaseModel):
    """A crop the round showed and the reviewer left unflagged: seen, and nothing more."""

    model_config = ConfigDict(extra="forbid")
    id: str
    image_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class Round(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    client_id: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=32)
    answers: list[Answer] = Field(default_factory=list, max_length=4096)
    #: The crops left unflagged. They are not answers: a crop nobody marked is not a confirmation,
    #: so it is recorded as seen, which keeps it out of the next round and out of every count.
    seen: list[Seen] = Field(default_factory=list, max_length=4096)


class Undo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: str = Field(min_length=1, max_length=128)


class CharacterEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    client_id: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=0)
    image_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    reading: str | None = Field(default=None, min_length=1, max_length=32)
    verdict: Literal["match", "wrong", "unsure"]
    issue: Literal["reading", "crop", "merged", "blank", "unclear", "other"] = "reading"
    correction: str | None = Field(default=None, max_length=32)
    note: str = Field(default="", max_length=2000)
    box: Box | None = None


def router(store: Store, *, corpus_reviews=None, media=None) -> APIRouter:
    from .server import cached_image

    api = APIRouter()
    image_root = images.images_root()
    inference_failures: set[str] = set()

    @lru_cache(maxsize=2)
    def url_index(stamp: int) -> dict:
        return {record.url: record for record in images.index(image_root) if not record.superseded_by}

    @memoize
    def index_stamp() -> int:
        path = image_root / "index.parquet"
        return file_stamp(path) or 0

    @lru_cache(maxsize=512)
    def page_file(page_id: str, stamp: int) -> Path | None:
        """The cached file a page is drawn from, or `None` when there is not one to draw.

        The lookup is cached on the page and the index's stamp, and deliberately says nothing about
        whether the file is usable: a value that carried its size or its validity would be answered
        from the cache after the file behind it changed, which is how a replaced or damaged file stays
        invisible. What the file *is* is judged separately, per file and per change.
        """
        page = store.page(page_id)
        if page is None:
            return None
        path = cached_image(page.sha256) if page.sha256 else None
        if path is None:
            record = url_index(stamp).get(page.image)
            path = cached_image(record.sha256) if record else None
        return path

    @memoize
    def page_image(page_id: str, stamp: int) -> tuple[Path, float, float] | None:
        """The cached file for a page and the pixels it holds per pixel the page records.

        The scale is measured from the file rather than assumed: a page may name the checksum of an
        image that is not the size the page records — a reduced copy, or a record whose dimensions
        came from a service — and a box in page pixels drawn onto it without the scale would be in
        the wrong place by exactly that factor.
        """
        path = page_file(page_id, stamp)
        if path is None:
            return None
        # Measured now, not remembered: `image_size` is itself cached per file *and modification
        # time*, so a file that changed under a cached page record is measured again.
        modified = file_stamp(path)
        size = image_size(str(path), modified) if modified is not None else None
        if size is None:
            # A file the cache cannot produce an image from is not a source. Returning `None` makes
            # the occurrence unavailable, which is what it is; raising here would take the catalogue
            # down for every page because one cached file is damaged.
            return None
        page = store.page(page_id)
        if page is None:
            return None
        width, height = size
        if not page.width or not page.height:
            return path, 1, 1
        return path, width / page.width, height / page.height

    def image_source(unit: Unit) -> tuple[Path, tuple[float, ...] | None, tuple[float, float]] | None:
        """The file a unit's image is cut from, the box in *its* pixels, and pixels per page pixel.

        A unit whose image is a pre-cut crop file has no page behind it, so its box is the whole file
        and the scale is one. Everything that draws or measures a crop goes through here, so the crop
        and the region around it cannot be cut from different places.
        """
        if unit.crop_sha256:
            path = cached_image(unit.crop_sha256)
            # A pre-cut crop is used only if it is an image. A damaged one falls through to the page
            # the unit came from rather than being drawn, and a unit with no page behind it then has
            # no source at all, which leaves it out of the collection instead of 500ing in it.
            modified = file_stamp(path) if path else None
            if modified is not None and readable_image(str(path), modified):
                return path, None, (1.0, 1.0)
        source = page_image(unit.page_id, index_stamp()) if unit.page_id else None
        if source and unit.box:
            path, sx, sy = source
            b = unit.box
            scaled = (b.x * sx, b.y * sy, b.w * sx, b.h * sy)
            modified = file_stamp(path)
            size = image_size(str(path), modified) if modified is not None else None
            x, y, w, h = scaled
            if (size is None or w <= 0 or h <= 0 or x >= size[0] or y >= size[1]
                    or x + w <= 0 or y + h <= 0):
                return None
            return path, scaled, (sx, sy)
        return None

    def shown(unit: Unit) -> str:
        """What a row is labelled with: the character the record was written with.

        The written character is what the collection is made of and what the find box matches, so a
        row labelled with its reading would answer a different question than the one that found it:
        340 units of this corpus are written in hiragana and read in katakana, so a search for ば
        would list rows labelled バ. A record that names no character falls back to its reading, and
        the reading itself is always on the record.
        """
        written = written_identity(unit)
        return written if single_character(written) else label(unit)

    def eligible(unit: Unit) -> bool:
        readable = single_character(label(unit)) or (
            not label(unit) and single_character(written_identity(unit))
            and not is_space_identity(written_identity(unit))
        )
        return unit.active and unit.granularity == "char" and bool(readable) and bool(
            unit.crop_sha256 or (unit.page_id and unit.box and unit.box.w > 0 and unit.box.h > 0)
        ) and image_source(unit) is not None

    def quizzable(unit: Unit) -> bool:
        """Whether an occurrence may be put in front of a reader as a quiz candidate.

        `eligible` is about whether there is something to look at; this adds the alignment-repair
        pass's own statement that a reader should not be asked about this crop. A withheld unit keeps
        its transcription and its page and stays readable, and is simply not dealt into a round: asking
        a person to judge ink the pass could not align spends their attention on a known-bad crop and
        then records their answer against it.
        """
        return eligible(unit) and not repair_withheld(unit)

    def item(unit: Unit, revision: int, state: str | None = None) -> dict:
        if state is None:
            standing = status.unit_reviews([unit], store.events())[unit.id]
            state = review_state(standing.human_review)
        source = image_source(unit)
        # Page-backed crops share a source checksum; revision and box pin the crop itself.
        digest = source[0].stem if source else None
        document_id = unit.document_id
        if not document_id and unit.page_id:
            page = store.page(unit.page_id)
            document_id = page.document_id if page else None
        production = production_info(store.document(document_id) if document_id else None)
        image_url = (media.local(source[0], source[1]) if source and media else
                     f"/atlas/characters/{quote(unit.id, safe='')}/image?revision={revision}"
                     + (f"&image_sha256={digest}" if digest else ""))
        return {"id": unit.id, "label": shown(unit), "reading": unit.reading,
                **production,
                "script": unit.script, "jibo": refs.jibo_of_unit(unit.unicode), "revision": revision,
                "state": state, "page_id": unit.page_id, "line_id": unit.line_id,
                # A mended alignment is an uncertainty about the crop, so it travels with the item
                # rather than staying in the table: `None` for a unit the pass never touched, so a
                # view can tell "not repaired" from "repaired and fine".
                "repair": repair_metadata(unit),
                "box": unit.box.model_dump() if unit.box else None,
                "image_sha256": digest,
                "image": image_url}

    def one(unit_id: str) -> tuple[Unit, int]:
        records = store.unit_snapshot(unit_id)
        if not records or not records[0][0].active:
            raise HTTPException(404, "This character is no longer available.")
        return records[0]

    def snapshot(unit: Unit, revision: int) -> dict:
        page = store.page(unit.page_id) if unit.page_id else None
        document_id = unit.document_id or (page.document_id if page else None)
        document = store.document(document_id) if document_id else None
        source = image_source(unit)
        return {"character": item(unit, revision), "source_refs": document.source_refs if document else {},
                "canvas": page.canvas if page else None, "page_index": page.seq if page else None,
                "image_sha256": source[0].stem if source else None}

    @lru_cache(maxsize=1)
    def catalogue_snapshot(generation):
        # Row parsing and review-journal decoding are shared across shuffles.
        # Source files are still checked by eligible() on every request.
        units = store.unit_snapshot()
        events = store.events()
        standing = status.unit_reviews([u for u, _ in units], events)
        seen = seen_boxes(events)
        documents = {doc.id: production_info(doc)["production"] for doc in store.documents()}
        pages = store.pages()
        kinds = {}
        for unit, _ in units:
            page = pages.get(unit.page_id)
            document_id = unit.document_id or (page.document_id if page else None)
            kinds[unit.id] = documents.get(document_id, "unknown")
        states = {key: review_state(value.human_review) for key, value in standing.items()}
        for unit, _ in units:
            if states.get(unit.id) == "pending" and unit.id in seen and seen[unit.id] == (
                    unit.box.model_dump(mode="json") if unit.box else None):
                states[unit.id] = "seen"
        return units, states, kinds

    @api.get("/atlas")
    def catalogue(
        reading: str | None = None,
        q: str | None = None,
        group: Literal["all", "kana", "kanji"] = "all",
        state: Literal["all", "pending", "seen", "checked", "flagged"] = "all",
        purpose: Literal["browse", "review"] = "browse",
        production: Literal["all", "non-movable-type", "manuscript", "woodblock", "movable-type", "mixed", "unknown"] | None = None,
        seed: int = 0,
        limit: Annotated[int, Query(ge=1, le=96)] = 60,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict:
        """One listing, two questions, and they are not the same question.

        Browsing asks *what is in this collection*. A crop the alignment-repair pass withheld is
        still an occurrence with a transcription and a page, and leaving it out would make the
        collection misreport its own contents; it carries `repair` metadata instead, so a viewer can
        mark the uncertainty.

        A round asks *what should this person be shown to judge*. The pass's own `quiz: false` is an
        answer to that: a known-bad crop spends a reviewer's attention and then records their answer
        against ink nobody could align. So `purpose=review` is the narrower list.

        The default is `browse`, because this endpoint is a catalogue before it is a queue, and a
        caller that wants the queue says so.
        """
        considered = quizzable if purpose == "review" else eligible
        scope = production or ("non-movable-type" if purpose == "review" else "all")
        generation = (file_stamp(store.path), file_stamp(Path(str(store.path) + "-wal")),
                      file_stamp(production_metadata.OVERRIDES))
        units, all_states, kinds = catalogue_snapshot(generation)
        records = [(u, rev) for u, rev in units
                   if (scope == "all" or (kinds[u.id] != "movable-type" if scope == "non-movable-type"
                                         else kinds[u.id] == scope)) and considered(u)]
        states = {u.id: all_states[u.id] for u, _ in records}
        categories: dict[str, Counter] = {}
        for unit, _ in records:
            counts = categories.setdefault(shown(unit), Counter())
            counts["total"] += 1
            counts[states[unit.id]] += 1
        counts = Counter(states[u.id] for u, _ in records)
        searched = matches(records, q) if q else records
        selected = [(u, rev) for u, rev in searched if (reading is None or shown(u) == reading)
                    and (group == "all" or character_group(u) == group)
                    and (state == "all" or states[u.id] == state)]
        random.Random(seed).shuffle(selected)
        if purpose == "review" and seed % 5:
            # Keep one in five shuffles as a random audit. The other rounds show uncertain
            # measurements first, with ties retaining their seeded order and stable pagination.
            selected.sort(key=lambda row: review_priority(row[0]))
        return {"total": len(selected), "available": len(records), "counts": dict(counts),
                # Which question this answer is: a caller reading `available` has to know whether it
                # counts the collection or only the queue.
                "purpose": purpose, "production": scope, "review_epoch": store.review_epoch(),
                "query": q or None, "matched": len(searched) if q else None,
                "categories": [{"label": name, **{key: c[key] for key in
                                  ("total", "pending", "seen", "checked", "flagged")}}
                               for name, c in sorted(categories.items(), key=lambda x: (-x[1]["total"], x[0]))],
                "items": [item(u, rev, states[u.id]) for u, rev in selected[offset:offset + limit]]}

    @api.get("/atlas/characters/{unit_id}")
    def character(unit_id: str) -> dict:
        unit, revision = one(unit_id)
        result = item(unit, revision)
        line = store.line(unit.line_id) if unit.line_id else None
        page = store.page(unit.page_id) if unit.page_id else None
        document = store.document(unit.document_id or page.document_id) if (unit.document_id or page) else None
        result.update({"context_image": result["image"] + "&context=true",
                       "text": line.text if line else "", "source": document.title if document else "",
                       "page_number": page.seq + 1 if page else None,
                       "line": line.model_dump(mode="json") if line else None})
        source = image_source(unit)
        if source and media:
            result["context_image"] = media.local(source[0], source[1], context=True)
        # The context is the character's own surroundings, cut from the same source with the same
        # scale the crop is cut with, and the outline is reported as the fraction of that rectangle
        # the character occupies. A unit whose image is a pre-cut crop file has no surroundings to
        # show, so it is reported as none rather than presenting the same crop twice.
        result["context_box"] = None
        result["context"] = False
        result["source_scale"] = [1.0, 1.0]
        if source and source[1] is not None:
            path, scaled, (sx, sy) = source
            with Image.open(path) as opened:
                bounds = crop_bounds(opened, scaled, context=True)
            left, top, right, bottom = bounds
            x, y, w, h = scaled
            result["context"] = True
            # The source image's pixels per page pixel: a cached image may be a different size than
            # the page it came from, so a box given in source pixels has to be divided by these to
            # become a page box again, which is what an edit is stored as.
            result["source_scale"] = [sx, sy]
            result["context_box"] = {"x": left, "y": top, "w": right - left, "h": bottom - top}
            result["crop_box"] = {"x": x, "y": y, "w": w, "h": h}
        return result

    @api.get("/atlas/characters/{unit_id}/image")
    def crop(unit_id: str, revision: int, context: bool = False,
             image_sha256: str | None = None) -> Response:
        unit, current = one(unit_id)
        if revision != current:
            raise HTTPException(409, "This crop changed. Reload the character.")
        source = image_source(unit)
        if source is None:
            raise HTTPException(404, "The character image is not cached.")
        path, box, _scale = source
        if image_sha256 is not None and image_sha256 != path.stem:
            raise HTTPException(409, "The source image changed. Reload this character.")
        try:
            content = thumbnail(str(path), path.stat().st_mtime_ns, box, context)
        except (OSError, ValueError, Image.DecompressionBombError):
            raise HTTPException(422, "The character image could not be opened.") from None
        return Response(content, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})

    @api.get("/lines/{line_id}")
    def line_record(line_id: str) -> dict:
        line = store.line(line_id)
        if line is None:
            raise HTTPException(404, "This line is unavailable.")
        return {**line.model_dump(mode="json"), "revision": store.revision(line_id)}

    @api.get("/atlas/characters/{unit_id}/suggestions")
    def suggestions(unit_id: str, revision: int, image_sha256: str) -> dict:
        unit, current = one(unit_id)
        source = image_source(unit)
        if current != revision or not source or source[0].stem != image_sha256:
            raise HTTPException(409, "This crop changed. Reload the character.")
        try:
            from .suggestions import infer
            path, box, _scale = source
            result = infer(str(path), path.stat().st_mtime_ns, box)
        except Exception as error:  # noqa: BLE001 — optional inference must not block review
            kind = type(error).__name__
            if kind not in inference_failures:
                inference_failures.add(kind)
                # Log a diagnostic once without exception text, which can contain private paths.
                logging.getLogger(__name__).warning("OCR suggestions unavailable (%s)", kind)
            return {"status": "unavailable", "candidates": [], "engines": []}
        return {**result, "revision": revision, "image_sha256": image_sha256}

    @api.get("/atlas/characters/{unit_id}/suggestions/context")
    def contextual_suggestions(unit_id: str, revision: int, image_sha256: str) -> dict:
        unit, current = one(unit_id)
        source = image_source(unit)
        if current != revision or not source or source[0].stem != image_sha256:
            raise HTTPException(409, "This crop changed. Reload the character.")
        from .context_suggestions import context_guesses
        line = store.line(unit.line_id) if unit.line_id else None
        neighbors = store.units_of_line(unit.line_id) if line else []
        result = context_guesses(unit, line, neighbors)
        return {**result, "revision": revision, "image_sha256": image_sha256,
                "line_revision": store.revision(line.id) if line else None}

    def correction_text(value: str | None, issue: str | None) -> str | None:
        text = unicodedata.normalize("NFC", value.strip()) if value else None
        if text and (not single_character(text) and issue != "merged"):
            raise BadRequest("Choose one character, or report joined characters.")
        if text and issue not in ("reading", "merged"):
            raise BadRequest("A reading suggestion belongs to a reading or joined-character issue.")
        return text

    def identity_correction(value: str | None, issue: str | None) -> str | None:
        """The written identity an answer proposes, canonically, or `None` when it proposes none.

        A written identity is what the character *is*, so it is one character and nothing else — a
        ligature is one code point too, which is why 𪜈 is accepted here where the two kana it reads as
        are not. It belongs to the character issue, and it is stored the way the layers route stores
        one, so a round and the character editor cannot record two spellings of one correction.
        """
        if not value:
            return None
        if issue != "character":
            raise BadRequest("A written character belongs to a wrong-character issue.")
        identity = canonical_identity(value)
        if not one_character(identity_text(identity)):
            raise BadRequest(
                "A character correction is one character; a ligature is one code point too.")
        return identity

    def repeat(previous: list[dict]) -> dict:
        return {"results": [{**r, "duplicate": True} for r in previous]}

    @api.post("/atlas/rounds")
    def submit(round: Round) -> dict:
        ids = [answer.id for answer in round.answers] + [crop.id for crop in round.seen]
        if not ids:
            raise BadRequest("A round needs at least one answer or one seen crop.")
        if len(set(ids)) != len(ids):
            raise BadRequest("A character can appear only once in a round.")
        prefix = f"quiz:{round.id}:"
        previous = store.submission_results(round.client_id, prefix)
        if previous:
            reviews = [r for r in previous if r["field"] == "review"]
            if {r["target_id"] for r in reviews} != {a.id for a in round.answers}:
                raise BadRequest("This round was already submitted with different characters.")
            for answer in round.answers:
                old = json.loads(next(r for r in reviews if r["target_id"] == answer.id)["review"]["evidence"])
                if (old["label"] != round.label or old["verdict"] != answer.verdict
                        or old.get("issue") != answer.issue
                        or old.get("suggested_reading") != correction_text(answer.correction, answer.issue)
                        or old.get("suggested_character") != identity_correction(answer.character, answer.issue)
                        or old["snapshot"]["image_sha256"] != answer.image_sha256):
                    raise BadRequest("This round was already saved with different answers.")
            return {"id": str(round.id), **repeat(previous)}
        requests = []
        for answer in round.answers:
            unit, revision = one(answer.id)
            if not eligible(unit):
                raise BadRequest("This character has no available crop to review.")
            if repair_withheld(unit):
                raise BadRequest("This character's alignment was withheld from review; "
                                 "correct the crop on its own page instead.")
            if answer.image_sha256 != image_source(unit)[0].stem:
                raise HTTPException(409, "The source image changed. Reload this round.")
            if shown(unit) != round.label:
                raise HTTPException(409, "A character's reading changed. Reload this round.")
            correction = correction_text(answer.correction, answer.issue)
            identity = identity_correction(answer.character, answer.issue)
            if answer.verdict == "match" and (answer.issue or correction or identity):
                raise BadRequest("A matching character cannot also have an unresolved issue.")
            resolved = bool(answer.issue == "reading" and correction and single_character(correction))
            if resolved and correction == shown(unit):
                raise BadRequest("Choose a different reading or mark the character as matching.")
            # The written identity is a different layer from the reading, so it is a separate event. A
            # corrected character carries its reading along: い corrected to り reads り, and what the
            # transcriber typed stays in `text_source`.
            written = bool(identity and identity != stored_identity(unit))
            # `correction` stays what the reviewer sent, which a retry is compared against.
            reading = correction if resolved else None
            if written and not resolved:
                derived = reading_of(identity_text(identity))
                reading = derived if derived and derived != label(unit) else None
            base = answer.revision
            evidence = json.dumps({"kind": "visual-quiz", "round": str(round.id),
                                   "label": round.label, "verdict": answer.verdict, "issue": answer.issue,
                                   "suggested_reading": correction,
                                   "suggested_character": identity,
                                   "snapshot": snapshot(unit, revision),
                                   "correction": {"reading": reading or label(unit),
                                                  "unicode": identity if written else stored_identity(unit),
                                                  "box": unit.box.model_dump() if unit.box else None}},
                                  ensure_ascii=False)
            if written:
                requests.append(ReviewRequest(
                    target_type="unit", target_id=answer.id, field="unicode", new=identity,
                    base_revision=base, client_id=round.client_id,
                    idempotency_key=prefix + answer.id + ":character", evidence=evidence,
                ))
                base += 1
            if reading:
                requests.append(ReviewRequest(
                    target_type="unit", target_id=answer.id, field="reading", new=reading,
                    base_revision=base, client_id=round.client_id,
                    idempotency_key=prefix + answer.id + ":reading", evidence=evidence,
                ))
                base += 1
            requests.append(ReviewRequest(
                target_type="unit", target_id=answer.id, field="review",
                new="reviewed" if answer.verdict == "match" or resolved or written else "disputed",
                base_revision=base, client_id=round.client_id,
                idempotency_key=prefix + answer.id, evidence=evidence,
            ))
        for crop in round.seen:
            try:
                unit, _ = one(crop.id)
            except HTTPException:
                continue
            # A crop that cannot be dealt any more, or whose pixels changed since the round was
            # drawn, was not seen as it stands; it is skipped rather than failing the round.
            if not eligible(unit) or repair_withheld(unit) or image_source(unit)[0].stem != crop.image_sha256:
                continue
            requests.append(ReviewRequest(
                target_type="unit", target_id=crop.id, field=SEEN, new=True, base_revision=None,
                client_id=round.client_id, idempotency_key=prefix + crop.id + ":seen",
                evidence=json.dumps({"kind": "visual-quiz-seen", "round": str(round.id),
                                     "label": round.label, "image_sha256": crop.image_sha256,
                                     "box": unit.box.model_dump() if unit.box else None},
                                    ensure_ascii=False),
            ))
        results = store.record_batch(requests)
        return {"id": str(round.id), "results": results}

    @api.post("/atlas/rounds/{round_id}/undo")
    def undo(round_id: UUID, request: Undo) -> dict:
        previous = store.submission_results(request.client_id, f"quiz:{round_id}:")
        if not previous:
            raise HTTPException(404, "No saved round belongs to this reviewer.")
        revisions = {r["target_id"]: r["revision"] for r in previous}
        requests = []
        for r in reversed(previous):
            target = r["target_id"]
            requests.append(ReviewRequest(
                target_type="unit", target_id=target, field=r["field"], new=r["review"]["old"],
                # A seen record changed nothing, so its undo has nothing to be stale against.
                base_revision=None if r["field"] == SEEN else revisions[target], client_id=request.client_id,
                idempotency_key=f"quiz-undo:{round_id}:{r['id']}", evidence=f"undo of {r['id']}",
            ))
            if r["field"] != SEEN:
                revisions[target] += 1
        return {"id": str(round_id), "results": store.record_batch(requests)}

    @api.post("/atlas/characters/{unit_id}")
    def edit(unit_id: str, edit: CharacterEdit, background: BackgroundTasks) -> dict:
        previous = store.submission_results(edit.client_id, f"edit:{edit.id}:")
        if previous:
            old = json.loads(next(r for r in previous if r["field"] == "review")["review"]["evidence"])
            if old.get("request") != edit.model_dump(mode="json"):
                raise BadRequest("This edit was already saved with different values.")
            return repeat(previous)
        unit, current_revision = one(unit_id)
        correction = correction_text(edit.correction, edit.issue)
        supplied = unicodedata.normalize("NFC", edit.reading.strip()) if edit.reading else None
        if supplied is not None and not single_character(supplied):
            raise BadRequest("Use one character for the reading, or report joined characters.")
        if not eligible(unit):
            raise BadRequest("This character has no available crop to review.")
        if edit.image_sha256 != image_source(unit)[0].stem:
            raise HTTPException(409, "The source image changed. Reload this character.")
        resolved = bool(edit.issue == "reading" and correction and single_character(correction))
        if edit.verdict == "match" and edit.issue not in (None, "reading"):
            raise BadRequest("A matching character cannot also have a crop or joined-character issue.")
        reading = correction if resolved else supplied or label(unit)
        requests = []
        revision = edit.revision
        if edit.box is not None:
            page = store.page(unit.page_id) if unit.page_id else None
            if (not page or edit.box.w <= 0 or edit.box.h <= 0 or edit.box.x < 0 or edit.box.y < 0
                    or edit.box.x + edit.box.w > page.width or edit.box.y + edit.box.h > page.height):
                raise BadRequest("The crop must stay inside the source image.")
            requests.append(ReviewRequest(
                target_type="unit", target_id=unit_id, field="box", new=edit.box.model_dump(),
                base_revision=revision, client_id=edit.client_id, idempotency_key=f"edit:{edit.id}:box",
                evidence=edit.note or "Crop adjusted in character review",
            ))
            revision += 1
        if reading != label(unit):
            requests.append(ReviewRequest(
                target_type="unit", target_id=unit_id, field="reading", new=reading,
                base_revision=revision, client_id=edit.client_id, idempotency_key=f"edit:{edit.id}:reading",
                evidence=edit.note or "Character review",
            ))
            revision += 1
        evidence = json.dumps({"kind": "character-review", "verdict": edit.verdict,
                               "issue": edit.issue, "note": edit.note, "suggested_reading": correction,
                               "request": edit.model_dump(mode="json"),
                               "snapshot": snapshot(unit, current_revision),
                               "correction": {"reading": reading, "box": edit.box.model_dump()
                                              if edit.box else unit.box.model_dump() if unit.box else None}},
                              ensure_ascii=False)
        requests.append(ReviewRequest(
            target_type="unit", target_id=unit_id, field="review",
            new="reviewed" if edit.verdict == "match" or resolved else "disputed",
            base_revision=revision, client_id=edit.client_id, idempotency_key=f"edit:{edit.id}:review",
            evidence=evidence,
        ))
        results = store.record_batch(requests)
        if edit.issue == "merged":
            schedule_refinement(background, {unit_id})
        return {"results": results}

    def schedule_refinement(background: BackgroundTasks, unit_ids: set[str]) -> None:
        from .refine import background_refine

        payload = review_export()
        payload["reviews"] = [r for r in payload["reviews"] if r["event"]["target_id"] in unit_ids]
        background.add_task(background_refine, store, payload)

    def review_export(*, include_processed: bool = False) -> dict:
        """Every character review the journal holds, with what each one saw and whether it stands.

        One builder for both doors: the JSON route a script reads and the attachment route the
        browser saves. Two builders would eventually disagree about what a review record is.
        """
        all_events = store.events()
        latest = {e.target_id: e.id for e in all_events if e.field == "review"
                  and not (e.evidence and '"kind": "feedback-reconciliation"' in e.evidence)}
        events = [e for e in all_events if e.field == "review" and e.evidence and
                  ('"kind": "visual-quiz"' in e.evidence or '"kind": "character-review"' in e.evidence)]
        records = {u.id: (u, revision) for u, revision in store.unit_snapshot()}
        output = []
        for event in events:
            record = records.get(event.target_id)
            if not record:
                continue
            unit, revision = record
            evidence = json.loads(event.evidence)
            observed = evidence.get("snapshot")
            correction = evidence.get("correction", {})
            expected_label = correction.get("reading", evidence.get("label"))
            expected_box = correction.get("box", observed["character"]["box"] if observed else None)
            # A written-identity correction, wherever the route that made it recorded one: a round
            # writes `correction.unicode` and the layers route writes `layer_correction.character`,
            # and both name `Unit.unicode`. It is compared only when the evidence says the identity
            # was what changed — a round that corrected a reading also records the identity it left
            # alone, and holding it to that would report every reading correction as stale the moment
            # the identity moved for another reason.
            changed_layers = {evidence.get("layer")} | set(
                (evidence.get("layer_correction") or {}).get("changed") or ())
            expected_unicode = None
            if "character" in changed_layers:
                layer = evidence.get("layer_correction") or {}
                # `correction.unicode` is the stored form and already canonical; the layers route's
                # `layer_correction.character` is the literal character, and the record holds a code
                # point, so it goes through the same canonicaliser before the two are compared. A
                # literal that is not one character is not a comparable identity and is left out
                # rather than made to look like a mismatch.
                recorded = correction.get("unicode") or layer.get("code_point")
                if recorded:
                    expected_unicode = canonical_identity(recorded)
                elif layer.get("character"):
                    try:
                        expected_unicode = canonical_identity(str(layer["character"]))
                    except BadRequest:
                        expected_unicode = None
            image = image_source(unit)
            # `evidence.correction.reading` records the unit's reading field, so the comparison is
            # against the reading and not against the written identity a row is labelled with: the
            # two differ on 340 units of this corpus, and a correction that changed the reading is
            # current exactly when the record still reads that way.
            current = bool(unit.active and observed and latest.get(unit.id) == event.id and label(unit) == expected_label
                           and image and image[0].stem == observed["image_sha256"]
                           and (unit.box.model_dump() if unit.box else None) == expected_box
                           and (not expected_unicode or stored_identity(unit) == expected_unicode))
            processing = unit.meta.get("feedback_repair")
            if unit.split_into:
                split = next((e for e in reversed(all_events) if e.target_id == unit.id
                              and e.field == "segmentation"), None)
                processing = {"result": "split", "children": unit.split_into,
                              "event_id": split.id if split else None, "automated": bool(split and split.role == "model")}
            output.append({"event": event.model_dump(mode="json"), "reviewed": observed,
                           "current": current, "current_revision": revision,
                           **({"processing": processing} if processing else {})})
        if corpus_reviews is not None:
            output.extend(corpus_reviews.exports())
        from .receipts import FeedbackReceipts

        pending, counts = FeedbackReceipts(store.directory).filter(output)
        return {"version": 1, "kind": "atlas-character-reviews",
                "reviews": output if include_processed else pending,
                "scope": "history" if include_processed else "unprocessed", "counts": counts}

    @api.get("/atlas/reviews")
    def export_reviews(include_processed: bool = False) -> dict:
        """The reviews as JSON, for a client that reads them."""
        return review_export(include_processed=include_processed)

    @api.get("/atlas/reviews.json")
    def export_reviews_file(include_processed: bool = False) -> Response:
        """The same payload as a download, so the browser saves it rather than a script does."""
        body = json.dumps(review_export(include_processed=include_processed), ensure_ascii=False, indent=2) + "\n"
        return Response(
            content=body, media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="atlas-character-reviews.json"'})

    return api
