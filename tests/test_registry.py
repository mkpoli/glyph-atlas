from kuzushiji_atlas import registry
from kuzushiji_atlas.schema import Licence


def test_every_source_declares_a_licence_and_attribution():
    sources = registry.load()
    assert {s.id for s in sources} >= {"codh-char-shape", "hi-lab-kuzushiji", "ndl-minhon-ocr", "honkoku-data"}
    for source in sources:
        assert source.licence not in (Licence.RESTRICTED, Licence.UNKNOWN)
        assert source.attribution
