"""Tests for the origin vocabulary and the document field it fills."""

import pytest
from pydantic import ValidationError

from glyph_atlas import origin
from glyph_atlas.schema import Document


def test_the_vocabulary_names_the_four_regions():
    assert {"china", "korea", "japan", "vietnam", "other", "unknown"} <= set(origin.TREE.nodes)
    assert origin.label("korea") == "Korea"


def test_a_document_defaults_to_unknown_and_takes_only_listed_values():
    assert Document(id="d", title="t").origin == "unknown"
    assert Document(id="d", title="t", origin="vietnam").origin == "vietnam"
    with pytest.raises(ValidationError):
        Document(id="d", title="t", origin="ryukyu")
