"""Character review uses real crops, durable decisions and atomic rounds."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from glyph_atlas import tables
from glyph_atlas.review import atlas as atlas_module
from glyph_atlas.review.atlas import image_size, readable_image
from glyph_atlas.review.server import create_app
from glyph_atlas.review.store import ReviewRequest, Store, apply, replay
from glyph_atlas.schema import Box, Document, Line, Page, ReviewState, Unit

PAGE = "hk:entry:with:separators:9"
LINE = PAGE + ":L3"


@pytest.fixture
def dataset(tmp_path: Path, monkeypatch):
    root = tmp_path / "dataset"
    root.mkdir()
    cache = tmp_path / "cache"
    monkeypatch.setenv("GLYPH_ATLAS_CACHE", str(cache))
    im = Image.new("RGB", (400, 600), "white")
    draw = ImageDraw.Draw(im)
    for i in range(16):
        x, y = 20 + i % 4 * 90, 20 + i // 4 * 130
        draw.line([(x + 5, y + 4), (x + 60, y + 95)], fill="black", width=5)
    image = tmp_path / "scan.jpg"
    im.save(image)
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    folder = cache / "images" / digest[:2]
    folder.mkdir(parents=True)
    (folder / (digest + ".jpg")).write_bytes(image.read_bytes())
    tables.write(root / "documents.parquet", [Document(id="d", title="Fixture")], Document)
    tables.write(root / "pages.parquet", [Page(id=PAGE, document_id="d", seq=9,
                 image="https://example.org/scan.jpg", width=400, height=600, sha256=digest)], Page)
    tables.write(root / "lines.parquet", [Line(id=LINE, page_id=PAGE, seq=3,
                 text_raw="あいう", text="あいう", box=Box(x=0, y=0, w=400, h=600))], Line)
    units = [Unit(id=LINE + f":u{i}", document_id="d", page_id=PAGE, line_id=LINE, seq=i,
                  reading="あ" if i < 12 else "シ", script="hiragana" if i < 12 else "katakana",
                  box=Box(x=20 + i % 4 * 90, y=20 + i // 4 * 130, w=65, h=100)) for i in range(16)]
    units.extend([Unit(id="blank", reading="　", box=Box(x=1, y=1, w=10, h=10), page_id=PAGE),
                  Unit(id="no-box", reading="あ", page_id=PAGE),
                  Unit(id="retired", reading="あ", box=Box(x=1, y=1, w=10, h=10), page_id=PAGE, active=False)])
    tables.write(root / "units.parquet", units, Unit)
    return root


def round_payload(client, count=4):
    data = client.get('/atlas', params={"reading": "あ", "state": "pending", "limit": count}).json()
    return {"id": str(uuid4()), "client_id": "fixture-reviewer", "label": "あ",
            "answers": [{"id": item["id"], "revision": item["revision"], "image_sha256": item["image_sha256"], "verdict": "match"}
                        for item in data['items']]}


def test_catalogue_filters_and_shuffle(dataset):
    client = TestClient(create_app(dataset))
    data = client.get('/atlas').json()
    assert data['available'] == 16
    assert data['counts'] == {"pending": 16}
    assert {c['label'] for c in data['categories']} == {"あ", "シ"}
    assert client.get('/atlas?reading=シ').json()['total'] == 4
    assert client.get('/atlas?group=kanji').json()['total'] == 0
    assert client.get('/atlas?group=kana').json()['total'] == 16
    assert client.get('/atlas?seed=12').json()['items'] != client.get('/atlas?seed=13').json()['items']


def test_catalogue_filters_hangul_as_its_own_group(dataset):
    units = list(tables.read(dataset / 'units.parquet', Unit))
    units.extend(Unit(id=f'jamo-{char}', document_id='d', page_id=PAGE, reading=char, script='hangul',
                      box=Box(x=10, y=10, w=20, h=30)) for char in ('ㅿ', 'ᄫ', '한'))
    tables.write(dataset / 'units.parquet', units, Unit)
    client = TestClient(create_app(dataset))
    hangul = client.get('/atlas?group=hangul').json()
    assert hangul['total'] == 3 and {i['label'] for i in hangul['items']} == {'ㅿ', 'ᄫ', '한'}
    assert client.get('/atlas?group=kana').json()['total'] == 16
    assert client.get('/atlas?group=kanji').json()['total'] == 0


def test_catalogue_counts_and_filters_by_book(dataset):
    tables.write(dataset / 'documents.parquet', [Document(id='d', title='Fixture'), Document(id='e', title='Second')], Document)
    units = list(tables.read(dataset / 'units.parquet', Unit))
    units.extend(Unit(id=f'second-{i}', document_id='e', page_id=PAGE, reading='あ', script='hiragana',
                      box=Box(x=10, y=10, w=20, h=30)) for i in range(2))
    tables.write(dataset / 'units.parquet', units, Unit)
    client = TestClient(create_app(dataset))
    shelf = client.get('/atlas').json()['documents']
    assert [(b['id'], b['title'], b['total'], b['pending']) for b in shelf] == [('d', 'Fixture', 16, 16), ('e', 'Second', 2, 2)]
    second = client.get('/atlas?document=e').json()
    assert second['total'] == 2 and {i['id'] for i in second['items']} == {'second-0', 'second-1'}
    assert client.get('/atlas?document=e&reading=シ').json()['total'] == 0
    assert client.get('/atlas?document=missing').json()['total'] == 0
    assert client.get('/atlas?document=').json()['total'] == 18


def test_catalogue_files_labels_under_graphemes_and_filters_by_one(dataset):
    units = list(tables.read(dataset / 'units.parquet', Unit))
    units.extend(Unit(id=f'extra-{i}', document_id='d', page_id=PAGE, reading=char, script='hiragana',
                      box=Box(x=10, y=10, w=20, h=30)) for i, char in enumerate(('\U0001B002', '\U0001B002', '※')))
    tables.write(dataset / 'units.parquet', units, Unit)
    client = TestClient(create_app(dataset))
    filed = {c['label']: c['grapheme'] for c in client.get('/atlas').json()['categories']}
    # 𛀂 is a hentaigana of あ, シ is filed under し, and ※ has no family, so it is its own grapheme.
    assert filed == {'あ': 'U+3042', '\U0001B002': 'U+3042', 'シ': 'U+3057', '※': 'U+203B'}
    family = client.get('/atlas', params={'grapheme': 'U+3042', 'limit': 96}).json()
    assert family['total'] == 14 and {i['label'] for i in family['items']} == {'あ', '\U0001B002'}
    assert client.get('/atlas', params={'grapheme': 'u+203b'}).json()['total'] == 1
    assert client.get('/atlas', params={'grapheme': 'U+4EEE'}).json()['total'] == 0


def test_character_group_follows_the_script_of_the_first_character():
    groups = {char: atlas_module.character_group(Unit(id=char, reading=char))
              for char in ('あ', 'ア', '𛀁', '仮', 'ㅿ', 'ᄫ', '한', '㉠', '', 'A')}
    assert groups == {'あ': 'kana', 'ア': 'kana', '𛀁': 'kana', '仮': 'kanji', 'ㅿ': 'hangul', 'ᄫ': 'hangul',
                      '한': 'hangul', '㉠': 'hangul', '': 'gugyeol', 'A': 'other'}


def test_catalogue_filters_gugyeol_as_its_own_group(dataset):
    units = list(tables.read(dataset / 'units.parquet', Unit))
    units.extend(Unit(id=f'gugyeol-{char}', document_id='d', page_id=PAGE, reading=char, script='gugyeol',
                      box=Box(x=10, y=10, w=20, h=30)) for char in ('', ''))
    tables.write(dataset / 'units.parquet', units, Unit)
    client = TestClient(create_app(dataset))
    gugyeol = client.get('/atlas?group=gugyeol').json()
    assert gugyeol['total'] == 2 and {i['label'] for i in gugyeol['items']} == {'', ''}
    assert client.get('/atlas?group=kana').json()['total'] == 16
    assert client.get('/atlas?group=kanji').json()['total'] == 0


def test_quick_review_excludes_movable_type_until_explicitly_selected(dataset):
    kinds = ['handwritten', 'printed/woodblock', 'printed/type', 'printed/type/metal/copper', 'mixed', 'unknown']
    ids = {kind: kind.replace('/', '-') for kind in kinds}
    docs = [Document(id='d', title='Fixture')] + [Document(id=ids[kind], title=kind, production=kind)
                                                for kind in kinds]
    tables.write(dataset / 'documents.parquet', docs, Document)
    units = list(tables.read(dataset / 'units.parquet', Unit))
    units.extend(Unit(id=ids[kind], document_id=ids[kind], page_id=PAGE, reading='字',
                      box=Box(x=10, y=10, w=20, h=30)) for kind in kinds)
    tables.write(dataset / 'units.parquet', units, Unit)
    client = TestClient(create_app(dataset))
    default = client.get('/atlas?purpose=review').json()
    assert default['production'] == 'not:printed/type'
    assert default['total'] == default['available'] == 20
    assert default['counts'] == {'pending': 20}
    assert next(c for c in default['categories'] if c['label'] == '字')['pending'] == 4
    assert {i['production'] for i in default['items']} == {'handwritten', 'printed/woodblock', 'mixed', 'unknown'}
    assert client.get('/atlas').json()['total'] == 22
    assert client.get('/atlas?purpose=review&production=all').json()['total'] == 22
    expected = {'handwritten': 1, 'printed': 3, 'printed/type': 2, 'printed/type/metal/copper': 1,
                'not:printed': 19, 'unknown': 17}
    for scope, total in expected.items():
        result = client.get('/atlas', params={'purpose': 'review', 'production': scope}).json()
        assert result['total'] == total, scope
    assert client.get('/atlas?purpose=review&production=woodblock').status_code == 422
    paged = [client.get('/atlas', params={'purpose': 'review', 'reading': '字', 'limit': 1,
                                        'offset': offset, 'seed': 5}).json()['items'][0]
             for offset in range(4)]
    assert len({i['id'] for i in paged}) == 4
    assert all(not i['production'].startswith('printed/type') for i in paged)


def test_review_material_follows_page_document_and_updated_source_evidence(dataset, tmp_path, monkeypatch):
    from glyph_atlas import production

    units = list(tables.read(dataset / 'units.parquet', Unit))
    for unit in units:
        unit.document_id = None
    tables.write(dataset / 'units.parquet', units, Unit)
    overrides = tmp_path / 'production.yaml'
    monkeypatch.setattr(production, 'OVERRIDES', overrides)
    client = TestClient(create_app(dataset))
    assert client.get('/atlas?purpose=review').json()['total'] == 16
    overrides.write_text('documents:\n  d:\n    production: printed/type/wood\n')
    assert client.get('/atlas?purpose=review').json()['total'] == 0
    explicit = client.get('/atlas?purpose=review&production=printed/type').json()
    assert explicit['total'] == 16
    assert client.get('/atlas').json()['total'] == 16


def test_a_box_outside_the_scan_is_not_offered_as_a_viewable_character(dataset):
    units = list(tables.read(dataset / 'units.parquet', Unit))
    outside = next(unit for unit in units if unit.id == LINE + ':u0')
    outside.box = Box(x=20, y=900, w=40, h=80)
    tables.write(dataset / 'units.parquet', units, Unit)
    client = TestClient(create_app(dataset))
    result = client.get('/atlas').json()
    assert result['available'] == 15
    assert outside.id not in {item['id'] for item in result['items']}
    detail = client.get('/atlas/characters/' + outside.id)
    assert detail.status_code == 200 and detail.json()['context'] is False
    assert client.get('/atlas/characters/' + outside.id + '/image?revision=0').status_code == 404


def test_real_character_image_and_context_do_not_guess_page_ids(dataset):
    client = TestClient(create_app(dataset))
    item = client.get('/atlas').json()['items'][0]
    response = client.get(item['image'])
    assert response.status_code == 200 and response.headers['content-type'] == 'image/webp'
    detail = client.get('/atlas/characters/' + item['id']).json()
    assert detail['page_id'] == PAGE and detail['line']['id'] == LINE
    assert detail['context_box']['w'] > detail['box']['w']
    assert client.get('/lines/' + LINE).json()['page_id'] == PAGE
    assert client.get(detail['context_image']).status_code == 200


def test_round_persists_distinct_verdicts_and_can_be_undone_after_restart(dataset):
    client = TestClient(create_app(dataset))
    payload = round_payload(client)
    payload['answers'][0]['verdict'] = 'wrong'
    payload['answers'][1]['verdict'] = 'unsure'
    result = client.post('/atlas/rounds', json=payload)
    assert result.status_code == 200, result.text
    assert len(result.json()['results']) == 4
    reopened = TestClient(create_app(dataset))
    data = reopened.get('/atlas').json()
    assert data['counts'] == {"checked": 2, "flagged": 2, "pending": 12}
    events = Store(dataset).events()
    assert '"verdict": "wrong"' in events[0].evidence
    assert '"verdict": "unsure"' in events[1].evidence
    assert reopened.post('/atlas/rounds/' + payload['id'] + '/undo',
                         json={"client_id": payload['client_id']}).status_code == 200
    assert reopened.get('/atlas').json()['counts'] == {"pending": 16}
    assert len(Store(dataset).events()) == 8


def test_partial_round_leaves_unselected_crops_unreviewed(dataset):
    client = TestClient(create_app(dataset))
    shown = client.get('/atlas?reading=あ&state=pending&limit=12').json()['items']
    payload = {"id": str(uuid4()), "client_id": "problems-only", "label": "あ",
               "answers": [{"id": item['id'], "revision": item['revision'],
                            "image_sha256": item['image_sha256'], "verdict": "wrong", "issue": issue}
                           for item, issue in zip(shown[:2], ['reading', 'crop'], strict=True)]}
    assert client.post('/atlas/rounds', json=payload).status_code == 200
    reopened = TestClient(create_app(dataset))
    assert reopened.get('/atlas').json()['counts'] == {"flagged": 2, "pending": 14}
    store = Store(dataset)
    assert len(store.events()) == 2
    for item in shown[2:]:
        detail = reopened.get('/atlas/characters/' + item['id']).json()
        assert detail['state'] == 'pending'
        assert detail['revision'] == item['revision']
    assert all(json.loads(e.evidence)['verdict'] == 'wrong' for e in store.events())
    assert reopened.post('/atlas/rounds/' + payload['id'] + '/undo',
                         json={"client_id": payload['client_id']}).status_code == 200
    assert reopened.get('/atlas').json()['counts'] == {"pending": 16}


def test_round_accepts_problems_from_additional_same_character_crops(dataset):
    units = list(tables.read(dataset / 'units.parquet', Unit))
    units.extend(Unit(id=f'extra-{i}', document_id='d', page_id=PAGE, line_id=LINE,
                      reading='あ', script='hiragana', box=Box(x=10 + i * 10, y=570, w=8, h=10))
                 for i in range(20))
    tables.write(dataset / 'units.parquet', units, Unit)
    client = TestClient(create_app(dataset))
    payload = round_payload(client, count=32)
    assert len(payload['answers']) == 32
    for answer in payload['answers']:
        answer.update(verdict='wrong', issue='crop')
    response = client.post('/atlas/rounds', json=payload)
    assert response.status_code == 200, response.text
    assert client.get('/atlas').json()['counts'] == {'flagged': 32, 'pending': 4}
    assert len(Store(dataset).events()) == 32
    assert client.post('/atlas/rounds/' + payload['id'] + '/undo',
                       json={'client_id': payload['client_id']}).status_code == 200
    assert client.get('/atlas').json()['counts'] == {'pending': 36}


def test_a_stale_round_writes_nothing(dataset):
    client = TestClient(create_app(dataset))
    payload = round_payload(client)
    client.post('/reviews', json={"target_type": "unit", "target_id": payload['answers'][-1]['id'],
                "field": "reading", "new": "い", "client_id": "someone-else"})
    # The stale revision is checked atomically even if the earlier answers are valid.
    payload['answers'][-1]['revision'] = 0
    payload['label'] = 'あ'
    # Change a note instead so the reading/category check does not short-circuit the transaction.
    payload = round_payload(client)
    client.post('/reviews', json={"target_type": "unit", "target_id": payload['answers'][-1]['id'],
                "field": "note", "new": "Another edit", "client_id": "someone-else"})
    before = len(Store(dataset).events())
    assert client.post('/atlas/rounds', json=payload).status_code == 409
    assert len(Store(dataset).events()) == before
    assert not client.get('/atlas?state=checked').json()['items']


def test_retry_is_idempotent_and_changed_retry_is_rejected(dataset):
    client = TestClient(create_app(dataset))
    payload = round_payload(client)
    assert client.post('/atlas/rounds', json=payload).status_code == 200
    retry = client.post('/atlas/rounds', json=payload)
    assert retry.status_code == 200
    assert all(r['duplicate'] for r in retry.json()['results'])
    assert len(Store(dataset).events()) == 4
    payload['answers'][-1]['verdict'] = 'wrong'
    assert client.post('/atlas/rounds', json=payload).status_code == 422
    assert len(Store(dataset).events()) == 4


def test_a_stale_undo_does_not_withdraw_other_answers(dataset):
    client = TestClient(create_app(dataset))
    payload = round_payload(client)
    client.post('/atlas/rounds', json=payload)
    client.post('/reviews', json={"target_type": "unit", "target_id": payload['answers'][-1]['id'],
                "field": "note", "new": "Later review", "client_id": "someone-else"})
    before = len(Store(dataset).events())
    assert client.post('/atlas/rounds/' + payload['id'] + '/undo',
                       json={"client_id": payload['client_id']}).status_code == 409
    assert len(Store(dataset).events()) == before
    assert client.get('/atlas?state=checked').json()['total'] == 4


def test_round_rejects_other_category_or_repeated_character(dataset):
    client = TestClient(create_app(dataset))
    payload = round_payload(client)
    payload['label'] = 'シ'
    assert client.post('/atlas/rounds', json=payload).status_code == 409
    payload['label'] = 'あ'
    payload['answers'][1] = payload['answers'][0]
    assert client.post('/atlas/rounds', json=payload).status_code == 422
    assert Store(dataset).events() == []


def test_crop_edit_publishes_new_image_and_old_review_still_conflicts(dataset):
    client = TestClient(create_app(dataset))
    item = client.get('/atlas?reading=あ').json()['items'][0]
    payload = {"id": str(uuid4()), "client_id": "editor", "reading": "い", "revision": item['revision'], "image_sha256": item['image_sha256'],
               "verdict": "match", "box": {**item['box'], "w": item['box']['w'] - 5}}
    result = client.post('/atlas/characters/' + item['id'], json=payload)
    assert result.status_code == 200, result.text
    detail = client.get('/atlas/characters/' + item['id']).json()
    assert detail['label'] == 'い' and detail['state'] == 'checked' and detail['revision'] == 3
    assert detail['box']['w'] == item['box']['w'] - 5
    assert detail['image'] != item['image']
    assert client.get(item['image']).status_code == 200  # immutable reviewed pixels remain available
    assert client.get(detail['image']).status_code == 200
    payload['id'] = str(uuid4())
    assert client.post('/atlas/characters/' + item['id'], json=payload).status_code == 409
    assert len(Store(dataset).events()) == 3


def test_round_survives_apply_and_replay(dataset):
    client = TestClient(create_app(dataset))
    payload = round_payload(client)
    client.post('/atlas/rounds', json=payload)
    apply(dataset)
    replay(dataset)
    reopened = TestClient(create_app(dataset))
    assert reopened.get('/atlas?state=checked').json()['total'] == 4
    assert len(reopened.get('/atlas/reviews').json()['reviews']) == 4


def test_url_index_resolves_images_without_a_page_checksum(dataset):
    from glyph_atlas import images

    page = next(iter(tables.read(dataset / 'pages.parquet', Page)))
    cached = images.images_root() / page.sha256[:2] / (page.sha256 + '.jpg')
    images.register(cached, page.image)
    page.sha256 = None
    tables.write(dataset / 'pages.parquet', [page], Page)
    client = TestClient(create_app(dataset))
    entries = client.get('/atlas').json()['items']
    assert len(entries) == 16
    assert client.get(entries[0]['image']).status_code == 200


def test_export_preserves_the_reviewed_snapshot_and_marks_undone_answers(dataset):
    client = TestClient(create_app(dataset))
    payload = round_payload(client, count=1)
    client.post('/atlas/rounds', json=payload)
    exported = client.get('/atlas/reviews').json()['reviews'][0]
    assert exported['current'] is True
    assert exported['reviewed']['character']['label'] == 'あ'
    client.post('/reviews', json={"target_type": "unit", "target_id": payload['answers'][0]['id'],
                "field": "reading", "new": "い", "client_id": "later-reviewer"})
    changed = client.get('/atlas/reviews').json()['reviews'][0]
    assert changed['current'] is False
    assert changed['reviewed']['character']['label'] == 'あ'
    payload2 = round_payload(client, count=1)
    client.post('/atlas/rounds', json=payload2)
    client.post('/atlas/rounds/' + payload2['id'] + '/undo', json={"client_id": payload2['client_id']})
    assert all(not r['current'] for r in client.get('/atlas/reviews').json()['reviews'])


def test_no_crop_cannot_be_submitted_as_a_visual_review(dataset):
    client = TestClient(create_app(dataset))
    payload = {"id": str(uuid4()), "client_id": "reader", "label": "あ",
               "answers": [{"id": "no-box", "revision": 0, "image_sha256": "0" * 64, "verdict": "match"}]}
    assert client.post('/atlas/rounds', json=payload).status_code == 422
    assert Store(dataset).events() == []


@pytest.mark.parametrize('box', [{'x': 1, 'y': 1, 'w': 0, 'h': 5}, {'x': -1, 'y': 1, 'w': 5, 'h': 5},
                                  {'x': 1, 'y': 1, 'w': 5, 'h': -5}, {'x': 390, 'y': 1, 'w': 20, 'h': 5}])
def test_invalid_crop_geometry_is_rejected_without_writes(dataset, box):
    client = TestClient(create_app(dataset))
    item = client.get('/atlas').json()['items'][0]
    payload = {"id": str(uuid4()), "client_id": "reader", "revision": item['revision'], "image_sha256": item['image_sha256'],
               "reading": item['label'], "verdict": "match", "box": box}
    assert client.post('/atlas/characters/' + item['id'], json=payload).status_code == 422
    assert Store(dataset).events() == []


def test_model_rejections_are_pending_and_human_undo_restores_that_state(dataset):
    units = list(tables.read(dataset / 'units.parquet', Unit))
    for unit in units:
        unit.review = ReviewState.REJECTED
    tables.write(dataset / 'units.parquet', units, Unit)
    client = TestClient(create_app(dataset))
    assert client.get('/atlas').json()['counts'] == {'pending': 16}
    payload = round_payload(client, count=1)
    payload['answers'][0]['verdict'] = 'wrong'
    assert client.post('/atlas/rounds', json=payload).status_code == 200
    assert client.get('/atlas?state=flagged').json()['total'] == 1
    client.post('/atlas/rounds/' + payload['id'] + '/undo', json={'client_id': payload['client_id']})
    assert client.get('/atlas').json()['counts'] == {'pending': 16}


def test_flagged_view_puts_crops_already_reviewed_in_the_inspector_last(dataset):
    """A flagged crop is a queue: one nobody has looked at yet still needs a person's eyes first."""
    client = TestClient(create_app(dataset))
    payload = round_payload(client, count=2)
    for answer in payload['answers']:
        answer.update(verdict='wrong', issue='blank')
    assert client.post('/atlas/rounds', json=payload).status_code == 200
    baseline = [item['id'] for item in client.get('/atlas?state=flagged').json()['items']]
    assert len(baseline) == 2
    target = baseline[0]

    current = client.get('/atlas/characters/' + target).json()
    edit = {'id': str(uuid4()), 'client_id': 'inspector', 'revision': current['revision'],
            'image_sha256': current['image_sha256'], 'verdict': 'wrong', 'issue': 'crop'}
    saved = client.post('/atlas/characters/' + target, json=edit)
    assert saved.status_code == 200, saved.text

    # A crop reviewed in the inspector moves behind the one nobody has looked at yet.
    ordered = [item['id'] for item in client.get('/atlas?state=flagged').json()['items']]
    assert ordered == [baseline[1], target]

    # Undoing that review is the store's own compensating event, the same mark any other undo
    # writes; taking it back makes the crop as unreviewed as it was before.
    review = next(r for r in saved.json()['results'] if r['field'] == 'review')
    Store(dataset).record_batch([ReviewRequest(
        target_type='unit', target_id=target, field='review', new=review['review']['old'],
        base_revision=review['revision'], client_id='inspector',
        idempotency_key='undo:' + review['id'], evidence='undo of ' + review['id'])])
    restored = [item['id'] for item in client.get('/atlas?state=flagged').json()['items']]
    assert restored == baseline


