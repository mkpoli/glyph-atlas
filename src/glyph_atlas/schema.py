"""Records of the dataset.

Every image is addressed, never copied: a page is a IIIF image or a plain URL plus a checksum, and a
unit is a rectangle on that page. Crops are materialised only for releases. Labels sit in layers that
can be filled independently: the transcriber's text, the diplomatic reading, the classification
(code points, script, variant), and the review state.

The three browsing layers retain different identities:

- Grapheme: a curated orthographic family, such as 仮 and 假.
- Character: one written character with its own encoded identity.
- Form: the exact ink of a source occurrence, represented by a Unit and its crop.

`Character.grapheme` points to the family's representative; `Unit.unicode` points
at the written character. Readings remain independent. 字母 is character metadata
in `Character.jibo`; sharing a 字母 alone does not establish a grapheme family.

"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from . import production


class Licence(StrEnum):
    CC0 = "CC0-1.0"
    CC_BY_4 = "CC-BY-4.0"
    CC_BY_SA_3 = "CC-BY-SA-3.0"
    CC_BY_SA_4 = "CC-BY-SA-4.0"
    CC_BY_SA_2_1_JP = "CC-BY-SA-2.1-JP"
    CC_BY_NC_4 = "CC-BY-NC-4.0"
    CC_BY_ND_4 = "CC-BY-ND-4.0"
    CC_BY_NC_SA_4 = "CC-BY-NC-SA-4.0"
    CC_BY_NC_ND_4 = "CC-BY-NC-ND-4.0"
    KOGL_1 = "KOGL-1"
    UNICODE = "Unicode-3.0"
    PUBLIC_DOMAIN = "PD"
    PDM = "PDM-1.0"
    RS_NOC_CR = "RS-NOC-CR"
    BESPOKE_FREE = "bespoke-free"
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


#: A node of `data/vocab/production.yaml`, written as its path, e.g. `printed/type/metal/copper`.
Production = Annotated[str, AfterValidator(production.check)]


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
    production: Production = "unknown"
    genre: list[str] = Field(default_factory=list, description="terms from data/vocab/genre.yaml")
    text_register: Register = Register.UNKNOWN
    dating: list[Dating] = Field(default_factory=list, description="several dates may apply, e.g. composition and copying")
    hands: list[str] = Field(default_factory=list)
    image_rights: Rights | None = None
    text_rights: Rights | None = None
    meta: dict[str, Any] = Field(default_factory=dict, description="upstream fields with no column of their own")


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
    meta: dict[str, Any] = Field(default_factory=dict)


class PageText(BaseModel):
    """Whole-page transcription text, for pages that have no line records yet."""

    page_id: str
    source: str
    revision: str | None = None
    text_raw: str


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
    match_confidence: float | None = Field(default=None, description="similarity of text and box in [0, 1]; a score, not a calibrated probability")
    meta: dict[str, Any] = Field(default_factory=dict)


class UnitKind(StrEnum):
    CHAR = "char"
    SEQUENCE = "sequence"
    LIGATURE = "ligature"
    ITERATION_MARK = "iteration-mark"
    VOICING_MARK = "voicing-mark"
    PUNCTUATION = "punctuation"
    GAP = "gap"
    UNREADABLE = "unreadable"


class Script(StrEnum):
    """The script of a character, as the character layer states it.

    `han` is Unicode's name for the script of the kanji and is what `data/vocab/characters.tsv`
    holds. `hangul` is the Korean alphabet, its jamo and its precomposed syllables alike.
    """

    HIRAGANA = "hiragana"
    HENTAIGANA = "hentaigana"
    KATAKANA = "katakana"
    HAN = "han"
    HANGUL = "hangul"
    SYMBOL = "symbol"
    LATIN = "latin"
    UNKNOWN = "unknown"


class VariantRef(BaseModel):
    scheme: Literal["mj", "ivs", "glyphwiki", "local"]
    id: str
    version: str | None = Field(default=None, description="registry version the id was taken from")


class Ligature(BaseModel):
    """Two kana set as one character: the characters it is made of and what they read as.

    合略仮名 such as 𪜈 (ト + モ) are one encoded identity made of two characters. The relation is
    not the grapheme relation and not the 字母 relation: the components are characters of their own,
    and the ligature is not a form of either. It is stated by `data/vocab/ligatures.yaml`, because a
    Unicode name states it for ゟ and not for 𪜈, whose name is `CJK UNIFIED IDEOGRAPH-2A708`.

    `components` are code points, as everywhere else in the layer, in writing order.
    """

    components: list[str] = Field(description="the characters the ligature is made of, in writing order")
    reading: str | None = Field(default=None, description="what the components read as together")
    kind: Literal["katakana-ligature", "hiragana-ligature"] = "katakana-ligature"
    evidence: str | None = Field(default=None, description="what states the pair, one line")


class Character(BaseModel):
    """One encoded character: the middle layer of the three.

    A row of `data/vocab/characters.tsv`, keyed by its code point, which is the Unicode scalar value
    and needs no id of its own. The row states what the character *is*: its name, script, block, the
    age of the assignment, the 字母 it derives from, what it reads as, the grapheme it is a form of,
    and the characters it is confusable with. Nothing here is a property of one written occurrence,
    so no unit and no crop repeats it.

    A form that is two kana set as one character carries `ligature`, the characters it is made of:
    ゟ is not a form of よ or of り, it is made of them, and 𪜈 is ト and モ set as one character.

    `script` is `schema.Script`, and hentaigana is one of its values: a hentaigana is a kana form
    Unicode encodes under that name, not a script of its own.

    `jibo` can name several derivations. `grapheme` names the representative of
    an explicitly curated family; it never replaces the character's own identity.
    Kana families follow the vocabulary, while cited Japanese old/new orthographic
    pairs can share a family. Unstated characters represent themselves.

    """

    code_point: str = Field(description="U+XXXX, four to six hex digits and uppercase")
    char: str = Field(description="the character itself")
    name: str | None = Field(default=None, description="the Unicode name, as UnicodeData.txt states it")
    alias: str | None = Field(default=None, description="a second name for the same code point, such as an MJ figure name")
    script: Script = Script.UNKNOWN
    category: str | None = Field(default=None, description="the Unicode general category, e.g. Lo")
    age: str | None = Field(default=None, description="the Unicode version that assigned the code point, e.g. 18.0")
    block: str | None = Field(default=None, description="the Unicode block name")
    jibo: list[str] = Field(default_factory=list, description="字母, the kanji the form derives from; empty when the form has none")
    readings: list[str] = Field(default_factory=list, description="what the character reads as, historical spelling kept; empty when it is not a kana")
    grapheme: str | None = Field(default=None, description="the representative code point of the curated grapheme family; null means itself")
    confusables: list[str] = Field(default_factory=list, description="code points Unicode's confusables table pairs this one with, both directions")
    ligature: Ligature | None = Field(default=None, description="the characters this one is made of, when it is two kana set as one; null when it is not a ligature")
    variants: list[VariantRef] = Field(default_factory=list, description="MJ, IVS, GlyphWiki or local shape ids")


class Classification(StrEnum):
    UNASSESSED = "unassessed"
    IDENTIFIED = "identified"
    AMBIGUOUS = "ambiguous"
    UNENCODED = "unencoded"
    UNIDENTIFIED = "unidentified"


class Candidate(BaseModel):
    """One scored alternative for a unit's code point."""

    unicode: str
    p: float
    jibo: str | None = None


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

    model_config = ConfigDict(extra="forbid")

    id: str
    document_id: str | None = Field(default=None, description="set on every unit, so that a standalone crop still reaches its rights")
    page_id: str | None = Field(default=None, description="null for a standalone crop with no page placement")
    line_id: str | None = None
    seq: int | None = Field(default=None, description="position in the line, 0-based")
    box: Box | None = Field(default=None, description="rectangle on the page image; null for a standalone crop")
    crop: str | None = Field(default=None, description="URL or archive path of a standalone crop image")
    crop_sha256: str | None = None
    kind: UnitKind = UnitKind.CHAR
    granularity: Literal["char", "sequence", "block"] = "char"
    text_source: str | None = Field(default=None, description="the transcriber's string for this unit")
    reading: str | None = Field(default=None, description="diplomatic reading, historical spelling kept")
    unicode: str | None = Field(
        default=None,
        description="code point sequence naming the character layer, e.g. U+1B002 or U+304B U+3099; "
        "U+1B127 and U+30CD are two characters, so the code point is what says which one was written",
    )
    classification: Classification = Classification.UNASSESSED
    script: Script = Script.UNKNOWN
    variants: list[VariantRef] = Field(default_factory=list, description="MJ, IVS, GlyphWiki or local shape ids")
    candidates: list[Candidate] = Field(default_factory=list, description="scored alternatives when classification is ambiguous")
    antecedent_ids: list[str] = Field(default_factory=list, description="units an iteration mark repeats")
    group_id: str | None = Field(default=None, description="連綿 group this unit belongs to")
    voicing: Literal["none", "dakuten", "handakuten"] | None = Field(
        default=None, description="mark actually present on the page"
    )
    method: Literal["import", "detect-align", "manual"] = "import"
    confidence: Confidence | None = None
    review: ReviewState = ReviewState.MACHINE
    upstream: dict[str, str] = Field(default_factory=dict, description="source id and upstream identifier")
    active: bool = Field(default=True, description="false once a split or merge retired this unit")
    split_into: list[str] = Field(default_factory=list)
    merged_into: str | None = None
    meta: dict[str, Any] = Field(
        default_factory=dict,
        description="fields with no column of their own, such as the audit sample a unit belongs to",
    )


class Group(BaseModel):
    """Several units joined by continuous strokes (連綿)."""

    id: str
    page_id: str
    box: Box
    unit_ids: list[str]


class Review(BaseModel):
    """One editorial decision, appended to the review log."""

    id: str
    target_type: Literal["unit", "line", "page", "document", "group"] = "unit"
    target_id: str
    field: str
    old: Any = None
    new: Any = None
    role: Literal["model", "transcriber", "reviewer", "adjudicator"]
    actor: str | None = Field(default=None, description="model name or anonymous reviewer id")
    evidence: str | None = None
    at: datetime
