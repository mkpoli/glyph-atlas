"""A records-only publication points only at images already published, and seals in import order."""
import importlib
import json
import re
import sqlite3
import subprocess
from pathlib import Path

import pytest

# Every migration, as a deployment applies them.
SCHEMA = "\n".join(p.read_text() for p in sorted(Path("apps/cloudflare/migrations").glob("*.sql")))


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
        db.execute("INSERT INTO corpus_units VALUES('codh:2',NULL,'U+306F',NULL,2,'corpus-0001.bin',?,?,'unknown',0)",
                   (len(records[0]), len(records[1])))
        db.execute("INSERT INTO corpus_units VALUES('codh:1','𛂥','U+306F',NULL,1,'corpus-0001.bin',0,?,'woodblock',0)",
                   (len(records[0]),))
    summary = seal.seal(corpus, tmp_path / "sealed")
    assert summary["corpus_units"] == 2 and summary["objects"] == 1
    publication = json.loads((tmp_path / "sealed" / "publication.json").read_text())
    key = publication["objects"][0]["key"]
    assert (tmp_path / "sealed" / publication["objects"][0]["file"]).read_bytes() == b"".join(records)
    sql = (tmp_path / "sealed" / publication["sql"][0]).read_text()
    lines = sql.splitlines()
    updates = "character=excluded.character,family=excluded.family,visual_group=excluded.visual_group,shuffle=excluded.shuffle," \
        "object=excluded.object,offset=excluded.offset,size=excluded.size,production=excluded.production"
    columns = "id,character,family,visual_group,shuffle,object,offset,size,production"
    assert lines[:2] == [
        (f"INSERT INTO corpus_units({columns}) VALUES('codh:1','𛂥','U+306F',NULL,1,'{key}',0,{len(records[0])},'woodblock') "
         f"ON CONFLICT(id) DO UPDATE SET {updates};"),
        (f"INSERT INTO corpus_units({columns}) VALUES('codh:2',NULL,'U+306F',NULL,2,'{key}',{len(records[0])},{len(records[1])},'unknown') "
         f"ON CONFLICT(id) DO UPDATE SET {updates};")]
    # A part applied before the last one leaves a named glyph named and the counts as they were.
    partial = sqlite3.connect(":memory:")
    partial.executescript(SCHEMA)
    partial.execute("INSERT INTO corpus_units VALUES('codh:1','𛂥','U+306F',NULL,1,'old',0,1,'woodblock',1)")
    partial.execute("INSERT INTO corpus_characters VALUES('𛂥','woodblock',1,1)")
    partial.executescript("\n".join(line for line in lines if line.startswith("INSERT INTO corpus_units")))
    assert partial.execute("SELECT object,named FROM corpus_units WHERE id='codh:1'").fetchone() == (key, 1)
    assert partial.execute("SELECT * FROM corpus_characters").fetchall() == [("𛂥", "woodblock", 1, 1)]
    replayed = sqlite3.connect(":memory:")
    replayed.executescript(SCHEMA)
    replayed.execute("INSERT INTO corpus_characters VALUES('gone','unknown',9,0)")
    # A glyph a review named before this publication stays named after its row is rewritten.
    replayed.execute("INSERT INTO corpus_units VALUES('codh:1','𛂥','U+306F',NULL,1,'old',0,1,'unknown',0)")
    replayed.execute("INSERT INTO units VALUES('codh:1','corpus','𛂥',NULL,NULL,NULL,'woodblock','kana','checked',1,1,1,1,'{}','{}','{}','{}')")
    replayed.executescript(sql)
    assert replayed.execute("SELECT character,named FROM corpus_units WHERE id='codh:1'").fetchone() == ("𛂥", 1)
    # The last part regenerates the per-character counts from the rows D1 then holds.
    assert replayed.execute("SELECT * FROM corpus_characters").fetchall() == [("𛂥", "woodblock", 1, 1)]


def test_an_export_that_packed_images_is_refused(scripts, tmp_path):
    seal = importlib.import_module("seal_cloudflare_records")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    with sqlite3.connect(corpus / "corpus.sqlite") as db:
        db.executescript(SCHEMA)
        db.execute("INSERT INTO media VALUES(?, 'pack-10001.bin', 0, 1, 'image/webp')", ("c" * 64,))
    with pytest.raises(ValueError, match="packed images"):
        seal.seal(corpus, tmp_path / "sealed")


def test_a_publication_file_gets_the_migrations_it_has_not_had(scripts, tmp_path):
    cloudflare_schema = importlib.import_module("cloudflare_schema")
    with sqlite3.connect(tmp_path / "catalogue.sqlite") as db:
        db.executescript(Path("apps/cloudflare/migrations/0001_catalogue.sql").read_text())
        cloudflare_schema.schema(db)
        cloudflare_schema.schema(db)
        assert {r[1] for r in db.execute("PRAGMA table_info(corpus_units)")} >= {"production", "named"}
        assert db.execute("PRAGMA user_version").fetchone()[0] == len(list(Path("apps/cloudflare/migrations").glob("*.sql")))