def test_flagged_view_can_hide_crops_already_reviewed_in_the_inspector(dataset):
    """`reported=hide` leaves out a flagged crop a person already looked at in the inspector, and
    counts it as `reported_count`; `reported=show`, the default, leaves every caller unaffected."""
    client = TestClient(create_app(dataset))
    payload = round_payload(client, count=2)
    for answer in payload['answers']:
        answer.update(verdict='wrong', issue='blank')
    assert client.post('/atlas/rounds', json=payload).status_code == 200
    baseline = [item['id'] for item in client.get('/atlas?state=flagged').json()['items']]
    assert len(baseline) == 2
    target = baseline[0]

    shown = client.get('/atlas?state=flagged').json()
    assert shown['reported_count'] == 0
    assert client.get('/atlas?state=flagged&reported=show').json()['reported_count'] == 0

    current = client.get('/atlas/characters/' + target).json()
    edit = {'id': str(uuid4()), 'client_id': 'inspector', 'revision': current['revision'],
            'image_sha256': current['image_sha256'], 'verdict': 'wrong', 'issue': 'crop'}
    saved = client.post('/atlas/characters/' + target, json=edit)
    assert saved.status_code == 200, saved.text

    # The default, `reported=show`, is unaffected: both crops still list, the reviewed one last.
    shown = client.get('/atlas?state=flagged').json()
    assert [item['id'] for item in shown['items']] == [i for i in baseline if i != target] + [target]
    assert shown['reported_count'] == 1

    # `reported=hide` leaves the reviewed crop out, and still reports it as hidden.
    hidden = client.get('/atlas?state=flagged&reported=hide').json()
    assert [item['id'] for item in hidden['items']] == [i for i in baseline if i != target]
    assert hidden['total'] == 1
    assert hidden['reported_count'] == 1

    # Undoing that review brings the crop back.
    review = next(r for r in saved.json()['results'] if r['field'] == 'review')
    Store(dataset).record_batch([ReviewRequest(
        target_type='unit', target_id=target, field='review', new=review['review']['old'],
        base_revision=review['revision'], client_id='inspector',
        idempotency_key='undo:' + review['id'], evidence='undo of ' + review['id'])])
    restored = client.get('/atlas?state=flagged&reported=hide').json()
    assert sorted(item['id'] for item in restored['items']) == sorted(baseline)
    assert restored['reported_count'] == 0


