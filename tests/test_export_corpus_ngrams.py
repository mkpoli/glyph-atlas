import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("export_corpus_ngrams", ROOT / "scripts" / "export_corpus_ngrams.py")
export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export)


def row(id, line_id=None, seq=None, page_id="codh:1:1_00004_2"):
    return {"id": id, "line_id": line_id, "seq": seq, "page_id": page_id}


def test_a_codh_glyph_is_placed_by_its_block_and_character_number():
    assert export.position(row("codh:1:1_00004_2:B0002:C0042")) == ("codh:1:1_00004_2:B0002", 42)
    assert export.position(row("hl:x:3", "hl:x_3_001", 3)) == ("hl:x_3_001", 3)
    assert export.position(row("hi:34000001", page_id=None)) is None


def test_text_is_composed_as_the_worker_composes_a_query():
    assert export.compose("が") == "が"
    assert export.compose("豈が") == "豈が"
