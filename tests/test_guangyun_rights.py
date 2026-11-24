"""Tests for the rights recorded for the copies of the 澤存堂本 廣韻 headwords are cut from."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

from glyph_atlas.schema import Licence

ROOT = Path(__file__).parents[1]


def test_every_copy_is_recorded_as_the_public_domain_reproduction_of_an_old_print():
    spec = importlib.util.spec_from_file_location("cut_guangyun_headwords", ROOT / "scripts" / "cut_guangyun_headwords.py")
    cut = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cut)
    source = yaml.safe_load((ROOT / "data" / "sources" / "guangyun-zecuntang.yaml").read_text(encoding="utf-8"))
    rights = {copy["id"]: cut.document_of(source, copy, copy["volumes"][0]).image_rights for copy in source["copies"]}
    assert (rights["photographs"].licence, rights["photographs"].holder_terms) == (Licence.PUBLIC_DOMAIN, Licence.UNKNOWN)
    assert (rights["ioc"].licence, rights["ioc"].holder_terms) == (Licence.PUBLIC_DOMAIN, None)
    # Only the flat scan's columns are read as one evenly spaced row.
    assert [copy["id"] for copy in source["copies"] if copy.get("comb")] == ["ioc"]
