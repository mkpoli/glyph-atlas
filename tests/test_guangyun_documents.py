"""Tests for the documents recorded for the copies of the 澤存堂本 廣韻 headwords are cut from."""

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


def test_every_volume_of_every_copy_has_a_title_of_its_own():
    spec = importlib.util.spec_from_file_location("cut_guangyun_headwords", ROOT / "scripts" / "cut_guangyun_headwords.py")
    cut = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cut)
    source = yaml.safe_load((ROOT / "data" / "sources" / "guangyun-zecuntang.yaml").read_text(encoding="utf-8"))
    titles = [cut.document_of(source, copy, volume).title for copy in source["copies"] for volume in copy["volumes"]]
    assert len(set(titles)) == len(titles) == 10
    assert titles[0] == "廣韻卷一　上平聲（澤存堂本，韻典網照片）"
    assert "廣韻卷五　入聲（澤存堂本，東京大學藏）" in titles
