"""Records of the dataset.

Every image is addressed, never copied: a page is a IIIF image or a plain URL plus a checksum, and a
unit is a rectangle on that page. Crops are materialised only for releases. Labels sit in layers that
can be filled independently: the transcriber's text, the diplomatic reading, the classification
(code points, script, 字母, variant), and the review state.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class Licence(StrEnum):
    CC0 = "CC0-1.0"
    CC_BY_4 = "CC-BY-4.0"
    CC_BY_SA_3 = "CC-BY-SA-3.0"
    CC_BY_SA_4 = "CC-BY-SA-4.0"
    CC_BY_SA_2_1_JP = "CC-BY-SA-2.1-JP"
    UNICODE = "Unicode-3.0"
    PUBLIC_DOMAIN = "PD"
    RESTRICTED = "restricted"
    UNKNOWN = "unknown"


class Box(BaseModel):
    """Pixel rectangle on the full-size page image, origin top-left."""

    x: int
    y: int
    w: int
    h: int

    def iiif_region(self) -> str:
        return f"{self.x},{self.y},{self.w},{self.h}"


class Rights(BaseModel):
    licence: Licence
    holder: str | None = None
    attribution: str
    evidence: str | None = Field(default=None, description="URL of the page that states the licence")
    checked: date | None = None


class Source(BaseModel):
    id: str
    name: str
    publisher: str
    kind: Literal["character-dataset", "line-dataset", "transcription-corpus", "image-collection", "reference-table"]
    url: str
    licence: Licence
    attribution: str
    version: str | None = None
    doi: str | None = None
    released: date | None = None


class Production(StrEnum):
    MANUSCRIPT = "manuscript"
    WOODBLOCK = "woodblock"
    MOVABLE_TYPE = "movable-type"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class Register(StrEnum):
    WABUN = "wabun"
    KANBUN = "kanbun"
    KANBUN_KUNDOKU = "kanbun-kundoku"
    SOROBUN = "sorobun"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class Dating(BaseModel):
    """A date as the source states it and as an interval."""

    literal: str | None = Field(default=None, description="和暦 or other wording as written, e.g. 文政3")
    start: int | None = Field(default=None, description="earliest possible year (proleptic Gregorian)")
    end: int | None = None
    kind: Literal["composition", "copying", "publication", "impression", "unknown"] = "unknown"
    evidence: str | None = None


class Document(BaseModel):
    id: str
    title: str
    source_refs: dict[str, str] = Field(default_factory=dict, description="upstream ids by source id")
    holder: str | None = None
    shelfmark: str | None = None
    production: Production = Production.UNKNOWN
    genre: list[str] = Field(default_factory=list, description="terms from data/vocab/genre.yaml")
    text_register: Register = Register.UNKNOWN
    dating: Dating | None = None
    hands: list[str] = Field(default_factory=list)
    image_rights: Rights | None = None
    text_rights: Rights | None = None


class Page(BaseModel):
    id: str
    document_id: str
    seq: int
    canvas: str | None = Field(default=None, description="IIIF canvas id")
    image: str = Field(description="IIIF image service base or a direct URL of the full-size image")
    width: int
    height: int
    sha256: str | None = None
    transcription: dict[str, str] = Field(default_factory=dict, description="source id, entry id, revision")


class LineRole(StrEnum):
    MAIN = "main"
    RUBY = "ruby"
    WARIGAKI = "warigaki"
    NOTE = "note"
    MARGINAL = "marginal"
    TITLE = "title"
    OTHER = "other"


class Line(BaseModel):
    id: str
    page_id: str
    seq: int
    box: Box | None = None
    vertical: bool = True
    role: LineRole = LineRole.MAIN
    text_raw: str = Field(description="transcription line as written, markup included")
    text: str = Field(description="plain text after markup removal")
    match_method: str | None = Field(default=None, description="how the box was assigned")
    match_confidence: float | None = None


class UnitKind(StrEnum):
    CHAR = "char"
    LIGATURE = "ligature"
    ITERATION_MARK = "iteration-mark"
    VOICING_MARK = "voicing-mark"
    PUNCTUATION = "punctuation"
    GAP = "gap"
    UNREADABLE = "unreadable"


class Script(StrEnum):
    HIRAGANA = "hiragana"
    HENTAIGANA = "hentaigana"
    KATAKANA = "katakana"
    KANJI = "kanji"
    SYMBOL = "symbol"
    LATIN = "latin"
    UNKNOWN = "unknown"


class VariantRef(BaseModel):
    scheme: Literal["mj", "ivs", "glyphwiki", "local"]
    id: str


class Confidence(BaseModel):
    detection: float | None = None
    segmentation: float | None = None
    text: float | None = None
    jibo: float | None = None
    model: str | None = Field(default=None, description="model and checkpoint that produced the scores")


class ReviewState(StrEnum):
    MACHINE = "machine"
    TRANSCRIBER = "transcriber"
    REVIEWED = "reviewed"
    DOUBLE_REVIEWED = "double-reviewed"
    ADJUDICATED = "adjudicated"
    DISPUTED = "disputed"
    REJECTED = "rejected"


class Unit(BaseModel):
    """One graphic unit on a page: usually a character, sometimes a ligature or a mark."""

    id: str
    page_id: str
    line_id: str | None = None
    seq: int | None = Field(default=None, description="position in the line, 0-based")
    box: Box
    kind: UnitKind = UnitKind.CHAR
    text_source: str | None = Field(default=None, description="the transcriber's string for this unit")
    reading: str | None = Field(default=None, description="diplomatic reading, historical spelling kept")
    unicode: str | None = Field(default=None, description="code point sequence, e.g. U+1B002 or U+304B U+3099")
    script: Script = Script.UNKNOWN
    jibo: str | None = Field(default=None, description="字母, the kanji the kana form derives from")
    variant: VariantRef | None = None
    group_id: str | None = Field(default=None, description="連綿 group this unit belongs to")
    voicing: Literal["none", "dakuten", "handakuten"] | None = Field(
        default=None, description="mark actually present on the page"
    )
    method: Literal["import", "detect-align", "manual"] = "import"
    confidence: Confidence | None = None
    review: ReviewState = ReviewState.MACHINE
    upstream: dict[str, str] = Field(default_factory=dict, description="source id and upstream identifier")
    split_into: list[str] = Field(default_factory=list)
    merged_into: str | None = None


class Group(BaseModel):
    """Several units joined by continuous strokes (連綿)."""

    id: str
    page_id: str
    box: Box
    unit_ids: list[str]


class Review(BaseModel):
    """One editorial decision, appended to the review log."""

    unit_id: str
    field: str
    old: str | None
    new: str | None
    role: Literal["model", "transcriber", "reviewer", "adjudicator"]
    evidence: str | None = None
    at: datetime