def test_changed_url_keyed_image_cannot_be_saved_as_the_image_seen(dataset, tmp_path):
    from glyph_atlas import images

    page = next(iter(tables.read(dataset / 'pages.parquet', Page)))
    file = images.images_root() / page.sha256[:2] / (page.sha256 + '.jpg')
    images.register(file, page.image)
    page.sha256 = None
    tables.write(dataset / 'pages.parquet', [page], Page)
    client = TestClient(create_app(dataset))
    payload = round_payload(client, count=1)
    old_image = client.get('/atlas?reading=あ&limit=1').json()['items'][0]['image']
    changed = tmp_path / 'changed.jpg'
    Image.new('RGB', (400, 600), 'gray').save(changed)
    images.register(changed, page.image)
    assert client.get(old_image).status_code == 200
    assert client.get('/atlas?reading=あ&limit=1').json()['items'][0]['image'] != old_image
    response = client.post('/atlas/rounds', json=payload)
    assert response.status_code == 409
    assert Store(dataset).events() == []


def test_issue_can_be_saved_without_transcribing_joined_characters(dataset):
    import json
    client = TestClient(create_app(dataset))
    item = client.get('/atlas?limit=1').json()['items'][0]
    payload = {'id': str(uuid4()), 'client_id': 'editor', 'revision': item['revision'],
               'image_sha256': item['image_sha256'], 'verdict': 'wrong', 'issue': 'merged'}
    response = client.post('/atlas/characters/' + item['id'], json=payload)
    assert response.status_code == 200, response.text
    current = client.get('/atlas/characters/' + item['id']).json()
    assert current['state'] == 'flagged' and current['label'] == item['label']
    assert json.loads(Store(dataset).events()[-1].evidence)['issue'] == 'merged'
    assert client.post('/atlas/characters/' + item['id'], json=payload).status_code == 200
    assert len(Store(dataset).events()) == 1
    payload['issue'] = 'blank'
    assert client.post('/atlas/characters/' + item['id'], json=payload).status_code == 422


def test_round_issues_and_optional_reading_corrections_survive_retry_and_undo(dataset):
    import json
    client = TestClient(create_app(dataset))
    payload = round_payload(client, 4)
    payload['answers'][0].update(verdict='wrong', issue='reading', correction='い')
    payload['answers'][1].update(verdict='wrong', issue='merged', correction='シヨロ')
    payload['answers'][2].update(verdict='wrong', issue='blank')
    result = client.post('/atlas/rounds', json=payload)
    assert result.status_code == 200, result.text
    assert len(Store(dataset).events()) == 5
    current = client.get('/atlas/characters/' + payload['answers'][0]['id']).json()
    assert current['label'] == 'い' and current['state'] == 'checked'
    merged = client.get('/atlas/characters/' + payload['answers'][1]['id']).json()
    assert merged['label'] == 'あ' and merged['state'] == 'flagged'
    assert any(json.loads(e.evidence).get('suggested_reading') == 'シヨロ' for e in Store(dataset).events())
    assert all(r['current'] for r in client.get('/atlas/reviews').json()['reviews'])
    assert client.post('/atlas/rounds', json=payload).status_code == 200
    assert len(Store(dataset).events()) == 5
    reopened = TestClient(create_app(dataset))
    undo = reopened.post('/atlas/rounds/' + payload['id'] + '/undo', json={'client_id': payload['client_id']})
    assert undo.status_code == 200, undo.text
    assert reopened.get('/atlas?state=pending').json()['total'] == 16
    assert reopened.get('/atlas/characters/' + payload['answers'][0]['id']).json()['label'] == 'あ'
    assert all(not r['current'] for r in reopened.get('/atlas/reviews').json()['reviews'])
    assert reopened.post('/atlas/rounds/' + payload['id'] + '/undo', json={'client_id': payload['client_id']}).status_code == 200
    assert len(Store(dataset).events()) == 10


def test_corrected_round_undo_cannot_overwrite_an_intervening_edit(dataset):
    client = TestClient(create_app(dataset))
    payload = round_payload(client, 3)
    payload['answers'][0].update(verdict='wrong', issue='reading', correction='い')
    assert client.post('/atlas/rounds', json=payload).status_code == 200
    target = payload['answers'][0]['id']
    client.post('/reviews', json={'target_type':'unit', 'target_id':target, 'field':'note', 'new':'Later', 'client_id':'another'})
    count = len(Store(dataset).events())
    assert client.post('/atlas/rounds/' + payload['id'] + '/undo', json={'client_id':payload['client_id']}).status_code == 409
    assert len(Store(dataset).events()) == count
    assert client.get('/atlas/characters/' + target).json()['label'] == 'い'


@pytest.mark.parametrize('change', [{'issue':'blank'}, {'correction':'ア'}, {'issue':'merged', 'correction':'アカ'}])
def test_retry_cannot_change_saved_error_type_or_reading(dataset, change):
    client = TestClient(create_app(dataset))
    payload = round_payload(client, 1)
    payload['answers'][0].update(verdict='wrong', issue='reading', correction='い')
    assert client.post('/atlas/rounds', json=payload).status_code == 200
    payload['answers'][0].update(change)
    assert client.post('/atlas/rounds', json=payload).status_code == 422
    assert len(Store(dataset).events()) == 2


def test_suggestions_are_optional_revision_bound_and_do_not_write_reviews(dataset, monkeypatch, caplog):
    from glyph_atlas.review import suggestions
    calls = []
    def infer(path, stamp, box):
        calls.append(box)
        return {'status':'ready', 'candidates':[{'text':'シヨロ', 'engine':'fixture', 'score':.7}], 'engines':[]}
    monkeypatch.setattr(suggestions, 'infer', infer)
    client = TestClient(create_app(dataset))
    item = client.get('/atlas?limit=1').json()['items'][0]
    route = '/atlas/characters/' + item['id'] + '/suggestions'
    params = {'revision':item['revision'], 'image_sha256':item['image_sha256']}
    assert client.get(route, params=params).json()['candidates'][0]['text'] == 'シヨロ'
    assert len(calls) == 1 and Store(dataset).events() == []
    client.post('/reviews', json={'target_type':'unit', 'target_id':item['id'], 'field':'note', 'new':'Later', 'client_id':'another'})
    assert client.get(route, params=params).status_code == 409
    assert len(calls) == 1
    def broken(*args):
        raise RuntimeError('Internal model path must never be exposed')
    monkeypatch.setattr(suggestions, 'infer', broken)
    params['revision'] += 1
    result = client.get(route, params=params)
    assert result.status_code == 200 and result.json()['status'] == 'unavailable'
    assert 'Internal' not in result.text
    assert 'Internal' not in caplog.text
    assert 'OCR suggestions unavailable (RuntimeError)' in caplog.text
    client.get(route, params=params)
    assert caplog.text.count('OCR suggestions unavailable') == 1


#: A character from the supplementary plane (U+2A708, 𪜈), which is where a naive search breaks:
#: Python indexes it as one character and JavaScript indexes its UTF-16 form as two.
SUPPLEMENTARY = "\U0002A708"


@pytest.fixture
def searched(tmp_path: Path, monkeypatch):
    """A catalogue holding U+2A708 three times, three other characters once each, and no reading.

    Disposable: no imported corpus records U+2A708, so the character can only be exercised on a
    fixture. The units carry no `reading` of their own beyond the character, which is what makes the
    test distinguish a search on the written identity from one on the reading.
    """
    root = tmp_path / "searched"
    root.mkdir()
    cache = tmp_path / "cache"
    monkeypatch.setenv("GLYPH_ATLAS_CACHE", str(cache))
    im = Image.new("RGB", (300, 300), "white")
    ImageDraw.Draw(im).rectangle([10, 10, 200, 200], outline="black", width=4)
    image = tmp_path / "scan.jpg"
    im.save(image)
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    folder = cache / "images" / digest[:2]
    folder.mkdir(parents=True)
    (folder / (digest + ".jpg")).write_bytes(image.read_bytes())
    tables.write(root / "documents.parquet", [Document(id="d", title="Fixture")], Document)
    tables.write(root / "pages.parquet", [Page(id=PAGE, document_id="d", seq=0,
                 image="https://example.org/scan.jpg", width=300, height=300, sha256=digest)], Page)
    tables.write(root / "lines.parquet", [Line(id=LINE, page_id=PAGE, seq=0,
                 text_raw=SUPPLEMENTARY, text=SUPPLEMENTARY, box=Box(x=0, y=0, w=300, h=300))], Line)
    units = [
        Unit(id=f"{LINE}:s{i}", document_id="d", page_id=PAGE, line_id=LINE, seq=i,
             reading=SUPPLEMENTARY, unicode="U+2A708", script="han",
             box=Box(x=20 + i * 10, y=20, w=60, h=60))
        for i in range(3)
    ]
    units += [
        Unit(id=f"{LINE}:k{i}", document_id="d", page_id=PAGE, line_id=LINE, seq=10 + i,
             reading=char, unicode=code, script="hiragana",
             box=Box(x=200 + i * 10, y=20, w=60, h=60))
        # ね and ネ share a reading and a grapheme; が is one character written two ways in Unicode.
        for i, (char, code) in enumerate((("ね", "U+306D"), ("ネ", "U+30CD"), ("が", "U+304C")))
    ]
    units += [
        # The written identity and the reading disagree: this is the case that tells a search on the
        # written character apart from a search on the reading. A source may record the character it
        # printed while reading it another way, and ゐ read as い is exactly that.
        Unit(id=f"{LINE}:hira", document_id="d", page_id=PAGE, line_id=LINE, seq=20,
             reading="い", unicode="U+3090", script="hiragana", box=Box(x=20, y=200, w=60, h=60)),
        # A recorded written identity with no reading at all: reviewable, and findable by its
        # character, which is the only name it has.
        Unit(id=f"{LINE}:noreading", document_id="d", page_id=PAGE, line_id=LINE, seq=21,
             reading=None, text_source=None, unicode="U+2A708", script="han",
             box=Box(x=100, y=200, w=60, h=60)),
        # The 1,220 U+3000 units of the real corpus: recorded, not reviewable, and not findable.
        Unit(id=f"{LINE}:space", document_id="d", page_id=PAGE, line_id=LINE, seq=22,
             reading=None, text_source=None, unicode="U+3000", script="unknown",
             box=Box(x=180, y=200, w=60, h=60)),
    ]
    tables.write(root / "units.parquet", units, Unit)
    return root


def test_search_finds_every_occurrence_of_a_supplementary_character(searched: Path):
    """The example: three recorded occurrences of 𪜈, and the count says three."""
    client = TestClient(create_app(searched))
    body = client.get("/atlas", params={"q": SUPPLEMENTARY, "limit": 96}).json()
    assert body["total"] == 4, body
    assert body["matched"] == 4
    assert body["query"] == SUPPLEMENTARY
    assert {item["id"] for item in body["items"]} == (
        {f"{LINE}:s{i}" for i in range(3)} | {f"{LINE}:noreading"})
    assert all(item["label"] == SUPPLEMENTARY for item in body["items"])


def test_search_accepts_the_code_point_notation(searched: Path):
    """`U+2A708` and the character are the same query, in either case and padded or not."""
    client = TestClient(create_app(searched))
    for notation in ("U+2A708", "u+2a708", "U+02A708"):
        body = client.get("/atlas", params={"q": notation}).json()
        assert body["total"] == 4, f"{notation!r} found {body['total']}"


def test_a_hex_looking_word_is_text_and_not_a_code_point(searched: Path):
    """`A`, `F` and `1` are letters, and a bare `2a708` is a word.

    Reading any hex-looking string as a code point turns ordinary searches into control characters:
    `A` would become U+000A and the word `2a708` would become five characters nobody asked for. Only
    the explicit notation is a code point.
    """
    client = TestClient(create_app(searched))
    assert client.get("/atlas", params={"q": "A"}).json()["total"] == 0
    assert client.get("/atlas", params={"q": "2a708"}).json()["total"] == 0, "not U+2A708"
    assert client.get("/atlas", params={"q": "U+2A708"}).json()["total"] == 4


