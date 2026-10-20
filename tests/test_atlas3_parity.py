import http.server
import importlib
import json
import sqlite3
import sys
import threading
import time
import urllib.parse
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "atlas3"))

PAGE = 2**24
CROPS = [(3 * PAGE + slot, 0, "ar:book-1:3-ocr0-" + str(slot), "あい"[slot % 2]) for slot in range(4)]
GLYPHS = [(5 * PAGE + slot, 1, f"codh:200:p5:C{slot:04d}", "うえ"[slot % 2]) for slot in range(3)]


def write(path: Path, schema: str, table: str, rows: list[tuple]) -> None:
    db = sqlite3.connect(path)
    db.execute(schema)
    db.executemany(f"INSERT INTO {table} VALUES({','.join('?' * len(rows[0]))})", rows)
    db.commit()
    db.close()


@pytest.fixture
def root(tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    letters = sorted({c for *_, c in CROPS + GLYPHS})
    db = sqlite3.connect(out / "atlas.sqlite")
    db.execute("CREATE TABLE crops(id INTEGER PRIMARY KEY, kind INTEGER, book INTEGER, char INTEGER)")
    db.execute("CREATE TABLE graphemes(id INTEGER PRIMARY KEY, text TEXT)")
    db.execute("CREATE TABLE books(id INTEGER PRIMARY KEY, key TEXT)")
    db.executemany("INSERT INTO graphemes VALUES(?,?)", list(enumerate(letters, 1)))
    db.executemany("INSERT INTO books VALUES(?,?)", [(1, "ar:book-1"), (2, "codh:200")])
    db.executemany("INSERT INTO crops VALUES(?,?,?,?)",
                   [(n, kind, kind + 1, letters.index(c) + 1) for n, kind, _, c in CROPS + GLYPHS])
    db.commit()
    db.close()
    write(out / "mirror.sqlite", "CREATE TABLE ids(text TEXT PRIMARY KEY, id INTEGER NOT NULL UNIQUE)", "ids",
          [(text, n) for n, _, text, _ in CROPS + GLYPHS])
    write(out / "atlas-runs.sqlite", "CREATE TABLE runs(size INTEGER, first INTEGER, second INTEGER)", "runs",
          [(2, CROPS[0][0], CROPS[1][0]), (2, CROPS[2][0], CROPS[3][0])])
    write(tmp_path / "source-corpus_documents.sqlite", "CREATE TABLE corpus_documents(document, n, title)",
          "corpus_documents", [("codh:200", 3, "T")])
    write(tmp_path / "source-pages.sqlite", "CREATE TABLE pages(id, document, data)", "pages",
          [("ar:book-1:3", "ar:book-1", "{}")])
    write(tmp_path / "source-document_characters.sqlite", "CREATE TABLE document_characters(document, ord, unit, data)",
          "document_characters", [("ainu:1", 0, "u", "{}"), ("ainu:1", 1, "v", "{}")])
    write(tmp_path / "source-media.sqlite", "CREATE TABLE media(key)", "media", [("a" * 64,), ("b" * 64,)])
    monkeypatch.setenv("ATLAS3_DIR", str(tmp_path))
    return tmp_path


def serve(answer):
    """A host answering every GET with `answer(path)` -> (status, headers, body); it logs request times."""
    times = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            times.append(time.monotonic())
            status, headers, body = answer(urllib.parse.unquote(self.path))
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}", times


def site(changes=None):
    """A Worker that names each crop as it was asked for it, so the new one answers public ids."""
    changes = changes or {}

    def answer(path):
        if path.startswith("/atlas/media/"):
            return 200, {"content-type": "image/webp"}, changes.get("image", b"RIFF....WEBP")
        body = {"path": path.split("?")[0], "ok": True, "stamp": "2026-10-20T00:00:00Z"}
        if path.startswith("/atlas/characters/"):
            crop = path.split("/")[3]
            body = {"id": crop, "label": "あ", "url": f"/atlas/characters/{crop}/line", "links": [crop]}
        body.update(changes.get(path.split("?")[0], {}))
        return 200, {"content-type": "application/json", "date": str(time.time()), "report-to": str(time.time())}, \
            json.dumps(body, ensure_ascii=False).encode()
    return answer


