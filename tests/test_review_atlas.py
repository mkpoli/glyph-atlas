"""Character review uses real crops, durable decisions and atomic rounds."""
from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from kuzushiji_atlas import tables
from kuzushiji_atlas.review.server import create_app
from kuzushiji_atlas.review.store import Store, apply, replay
from kuzushiji_atlas.schema import Box, Document, Line, Page, ReviewState, Unit

PAGE = "hk:entry:with:separators:9"
LINE = PAGE + ":L3"


@pytest.fixture
def dataset(tmp_path: Path, monkeypatch):
    root = tmp_path / "dataset"
    root.mkdir()
    cache = tmp_path / "cache"
    monkeypatch.setenv("KUZUSHIJI_ATLAS_CACHE", str(cache))
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


def test_real_character_image_and_context_do_not_guess_page_ids(dataset):
    client = TestClient(create_app(dataset))
    item = client.get('/atlas').json()['items'][0]
    response = client.get(item['image'])
    assert response.status_code == 200 and response.headers['content-type'] == 'image/jpeg'
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
    assert client.post('/atlas/rounds', json=payload).status_code == 422
    payload['label'] = 'あ'
    payload['answers'][1] = payload['answers'][0]
    assert client.post('/atlas/rounds', json=payload).status_code == 422
    assert Store(dataset).events() == []


def test_reading_and_crop_edit_is_atomic_and_invalidates_old_image(dataset):
    client = TestClient(create_app(dataset))
    item = client.get('/atlas?reading=あ').json()['items'][0]
    payload = {"id": str(uuid4()), "client_id": "editor", "reading": "い", "revision": item['revision'], "image_sha256": item['image_sha256'],
               "verdict": "match", "box": {**item['box'], "w": item['box']['w'] - 5}}
    result = client.post('/atlas/characters/' + item['id'], json=payload)
    assert result.status_code == 200, result.text
    detail = client.get('/atlas/characters/' + item['id']).json()
    assert detail['label'] == 'い' and detail['state'] == 'checked' and detail['revision'] == 3
    assert detail['box']['w'] == item['box']['w'] - 5
    assert client.get(item['image']).status_code == 409
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
    from kuzushiji_atlas import images

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


def test_changed_url_keyed_image_cannot_be_saved_as_the_image_seen(dataset, tmp_path):
    from kuzushiji_atlas import images

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
    assert client.get(old_image).status_code == 409
    response = client.post('/atlas/rounds', json=payload)
    assert response.status_code == 409
    assert Store(dataset).events() == []
