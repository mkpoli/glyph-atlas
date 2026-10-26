from glyph_atlas import registry
from glyph_atlas.schema import Licence


def test_every_source_declares_a_licence_and_attribution():
    sources = registry.load()
    assert {s.id for s in sources} >= {"codh-char-shape", "hi-lab-kuzushiji", "ndl-minhon-ocr", "honkoku-data"}
    for source in sources:
        # The Kyoto research snapshots are collected locally with unstated annotation rights.
        if source.id == "kyodokenkyu":
            assert source.licence == Licence.UNKNOWN
        else:
            assert source.licence not in (Licence.RESTRICTED, Licence.UNKNOWN)
        assert source.attribution
