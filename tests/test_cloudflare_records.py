"""A records-only publication points only at images already published, and seals in import order."""
import importlib
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

SCHEMA = Path("apps/cloudflare/migrations/0001_catalogue.sql").read_text()


@pytest.fixture
def scripts(monkeypatch):
    monkeypatch.syspath_prepend(str(Path("scripts").resolve()))


def test_only_images_an_earlier_publication_packed_count_as_published(scripts, tmp_path):
    export = importlib.import_module("export_cloudflare_corpus")
    old, new = "a" * 64, "b" * 64
    with sqlite3.connect(tmp_path / "corpus.sqlite") as db:
        db.executescript(SCHEMA)
        db.execute("INSERT INTO media VALUES(?, 'pack-10001.bin', 0, 1, 'image/webp')", (old,))
    (tmp_path / "keys.txt").write_text(old + "\n")
    for source in (tmp_path / "corpus.sqlite", tmp_path / "keys.txt"):
        published = export.Published(export.published_keys(source))
        published.add(old, None)
        with pytest.raises(ValueError, match="never published"):
            published.add(new, None)


def test_the_publisher_refuses_a_manifest_without_sql_before_uploading(tmp_path):
    (tmp_path / "publication.json").write_text(json.dumps({"objects": [{"key": "packs/x.bin", "file": "objects/x.bin"}]}))
    run = subprocess.run(["bash", "scripts/publish_cloudflare.sh", str(tmp_path)], capture_output=True, text=True,
                         check=False)
    assert run.returncode != 0 and "put " not in run.stdout and "published" not in run.stdout


def test_a_records_only_export_seals_into_packs_and_ordered_sql(scripts, tmp_path):
    seal = importlib.import_module("seal_cloudflare_records")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    records = [json.dumps({"id": i, "label": "は"}).encode() for i in ("codh:1", "codh:2")]
    (corpus / "corpus-0001.bin").write_bytes(b"".join(records))
    with sqlite3.connect(corpus / "corpus.sqlite") as db:
        db.executescript(SCHEMA)
        db.execute("INSERT INTO corpus_units VALUES('codh:2',NULL,'U+306F',NULL,2,'corpus-0001.bin',?,?)",
                   (len(records[0]), len(records[1])))
        db.execute("INSERT INTO corpus_units VALUES('codh:1','𛂥','U+306F',NULL,1,'corpus-0001.bin',0,?)",
                   (len(records[0]),))
    summary = seal.seal(corpus, tmp_path / "sealed")
    assert summary["corpus_units"] == 2 and summary["objects"] == 1
    publication = json.loads((tmp_path / "sealed" / "publication.json").read_text())
    key = publication["objects"][0]["key"]
    assert (tmp_path / "sealed" / publication["objects"][0]["file"]).read_bytes() == b"".join(records)
    lines = (tmp_path / "sealed" / publication["sql"][0]).read_text().splitlines()
    assert lines == [f"INSERT OR REPLACE INTO corpus_units VALUES('codh:1','𛂥','U+306F',NULL,1,'{key}',0,{len(records[0])});",
                     f"INSERT OR REPLACE INTO corpus_units VALUES('codh:2',NULL,'U+306F',NULL,2,'{key}',{len(records[0])},{len(records[1])});"]
    replayed = sqlite3.connect(":memory:")
    replayed.executescript(SCHEMA + "\n".join(lines))
    assert replayed.execute("SELECT character FROM corpus_units WHERE id='codh:1'").fetchone() == ("𛂥",)


def test_an_export_that_packed_images_is_refused(scripts, tmp_path):
    seal = importlib.import_module("seal_cloudflare_records")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    with sqlite3.connect(corpus / "corpus.sqlite") as db:
        db.executescript(SCHEMA)
        db.execute("INSERT INTO media VALUES(?, 'pack-10001.bin', 0, 1, 'image/webp')", ("c" * 64,))
    with pytest.raises(ValueError, match="packed images"):
        seal.seal(corpus, tmp_path / "sealed")
