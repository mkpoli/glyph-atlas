"""Small CODH-shaped corpus for the browser review check."""
import json
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
from glyph_atlas import tables
from glyph_atlas.corpus import CorpusAPI, build_chars
from glyph_atlas.corpus.index import sample_units
from glyph_atlas.review.corpus_reviews import CorpusEdit, CorpusReviews
from glyph_atlas.schema import Box, Document, Page, Unit

root = Path(sys.argv[1]).parent
out = root / "codh-full"
out.mkdir(parents=True)
doc = Document(id="codh:200008316", title="Corpus review fixture", holder="Test holder",
               image_rights={"licence": "CC-BY-SA-4.0", "holder": "Test holder", "attribution": "Test holder"})
page = Page(id="codh:200008316:200008316_00030_2", document_id=doc.id, seq=51,
            image="https://codh.rois.ac.jp/char-shape/iiif/200008316/200008316_00030_2.tif",
            width=2878, height=4252)
units = [Unit(id=f"{page.id}:B0001:C00{n}", document_id=doc.id, page_id=page.id,
              seq=n, text_source=char, reading=char, unicode=f"U+{ord(char):04X}", method="import",
              box=Box(x=2305, y=1498+n*240, w=81, h=210)) for n, char in enumerate(['在', '在', '有'])]
for name, rows, model in [('documents', [doc], Document), ('pages', [page], Page), ('units', units, Unit)]:
    tables.write(out / f'{name}.parquet', rows, model)
directory = root / 'corpus-index'
build_chars(root, directory)
sample_units(root, directory, rebuild=True)
reviews = CorpusReviews(CorpusAPI(root, directory))
item = reviews.detail(units[0].id)
reviews.record(CorpusEdit(id=UUID('3f7990bb-c834-41e9-b8fe-cb9db1c5757e'), identity=item['id'],
               client_id='reported-example', revision=0, source_revision=item['source_revision'],
               verdict='wrong', issue='reading'), actor_kind='user-report',
               suggestions=[{'text': '有', 'engine': 'Reported correction'}])
print(json.dumps({'reported': units[0].id, 'next': units[1].id}))
