"""Write the D1 statements that take withdrawn documents (`data/vocab/withdrawn.yaml`) off the site.

    python scripts/withdraw_documents.py > withdraw.sql
    (cd apps/cloudflare && bunx wrangler d1 execute glyph-atlas --remote --file ../../withdraw.sql)

For each document, its crops are the `units` rows naming it and the ids in `withdrawn.corpus_range`,
which holds its corpus glyphs and the `units` rows a round or review wrote for them (with no
document). They go with everything that names them: review rows, marks, shapes, suspects, redirects,
pairs, form placements, written forms, gallery and follow rows, the claims made about its crops with
their evidence, premises and actions, the crops' evidence versions, and the image rows of its own crops.
The corpus counts are then recounted, and the stamp the Worker keys its cached listings on is
written last. Pack bytes stay in R2. The image rows of corpus display crops and form tiles are not
derivable here and stay, keyed by content hash and named by no row. Nothing is sent to D1 here, and a
second run of the statements removes nothing more.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from cloudflare_schema import CORPUS_REFRESH

from glyph_atlas import withdrawn

_spec = importlib.util.spec_from_file_location("refresh", ROOT / "scripts" / "refresh_published_units.py")
refresh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(refresh)

MEDIA_KEY = "replace(replace(json_extract(data,'$.{field}'),'/atlas/media/',''),'.webp','')"
#: Every origin a `units` row has, named so that `unit_document_sample` (origin, document, …) serves the
#: lookup by document instead of a scan of the whole table.
ORIGINS = "origin IN ('local','corpus','retired')"


def quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def statements(documents) -> list[str]:
    sql = []
    for document in sorted(documents):
        mine = f"{ORIGINS} AND document={quote(document)}"
        low, high = map(quote, withdrawn.corpus_range(document))
        corpus = f"id>={low} AND id<{high}"
        own = f"(SELECT id FROM units WHERE {mine} UNION SELECT id FROM units WHERE {corpus})"
        keys = " UNION ".join(f"SELECT {MEDIA_KEY.format(field=field)} FROM units WHERE {mine}"
                              for field in ("image", "context_image"))
        sql += [
            *(f"DELETE FROM {table} WHERE target IN {own};" for table in ("events", "seen", "skips", "written_forms")),
            *(f"DELETE FROM {table} WHERE id IN {own};" for table in ("unit_marks", "unit_shapes", "unit_suspects")),
            *(f"DELETE FROM {table} WHERE id IN {own} OR {corpus};" for table in ("form_units", "form_bases")),
            f"DELETE FROM unit_redirects WHERE id IN {own} OR target IN {own};",
            f"DELETE FROM unit_ngrams WHERE document={quote(document)} OR first IN {own} OR second IN {own} OR third IN {own};",
            f"DELETE FROM document_characters WHERE document={quote(document)};",
            f"DELETE FROM media WHERE key IN ({keys});",
            f"DELETE FROM units WHERE {mine} OR {corpus};",
            # The claims about the crops go once the crops have: the document's crops, as their versions
            # name them whatever cut a claim was made on, and its corpus glyphs. Then the evidence,
            # premises and actions of those claims; the resolved rows went with the crops (0049).
            *(f"DELETE FROM assertions WHERE {where} AND EXISTS (SELECT 1 FROM assertion_evidence e "
              f"WHERE e.assertion=assertions.id AND e.kind='crop');"
              for where in (f"subject IN (SELECT unit FROM crop_versions WHERE document={quote(document)})",
                            f"subject>={low} AND subject<{high}")),
            *(f"DELETE FROM {table} WHERE assertion NOT IN (SELECT id FROM assertions);"
              for table in ("assertion_evidence", "assertion_premises", "assertion_actions")),
            # A crop's evidence versions may go once the crop has left the site, so they follow it.
            f"DELETE FROM crop_versions WHERE document={quote(document)};",
            f"DELETE FROM crop_versions WHERE unit>={low} AND unit<{high};",
            *(f"DELETE FROM {table} WHERE {corpus};" for table in ("corpus_follow", "corpus_gallery", "corpus_units")),
            f"DELETE FROM corpus_document_counts WHERE document={quote(document)};",
        ]
    return sql + [CORPUS_REFRESH.strip(), refresh.VERSION_BUMP.strip()]


def main() -> None:
    print("\n".join(statements(withdrawn.documents())))


if __name__ == "__main__":
    main()
