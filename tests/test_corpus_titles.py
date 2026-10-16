import sys
from pathlib import Path

from glyph_atlas import tables
from glyph_atlas.corpus.sources import Corpus
from glyph_atlas.schema import Document

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from export_cloudflare_corpus import corpus_titles


def corpus(root: Path, name: str, documents: list[Document]) -> Corpus:
    (root / name).mkdir()
    tables.write(root / name / "documents.parquet", documents, Document)
    return Corpus(name=name, directory=root / name)


def test_each_corpus_documents_title_is_read_by_its_id(tmp_path):
    first = corpus(tmp_path, "one", [Document(id="one:a", title="Book A"), Document(id="one:b", title="Book B")])
    second = corpus(tmp_path, "two", [Document(id="two:a", title="二の巻")])
    assert corpus_titles([first, second]) == {"one:a": "Book A", "one:b": "Book B", "two:a": "二の巻"}


def test_a_corpus_without_documents_names_none(tmp_path):
    (tmp_path / "bare").mkdir()
    assert corpus_titles([Corpus(name="bare", directory=tmp_path / "bare")]) == {}

