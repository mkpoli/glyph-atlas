import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pydantic import ValidationError

from glyph_atlas import tables, written_form
from glyph_atlas.schema import Unit


@pytest.mark.parametrize("value", ["𮟃", "還", "か\u3099", "葛\U000E0100", "⿺辶𦊷", "⿰氵⿱冖月", "⿲木木木", "⿱⿰口口⿰口口",
                                   "〾⿰木？", "⿰\uE000木", "⿰⺡骨"])
def test_a_character_or_a_description_is_a_written_form(value):
    assert written_form.check(value) == value


@pytest.mark.parametrize("value", ["", " 還", "還還", "⿰木", "⿰木木木", "⿲木木", "⿰木a", "⿰木 木", "\u3099",
                                   "⿰", "x" * 65, "⿰木\u200b"])
def test_anything_else_is_refused(value):
    with pytest.raises(ValueError):
        written_form.check(value)


def test_a_unit_holds_its_written_form_and_refuses_a_malformed_one():
    assert Unit(id="u", unicode="U+9084", written_form="𮟃").written_form == "𮟃"
    assert Unit(id="u").written_form is None
    with pytest.raises(ValidationError):
        Unit(id="u", written_form="⿺辶")


def test_a_table_written_before_the_column_reads_as_none(tmp_path):
    units = tmp_path / "units.parquet"
    pq.write_table(pa.table({"id": ["u"], "reading": ["還"]}), units)
    assert [u.written_form for u in tables.read(units, Unit)] == [None]
    tables.write(units, [Unit(id="u", written_form="⿺辶𦊷")], Unit)
    assert tables.read(units, Unit)[0].written_form == "⿺辶𦊷"