def run(monkeypatch, root, old, new, *flags):
    sys.modules.pop("parity", None)
    parity = importlib.import_module("parity")
    monkeypatch.setattr(sys, "argv", ["parity.py", "--old", old, "--new", new, "--rate", "500", "--per-route", "2",
                                      "--images", "2", *flags])
    with pytest.raises(SystemExit) as stopped:
        parity.main()
    report = next((root / "out").glob("parity-*.txt")).read_text()
    return stopped.value.code, report


def test_public_ids_are_crockford_base_32_with_no_leading_zero():
    parity = importlib.import_module("parity")
    assert parity.public_id(PAGE) == "g0000"
    assert parity.public_id(3 * PAGE + 31) == "1g000z"
    assert set(parity.DIGITS).isdisjoint("ilou")


def test_the_same_answers_under_text_and_public_ids_pass(root, monkeypatch):
    (old, a, _), (new, b, _) = serve(site()), serve(site())
    code, report = run(monkeypatch, root, a, b)
    old.shutdown(), new.shutdown()
    assert code == 0, report
    assert "0 differ" in report
    assert "/atlas/characters/{id}\t4\t0\t200x4" in report  # two crops and two corpus glyphs
    assert "/atlas/documents/{doc}/characters\t1\t0" in report


def test_a_changed_field_is_listed_by_route_and_path(root, monkeypatch):
    (old, a, _), (new, b, _) = serve(site()), serve(site({"/layers/summary": {"ok": False}}))
    code, report = run(monkeypatch, root, a, b)
    old.shutdown(), new.shutdown()
    assert code == 1
    assert "/layers/summary ok: 1 answers" in report
    assert "old true | new false" in report


def test_asking_both_workers_by_text_id_compares_the_same(root, monkeypatch):
    (old, a, _), (new, b, _) = serve(site()), serve(site())
    code, report = run(monkeypatch, root, a, b, "--new-ids", "text")
    assert "new ids text" in report
    old.shutdown(), new.shutdown()
    assert code == 0  # both answer what they were asked, and both normalise to the public id


def test_images_are_compared_by_hash(root, monkeypatch):
    (old, a, _), (new, b, _) = serve(site()), serve(site({"image": b"other"}))
    code, report = run(monkeypatch, root, a, b)
    old.shutdown(), new.shutdown()
    assert code == 1
    assert "/atlas/media/{key}.webp (bytes sha256): 2 answers" in report


def test_requests_keep_to_the_rate_across_both_hosts(root, monkeypatch):
    (old, a, first), (new, b, second) = serve(site()), serve(site())
    sys.modules.pop("parity", None)
    parity = importlib.import_module("parity")
    limiter = parity.Limiter(20)
    for _ in range(4):
        parity.fetch(a, "/health", limiter)
        parity.fetch(b, "/health", limiter)
    old.shutdown(), new.shutdown()
    times = sorted(first + second)
    assert len(times) == 8 and times[-1] - times[0] >= 0.3  # seven gaps of 1/20 s


def test_headers_the_edge_cache_rewrites_count_only_when_both_answers_hit_or_miss():
    parity = importlib.import_module("parity")
    case = parity.Case("/api/ranking", "/api/ranking", "/api/ranking")
    miss = parity.Answer(200, {"content-type": "application/json", "cache-control": "public, max-age=300"}, b"{}")
    hit = parity.Answer(200, {"content-type": "application/json", "cache-control": "public, max-age=14400",
                              "cf-cache-status": "HIT", "accept-ranges": "bytes"}, b"{}")
    ids = parity.Ids(sqlite3.connect(":memory:"))
    assert parity.compare(case, miss, hit, ids) == []
    assert parity.compare(case, miss, parity.Answer(200, {**hit.headers, "cf-cache-status": "MISS"}, b"{}"), ids) == [
        ("(header accept-ranges)", "(absent)", "bytes"), ("(header cache-control)", "public, max-age=300", "public, max-age=14400")]


def test_the_time_an_answer_was_made_may_differ(root, monkeypatch):
    (old, a, _), (new, b, _) = serve(site({"/api/ranking": {"at": "06:44:31.018Z"}})), \
        serve(site({"/api/ranking": {"at": "06:44:31.427Z"}}))
    code, report = run(monkeypatch, root, a, b)
    old.shutdown(), new.shutdown()
    assert code == 0, report
    assert "1 explained" in report
