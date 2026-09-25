"""Keep transcription classes separate from the character visible in a crop."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from .. import refs

NORMALIZED_CORPORA = frozenset({"codh", "codh-full"})
NORMALIZATION_EVIDENCE = "https://codh.rois.ac.jp/char-shape/#version"
IDENTITY_FIELDS = (
    "source_label", "source_code_point", "written_character", "identity_basis",
    "identity_status", "identity_evidence", "grapheme", "family_members",
    "requires_family_scope", "visual_assignment", "visual_group", "form_cluster", "form_decision",
    "production", "production_label", "production_evidence",
)


@lru_cache(maxsize=8192)
def family_of(code_point: str | None) -> dict[str, Any] | None:
    """The grapheme family of one code point; a sequence such as ツ + U+309A has none of its own."""
    return refs.grapheme_info(code_point) if code_point and len(code_point.split()) == 1 else None


def identity_fields(
    row: dict[str, Any], corpus: str | None, *, human_character: str | None = None,
    source_revision: str | None = None,
) -> dict[str, Any]:
    """Resolve identity without rewriting the source's own class or transcription."""
    cp = row.get("source_code_point") or row.get("unicode") or row.get("codepoint") or row.get("code_point")
    family = family_of(cp)
    encoded = refs.from_code_points(cp.split()) if cp and cp.startswith("U+") else None
    source_label = row.get("source_label")
    if source_label is None:
        source_label = row.get("text_source") or encoded or row.get("char") or row.get("label")
    normalized = corpus in NORMALIZED_CORPORA
    ambiguous = normalized and family is not None and family["character_count"] > 1
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
        # A person named this glyph's form, directly or through its cluster; a glyph marked as not
        # having its cluster's form stays unassigned rather than falling back to the visual model.
        written, basis = decided["form"], decided["basis"]
        ambiguous = written is None
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
            members = [member["char"] for member in family["members"]] if family else [encoded]
            if assignment is not None and (candidate is None or candidate in members):
                # A provenance-bound visual inspection can also leave conflicting
                # source labels unresolved; retain that result until reviewed.
                written, basis = candidate, "visual_model"
                ambiguous = ambiguous or (written is None and len(members) > 1)
    return {
        "source_label": source_label,
        "source_code_point": cp,
        "written_character": written,
        "identity_basis": basis,
        "identity_status": "assigned" if written else "unassigned",
        "identity_evidence": NORMALIZATION_EVIDENCE if normalized else None,
        "grapheme": family["code_point"] if family else cp,
        "family_members": family["members"] if family else [],
        "requires_family_scope": ambiguous,
        "visual_assignment": assignment,
        "visual_group": (assignment or {}).get("visual_group"),
        "form_cluster": forms.cluster_of(identity) if identity else None,
        "form_decision": decided,
    }


def production_fields(document: dict[str, Any]) -> dict[str, Any]:
    from ..production import production_info
    return production_info(document)
