"""Keep transcription classes separate from the character visible in a crop."""

from __future__ import annotations

import json
import unicodedata
from functools import lru_cache
from typing import Any

from .. import refs, representation
from ..clusters import is_conjoining_jamo_syllable

NORMALIZED_CORPORA = frozenset({"codh", "codh-full"})
NORMALIZATION_EVIDENCE = "https://codh.rois.ac.jp/char-shape/#version"
#: What names the form of a block whose source leaves several, by its shape (docs/reports/kokatsuji-forms.md).
SHAPE_EVIDENCE = "data/vocab/kokatsuji-forms.tsv"
BLOCKS = "codh-omt:001:"
IDENTITY_FIELDS = (
    "source_label", "source_code_point", "written_character", "identity_basis",
    "identity_status", "identity_evidence", "grapheme", "family_members",
    "requires_family_scope", "visual_assignment", "visual_group", "form_cluster", "form_decision",
    "production", "production_label", "production_evidence",
)


def members_for(family: dict[str, Any] | None, char: str | None) -> list[dict[str, Any]]:
    """The members of a family a glyph labelled `char` may be written as: its small seal characters
    only where the label is one."""
    if not family:
        return []
    return family["members"] if refs.is_small_seal(char or "") else refs.transcribed(family["members"])


@lru_cache(maxsize=8192)
def family_of(code_point: str | None) -> dict[str, Any] | None:
    """The grapheme family of one code point; a sequence such as ツ + U+309A has none of its own.

    An old-Hangul syllable spelt with conjoining jamo is one character, and is its own family.
    """
    if not code_point:
        return None
    if len(code_point.split()) == 1 or is_conjoining_jamo_syllable(refs.to_char(code_point)):
        return refs.grapheme_info(code_point)
    return None


def shape_of(identity: str | None, code_point: str | None, source_label: str | None) -> str | None:
    """The character a 古活字データセット block is printed as, where its 字母 is that of several and the
    source names none. A row stands only for a block that still has the kana the row repeats."""
    if code_point or not (identity or "").startswith(BLOCKS):
        return None
    row = refs.kokatsuji_forms().get(identity.removeprefix(BLOCKS))
    return refs.to_char(row["code_point"]) if row and row["kana"] == source_label else None


def label_scope(row: dict[str, Any]) -> str | None:
    """What a unit's label names, where its source says: "grapheme" for the character and not its printed form.

    A detail record carries it as `source_label_scope`, a dataset unit in its `meta` as `label_scope`."""
    if row.get("source_label_scope"):
        return row["source_label_scope"]
    meta = row.get("meta")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except ValueError:
            return None
    return meta.get("label_scope") if isinstance(meta, dict) else None


def identity_fields(
    row: dict[str, Any], corpus: str | None, *, human_character: str | None = None,
    source_revision: str | None = None,
) -> dict[str, Any]:
    """Resolve identity without rewriting the source's own class or transcription.

    A source that labels a glyph with a CJK compatibility ideograph names one figure of the unified
    ideograph it stands for (docs/decisions/0013-finer-forms.md): the glyph is written as that
    ideograph, a normalised corpus included, and `source_code_point` keeps the source's own.
    """
    cp = row.get("source_code_point") or row.get("unicode") or row.get("codepoint") or row.get("code_point")
    # A record's `code_point` is of the form it was given here; its source names none.
    if row.get("identity_basis") == "glyph_shape" and not row.get("source_code_point"):
        cp = None
    encoded = refs.from_code_points(cp.split()) if cp and cp.startswith("U+") else None
    source_label = row.get("source_label")
    if source_label is None:
        source_label = row.get("text_source") or encoded or row.get("char") or row.get("label")
    # The label is the code point, or with none the source's text.
    figure = representation.compatibility(encoded or source_label or "")
    if figure:
        encoded = unicodedata.normalize("NFC", encoded or source_label)
    family = family_of(" ".join(refs.to_code_points(encoded)) if figure else cp)
    # A source may say of one glyph that its label names the character and not the form it is printed in: the
    # table of 龍龕手鏡 writes a 俗 form and its 正 form beside it with one character.
    scoped = label_scope(row) == "grapheme"
    normalized = corpus in NORMALIZED_CORPORA or scoped
    # No corpus writes a small seal character for its Han character, so a label that is one names it.
    ambiguous = scoped or (normalized and not figure and not refs.is_small_seal(encoded or source_label or "")
                           and len(members_for(family, encoded or source_label)) > 1)
    basis = "normalized_transcription" if normalized else "source_transcription"
    written = None if ambiguous else encoded or source_label
    assignment = None
    identity = row.get("unit_id") or row.get("identity_key") or row.get("id")
    from .. import forms

    decided = forms.form_for(identity) if identity else None
    if human_character:
        written, basis = human_character, "human_review"
        family = family_of(" ".join(refs.to_code_points(human_character)))
    elif decided is not None:
        # A person named this glyph's form, directly or through its cluster, or reported what
        # character it is; a glyph marked as not having its cluster's form stays unassigned rather
        # than falling back to the visual model.
        written, basis = decided["form"] or decided.get("character"), decided["basis"]
        ambiguous = written is None
        if written:
            # The glyph belongs to the family of what it is written as: a kana form keeps its
            # family (𛂞 is は's), a kanji variant has its own (仿 is not 倣's), and a reported
            # character brings its own.
            family = family_of(" ".join(refs.to_code_points(written)))
    elif shape := shape_of(identity, cp, source_label):
        written, basis, ambiguous = shape, "glyph_shape", False
        family = family_of(refs.to_code_point(shape))
    else:
        try:
            from ..visual_families import assignment_for, evidence_signature
        except ImportError:
            assignment_for = None
        if assignment_for and identity:
            signature = row.get("source_signature") or evidence_signature(
                identity, cp, row.get("page_id") or (row.get("source") or {}).get("page_id"),
                row.get("box"), row.get("crop"),
            )
            assignment = assignment_for(identity, source_revision, source_label=source_label,
                                        crop_sha256=row.get("crop_sha256"), source_signature=signature)
            candidate = (assignment or {}).get("written_character")
            members = [member["char"] for member in members_for(family, encoded or source_label)] if family else [encoded]
            if assignment is not None and (candidate is None or candidate in members):
                # A provenance-bound visual inspection can also leave conflicting
                # source labels unresolved; retain that result until reviewed.
                written, basis = candidate, "visual_model"
                ambiguous = ambiguous or (written is None and len(members) > 1)
    # A label left open still names its grapheme: an unencoded one (an IDS) is its own.
    named = written or (source_label if scoped else None)
    return {
        "source_label": source_label,
        "source_code_point": cp,
        "written_character": written,
        "identity_basis": basis,
        "identity_status": "assigned" if written else "unassigned",
        "identity_evidence": SHAPE_EVIDENCE if basis == "glyph_shape" else NORMALIZATION_EVIDENCE if corpus in NORMALIZED_CORPORA else None,
        # A character with no family (one Unicode lacks, written as an IDS) is its own grapheme, as the
        # Worker files it.
        "grapheme": family["code_point"] if family else cp or (" ".join(refs.to_code_points(named)) if named else None),
        "family_members": members_for(family, written or encoded or source_label),
        "requires_family_scope": ambiguous,
        "visual_assignment": assignment,
        "visual_group": (assignment or {}).get("visual_group"),
        "form_cluster": forms.cluster_of(identity) if identity else None,
        "form_decision": decided,
    }


def production_fields(document: dict[str, Any]) -> dict[str, Any]:
    from ..production import production_info
    return production_info(document)
