import pytest
from PIL import Image, ImageDraw

from glyph_atlas.partial_split import child_units, propose_partial
from glyph_atlas.schema import Box, Unit
from glyph_atlas.split_proposals import ink_profile


def crop():
    image = Image.new("RGB", (50, 180), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((15, 8, 25, 103), fill="black")
    draw.rectangle((15, 132, 25, 171), fill="black")
    return image


def recognize(image):
    profile = ink_profile(image, smooth=1)
    rows = [i for i, ink in enumerate(profile) if ink > 0]
    joined = rows[-1] - rows[0] > 70
    return {"votes": [{"engine": "NDLkotenOCR", "text": "とり" if joined else "}", "score": .99},
                      {"engine": "Atlas classifier", "text": "て" if joined else "い", "score": .98}]}


def test_connected_pair_stays_together_while_single_child_is_extracted():
    result = propose_partial(crop(), "とりい", recognize)
    assert result["accepted"] and result["partial"]
    assert result["text"] == ["とり", "い"]
    assert 103 < result["cuts"][0] < 132
    assert result["children"][1]["engine"] == "Atlas classifier"


def test_no_equal_slicing_when_strokes_never_leave_a_gap():
    image = Image.new("RGB", (50, 180), "white")
    ImageDraw.Draw(image).rectangle((15, 2, 25, 178), fill="black")
    def never(_):
        raise AssertionError("no blank boundary to assess")
    assert not propose_partial(image, "とりい", never)["accepted"]


def test_recognition_must_reproduce_the_source_sequence():
    assert not propose_partial(crop(), "かきく", recognize)["accepted"]


def test_japanese_model_disagreement_is_not_treated_as_ascii_abstention():
    def disagree(image):
        result = recognize(image)
        if result['votes'][0]['text'] == '}':
            result['votes'][0]['text'] = 'ろ'
        return result
    assert not propose_partial(crop(), 'とりい', disagree)['accepted']


def test_family_probability_cannot_supply_an_exact_child_label():
    def family_only(image):
        result = recognize(image)
        result["votes"][1]["identity_scope"] = "family"
        return result
    assert not propose_partial(crop(), "とりい", family_only)["accepted"]


def test_child_records_keep_source_and_distinguish_character_from_group():
    parent = Unit(id='source:1', document_id='d', page_id='p', line_id='l', seq=2,
                  box=Box(x=100, y=200, w=50, h=180), reading='とりい',
                  text_source='とりい', unicode='U+3068 U+308A U+3044', granularity='block')
    proposal = propose_partial(crop(), 'とりい', recognize)
    retired, children = child_units(parent, proposal, 'a' * 64)
    assert not retired.active and retired.text_source == 'とりい'
    assert children[0].kind == 'sequence' and children[0].granularity == 'sequence'
    assert children[1].kind == 'char' and children[1].reading == 'い'
    assert all(c.review == 'machine' and c.antecedent_ids == [parent.id] for c in children)
    assert children[0].box.y == 200 and children[1].box.y == 200 + proposal['cuts'][0]
    assert [c.id for c in child_units(parent, proposal, 'a' * 64)[1]] == retired.split_into
    parent.unicode = 'U+2A708'
    with pytest.raises(ValueError, match='ligature'):
        child_units(parent, proposal, 'a' * 64)
