import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load():
    spec = importlib.util.spec_from_file_location("flag_corpus_glyphs", ROOT / "scripts/flag_corpus_glyphs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def entry(n, form="𛁈", realigned="所", label="シ"):
    return {"id": f"hl:a_1_000:x:{n}", "label": label, "realigned": realigned, "form": form, "line_id": "hl:a_1_000", "box": [1, 2, 3, 4]}


def test_a_glyph_is_checked_against_the_form_the_site_shows():
    flag = load()
    assert flag.glyph(entry(1)) == {"id": "hl:a_1_000:x:1", "character": "𛁈", "realigned": "所", "transcribed": "シ"}
    assert flag.glyph(entry(2, realigned=None))["realigned"] is None


def test_a_family_code_point_names_no_shown_character():
    flag = load()
    with pytest.raises(ValueError):
        flag.glyph(entry(1, form="U+3057"))
    with pytest.raises(ValueError):
        flag.glyph(entry(1, form=None))


def test_batches_stay_within_the_worker_limit():
    flag = load()
    planned = flag.batches([entry(n) for n in range(200)])
    assert [len(batch["glyphs"]) for batch in planned] == [96, 96, 8]
    assert {batch["finding"] for batch in planned} == {"box-relabel"}
    with pytest.raises(ValueError):
        flag.batches([entry(1), entry(1)])