def test_search_is_exact_and_does_not_widen_to_a_shared_reading(searched: Path):
    """ね and ネ are one reading and one grapheme, and a search for one is not a search for both."""
    client = TestClient(create_app(searched))
    ne = client.get("/atlas", params={"q": "ね"}).json()
    assert ne["total"] == 1 and ne["items"][0]["label"] == "ね"
    katakana = client.get("/atlas", params={"q": "ネ"}).json()
    assert katakana["total"] == 1 and katakana["items"][0]["label"] == "ネ"


def test_search_matches_a_character_recorded_with_a_combining_mark(searched: Path):
    """が is U+304C in NFC and か + U+3099 in NFD; a query in either form finds the unit."""
    client = TestClient(create_app(searched))
    for form in ("が", "か\u3099"):
        body = client.get("/atlas", params={"q": form}).json()
        assert body["total"] == 1, f"{form!r} found {body['total']}"
        assert body["items"][0]["label"] == "が"


def test_search_reaches_every_match_through_pagination(searched: Path):
    """A match beyond the first page is reachable: the count is taken before the page is cut."""
    client = TestClient(create_app(searched))
    first = client.get("/atlas", params={"q": SUPPLEMENTARY, "limit": 2}).json()
    assert first["total"] == 4 and len(first["items"]) == 2
    second = client.get("/atlas", params={"q": SUPPLEMENTARY, "limit": 2, "offset": 2}).json()
    assert second["total"] == 4 and len(second["items"]) == 2
    assert not ({item["id"] for item in first["items"]}
                & {item["id"] for item in second["items"]}), "the pages do not overlap"
    assert ({item["id"] for item in first["items"]} | {item["id"] for item in second["items"]}
            == {f"{LINE}:s{i}" for i in range(3)} | {f"{LINE}:noreading"})


def test_search_that_matches_nothing_answers_zero_and_the_collection_still_loads(searched: Path):
    """A character the dataset has no occurrence of is an empty result, not an error."""
    client = TestClient(create_app(searched))
    body = client.get("/atlas", params={"q": "𰃂"}).json()
    assert body["total"] == 0 and body["items"] == [] and body["matched"] == 0
    # Clearing the box is the same request the collection always made.
    everything = client.get("/atlas").json()
    # Eight reviewable units: the fixture holds nine, and the recorded U+3000 is not a row.
    assert everything["total"] == 8 and everything["query"] is None
    assert everything["matched"] is None


def test_a_search_result_is_a_real_occurrence_a_reviewer_can_decide_on(searched: Path):
    """A matched crop carries what the round needs, so search did not change the review path."""
    client = TestClient(create_app(searched))
    item = client.get("/atlas", params={"q": SUPPLEMENTARY}).json()["items"][0]
    assert item["revision"] == 0
    assert item["image"].startswith("/atlas/media/")
    image = client.get(item["image"]).status_code
    assert image == 200, "the matched occurrence serves its crop"
    round_id = str(uuid4())
    answer = client.post("/atlas/rounds", json={
        "id": round_id, "client_id": "searcher", "label": SUPPLEMENTARY,
        "answers": [{"id": item["id"], "revision": item["revision"],
                     "image_sha256": item["image_sha256"], "verdict": "wrong", "issue": "crop"}]})
    assert answer.status_code == 200, answer.text
    undo = client.post(f"/atlas/rounds/{round_id}/undo", json={"client_id": "searcher"})
    assert undo.status_code == 200, undo.text


def test_search_matches_the_written_character_not_the_reading(searched: Path):
    """ゐ recorded with the reading い is found by ゐ and by U+3090, and not by い.

    This is the difference between a search on what the source printed and a search on how it is
    read: a reading is shared by several characters, so a search that answered from it would return
    the wrong records and would miss this one when the reader knows the character.
    """
    client = TestClient(create_app(searched))
    written = client.get("/atlas", params={"q": "ゐ"}).json()
    assert written["total"] == 1, written
    assert written["items"][0]["id"] == f"{LINE}:hira"
    assert written["items"][0]["reading"] == "い", "the record still says how it reads"
    assert client.get("/atlas", params={"q": "U+3090"}).json()["total"] == 1
    # The record is not found by its reading. Other records reading い are found by theirs, which is
    # a different question and is answered by the character they were written with.
    by_reading = client.get("/atlas", params={"q": "い"}).json()
    assert f"{LINE}:hira" not in {item["id"] for item in by_reading["items"]}


def test_an_occurrence_with_a_written_identity_and_no_reading_is_findable(searched: Path):
    """A record that names its character and carries no reading is still an occurrence.

    Eligibility used to ask for a reading, which hid exactly this record before a search could reach
    it. It is shown by its character, and it can be decided on like any other occurrence.
    """
    client = TestClient(create_app(searched))
    body = client.get("/atlas", params={"q": "U+2A708"}).json()
    record = next(item for item in body["items"] if item["id"] == f"{LINE}:noreading")
    assert record["label"] == SUPPLEMENTARY, "shown by the character it was written with"
    assert record["reading"] is None
    assert client.get(record["image"]).status_code == 200
    # A category the row belongs to has to be one a reviewer can open, which means the category
    # filter asks the same question the label answers.
    category = next(c for c in body["categories"] if c["label"] == SUPPLEMENTARY)
    assert category["total"] == 4, "the category holds every occurrence, this one among them"
    opened = client.get("/atlas", params={"reading": SUPPLEMENTARY}).json()
    assert f"{LINE}:noreading" in {item["id"] for item in opened["items"]}


def test_the_recorded_space_units_are_not_reviewable_rows(searched: Path):
    """U+3000 is recorded 1,220 times in the real corpus and has no glyph to review or to find."""
    client = TestClient(create_app(searched))
    assert client.get("/atlas", params={"q": "U+3000"}).json()["total"] == 0
    assert client.get("/atlas", params={"q": "　"}).json()["total"] == 0
    assert client.get("/atlas", params={"q": " "}).json()["total"] == 0
    ids = {item["id"] for item in client.get("/atlas").json()["items"]}
    assert f"{LINE}:space" not in ids, "it is kept as a record and left out of the collection"


@pytest.fixture
def scaled(tmp_path: Path, monkeypatch):
    """A page whose cached image is half its recorded size, which is where coordinates can drift.

    The box a record carries is in page pixels and the crop is cut from the cached image's pixels, so
    the two spaces differ by the scale. A test that only ever uses an image the page's own size
    cannot see a mistake in the conversion between them.
    """
    root = tmp_path / "scaled"
    root.mkdir()
    cache = tmp_path / "cache"
    monkeypatch.setenv("GLYPH_ATLAS_CACHE", str(cache))
    im = Image.new("RGB", (400, 300), (238, 232, 219))
    draw = ImageDraw.Draw(im)
    draw.rectangle([40, 40, 120, 140], fill=(40, 30, 20))
    image = tmp_path / "scan.jpg"
    im.save(image)
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    folder = cache / "images" / digest[:2]
    folder.mkdir(parents=True)
    (folder / (digest + ".jpg")).write_bytes(image.read_bytes())
    tables.write(root / "documents.parquet", [Document(id="d", title="Scaled")], Document)
    # The page is twice the cached image: scale 0.5 in both directions.
    tables.write(root / "pages.parquet", [Page(id=PAGE, document_id="d", seq=0,
                 image="https://example.org/scaled.jpg", width=800, height=600, sha256=digest)], Page)
    tables.write(root / "lines.parquet", [Line(id=LINE, page_id=PAGE, seq=0,
                 text_raw="あ", text="あ", box=Box(x=0, y=0, w=800, h=600))], Line)
    units = [Unit(id=f"{LINE}:u0", document_id="d", page_id=PAGE,
                  line_id=LINE, seq=0, reading="あ", unicode="U+3042", script="hiragana",
                  box=Box(x=100, y=100, w=200, h=200))]
    # A record whose image is a pre-cut crop file rather than a page: it has no surroundings, which
    # is the case the interface must not offer to adjust.
    crop_file = tmp_path / "crop.jpg"
    im.crop((40, 40, 120, 140)).save(crop_file)
    crop_digest = hashlib.sha256(crop_file.read_bytes()).hexdigest()
    crop_folder = cache / "images" / crop_digest[:2]
    crop_folder.mkdir(parents=True, exist_ok=True)
    (crop_folder / (crop_digest + ".jpg")).write_bytes(crop_file.read_bytes())
    units.append(Unit(id=f"{LINE}:u1", document_id="d", page_id=None, line_id=LINE, seq=1,
                      reading="い", unicode="U+3044", script="hiragana", crop_sha256=crop_digest))
    tables.write(root / "units.parquet", units, Unit)
    return root


def test_a_scaled_page_reports_the_scale_and_the_crop_in_source_pixels(scaled: Path):
    """A box in source pixels, and the scale that turns it back into a page box."""
    body = TestClient(create_app(scaled)).get("/atlas/characters/" + f"{LINE}:u0").json()
    assert body["source_scale"] == [0.5, 0.5], body["source_scale"]
    # The page box is (100,100,200,200); the cached image holds it at half.
    assert body["crop_box"] == {"x": 50, "y": 50, "w": 100, "h": 100}
    assert body["context"] is True
    box = body["context_box"]
    assert box["w"] > body["crop_box"]["w"] and box["h"] > body["crop_box"]["h"]
    # The context is the character's own surroundings, cut from the image that exists.
    assert box["x"] >= 0 and box["y"] >= 0 and box["x"] + box["w"] <= 400 and box["y"] + box["h"] <= 300


def test_a_drag_in_a_scaled_page_saves_a_page_box(scaled: Path):
    """The round trip: source pixels through the scale to a page box the store accepts.

    A drag at fraction f of the context view is at page pixel `context/scale + f * context/scale`.
    Saving that box and reading the record back has to give the same rectangle, which is what fails
    silently when the conversion is missing: the store would hold half-size coordinates.
    """
    client = TestClient(create_app(scaled))
    unit = f"{LINE}:u0"
    body = client.get("/atlas/characters/" + unit).json()
    scale = body["source_scale"]
    context = body["context_box"]
    page = {"x": context["x"] / scale[0], "y": context["y"] / scale[1],
            "w": context["w"] / scale[0], "h": context["h"] / scale[1]}
    # A drag from a quarter to three quarters of the view, in page pixels.
    start = (round(page["x"] + 0.25 * page["w"]), round(page["y"] + 0.25 * page["h"]))
    end = (round(page["x"] + 0.75 * page["w"]), round(page["y"] + 0.75 * page["h"]))
    dragged = {"x": min(start[0], end[0]), "y": min(start[1], end[1]),
               "w": abs(end[0] - start[0]), "h": abs(end[1] - start[1])}

    saved = client.post("/atlas/characters/" + unit, json={
        "id": str(uuid4()), "client_id": "dragger", "revision": body["revision"],
        "image_sha256": body["image_sha256"], "reading": "あ", "verdict": "wrong", "issue": "crop",
        "box": dragged})
    assert saved.status_code == 200, saved.text

    after = client.get("/atlas/characters/" + unit).json()
    assert after["box"] == dragged, "the record holds the page box that was dragged"
    # And the same rectangle is what the view reports back, in its own pixels.
    assert after["crop_box"] == {"x": dragged["x"] * scale[0], "y": dragged["y"] * scale[1],
                                 "w": dragged["w"] * scale[0], "h": dragged["h"] * scale[1]}


def test_a_drag_at_the_page_edge_saves_a_box_inside_the_page(scaled: Path):
    """The edge is where a scale error is largest, so the drag is clamped to the page it belongs to."""
    client = TestClient(create_app(scaled))
    unit = f"{LINE}:u0"
    body = client.get("/atlas/characters/" + unit).json()
    scale = body["source_scale"]
    context = body["context_box"]
    right = (context["x"] + context["w"]) / scale[0]
    bottom = (context["y"] + context["h"]) / scale[1]
    dragged = {"x": round(right) - 40, "y": round(bottom) - 40, "w": 40, "h": 40}
    saved = client.post("/atlas/characters/" + unit, json={
        "id": str(uuid4()), "client_id": "dragger", "revision": body["revision"],
        "image_sha256": body["image_sha256"], "reading": "あ", "verdict": "wrong", "issue": "crop",
        "box": dragged})
    assert saved.status_code == 200, saved.text
    after = client.get("/atlas/characters/" + unit).json()
    assert after["box"] == dragged
    assert after["crop_box"]["x"] >= 0 and after["crop_box"]["y"] >= 0
    # The outline stays inside the image it is drawn on, which is the source file's size.
    source = (after["crop_box"]["x"] + after["crop_box"]["w"], after["crop_box"]["y"] + after["crop_box"]["h"])
    assert source[0] <= 400 and source[1] <= 300, f"the crop runs past the cached image: {source}"


def test_a_crop_only_record_has_no_context_and_no_grab_to_adjust(scaled: Path):
    """A pre-cut crop file has no page behind it, so there is nothing around it to show or edit."""
    client = TestClient(create_app(scaled))
    body = client.get("/atlas/characters/" + f"{LINE}:u1").json()
    assert body["context"] is False and body["context_box"] is None
    assert body["source_scale"] == [1, 1], "a pre-cut crop has no page to be scaled against"
    # The endpoint that serves the context image still answers, since the interface only asks when
    # the record says there is one, and an edit that names no box is not an edit.
    assert client.get(body["context_image"]).status_code == 200