def test_a_file_built_with_an_earlier_draft_of_0006_is_refused(scripts, tmp_path):
    cloudflare_schema = importlib.import_module("cloudflare_schema")
    with sqlite3.connect(tmp_path / "corpus.sqlite") as db:
        db.executescript(Path("apps/cloudflare/migrations/0001_catalogue.sql").read_text())
        db.execute("ALTER TABLE corpus_units ADD COLUMN production TEXT NOT NULL DEFAULT 'unknown'")
        db.execute("PRAGMA user_version=6")
        with pytest.raises(ValueError, match="rebuild this export"):
            cloudflare_schema.schema(db)


def test_resuming_an_export_whose_corpus_rows_predate_their_material_is_refused(scripts, tmp_path):
    export = importlib.import_module("export_cloudflare_corpus")
    with sqlite3.connect(tmp_path / "corpus.sqlite") as db:
        db.executescript(Path("apps/cloudflare/migrations/0001_catalogue.sql").read_text())
        db.execute("INSERT INTO corpus_units VALUES('codh-omt:1','と','U+3068',NULL,1,'corpus-0001.bin',0,1)")
    with pytest.raises(ValueError, match="predate their material"):
        export.export(tmp_path, resume=True, published=set())
    with sqlite3.connect(tmp_path / "corpus.sqlite") as db:
        assert "production" not in {r[1] for r in db.execute("PRAGMA table_info(corpus_units)")}


def test_the_migration_and_the_publication_scripts_agree_on_a_label_category(scripts):
    cloudflare_schema = importlib.import_module("cloudflare_schema")
    migration = Path("apps/cloudflare/migrations/0006_corpus_rounds.sql").read_text()
    case = re.search(r"CASE\n[\s\S]*?END", migration).group(0)
    db = sqlite3.connect(":memory:")
    rows = db.execute(f"WITH RECURSIVE n(c) AS (SELECT 0 UNION ALL SELECT c+1 FROM n WHERE c<1114111) "
                      f"SELECT c,{case} FROM n WHERE {case}!='other'").fetchall()
    expected = [(c, cloudflare_schema.category_of(chr(c))) for c in range(0x110000)
                if not 0xD800 <= c <= 0xDFFF and cloudflare_schema.category_of(chr(c)) in ("kana", "kanji")]
    assert rows == expected
    assert [cloudflare_schema.category_of(v) for v in ("ア", "𛀁", "仮", "々", "〆", "A", "")] == \
        ["kana", "kana", "kanji", "kanji", "other", "other", "other"]


def test_the_hangul_migration_and_the_publication_scripts_agree_on_a_hangul_label(scripts):
    """0008 names the Hangul ranges the publication scripts and the Worker give `hangul`."""
    cloudflare_schema = importlib.import_module("cloudflare_schema")
    migration = Path("apps/cloudflare/migrations/0008_hangul_category.sql").read_text()
    case = re.search(r"CASE\n[\s\S]*?END", migration).group(0)
    db = sqlite3.connect(":memory:")
    rows = db.execute(f"WITH RECURSIVE n(c) AS (SELECT 0 UNION ALL SELECT c+1 FROM n WHERE c<1114111) "
                      f"SELECT c FROM n WHERE {case}='hangul'").fetchall()
    assert [c for (c,) in rows] == [c for c in range(0x110000) if not 0xD800 <= c <= 0xDFFF
                                    and cloudflare_schema.category_of(chr(c)) == "hangul"]
    assert [cloudflare_schema.category_of(v) for v in ("ㅿ", "ᄫ", "한", "㉠")] == ["hangul"] * 4


def test_the_hangul_migration_moves_a_row_published_as_other(scripts):
    cloudflare_schema = importlib.import_module("cloudflare_schema")
    db = sqlite3.connect(":memory:")
    cloudflare_schema.schema(db)
    for unit_id, label, category in (("jamo", "ㅿ", "other"), ("kana", "あ", "kana"), ("latin", "A", "other")):
        db.execute("INSERT INTO units VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (unit_id, "corpus", label, None, None, None, "unknown", category, "pending", 0, 1, 1, 0,
                    json.dumps({"label": label, "category": category}), "{}", "{}", "{}"))
    db.executescript(Path("apps/cloudflare/migrations/0008_hangul_category.sql").read_text())
    assert dict(db.execute("SELECT id,category FROM units")) == {"jamo": "hangul", "kana": "kana", "latin": "other"}
    assert json.loads(db.execute("SELECT data FROM units WHERE id='jamo'").fetchone()[0])["category"] == "hangul"
