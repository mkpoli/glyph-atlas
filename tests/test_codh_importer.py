import io
import zipfile

from PIL import Image

from glyph_atlas import refs
from glyph_atlas.importers import codh
from glyph_atlas.schema import Script, UnitKind


def make_zip(tmp_path):
    path = tmp_path / "900000001.zip"
    image = io.BytesIO()
    Image.new("RGB", (120, 80), "white").save(image, format="JPEG")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("900000001/images/900000001_00003_1.jpg", image.getvalue())
        z.writestr(
            "900000001/900000001_coordinate.csv",
            "Unicode,Image,X,Y,Block ID,Char ID,Width,Height\n"
            "U+304B,900000001_00003_1,10,20,B0001,C0001,30,40\n"
            "U+3005,900000001_00003_1,10,60,B0001,C0002,30,15\n"
            "U+6F22,900000001_00003_1,50,20,B0002,C0001,30,40\n",
        )
    return path


def test_codh_rows_become_pages_and_units(tmp_path):
    document, pages, units = codh.read(make_zip(tmp_path), title="試し")
    assert document.id == "codh:900000001" and document.image_rights.licence.value == "CC-BY-SA-4.0"
    assert len(pages) == 1 and pages[0].width == 120 and pages[0].seq == 5
    assert pages[0].image.endswith("900000001_00003_1.tif")
    assert [u.unicode for u in units] == ["U+304B", "U+3005", "U+6F22"]
    assert units[0].script is Script.HIRAGANA and refs.jibo_of_unit(units[0].unicode) is None
    assert units[1].kind is UnitKind.ITERATION_MARK
    assert units[2].script is Script.HAN and units[2].box.iiif_region() == "50,20,30,40"
    assert units[0].id == "codh:900000001:900000001_00003_1:B0001:C0001"
    assert all(u.document_id == document.id and u.active for u in units)