def test_an_exported_review_says_whether_it_still_stands(searched: Path):
    """Currentness is about the reading and the box a review recorded, not the row's written label.

    Three cases, and the one that matters here is a record written as one character and read as
    another: `evidence.correction.reading` holds the reading, so a review of that record is current
    while the reading is unchanged, even though the written identity differs from it.
    """
    client = TestClient(create_app(searched))

    def export_for(client_id):
        found = [r for r in client.get("/atlas/reviews").json()["reviews"]
                 if r["event"]["actor"] == client_id]
        return found[0] if found else None

    # A record whose written identity and reading differ.
    written = client.get("/atlas/characters/" + f"{LINE}:hira").json()
    assert written["label"] == "ゐ" and written["reading"] == "い", "the fixture's differing record"
    matched = client.post("/atlas/rounds", json={
        "id": str(uuid4()), "client_id": "matcher", "label": written["label"],
        "answers": [{"id": written["id"], "revision": written["revision"],
                     "image_sha256": written["image_sha256"], "verdict": "match"}]})
    assert matched.status_code == 200, matched.text
    assert export_for("matcher")["current"] is True, "an unchanged match is current"

    # A reading correction: current while the record still reads that way.
    unit = client.get("/atlas/characters/" + f"{LINE}:k0").json()
    corrected = client.post("/atlas/rounds", json={
        "id": str(uuid4()), "client_id": "corrector", "label": unit["label"],
        "answers": [{"id": unit["id"], "revision": unit["revision"],
                     "image_sha256": unit["image_sha256"], "verdict": "wrong", "issue": "reading",
                     "correction": "ヌ"}]})
    assert corrected.status_code == 200, corrected.text
    assert export_for("corrector")["current"] is True, "the correction is what the record now reads"

    # A later edit to the same unit leaves the earlier review behind it.
    later = client.post("/atlas/characters/" + unit["id"], json={
        "id": str(uuid4()), "client_id": "later", "revision": client.get(
            "/atlas/characters/" + unit["id"]).json()["revision"],
        "image_sha256": client.get("/atlas/characters/" + unit["id"]).json()["image_sha256"],
        "reading": "メ", "verdict": "match"})
    assert later.status_code == 200, later.text
    assert export_for("corrector")["current"] is False, "a newer decision supersedes the earlier one"


@pytest.fixture
def repaired(tmp_path: Path, monkeypatch):
    """A catalogue whose units carry the alignment-repair pass's own record.

    Four occurrences, which are the four cases the pass can leave behind: one it withheld from a
    quiz, one it mended and trusts, one it mended and a person has since settled, and one it never
    touched. `meta["alignment_repair"]` is the shape `glyph_atlas.repair` writes.
    """
    root = tmp_path / "repaired"
    root.mkdir()
    cache = tmp_path / "cache"
    monkeypatch.setenv("GLYPH_ATLAS_CACHE", str(cache))
    im = Image.new("RGB", (400, 300), (238, 232, 219))
    ImageDraw.Draw(im).rectangle([30, 30, 150, 200], fill=(40, 30, 20))
    image = tmp_path / "scan.jpg"
    im.save(image)
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    folder = cache / "images" / digest[:2]
    folder.mkdir(parents=True)
    (folder / (digest + ".jpg")).write_bytes(image.read_bytes())
    tables.write(root / "documents.parquet", [Document(id="d", title="Fixture")], Document)
    tables.write(root / "pages.parquet", [Page(id=PAGE, document_id="d", seq=0,
                 image="https://example.org/scan.jpg", width=400, height=300, sha256=digest)], Page)
    tables.write(root / "lines.parquet", [Line(id=LINE, page_id=PAGE, seq=0,
                 text_raw="あいうえ", text="あいうえ", box=Box(x=0, y=0, w=400, h=300))], Line)

    def unit(name: str, seq: int, reading: str, **meta: object) -> Unit:
        return Unit(id=f"{LINE}:{name}", document_id="d", page_id=PAGE, line_id=LINE, seq=seq,
                    reading=reading, unicode=f"U+{ord(reading):04X}", script="hiragana",
                    box=Box(x=30 + seq * 40, y=30, w=70, h=80),
                    meta={"alignment_repair": meta} if meta else {})

    withheld = unit("withheld", 0, "あ", status="uncertain", machine=True, verified=False,
                    reliable=False, withheld=True, quiz=False, reason="two hypotheses within margin")
    trusted = unit("trusted", 1, "い", status="repaired", machine=True, verified=False,
                   reliable=True, withheld=False, quiz=True, reason=None)
    settled = unit("settled", 2, "う", status="repaired", machine=True, verified=False,
                   reliable=True, withheld=False, quiz=True, reason=None)
    untouched = unit("untouched", 3, "え")
    tables.write(root / "units.parquet", [withheld, trusted, settled, untouched], Unit)
    store = Store(root)
    # A person has since settled one mended occurrence, which is what makes it a human decision even
    # though the pass touched it.
    store.record(ReviewRequest(target_type="unit", target_id=f"{LINE}:settled", field="review",
                               new="reviewed", client_id="reviewer"))
    return root


def test_browsing_keeps_a_withheld_crop_and_review_leaves_it_out(repaired: Path):
    """One endpoint, two questions: the collection has the occurrence, the queue does not.

    Hiding a withheld crop from browsing would make the collection misreport what it holds, and
    offering it to a quiz spends a reviewer on ink nobody could align. The same route answers both,
    so the answer says which question it answered.
    """
    client = TestClient(create_app(repaired))
    everything = {f"{LINE}:withheld", f"{LINE}:trusted", f"{LINE}:settled", f"{LINE}:untouched"}

    browse = client.get("/atlas", params={"limit": 96}).json()
    assert browse["purpose"] == "browse", "a catalogue is a catalogue unless it is asked to be a queue"
    assert {item["id"] for item in browse["items"]} == everything
    assert browse["available"] == 4, "the collection counts what it holds"
    withheld = next(item for item in browse["items"] if item["id"] == f"{LINE}:withheld")
    assert withheld["repair"]["withheld"] is True, "the gallery can mark the uncertainty"

    review = client.get("/atlas", params={"purpose": "review", "limit": 96}).json()
    assert review["purpose"] == "review"
    assert {item["id"] for item in review["items"]} == everything - {f"{LINE}:withheld"}
    assert review["available"] == 3, "the queue counts only what may be dealt in"
    assert all(item["repair"] is None or item["repair"]["quiz"] for item in review["items"])


def test_a_withheld_crop_keeps_its_own_page_and_its_repair_record(repaired: Path):
    """Excluded from the quiz is not hidden: the occurrence answers, and says why it is uncertain."""
    client = TestClient(create_app(repaired))
    detail = client.get("/atlas/characters/" + f"{LINE}:withheld")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["label"] == "あ" and body["state"] == "pending"
    assert body["repair"] == {"status": "uncertain", "machine": True, "verified": False,
                              "reliable": False, "withheld": True, "quiz": False,
                              "reason": "two hypotheses within margin"}
    assert client.get(body["image"]).status_code == 200, "the crop still serves"


def test_a_mended_crop_a_person_settled_stays_a_candidate_and_a_human_decision(repaired: Path):
    """A repair is not a review: the settled unit keeps its human state and stays in the quiz."""
    client = TestClient(create_app(repaired))
    body = client.get("/atlas/characters/" + f"{LINE}:settled").json()
    assert body["state"] == "checked", "the person's decision is what the state reports"
    assert body["repair"]["quiz"] is True and body["repair"]["withheld"] is False
    checked = client.get("/atlas", params={"state": "checked", "purpose": "review"}).json()
    assert {item["id"] for item in checked["items"]} == {f"{LINE}:settled"}
    # `counts` counts review states, so a machine repair is `pending`: it is not a person's decision
    # and must never be reported as one.
    assert checked["counts"]["checked"] == 1, "only the person's decision is a checked count"
    assert checked["counts"]["pending"] == 2, "the two mended units are still unreviewed"
    assert "machine" not in checked["counts"], "a repair is not a review state"


def test_an_untouched_occurrence_says_nothing_about_repair(repaired: Path):
    """`None` rather than an empty record, so a view can tell "never repaired" from "fine"."""
    client = TestClient(create_app(repaired))
    body = client.get("/atlas/characters/" + f"{LINE}:untouched").json()
    assert body["repair"] is None
    assert body["state"] == "pending"
    mended = client.get("/atlas/characters/" + f"{LINE}:trusted").json()
    assert mended["repair"]["withheld"] is False and mended["repair"]["reliable"] is True
    assert mended["repair"]["status"] == "repaired"
    assert mended["state"] == "pending", "a machine repair is not a person's confirmation"


def test_a_round_refuses_a_withheld_crop_and_still_takes_a_mended_one(repaired: Path):
    """The refusal says which crop and what to do instead, and a mended crop is business as usual."""
    client = TestClient(create_app(repaired))
    held = client.get("/atlas/characters/" + f"{LINE}:withheld").json()
    refused = client.post("/atlas/rounds", json={
        "id": str(uuid4()), "client_id": "reviewer", "label": "あ",
        "answers": [{"id": held["id"], "revision": held["revision"],
                     "image_sha256": held["image_sha256"], "verdict": "match"}]})
    # A refusal the client can act on: the service answers 422 for a request it will not take.
    assert refused.status_code == 422, refused.text
    assert "withheld" in refused.json()["detail"]

    mended = client.get("/atlas/characters/" + f"{LINE}:trusted").json()
    accepted = client.post("/atlas/rounds", json={
        "id": str(uuid4()), "client_id": "reviewer", "label": "い",
        "answers": [{"id": mended["id"], "revision": mended["revision"],
                     "image_sha256": mended["image_sha256"], "verdict": "match"}]})
    assert accepted.status_code == 200, accepted.text


def test_a_withheld_crop_is_unreviewed_in_both_listings(repaired: Path):
    """`state` counts review states, so a withheld crop is `pending` wherever it appears.

    The repair flag is not a review state and must not become one: the reviewer queue leaves the
    withheld crop out, and the counts of the queue do not silently reclassify it as something a
    person decided.
    """
    client = TestClient(create_app(repaired))
    browse = client.get("/atlas", params={"state": "pending", "limit": 96}).json()
    assert browse["counts"]["pending"] == 3, "the withheld and the two mended units are all unreviewed"
    assert browse["counts"]["checked"] == 1
    assert f"{LINE}:withheld" in {item["id"] for item in browse["items"]}

    review = client.get("/atlas", params={"state": "pending", "purpose": "review"}).json()
    assert review["counts"]["pending"] == 2, "the queue counts only what it may deal in"
    assert review["counts"]["checked"] == 1
    assert f"{LINE}:withheld" not in {item["id"] for item in review["items"]}


def test_a_written_character_correction_keeps_the_reading(searched: Path):
    """The two layers are separate: correcting what a crop *is* leaves what it *reads* alone.

    `correction` names a reading and `character` names the encoded identity, so an answer that
    carries the written character writes `unicode` and nothing else. A unit reading ね whose crop is
    really ネ becomes ネ that still reads ね.
    """
    client = TestClient(create_app(searched))
    unit = client.get("/atlas/characters/" + f"{LINE}:k0").json()
    assert unit["label"] == "ね" and unit["reading"] == "ね"
    answer = client.post("/atlas/rounds", json={
        "id": str(uuid4()), "client_id": "reviewer", "label": "ね",
        "answers": [{"id": unit["id"], "revision": unit["revision"],
                     "image_sha256": unit["image_sha256"], "verdict": "wrong",
                     "issue": "character", "character": "ネ"}]})
    assert answer.status_code == 200, answer.text
    after = client.get("/atlas/characters/" + unit["id"]).json()
    assert after["label"] == "ネ", "the written identity changed"
    assert after["reading"] == "ね", "the reading the source recorded is untouched"
    assert after["state"] == "checked", "the reviewer settled it"

    # The journal holds the identity event and the review, and nothing that rewrites a reading.
    fields = {row["field"] for row in answer.json()["results"]}
    assert fields == {"unicode", "review"}, f"a reading event was written: {fields}"


def test_the_round_records_the_identity_and_the_reading_snapshot(searched: Path):
    """The evidence says which identity was proposed and what the record read at the time."""
    client = TestClient(create_app(searched))
    unit = client.get("/atlas/characters/" + f"{LINE}:k0").json()
    answer = client.post("/atlas/rounds", json={
        "id": str(uuid4()), "client_id": "reviewer", "label": "ね",
        "answers": [{"id": unit["id"], "revision": unit["revision"],
                     "image_sha256": unit["image_sha256"], "verdict": "wrong",
                     "issue": "character", "character": "U+30CD"}]})
    assert answer.status_code == 200, answer.text
    event = next(r for r in answer.json()["results"] if r["field"] == "unicode")
    evidence = json.loads(event["review"]["evidence"])
    assert evidence["suggested_character"] == "U+30CD"
    assert evidence["correction"]["unicode"] == "U+30CD"
    assert evidence["correction"]["reading"] == "ね", "the reading is snapshotted, not replaced"
    assert event["review"]["new"] == "U+30CD"


