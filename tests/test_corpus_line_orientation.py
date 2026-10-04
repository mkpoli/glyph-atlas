import sys
from pathlib import Path

from glyph_atlas import tables
from glyph_atlas.corpus.sources import Corpus
from glyph_atlas.schema import Line

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from export_cloudflare_corpus import line_orientation


def test_a_sharded_lines_table_gives_each_lines_direction(tmp_path):
    (tmp_path / "lines").mkdir()
    tables.write(tmp_path / "lines" / "part-0.parquet", [Line(id="H", page_id="P", seq=0, text_raw="ab", text="ab", vertical=False)], Line)
    (tmp_path / "lines" / "MANIFEST.json").write_text("{}")
    assert line_orientation(Corpus(name="test", directory=tmp_path)) == {"H": False}
