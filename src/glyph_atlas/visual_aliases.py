"""Share visual results for identical crop bytes while retaining each source identity."""
from copy import deepcopy


def alias_assignments(assignments: dict, duplicates: list[dict]) -> dict:
    """Return only safely matched alias proposals; never replace existing assignments."""
    result = {}
    for alias in duplicates:
        identity = alias["excluded_id"]
        if identity in assignments or identity in result:
            continue
        canonical = assignments.get(alias["kept_id"])
        if not canonical or not alias.get("same_crop_bytes"):
            continue
        sha = alias.get("crop_sha256")
        signature = alias.get("source_signature")
        if not sha or not signature or canonical.get("crop_sha256") != sha:
            continue
        if canonical.get("source_signature") != alias.get("kept_source_signature"):
            continue
        if canonical.get("family") != alias.get("family"):
            continue
        row = deepcopy(canonical)
        for field in ("source_label", "source_code_point", "source_signature", "source_revision",
                      "corpus", "document_id", "page_id", "box", "source_crop", "source_url",
                      "production", "production_evidence"):
            row[field] = alias.get(field)
        # The kept row's inspection was of that source, not this one; an alias shares the pixels only.
        row.update(inspection=None, assignment_method="identical_crop")
        row.update(id=identity, crop_sha256=sha, verified=False, confirmed_by_human=False,
                   identity_basis="visual_model", identical_crop_of=alias["kept_id"],
                   duplicate_evidence="sha256-identical crop bytes")
        result[identity] = row
    return result