def test_a_character_answer_is_idempotent_and_a_changed_one_is_refused(searched: Path):
    """A retry with the same identity returns the saved result; a different one is refused."""
    client = TestClient(create_app(searched))
    unit = client.get("/atlas/characters/" + f"{LINE}:k1").json()
    payload = {"id": str(uuid4()), "client_id": "reviewer", "label": "ネ",
               "answers": [{"id": unit["id"], "revision": unit["revision"],
                            "image_sha256": unit["image_sha256"], "verdict": "wrong",
                            "issue": "character", "character": "ね"}]}
    first = client.post("/atlas/rounds", json=payload)
    assert first.status_code == 200, first.text
    retry = client.post("/atlas/rounds", json=payload)
    assert retry.status_code == 200 and all(r["duplicate"] for r in retry.json()["results"])
    changed = client.post("/atlas/rounds", json={**payload, "answers": [
        {**payload["answers"][0], "character": "が"}]})
    assert changed.status_code == 422, changed.text
    assert client.get("/atlas/characters/" + unit["id"]).json()["label"] == "ね"


def test_undoing_a_character_round_restores_identity_and_state(searched: Path):
    """An undo is the journal's own compensating event, so it puts the identity back."""
    client = TestClient(create_app(searched))
    unit = client.get("/atlas/characters/" + f"{LINE}:k0").json()
    round_id = str(uuid4())
    saved = client.post("/atlas/rounds", json={
        "id": round_id, "client_id": "reviewer", "label": "ね",
        "answers": [{"id": unit["id"], "revision": unit["revision"],
                     "image_sha256": unit["image_sha256"], "verdict": "wrong",
                     "issue": "character", "character": "ネ"}]})
    assert saved.status_code == 200, saved.text
    assert client.get("/atlas/characters/" + unit["id"]).json()["label"] == "ネ"
    undone = client.post(f"/atlas/rounds/{round_id}/undo", json={"client_id": "reviewer"})
    assert undone.status_code == 200, undone.text
    after = client.get("/atlas/characters/" + unit["id"]).json()
    assert after["label"] == "ね", "the identity is back"
    assert after["state"] == "pending", "and it is unreviewed again"


def test_a_character_correction_is_current_then_stale_after_a_later_mutation(searched: Path):
    """The export compares the identity it recorded, so a later change makes it stale."""
    client = TestClient(create_app(searched))
    unit = client.get("/atlas/characters/" + f"{LINE}:k2").json()
    saved = client.post("/atlas/rounds", json={
        "id": str(uuid4()), "client_id": "reviewer", "label": "が",
        "answers": [{"id": unit["id"], "revision": unit["revision"],
                     "image_sha256": unit["image_sha256"], "verdict": "wrong",
                     "issue": "character", "character": "ざ"}]})
    assert saved.status_code == 200, saved.text
    mine = next(r for r in client.get("/atlas/reviews").json()["reviews"]
                if r["event"]["actor"] == "reviewer")
    assert mine["current"] is True, "the review reads the identity it recorded"

    # Somebody corrects the same occurrence again.
    later = client.get("/atlas/characters/" + unit["id"]).json()
    again = client.post("/atlas/rounds", json={
        "id": str(uuid4()), "client_id": "someone-else", "label": "ざ",
        "answers": [{"id": later["id"], "revision": later["revision"],
                     "image_sha256": later["image_sha256"], "verdict": "wrong",
                     "issue": "character", "character": "じ"}]})
    assert again.status_code == 200, again.text
    after = next(r for r in client.get("/atlas/reviews").json()["reviews"]
                 if r["event"]["actor"] == "reviewer")
    assert after["current"] is False, "a newer identity supersedes the earlier review"


def test_a_character_answer_refuses_what_is_not_one_written_character(searched: Path):
    """A written identity is one character; a multi-character string is not an identity at all."""
    client = TestClient(create_app(searched))

    def answer(occurrence="k0", label="ね", **overrides):
        # One occurrence per case: a successful write moves the revision, and a request refused for a
        # bad body would otherwise be masked by the stale-revision check that runs before it.
        fresh = client.get("/atlas/characters/" + f"{LINE}:{occurrence}").json()
        body = {"id": str(uuid4()), "client_id": "reviewer", "label": label,
                "answers": [{"id": fresh["id"], "revision": fresh["revision"],
                             "image_sha256": fresh["image_sha256"], "verdict": "wrong",
                             "issue": "character", "character": "ネ"}]}
        body["answers"][0].update(overrides)
        return client.post("/atlas/rounds", json=body)

    assert answer(character="トモ").status_code == 422, "two kana are not one identity"
    assert answer(character="ネ", issue="reading").status_code == 422, "an identity needs its issue"
    assert answer(character="ネ", verdict="match").status_code == 422, "a match claims no issue"
    # The reading route is unchanged: とも is a reading, and two kana are not an identity.
    assert answer(character=None, issue="reading", correction="とも").status_code == 422

    # A well-formed identity on one occurrence is taken, and a second round on that same occurrence
    # is a new decision against a revision that has moved.
    first = answer(occurrence="k1", label="ネ")
    assert first.status_code == 200, first.text
    assert answer(occurrence="k1", label="ネ", character="わ", revision=0).status_code == 409


