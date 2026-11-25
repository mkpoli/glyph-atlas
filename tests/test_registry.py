from glyph_atlas import registry
from glyph_atlas.schema import Licence


def test_every_source_declares_a_licence_and_attribution():
    sources = registry.load()
    assert {s.id for s in sources} >= {"codh-char-shape", "hi-lab-kuzushiji", "ndl-minhon-ocr", "honkoku-data"}
    for source in sources:
        # The Kyoto research snapshots are collected locally with unstated annotation rights, and
        # the BabelStone coin article states no licence or photographer credit for its coin images.
        if source.id in ("kyodokenkyu", "babelstone-tangut-coins"):
            assert source.licence == Licence.UNKNOWN
        else:
            assert source.licence not in (Licence.RESTRICTED, Licence.UNKNOWN)
        assert source.attribution


def test_only_the_documents_a_source_names_have_their_pages_hosted():
    hosted = registry.hosted_documents()
    assert "codh-tensho:TE00039" in hosted
    # The Tangut plates are scans of a printed edition whose hosting nobody has decided.
    assert not any(document.startswith("ecang-heishuicheng:") for document in hosted)