@pytest.fixture
def damaged(tmp_path: Path, monkeypatch):
    """A catalogue whose crop cache holds a truncated file, which a cache hit alone cannot detect.

    Three occurrences: one backed only by the damaged pre-cut crop, one backed by that crop *and* a
    page with a box, and one untouched. The damaged file is a real image cut short, so a reader that
    trusts the file's existence alone will fail on it exactly as the browser did.
    """
    root = tmp_path / "damaged"
    root.mkdir()
    cache = tmp_path / "cache"
    monkeypatch.setenv("GLYPH_ATLAS_CACHE", str(cache))
    im = Image.new("RGB", (400, 300), (238, 232, 219))
    ImageDraw.Draw(im).rectangle([40, 40, 160, 200], fill=(40, 30, 20))
    good = tmp_path / "good.jpg"
    im.save(good)
    digest = hashlib.sha256(good.read_bytes()).hexdigest()
    folder = cache / "images" / digest[:2]
    folder.mkdir(parents=True)
    (folder / (digest + ".jpg")).write_bytes(good.read_bytes())

    # A second sound image with its own content, so it has its own file in the cache.
    other = Image.new("RGB", (120, 120), (30, 60, 200))
    ImageDraw.Draw(other).ellipse([20, 20, 100, 100], fill=(250, 250, 250))
    sound = tmp_path / "sound.png"
    other.save(sound, format="PNG")
    sound_digest = hashlib.sha256(sound.read_bytes()).hexdigest()
    sound_folder = cache / "images" / sound_digest[:2]
    sound_folder.mkdir(parents=True, exist_ok=True)
    (sound_folder / (sound_digest + ".png")).write_bytes(sound.read_bytes())

    # An image cut in half: the header survives, so `open()` succeeds, but it is not a whole image.
    broken_bytes = good.read_bytes()[: len(good.read_bytes()) // 2]
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(broken_bytes)
    broken_digest = hashlib.sha256(broken_bytes).hexdigest()
    broken_folder = cache / "images" / broken_digest[:2]
    broken_folder.mkdir(parents=True, exist_ok=True)
    (broken_folder / (broken_digest + ".jpg")).write_bytes(broken_bytes)

    tables.write(root / "documents.parquet", [Document(id="d", title="Fixture")], Document)
    # The page names the same content, so the cache holds a file the scale can be measured from and
    # a test can damage: without one the page has no source at all and there is nothing to truncate.
    tables.write(root / "pages.parquet", [Page(id=PAGE, document_id="d", seq=0,
                 image="https://example.org/scan.jpg", width=400, height=300, sha256=digest)], Page)
    tables.write(root / "lines.parquet", [Line(id=LINE, page_id=PAGE, seq=0,
                 text_raw="あいう", text="あいう", box=Box(x=0, y=0, w=400, h=300))], Line)
    tables.write(root / "units.parquet", [
        # Only the damaged crop: nothing valid is behind it.
        Unit(id=f"{LINE}:onlybroken", document_id="d", page_id=None, line_id=LINE, seq=0,
             reading="あ", unicode="U+3042", script="hiragana", crop_sha256=broken_digest),
        # The damaged crop, but the unit still has its page and box.
        Unit(id=f"{LINE}:fallback", document_id="d", page_id=PAGE, line_id=LINE, seq=1,
             reading="い", unicode="U+3044", script="hiragana",
             box=Box(x=40, y=40, w=80, h=90), crop_sha256=broken_digest),
        # A sound crop of its own, so the fallback is not simply everything failing: it must not
        # share bytes with the page, or damaging the page would damage it as well.
        Unit(id=f"{LINE}:good", document_id="d", page_id=None, line_id=LINE, seq=2,
             reading="う", unicode="U+3046", script="hiragana", crop_sha256=sound_digest),
    ], Unit)
    return root


def test_a_damaged_crop_is_not_eligible_and_a_page_behind_it_still_is(damaged: Path):
    """A cache hit is not proof the bytes are an image; a valid page behind it is."""
    client = TestClient(create_app(damaged))
    listed = client.get("/atlas", params={"limit": 96})
    assert listed.status_code == 200, listed.text
    ids = {item["id"] for item in listed.json()["items"]}
    assert f"{LINE}:onlybroken" not in ids, "the unit has no image that can be drawn"
    assert f"{LINE}:fallback" in ids, "its page is a valid source, so it is still reviewable"
    assert f"{LINE}:good" in ids, "a sound crop is unaffected"
    assert listed.json()["available"] == 2


def test_a_damaged_crop_does_not_break_the_inspector_or_the_crop_route(damaged: Path):
    """The detail view and the image route answer about the damaged unit instead of failing."""
    client = TestClient(create_app(damaged))
    detail = client.get("/atlas/characters/" + f"{LINE}:fallback")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["label"] == "い"
    image = client.get(body["image"])
    assert image.status_code == 200, image.text
    assert image.headers["content-type"] == "image/webp"

    # A unit whose only source is the damaged file answers 404 for its crop rather than 500, and the
    # catalogue around it is unaffected.
    orphan = client.get("/atlas/characters/" + f"{LINE}:onlybroken")
    assert orphan.status_code == 200, "the record itself is still readable"
    assert orphan.json()["image_sha256"] is None, "it reports that it has no source"
    assert client.get("/atlas", params={"limit": 96}).status_code == 200


def test_a_crop_in_another_format_is_not_rejected_for_lacking_a_jpeg_end(searched: Path):
    """The cache holds whatever a holder serves; only a JPEG has a JPEG's end marker.

    The completeness check exists because a truncated JPEG passes `verify()`. Applying its end-of-
    image marker to every format refuses every PNG, GIF and WebP in the cache, which is how a fix for
    one damaged file became a catalogue that would not draw a good one.
    """
    for kind, name in (("PNG", "page.png"), ("GIF", "page.gif"), ("WEBP", "page.webp")):
        pixel = Image.new("RGB", (64, 64), (200, 30, 30))
        target = searched / name
        pixel.save(target, format=kind)
        assert readable_image(str(target), target.stat().st_mtime_ns), f"{kind} was refused"
        assert image_size(str(target), target.stat().st_mtime_ns) == (64, 64), f"{kind} size"
    # And a truncated PNG is still refused, so the check has not simply become permissive.
    whole = (searched / "page.png").read_bytes()
    cut = searched / "cut.png"
    cut.write_bytes(whole[: len(whole) // 2])
    assert not readable_image(str(cut), cut.stat().st_mtime_ns), "a truncated PNG is refused"


def test_a_damaged_page_file_does_not_take_the_catalogue_down(damaged: Path):
    """A page whose cached file is unusable makes its units unavailable, not the whole listing.

    `page_image` reads the file's header to learn the scale a box is drawn at, so a damaged page used
    to raise out of the catalogue and fail every document because one file was bad.
    """
    root = damaged
    page_digest = Store(root).page(PAGE).sha256
    intact = client_get_page_bytes(root, page_digest)
    try:
        damage_page_file(root, page_digest, intact)
        client = TestClient(create_app(root))
        listed = client.get("/atlas", params={"limit": 96})
        assert listed.status_code == 200, listed.text
        ids = {item["id"] for item in listed.json()["items"]}
        assert f"{LINE}:fallback" not in ids, "its page cannot be drawn, so it has no source"
        assert listed.json()["available"] == 1, "the sound crop is still listed"
    finally:
        restore_page_file(root, page_digest, intact)


def client_get_page_bytes(root: Path, digest: str) -> bytes:
    """The bytes of a cached page, so a test can damage and then restore them."""
    folder = Path(os.environ["GLYPH_ATLAS_CACHE"]) / "images" / digest[:2]
    return next(folder.glob(f"{digest}.*")).read_bytes()


def damage_page_file(root: Path, digest: str, intact: bytes) -> None:
    """Truncate a cached page the way an interrupted download leaves it."""
    folder = Path(os.environ["GLYPH_ATLAS_CACHE"]) / "images" / digest[:2]
    target = next(folder.glob(f"{digest}.*"))
    target.write_bytes(intact[: len(intact) // 2])
    os.utime(target, None)


def restore_page_file(root: Path, digest: str, intact: bytes) -> None:
    folder = Path(os.environ["GLYPH_ATLAS_CACHE"]) / "images" / digest[:2]
    target = next(folder.glob(f"{digest}.*"))
    target.write_bytes(intact)
    os.utime(target, None)
    readable_image.cache_clear()
    image_size.cache_clear()


def test_a_layers_identity_correction_is_current_until_the_identity_moves(searched: Path):
    """The layers route records the literal character; the record holds a code point.

    `POST /layers/units` writes `layer_correction.character` as the character itself while `Unit.
    unicode` holds `U+XXXX`, so a comparison that does not canonicalise both sides reports a
    correction that was just saved as already stale. This is the path the browser found.
    """
    client = TestClient(create_app(searched))
    unit = client.get("/atlas/characters/" + f"{LINE}:k0").json()
    saved = client.post("/layers/units/" + unit["id"], json={
        "id": str(uuid4()), "client_id": "corrector", "revision": unit["revision"],
        "image_sha256": unit["image_sha256"], "character": "ヌ", "verdict": "wrong",
        "issue": "character", "note": ""})
    assert saved.status_code == 200, saved.text
    assert saved.json()["layers"]["code_point"] == "U+30CC", saved.json()["layers"]

    def mine():
        return next(r for r in client.get("/atlas/reviews").json()["reviews"]
                    if r["event"]["actor"] == "corrector")

    assert mine()["current"] is True, "the correction reads the identity it recorded"

    # A later change to the same occurrence's identity makes it stale.
    after = client.get("/atlas/characters/" + unit["id"]).json()
    assert after["label"] == "ヌ", "the written identity is what changed"
    later = client.post("/layers/units/" + unit["id"], json={
        "id": str(uuid4()), "client_id": "someone-else", "revision": after["revision"],
        "image_sha256": after["image_sha256"], "character": "ワ", "verdict": "wrong",
        "issue": "character", "note": ""})
    assert later.status_code == 200, later.text
    assert mine()["current"] is False, "another identity supersedes it"


def test_a_page_file_that_changes_under_a_cached_record_is_judged_again(repaired: Path):
    """A file replaced after the catalogue read it is not answered from the earlier answer.

    The page lookup is cached on the page and the index stamp, so a record that is still the same
    record would keep answering with the file's old size and validity. The check is keyed by the
    file's own modification time, so the whole API path sees the change: the same request answers
    differently once the bytes behind it are damaged, and again once they are restored.
    """
    client = TestClient(create_app(repaired))
    digest = Store(repaired).page(PAGE).sha256
    folder = Path(os.environ["GLYPH_ATLAS_CACHE"]) / "images" / digest[:2]
    target = next(folder.glob(f"{digest}.*"))
    intact = target.read_bytes()

    first = client.get("/atlas", params={"limit": 96}).json()
    assert first["available"] > 0, "the fixture starts readable"
    before = {item["id"] for item in first["items"]}

    try:
        target.write_bytes(intact[: len(intact) // 2])
        os.utime(target, None)
        damaged = client.get("/atlas", params={"limit": 96})
        assert damaged.status_code == 200, damaged.text
        after = {item["id"] for item in damaged.json()["items"]}
        assert after != before or damaged.json()["available"] != first["available"], (
            "the catalogue answered from the file it had already judged")
    finally:
        target.write_bytes(intact)
        os.utime(target, None)
        readable_image.cache_clear()
        image_size.cache_clear()
        restored = client.get("/atlas", params={"limit": 96}).json()
        assert {item["id"] for item in restored["items"]} == before, "the restore is seen too"




def test_the_export_is_the_same_payload_as_a_file(searched: Path):
    """The JSON route and the download route answer with one payload, and the file is attached.

    A browser saves what the server marks as an attachment, which is a download the reader can see
    happen; the two routes share one builder so they cannot describe a review differently.
    """
    client = TestClient(create_app(searched))
    item = client.get("/atlas", params={"q": SUPPLEMENTARY}).json()["items"][0]
    saved = client.post("/atlas/rounds", json={
        "id": str(uuid4()), "client_id": "exporter", "label": SUPPLEMENTARY,
        "answers": [{"id": item["id"], "revision": item["revision"],
                     "image_sha256": item["image_sha256"], "verdict": "match"}]})
    assert saved.status_code == 200, saved.text

    api = client.get("/atlas/reviews").json()
    assert api["kind"] == "atlas-character-reviews" and len(api["reviews"]) == 1

    file = client.get("/atlas/reviews.json")
    assert file.status_code == 200
    assert file.headers["content-type"].startswith("application/json")
    assert file.headers["content-disposition"] == 'attachment; filename="atlas-character-reviews.json"'
    assert json.loads(file.text) == api, "the download is the same document the API answers"


def test_context_suggestions_are_independent_fresh_and_revision_bound(dataset, monkeypatch):
    from glyph_atlas.review import suggestions
    units = [Unit(id=LINE + f":ctx{i}", line_id=LINE, page_id=PAGE, seq=i + 1,
                  text_source=text, reading=text, box=Box(x=20, y=20 + i * 100, w=65, h=80))
             for i, text in enumerate("あいう")]
    tables.write(dataset / "units.parquet", units, Unit)

    def forbidden(*args, **kwargs):
        raise AssertionError("context must not load or wait on OCR")
    monkeypatch.setattr(suggestions, "infer", forbidden)
    client = TestClient(create_app(dataset))
    item = client.get('/atlas/characters/' + units[0].id).json()
    route = '/atlas/characters/' + units[0].id + '/suggestions/context'
    params = {"revision": item['revision'], "image_sha256": item['image_sha256']}
    store = Store(dataset)
    events = list(store.events())
    response = client.get(route, params=params)
    assert response.status_code == 200
    assert "あい" in [c['text'] for c in response.json()['candidates']]
    assert "votes" not in response.json()
    assert list(store.events()) == events
    assert client.get(route, params={**params, "revision": 999}).status_code == 409
    assert client.get(route, params={**params, "image_sha256": "stale"}).status_code == 409

    # A line edit can change context without changing the crop revision. Do not cache it by crop.
    with store._connection() as conn:
        row = conn.execute("SELECT data FROM lines WHERE id = ?", (LINE,)).fetchone()
        data = json.loads(row[0]); data['text_raw'] = 'あえう'; data['text'] = 'あえう'
        conn.execute("UPDATE lines SET data = ? WHERE id = ?", (json.dumps(data), LINE))
    updated = client.get(route, params=params).json()
    assert "あえ" in [c['text'] for c in updated['candidates']]
    assert "あい" not in [c['text'] for c in updated['candidates']]


def test_a_round_that_corrects_the_character_carries_its_reading(searched: Path):
    """A crop read ね corrected to り reads り, and a retry of the same round is still one save."""
    client = TestClient(create_app(searched))
    unit = client.get("/atlas/characters/" + f"{LINE}:k0").json()
    payload = {"id": str(uuid4()), "client_id": "reviewer", "label": "ね",
               "answers": [{"id": unit["id"], "revision": unit["revision"], "image_sha256": unit["image_sha256"],
                            "verdict": "wrong", "issue": "character", "character": "り"}]}
    answer = client.post("/atlas/rounds", json=payload)
    assert answer.status_code == 200, answer.text
    assert {row["field"] for row in answer.json()["results"]} == {"unicode", "reading", "review"}
    after = client.get("/atlas/characters/" + unit["id"]).json()
    assert after["label"] == "り" and after["reading"] == "り"
    retry = client.post("/atlas/rounds", json=payload)
    assert retry.status_code == 200 and all(r["duplicate"] for r in retry.json()["results"])


def seen_round(client, shown, flagged=()):
    """A round that flags `flagged` and records every other shown crop as seen."""
    answers = [{"id": item['id'], "revision": item['revision'], "image_sha256": item['image_sha256'],
                "verdict": "wrong", "issue": "crop"} for item in shown if item['id'] in flagged]
    seen = [{"id": item['id'], "image_sha256": item['image_sha256']}
            for item in shown if item['id'] not in flagged]
    return {"id": str(uuid4()), "client_id": "seen-reviewer", "label": "あ", "answers": answers, "seen": seen}


def test_a_seen_crop_is_not_dealt_again_and_is_no_confirmation(dataset):
    client = TestClient(create_app(dataset))
    shown = client.get('/atlas?reading=あ&state=pending&limit=4').json()['items']
    payload = seen_round(client, shown, flagged={shown[0]['id']})
    assert client.post('/atlas/rounds', json=payload).status_code == 200
    counts = client.get('/atlas').json()['counts']
    assert counts == {"flagged": 1, "seen": 3, "pending": 12}
    pending = {i['id'] for i in client.get('/atlas?reading=あ&state=pending&limit=96').json()['items']}
    assert not pending & {item['id'] for item in shown}
    store = Store(dataset)
    seen = [e for e in store.events() if e.field == "seen"]
    assert len(seen) == 3 and all(json.loads(e.evidence)['kind'] == 'visual-quiz-seen' for e in seen)
    # Nothing about a seen crop is a decision: its review state is untouched and it is not exported.
    assert all(store.unit(e.target_id).review == ReviewState.MACHINE for e in seen)
    exported = {r['event']['target_id'] for r in client.get('/atlas/reviews').json()['reviews']}
    assert exported == {shown[0]['id']}


def test_a_flagged_crop_is_not_dealt_and_keeps_its_flag(dataset):
    client = TestClient(create_app(dataset))
    listing = '/atlas?reading=あ&state=pending&purpose=review&seed=3&limit=96'
    before = client.get(listing).json()['total']
    shown = client.get(listing).json()['items'][:4]
    flagged = shown[0]['id']
    assert client.post('/atlas/rounds', json=seen_round(client, shown, flagged={flagged})).status_code == 200
    after = client.get(listing).json()
    assert not {i['id'] for i in after['items']} & {i['id'] for i in shown} and after['total'] == before - 4
    assert client.get('/atlas/characters/' + flagged).json()['state'] == 'flagged'
    assert flagged in {i['id'] for i in client.get('/atlas?state=flagged&limit=96').json()['items']}


def test_undoing_a_round_that_flagged_a_seen_crop_leaves_it_seen(dataset):
    client = TestClient(create_app(dataset))
    shown = client.get('/atlas?reading=あ&state=pending&limit=4').json()['items']
    assert client.post('/atlas/rounds', json=seen_round(client, shown)).status_code == 200
    item = client.get('/atlas/characters/' + shown[0]['id']).json()
    flag = {"id": str(uuid4()), "client_id": "second", "label": "あ", "seen": [],
            "answers": [{"id": item['id'], "revision": item['revision'], "image_sha256": item['image_sha256'],
                         "verdict": "wrong", "issue": "crop"}]}
    assert client.post('/atlas/rounds', json=flag).status_code == 200
    assert client.post('/atlas/rounds/' + flag['id'] + '/undo', json={"client_id": "second"}).status_code == 200
    assert client.get('/atlas').json()['counts'] == {"seen": 4, "pending": 12}
    assert item['id'] not in {i['id'] for i in client.get('/atlas?reading=あ&state=pending&limit=96').json()['items']}


def skip_round(shown, reviewer, skipped):
    """A round in which `reviewer` skips the `skipped` crops and records nothing else."""
    return {"id": str(uuid4()), "client_id": reviewer, "label": "あ", "answers": [], "seen": [],
            "skipped": [{"id": item['id'], "image_sha256": item['image_sha256']}
                        for item in shown if item['id'] in skipped]}


def test_a_skipped_crop_rests_for_its_reviewer_and_comes_first_for_others(dataset):
    client = TestClient(create_app(dataset))
    shown = client.get('/atlas?reading=あ&state=pending&limit=4').json()['items']
    crop = shown[1]['id']
    assert client.post('/atlas/rounds', json=skip_round(shown, "alice", {crop})).status_code == 200
    # Nothing about the crop changed: it is still pending for everyone, and nothing is exported.
    assert client.get('/atlas/characters/' + crop).json()['state'] == 'pending'
    assert crop not in {r['event']['target_id'] for r in client.get('/atlas/reviews').json()['reviews']}
    mine = client.get('/atlas?reading=あ&state=pending&purpose=review&reviewer=alice&limit=96').json()
    assert crop not in {i['id'] for i in mine['items']}
    assert next(c for c in mine['categories'] if c['label'] == 'あ')['skipped'] == 1
    theirs = client.get('/atlas?reading=あ&state=pending&purpose=review&reviewer=bob&seed=3&limit=96').json()
    assert theirs['items'][0]['id'] == crop


def test_a_crop_two_reviewers_skip_is_hard_and_an_undo_takes_a_skip_back(dataset):
    client = TestClient(create_app(dataset))
    shown = client.get('/atlas?reading=あ&state=pending&limit=4').json()['items']
    crop = shown[0]['id']
    assert client.post('/atlas/rounds', json=skip_round(shown, "alice", {crop})).status_code == 200
    second = skip_round(shown, "bob", {crop})
    assert client.post('/atlas/rounds', json=second).status_code == 200
    assert client.get('/atlas?state=hard&limit=96').json()['items'][0]['id'] == crop
    # The Flagged view lists it with the flagged crops.
    assert crop in {i['id'] for i in client.get('/atlas?state=attention&limit=96').json()['items']}
    assert crop not in {i['id'] for i in client.get(
        '/atlas?reading=あ&state=pending&purpose=review&reviewer=carol&limit=96').json()['items']}
    assert client.get('/atlas').json()['counts'] == {"hard": 1, "pending": 15}
    assert client.post('/atlas/rounds/' + second['id'] + '/undo', json={"client_id": "bob"}).status_code == 200
    assert client.get('/atlas').json()['counts'] == {"pending": 16}


def test_undoing_a_second_skip_keeps_the_same_reviewers_first():
    at = datetime(2026, 9, 20, tzinfo=UTC)
    box = {"x": 1, "y": 2, "w": 3, "h": 4}

    def event(id, new, evidence, day):
        return SimpleNamespace(id=id, field="seen", new=new, evidence=evidence, actor="alice",
                               target_id="u", at=at + timedelta(days=day))

    first = event("e1", "skipped", json.dumps({"box": box}), 0)
    second = event("e2", "skipped", json.dumps({"box": box}), 4)
    undo = event("e3", None, "undo of e2", 5)
    assert atlas_module.skip_marks([first, second, undo]) == {"u": {"alice": [(at, box)]}}


def test_undoing_a_skip_leaves_an_earlier_seen_record(dataset):
    client = TestClient(create_app(dataset))
    shown = client.get('/atlas?reading=あ&state=pending&limit=2').json()['items']
    assert client.post('/atlas/rounds', json=seen_round(client, shown[:1])).status_code == 200
    skip = skip_round(shown, "bob", {shown[0]['id']})
    assert client.post('/atlas/rounds', json=skip).status_code == 200
    assert client.post('/atlas/rounds/' + skip['id'] + '/undo', json={"client_id": "bob"}).status_code == 200
    assert client.get('/atlas').json()['counts'] == {"seen": 1, "pending": 15}


def test_undoing_a_round_makes_its_seen_crops_pending_again(dataset):
    client = TestClient(create_app(dataset))
    shown = client.get('/atlas?reading=あ&state=pending&limit=4').json()['items']
    payload = seen_round(client, shown)
    assert payload['answers'] == []
    assert client.post('/atlas/rounds', json=payload).status_code == 200
    assert client.get('/atlas').json()['counts'] == {"seen": 4, "pending": 12}
    assert client.post('/atlas/rounds/' + payload['id'] + '/undo',
                       json={"client_id": payload['client_id']}).status_code == 200
    assert client.get('/atlas').json()['counts'] == {"pending": 16}


def test_a_crop_whose_box_moved_since_it_was_seen_is_pending_again(dataset):
    client = TestClient(create_app(dataset))
    shown = client.get('/atlas?reading=あ&state=pending&limit=1').json()['items']
    assert client.post('/atlas/rounds', json=seen_round(client, shown)).status_code == 200
    store = Store(dataset)
    unit = store.unit(shown[0]['id'])
    moved = unit.box.model_copy(update={"x": unit.box.x + 2}).model_dump()
    store.record(ReviewRequest(target_id=unit.id, field="box", new=moved, base_revision=store.revision(unit.id),
                               client_id="fixture", idempotency_key="move"))
    assert TestClient(create_app(dataset)).get('/atlas').json()['counts'] == {"pending": 16}


def test_a_round_must_carry_an_answer_or_a_seen_crop(dataset):
    client = TestClient(create_app(dataset))
    empty = {"id": str(uuid4()), "client_id": "seen-reviewer", "label": "あ", "answers": [], "seen": []}
    assert client.post('/atlas/rounds', json=empty).status_code == 422


def test_a_seen_crop_pins_nothing_for_the_repair_or_the_scan(dataset):
    from glyph_atlas import repair
    from glyph_atlas.review import preflight

    client = TestClient(create_app(dataset))
    shown = client.get('/atlas?reading=あ&state=pending&limit=2').json()['items']
    assert client.post('/atlas/rounds', json=seen_round(client, shown)).status_code == 200
    assert repair.human_state(dataset).units == {}
    assert preflight._human_targets(Store(dataset)) == set()


def test_a_seen_crop_keeps_its_revision_so_an_answer_on_it_is_not_stale(dataset):
    """Passing a crop changes nothing about it, so a second reviewer's flag on it still saves."""
    client = TestClient(create_app(dataset))
    shown = client.get('/atlas?reading=あ&state=pending&limit=2').json()['items']
    assert client.post('/atlas/rounds', json=seen_round(client, shown)).status_code == 200
    assert Store(dataset).revision(shown[0]['id']) == shown[0]['revision']
    flag = {"id": str(uuid4()), "client_id": "second-reviewer", "label": "あ",
            "answers": [{"id": shown[0]['id'], "revision": shown[0]['revision'],
                         "image_sha256": shown[0]['image_sha256'], "verdict": "wrong", "issue": "crop"}]}
    assert client.post('/atlas/rounds', json=flag).status_code == 200


def test_undoing_a_round_survives_a_later_edit_of_a_seen_crop(dataset):
    client = TestClient(create_app(dataset))
    shown = client.get('/atlas?reading=あ&state=pending&limit=2').json()['items']
    payload = seen_round(client, shown, flagged={shown[0]['id']})
    assert client.post('/atlas/rounds', json=payload).status_code == 200
    store = Store(dataset)
    unit = store.unit(shown[1]['id'])
    store.record(ReviewRequest(target_id=unit.id, field="note", new="later", base_revision=store.revision(unit.id),
                               client_id="fixture", idempotency_key="later-note"))
    assert client.post('/atlas/rounds/' + payload['id'] + '/undo',
                       json={"client_id": payload['client_id']}).status_code == 200


def test_a_seen_crop_retired_since_the_round_was_drawn_is_skipped(dataset):
    client = TestClient(create_app(dataset))
    shown = client.get('/atlas?reading=あ&state=pending&limit=2').json()['items']
    store = Store(dataset)
    store.record(ReviewRequest(target_id=shown[1]['id'], field="active", new=False,
                               base_revision=store.revision(shown[1]['id']), client_id="fixture",
                               idempotency_key="retire"))
    assert client.post('/atlas/rounds', json=seen_round(client, shown)).status_code == 200
    assert [e.target_id for e in Store(dataset).events() if e.field == "seen"] == [shown[0]['id']]


def test_a_rebuilt_store_agrees_with_the_live_one_about_seen_crops(dataset):
    client = TestClient(create_app(dataset))
    shown = client.get('/atlas?reading=あ&state=pending&limit=3').json()['items']
    assert client.post('/atlas/rounds', json=seen_round(client, shown)).status_code == 200
    before = {item['id']: Store(dataset).revision(item['id']) for item in shown}
    apply(dataset)
    assert replay(dataset)['repaired'] == 0
    assert {item['id']: Store(dataset).revision(item['id']) for item in shown} == before


def test_the_audit_does_not_read_a_seen_crop_as_reviewed(dataset):
    from glyph_atlas import audit

    client = TestClient(create_app(dataset))
    shown = client.get('/atlas?reading=あ&state=pending&limit=2').json()['items']
    assert client.post('/atlas/rounds', json=seen_round(client, shown)).status_code == 200
    apply(dataset)
    assert not set(audit._reviewed_units(dataset)) & {item['id'] for item in shown}


def test_a_seen_crop_may_name_the_image_the_round_showed(dataset):
    """The quiz sends the crop image it dealt; the local check compares the crop digest, so it is accepted."""
    client = TestClient(create_app(dataset))
    shown = client.get('/atlas?reading=あ&state=pending&limit=1').json()['items']
    payload = seen_round(client, shown)
    payload['seen'][0]['image'] = shown[0]['image']
    assert client.post('/atlas/rounds', json=payload).status_code == 200
    assert client.get('/atlas').json()['counts']['seen'] == 1


def test_a_crop_re_cut_after_the_round_was_dealt_is_not_seen(dataset):
    """A crop drawn from its page keeps the page's hash when it is re-cut; its image does not."""
    client = TestClient(create_app(dataset))
    shown = client.get('/atlas?reading=あ&state=pending&limit=1').json()['items']
    store = Store(dataset)
    unit = store.unit(shown[0]['id'])
    moved = unit.box.model_copy(update={"y": unit.box.y + 3}).model_dump()
    store.record(ReviewRequest(target_id=unit.id, field="box", new=moved, base_revision=store.revision(unit.id),
                               client_id="fixture", idempotency_key="re-cut"))
    payload = seen_round(client, shown)
    payload['seen'][0]['image'] = shown[0]['image']
    assert client.post('/atlas/rounds', json=payload).status_code == 200
    assert not [e for e in Store(dataset).events() if e.field == "seen"], "the new cut was never shown"


def test_history_lists_reviewer_decisions_newest_first_with_paging_and_filters(dataset):
    client = TestClient(create_app(dataset))
    pending = client.get('/atlas', params={"reading": "あ", "state": "pending", "limit": 7}).json()['items']
    alice_answers, seen_crop, untouched = pending[:4], pending[4], pending[5]
    alice = {"id": str(uuid4()), "client_id": "alice", "label": "あ",
             "answers": [{"id": item["id"], "revision": item["revision"], "image_sha256": item["image_sha256"],
                          "verdict": "match"} for item in alice_answers],
             "seen": [{"id": seen_crop["id"], "image_sha256": seen_crop["image_sha256"]}]}
    assert client.post('/atlas/rounds', json=alice).status_code == 200

    shi = client.get('/atlas', params={"reading": "シ", "state": "pending", "limit": 2}).json()['items']
    bob = {"id": str(uuid4()), "client_id": "bob", "label": "シ",
           "answers": [{"id": item["id"], "revision": item["revision"], "image_sha256": item["image_sha256"],
                        "verdict": "wrong", "issue": "character", "character": "ミ"} for item in shi]}
    assert client.post('/atlas/rounds', json=bob).status_code == 200

    # A model event never belongs to a reviewer's history.
    Store(dataset).record_batch([ReviewRequest(
        target_type="unit", target_id=untouched["id"], field="review", new="disputed",
        client_id="pipeline", idempotency_key="model:1", evidence="{}")], role="model")

    assert client.post('/atlas/rounds/' + alice['id'] + '/undo', json={"client_id": "alice"}).status_code == 200

    full = client.get('/atlas/history', params={"limit": 100}).json()
    kinds = [item["kind"] for item in full["items"]]
    assert kinds.count("undo") == 4 and kinds.count("review") == 6, kinds
    assert full["next"] is None
    # Newest first: the four undos (one per undone answer) lead; a seen mark's own undo is not
    # a `review` event and a model event is not a reviewer's, so neither appears here.
    assert kinds[:4] == ["undo"] * 4
    ats = [item["at"] for item in full["items"]]
    assert ats == sorted(ats, reverse=True)
    assert {item["actor"] for item in full["items"]} == {"alice", "bob"}

    undo_item = full["items"][0]
    assert undo_item["undoes"] and undo_item["label"] == "あ" and undo_item["verdict"] == "match"

    bob_review = next(i for i in full["items"] if i["kind"] == "review" and i["actor"] == "bob")
    assert bob_review["issue"] == "character" and bob_review["character"] == "ミ" and bob_review["label"] == "シ"

    only_bob = client.get('/atlas/history', params={"actor": "bob"}).json()
    assert {i["actor"] for i in only_bob["items"]} == {"bob"}
    assert len(only_bob["items"]) == 2

    only_a_label = client.get('/atlas/history', params={"label": "あ"}).json()
    assert len(only_a_label["items"]) == 8, only_a_label  # 4 originals + their 4 undos
    assert all(i["label"] == "あ" for i in only_a_label["items"])

    page1 = client.get('/atlas/history', params={"limit": 3}).json()
    assert len(page1["items"]) == 3 and page1["next"] is not None
    page2 = client.get('/atlas/history', params={"limit": 100, "before": page1["next"]}).json()
    assert ([i["id"] for i in page1["items"]] + [i["id"] for i in page2["items"]]
            == [i["id"] for i in full["items"]])


def test_a_crop_carries_its_suspect_mark(dataset):
    first, second = LINE + ":u0", LINE + ":u1"
    box = {"x": 20, "y": 20, "w": 65, "h": 100}
    (dataset / "quiz-suspects.json").write_text(json.dumps({"suspects": {
        first: {"p": 0.01, "reads_as": "お", "label": "あ", "box": box},
        # Made for another cut of the crop: it no longer holds.
        second: {"p": 0.01, "reads_as": "お", "label": "あ", "box": box}}}))
    items = TestClient(create_app(dataset)).get('/atlas', params={"reading": "あ", "limit": 96}).json()["items"]
    marks = {item["id"]: item["suspect"] for item in items}
    assert marks[first] == {"p": 0.01, "reads_as": "お"}
    assert {mark for identity, mark in marks.items() if identity != first} == {None}
