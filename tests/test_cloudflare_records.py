"""A records-only publication points only at images already published, and seals in import order."""
import importlib
import json
import re
import sqlite3
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from glyph_atlas import drawn_forms

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
        db.execute("INSERT INTO corpus_units(id,character,family,visual_group,shuffle,object,offset,size,production,named) VALUES('codh:2',NULL,'U+306F',NULL,2,'corpus-0001.bin',?,?,'unknown',0)",
                   (len(records[0]), len(records[1])))
        db.execute("INSERT INTO corpus_units(id,character,family,visual_group,shuffle,object,offset,size,production,named,style) VALUES('codh:1','𛂥','U+306F',NULL,1,'corpus-0001.bin',0,?,'printed/woodblock',0,'cursive')",
                   (len(records[0]),))
    summary = seal.seal(corpus, tmp_path / "sealed")
    assert summary["corpus_units"] == 2 and summary["objects"] == 1
    publication = json.loads((tmp_path / "sealed" / "publication.json").read_text())
    key = publication["objects"][0]["key"]
    assert (tmp_path / "sealed" / publication["objects"][0]["file"]).read_bytes() == b"".join(records)
    sql = (tmp_path / "sealed" / publication["sql"][0]).read_text()
    lines = sql.splitlines()
    updates = "character=excluded.character,family=excluded.family,visual_group=excluded.visual_group,shuffle=excluded.shuffle," \
        "object=excluded.object,offset=excluded.offset,size=excluded.size,production=excluded.production,style=excluded.style," \
        "document=coalesce(excluded.document,corpus_units.document)"
    columns = "id,character,family,visual_group,shuffle,object,offset,size,production,style,document"
    # Each upsert then names the rows it leaves unwritten (`corpus_upsert`).
    assert [line.partition(" WHERE NOT ")[0] for line in lines[:2]] == [
        (f"INSERT INTO corpus_units({columns}) VALUES('codh:1','𛂥','U+306F',NULL,1,'{key}',0,{len(records[0])},'printed/woodblock','cursive',NULL) "
         f"ON CONFLICT(id) DO UPDATE SET {updates}"),
        (f"INSERT INTO corpus_units({columns}) VALUES('codh:2',NULL,'U+306F',NULL,2,'{key}',{len(records[0])},{len(records[1])},'unknown','unassessed',NULL) "
         f"ON CONFLICT(id) DO UPDATE SET {updates}")]
    # A part applied before the last one leaves a named glyph named and the counts as they were.
    partial = sqlite3.connect(":memory:")
    partial.executescript(SCHEMA)
    partial.execute("INSERT INTO corpus_units(id,character,family,visual_group,shuffle,object,offset,size,production,named) VALUES('codh:1','𛂥','U+306F',NULL,1,'old',0,1,'printed/woodblock',1)")
    partial.execute("INSERT INTO corpus_characters VALUES('𛂥','printed/woodblock',1,1)")
    partial.executescript("\n".join(line for line in lines if line.startswith("INSERT INTO corpus_units")))
    assert partial.execute("SELECT object,named FROM corpus_units WHERE id='codh:1'").fetchone() == (key, 1)
    assert partial.execute("SELECT * FROM corpus_characters").fetchall() == [("𛂥", "printed/woodblock", 1, 1)]
    replayed = sqlite3.connect(":memory:")
    replayed.executescript(SCHEMA)
    replayed.execute("INSERT INTO corpus_characters VALUES('gone','unknown',9,0)")
    # A glyph a review named before this publication stays named after its row is rewritten.
    replayed.execute("INSERT INTO corpus_units(id,character,family,visual_group,shuffle,object,offset,size,production,named) VALUES('codh:1','𛂥','U+306F',NULL,1,'old',0,1,'unknown',0)")
    replayed.execute("INSERT INTO units(id,origin,character,family,visual_group,production,category,state,revision,quiz,priority,shuffle,data,snapshot,context,visual,document) VALUES('codh:1','corpus','𛂥',NULL,NULL,'printed/woodblock','kana','checked',1,1,1,1,'{}','{}','{}','{}',NULL)")
    replayed.executescript(sql)
    assert replayed.execute("SELECT character,named FROM corpus_units WHERE id='codh:1'").fetchone() == ("𛂥", 1)
    # The last part regenerates the per-character counts from the rows D1 then holds.
    assert replayed.execute("SELECT * FROM corpus_characters").fetchall() == [("𛂥", "printed/woodblock", 1, 1)]


def test_an_export_that_packed_images_seals_its_media_first_and_insert_only(scripts, tmp_path):
    seal = importlib.import_module("seal_cloudflare_records")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    crop, context = b"crop-webp", b"context-webp"
    (corpus / "pack-10001.bin").write_bytes(crop + context)
    record = json.dumps({"id": "gl:1", "label": "天"}).encode()
    (corpus / "corpus-0001.bin").write_bytes(record)
    new, old = "a" * 64, "b" * 64
    with sqlite3.connect(corpus / "corpus.sqlite") as db:
        db.executescript(SCHEMA)
        db.execute("INSERT INTO media VALUES(?, 'pack-10001.bin', 0, ?, 'image/webp')", (new, len(crop)))
        db.execute("INSERT INTO media VALUES(?, 'pack-10001.bin', ?, ?, 'image/webp')", (old, len(crop), len(context)))
        db.execute("INSERT INTO corpus_units(id,character,family,visual_group,shuffle,object,offset,size,production,named) VALUES('gl:1','天','U+5929',NULL,1,'corpus-0001.bin',0,?,'printed/woodblock',0)",
                   (len(record),))
    summary = seal.seal(corpus, tmp_path / "sealed")
    assert summary["media"] == 2 and summary["objects"] == 2
    publication = json.loads((tmp_path / "sealed" / "publication.json").read_text())
    pack = next(o["key"] for o in publication["objects"]
                if (tmp_path / "sealed" / o["file"]).read_bytes() == crop + context)
    sql = "".join((tmp_path / "sealed" / part).read_text() for part in publication["sql"])
    assert sql.index("INSERT OR IGNORE INTO media") < sql.index("INSERT INTO corpus_units")
    site = sqlite3.connect(":memory:")
    site.executescript(SCHEMA)
    # A key D1 already holds keeps its row: the key hashes the crop's render specification, so both show one crop.
    site.execute("INSERT INTO media VALUES(?, 'packs/earlier.bin', 7, ?, 'image/webp')", (old, len(context)))
    site.executescript(sql)
    assert site.execute("SELECT object, offset FROM media WHERE key=?", (new,)).fetchone() == (pack, 0)
    assert site.execute("SELECT object, offset FROM media WHERE key=?", (old,)).fetchone() == ("packs/earlier.bin", 7)
    assert site.execute("SELECT count(*) FROM corpus_units").fetchone() == (1,)


def test_media_rows_naming_a_pack_the_export_lacks_are_refused(scripts, tmp_path):
    seal = importlib.import_module("seal_cloudflare_records")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    with sqlite3.connect(corpus / "corpus.sqlite") as db:
        db.executescript(SCHEMA)
        db.execute("INSERT INTO media VALUES(?, 'pack-10001.bin', 0, 1, 'image/webp')", ("c" * 64,))
    with pytest.raises(ValueError, match="not in the export"):
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


def test_resuming_an_export_made_with_the_site_databases_migrations_is_refused(scripts, tmp_path):
    export = importlib.import_module("export_cloudflare_corpus")
    with sqlite3.connect(tmp_path / "corpus.sqlite") as db:
        for migration in sorted(Path("apps/cloudflare/migrations").glob("*.sql")):
            db.executescript(migration.read_text())
    with pytest.raises(ValueError, match="export it again"):
        export.export(tmp_path, resume=True, published=set())


def test_an_export_reads_only_the_corpora_it_names(scripts, tmp_path, monkeypatch):
    export = importlib.import_module("export_cloudflare_corpus")
    names = sorted(set(export.UNIT_CORPORA) - export.PUBLISHED_LOCALLY)[:2]
    monkeypatch.setattr(export.sources, "discover",
                        lambda root: [SimpleNamespace(name=name) for name in [*names, "not-a-unit-corpus"]])
    assert [c.name for c in export.unit_corpora()] == names
    assert [c.name for c in export.unit_corpora([names[1]])] == [names[1]]
    with pytest.raises(ValueError, match="no unit corpus named nowhere"):
        export.export(tmp_path / "out", corpora=["nowhere"], published=set())
    assert not (tmp_path / "out").exists()


def test_the_migration_and_the_publication_scripts_agree_on_a_label_category(scripts):
    cloudflare_schema = importlib.import_module("cloudflare_schema")
    migration = Path("apps/cloudflare/migrations/0006_corpus_rounds.sql").read_text()
    case = re.search(r"CASE\n[\s\S]*?END", migration).group(0)
    db = sqlite3.connect(":memory:")
    rows = db.execute(f"WITH RECURSIVE n(c) AS (SELECT 0 UNION ALL SELECT c+1 FROM n WHERE c<1114111) "
                      f"SELECT c,{case} FROM n WHERE {case}!='other'").fetchall()
    # A drawn form takes its grapheme's category from the site's table, which the migration's ranges
    # predate (tests/test_drawn_forms.py holds that case).
    expected = [(c, cloudflare_schema.category_of(chr(c))) for c in range(0x110000)
                if not 0xD800 <= c <= 0xDFFF and not drawn_forms.drawn(chr(c))
                and cloudflare_schema.category_of(chr(c)) in ("kana", "kanji")]
    assert rows == expected
    assert [cloudflare_schema.category_of(v) for v in ("ア", "𛀁", "仮", "々", "〆", "𗀀", "\U00018B00", "\U0003D000", "\U0003FC3F",
                                                           "\U0003FC40", "A", "")] == \
        ["kana", "kana", "kanji", "kanji", "other", "tangut", "other", "seal", "seal", "other", "other", "other"]


def test_a_well_formed_description_sequence_is_kanji(scripts):
    cloudflare_schema = importlib.import_module("cloudflare_schema")
    assert [cloudflare_schema.category_of(v) for v in ("⿰亻哥", "⿰亻⿱亠女", "〾木", "⿰", "⿰亻", "⿲木木")] == \
        ["kanji", "kanji", "kanji", "other", "other", "other"]


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
        db.execute("INSERT INTO units(id,origin,character,family,visual_group,production,category,state,revision,quiz,priority,shuffle,data,snapshot,context,visual,document) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (unit_id, "corpus", label, None, None, "unknown", category, "pending", 0, 1, 1, 0,
                    json.dumps({"label": label, "category": category}), "{}", "{}", "{}", None))
    db.executescript(Path("apps/cloudflare/migrations/0008_hangul_category.sql").read_text())
    assert dict(db.execute("SELECT id,category FROM units")) == {"jamo": "hangul", "kana": "kana", "latin": "other"}
    assert json.loads(db.execute("SELECT data FROM units WHERE id='jamo'").fetchone()[0])["category"] == "hangul"


def test_the_gugyeol_migration_and_the_publication_scripts_agree_on_a_gugyeol_label(scripts):
    """0012 names the gugyeol range the publication scripts and the Worker give `gugyeol`."""
    cloudflare_schema = importlib.import_module("cloudflare_schema")
    migration = Path("apps/cloudflare/migrations/0012_gugyeol_category.sql").read_text()
    case = re.search(r"CASE\n[\s\S]*?END", migration).group(0)
    db = sqlite3.connect(":memory:")
    rows = db.execute(f"WITH RECURSIVE n(c) AS (SELECT 0 UNION ALL SELECT c+1 FROM n WHERE c<1114111) "
                      f"SELECT c FROM n WHERE {case}='gugyeol'").fetchall()
    assert [c for (c,) in rows] == [c for c in range(0x110000) if not 0xD800 <= c <= 0xDFFF
                                    and cloudflare_schema.category_of(chr(c)) == "gugyeol"]
    assert [cloudflare_schema.category_of(v) for v in ("", "", "")] == ["gugyeol"] * 3


def test_the_gugyeol_migration_moves_a_row_published_as_other(scripts):
    cloudflare_schema = importlib.import_module("cloudflare_schema")
    db = sqlite3.connect(":memory:")
    cloudflare_schema.schema(db)
    for unit_id, label, category in (("gugyeol", "", "other"), ("kana", "あ", "kana"), ("latin", "A", "other")):
        db.execute("INSERT INTO units(id,origin,character,family,visual_group,production,category,state,revision,quiz,priority,shuffle,data,snapshot,context,visual,document) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (unit_id, "corpus", label, None, None, "unknown", category, "pending", 0, 1, 1, 0,
                    json.dumps({"label": label, "category": category}), "{}", "{}", "{}", None))
    db.executescript(Path("apps/cloudflare/migrations/0012_gugyeol_category.sql").read_text())
    assert dict(db.execute("SELECT id,category FROM units")) == {"gugyeol": "gugyeol", "kana": "kana", "latin": "other"}
    assert json.loads(db.execute("SELECT data FROM units WHERE id='gugyeol'").fetchone()[0])["category"] == "gugyeol"


def test_the_document_migration_reads_the_book_from_the_page_id():
    """0024 fills a published local crop's book from its page id, `<document>:<page>`."""
    db = sqlite3.connect(":memory:")
    for path in sorted(Path("apps/cloudflare/migrations").glob("*.sql")):
        if path.name < "0024":
            db.executescript(path.read_text())
    rows = (("local-a", "local", {"page_id": "hl:00AB:12"}), ("local-b", "local", {"page_id": "hk:entry:with:separators:9"}),
            ("no-page", "local", {}), ("corpus", "corpus", {"page_id": "codh:1:2"}))
    for unit_id, origin, data in rows:
        db.execute("INSERT INTO units(id,origin,character,family,visual_group,production,category,state,"
                   "revision,quiz,priority,shuffle,data,snapshot,context,visual) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (unit_id, origin, "あ", None, None, "unknown", "kana", "pending", 0, 1, 1, 0,
                    json.dumps(data), "{}", "{}", "{}"))
    db.executescript(Path("apps/cloudflare/migrations/0024_unit_document.sql").read_text())
    assert dict(db.execute("SELECT id,document FROM units")) == {
        "local-a": "hl:00AB", "local-b": "hk:entry:with:separators", "no-page": None, "corpus": None}


def test_the_family_migration_refiles_a_crop_left_under_another_family():
    """0023 gives a local crop its character's family, or the character's own code points without one."""
    db = sqlite3.connect(":memory:")
    for path in sorted(Path("apps/cloudflare/migrations").glob("*.sql")):
        if path.name < "0023":
            db.executescript(path.read_text())
    grapheme = json.dumps({"grapheme": {"code_point": "U+3042"}})
    db.execute("INSERT INTO characters VALUES('U+30A2','ア','',?,?)", (grapheme, grapheme))
    for unit_id, origin, label, family in (("undone", "local", "ア", "U+4EEE"), ("mark", "local", "※", "U+4EEE"),
                                           ("kept", "local", "ア", "U+3042"), ("corpus", "corpus", "ア", "U+4EEE"),
                                           ("marked", "local", "ツ\u309a", None), ("unnamed", "local", "", None)):
        db.execute("INSERT INTO units(id,origin,character,family,visual_group,production,category,state,"
                   "revision,quiz,priority,shuffle,data,snapshot,context,visual) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (unit_id, origin, label, family, None, "unknown", "kana", "pending", 0, 1, 1, 0,
                    "{}", "{}", "{}", "{}"))
    db.executescript(Path("apps/cloudflare/migrations/0023_family_follows_character.sql").read_text())
    assert dict(db.execute("SELECT id,family FROM units")) == {
        "undone": "U+3042", "mark": "U+203B", "kept": "U+3042", "corpus": "U+4EEE",
        "marked": "U+30C4 U+309A", "unnamed": None}


def test_an_export_leaves_out_the_units_the_site_holds(scripts, tmp_path):
    export = importlib.import_module("export_cloudflare_corpus")
    (tmp_path / "live.txt").write_text("hl:a:1\n\nhl:a:2\n")
    with sqlite3.connect(tmp_path / "corpus.sqlite") as db:
        db.executescript(SCHEMA)
        db.execute("INSERT INTO corpus_units(id,character,shuffle,object,offset,size) VALUES('hl:b:1','あ',1,'x.bin',0,1)")
        assert export.held(db, export.unit_ids(tmp_path / "live.txt")) == {"hl:a:1", "hl:a:2", "hl:b:1"}
        assert export.held(db) == {"hl:b:1"}


def test_the_ainu_records_the_site_publishes_as_its_own_crops_are_not_exported_as_corpus_glyphs(scripts, monkeypatch):
    export = importlib.import_module("export_cloudflare_corpus")
    monkeypatch.setattr(export.sources, "discover",
                        lambda root: [SimpleNamespace(name=name) for name in ("ainu-records", "codh-full")])
    assert [c.name for c in export.unit_corpora()] == ["codh-full"]


def test_an_export_or_seal_drops_the_glyphs_the_site_publishes_as_its_own_crops(scripts, tmp_path, monkeypatch):
    import pyarrow as pa
    import pyarrow.parquet as pq

    export = importlib.import_module("export_cloudflare_corpus")
    units = tmp_path / "units.parquet"
    pq.write_table(pa.table({"id": ["hk:1", "hk:2"]}), units)
    monkeypatch.setattr(export.sources, "discover", lambda root: [
        SimpleNamespace(name="ainu-records", table=lambda name: units),
        SimpleNamespace(name="codh-full", table=lambda name: None)])
    ids = export.locally_published_ids()
    assert ids == {"hk:1", "hk:2"}
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE corpus_units (id TEXT PRIMARY KEY)")
    db.executemany("INSERT INTO corpus_units VALUES (?)", [("hk:1",), ("hk:2",), ("codh:1",)])
    assert export.drop_locally_published(db, ids) == 2
    assert [i for i, in db.execute("SELECT id FROM corpus_units")] == ["codh:1"]


def test_corpus_export_packs_cached_pages_locally_and_skips_uncached_remote_iiif(scripts, tmp_path, monkeypatch):
    """Exported corpus glyphs pack image and context_image under /atlas/media/; uncached IIIF pages
    are skipped."""
    from PIL import Image

    from glyph_atlas import images, tables
    from glyph_atlas.schema import Box, Document, Page, Rights, Unit

    cache = tmp_path / "cache"
    monkeypatch.setenv("GLYPH_ATLAS_CACHE", str(cache))
    monkeypatch.chdir(tmp_path)

    # Seed a cached full-size page for one IIIF service, leaving a second service uncached.
    scan = tmp_path / "scan.png"
    Image.new("RGB", (200, 200), (240, 235, 220)).save(scan)
    cached_url = "https://iiif.example/cached/p1"
    uncached_url = "https://gallica.bnf.fr/iiif/ark:/12148/btv1b00000000/f1"
    images.register(scan, cached_url, service=cached_url)

    corpus_dir = tmp_path / "work" / "honkoku-lines"
    corpus_dir.mkdir(parents=True)
    tables.write(corpus_dir / "documents.parquet", [
        Document(id="hl:d1", title="Cached Book", holder="Holder",
                 image_rights=Rights(licence="PD", attribution="Holder")),
    ], Document)
    tables.write(corpus_dir / "pages.parquet", [
        Page(id="hl:d1:0", document_id="hl:d1", seq=0, image=cached_url, width=200, height=200),
        Page(id="hl:d1:1", document_id="hl:d1", seq=1, image=uncached_url, width=200, height=200),
    ], Page)
    tables.write(corpus_dir / "units.parquet", [
        Unit(id="hl:d1:0:1", document_id="hl:d1", page_id="hl:d1:0", kind="char", granularity="char",
             box=Box(x=20, y=20, w=40, h=40), unicode="U+4E00", text_source="一", method="import"),
        Unit(id="hl:d1:1:1", document_id="hl:d1", page_id="hl:d1:1", kind="char", granularity="char",
             box=Box(x=20, y=20, w=40, h=40), unicode="U+4E8C", text_source="二", method="import"),
    ], Unit)

    export = importlib.import_module("export_cloudflare_corpus")
    assert not hasattr(export, "holder_image")
    assert not hasattr(export, "holder_context")
    out = tmp_path / "out"
    export.export(out, corpora=["honkoku-lines"])

    with sqlite3.connect(out / "corpus.sqlite") as db:
        rows = db.execute("SELECT id, object, offset, size FROM corpus_units").fetchall()
        media_keys = {r[0] for r in db.execute("SELECT key FROM media")}
    assert [r[0] for r in rows] == ["hl:d1:0:1"]
    _, obj, offset, size = rows[0]
    record = json.loads((out / obj).read_bytes()[offset:offset + size])
    assert record["image"].startswith("/atlas/media/")
    assert record["context_image"].startswith("/atlas/media/")
    assert record["image"].rsplit("/", 1)[-1].removesuffix(".webp") in media_keys
    assert record["context_image"].rsplit("/", 1)[-1].removesuffix(".webp") in media_keys
    progress = json.loads((out / "progress.json").read_text())
    assert progress["honkoku-lines"] == 1
    assert progress["uncached"] == 1


def test_anchor_export_skips_non_proxyable_and_clears_stale_rows(scripts, tmp_path, monkeypatch):
    from io import BytesIO

    from PIL import Image

    anchors = importlib.import_module("export_cloudflare_anchors")
    buf = BytesIO()
    Image.new("RGB", (40, 40), (240, 235, 220)).save(buf, format="PNG")
    png_bytes = buf.getvalue()

    class FakeResp:
        content = png_bytes

        def raise_for_status(self):
            pass

    entries = {
        "a1": {"id": "a1", "proxyable": True, "image": "https://example.org/a1.png",
               "written_character": "一", "grapheme": "U+4E00", "source": {"document_id": "d1"}},
        "a2": {"id": "a2", "proxyable": False, "image": "https://example.org/a2.png",
               "written_character": "二", "grapheme": "U+4E8C", "source": {"document_id": "d2"}},
    }
    fake_api = SimpleNamespace(root=tmp_path, index=SimpleNamespace(glyphs=SimpleNamespace(entries=entries)))
    monkeypatch.setattr(anchors.FrozenResolver, "_from_anchor", lambda self, entry: dict(entry))

    # Seed a stale anchor row from a prior run to verify it is cleared when anchors-records.bin is rewritten.
    catalogue_schema = importlib.import_module("catalogue_schema")
    out = tmp_path / "anchors"
    out.mkdir()
    with sqlite3.connect(out / "corpus.sqlite") as db:
        catalogue_schema.schema(db)
        db.execute("INSERT INTO corpus_units(id,character,shuffle,object,offset,size) VALUES('a2','二',1,'anchors-records.bin',0,99)")
        db.execute("INSERT INTO media VALUES('oldkey','anchors-images.bin',0,10,'image/webp')")

    result = anchors.export(out, api=fake_api, fetch=lambda *a, **kw: FakeResp())
    assert result == {"anchors": 1, "hosted_images": 1, "skipped": 1}
    with sqlite3.connect(out / "corpus.sqlite") as db:
        ids = [r[0] for r in db.execute("SELECT id FROM corpus_units")]
        media_keys = [r[0] for r in db.execute("SELECT key FROM media")]
    assert ids == ["a1"]
    assert "oldkey" not in media_keys and len(media_keys) == 1


def test_a_corpus_glyph_carries_its_sources_credit_line(scripts):
    exporter = importlib.import_module("export_cloudflare_corpus")
    credit = "東京大学史料編纂所「くずし字データセット」doi:10.57459/hi.34.kuzushiji"
    assert exporter.attribution({"image_rights": {"licence": "CC-BY-4.0", "attribution": credit}}) == credit
    assert exporter.attribution({"image_rights": {"licence": "PD"}}) is None
    assert exporter.attribution({}) is None


def test_a_label_credit_that_repeats_the_image_credit_is_left_out(scripts):
    export = importlib.import_module("export_cloudflare_corpus")
    same = "『日本古典籍くずし字データセット』（国文研ほか所蔵／CODH加工）"
    joined = {"image_rights": {"attribution": same}, "text_attribution": same}
    assert export.text_attribution(joined) is None
    joined = {"image_rights": {"attribution": "国立国会図書館デジタルコレクション"}, "text_attribution": "HDIC KRM, CC BY-SA 4.0"}
    assert export.text_attribution(joined) == "HDIC KRM, CC BY-SA 4.0"


def test_an_export_finds_and_drops_the_glyphs_of_lines_aligned_in_the_old_detection_order(scripts, tmp_path):
    import pyarrow as pa
    import pyarrow.dataset as ds
    import pyarrow.parquet as pq

    export = importlib.import_module("export_cloudflare_corpus")
    rows = []
    for line, page, ys in (("stale", "p:0", (90, 10, 130, 50)), ("read", "p:1", (10, 50, 90, 130))):
        for seq, y in enumerate(ys, start=1):
            rows.append({"id": f"{line}:{seq}", "page_id": page, "line_id": line, "seq": seq,
                         "box": {"x": 100, "y": y, "w": 30, "h": 28}, "method": "detect-align", "kind": "char"})
    rows.append({"id": "imported:1", "page_id": "p:0", "line_id": "imported", "seq": 1,
                 "box": {"x": 0, "y": 0, "w": 1, "h": 1}, "method": "import", "kind": "char"})
    pq.write_table(pa.Table.from_pylist(rows), tmp_path / "units.parquet")
    stale = export.stale_units(ds.dataset(tmp_path / "units.parquet"), {"stale": True, "read": True})
    assert stale == {f"stale:{seq}" for seq in range(1, 5)}
    with sqlite3.connect(tmp_path / "corpus.sqlite") as db:
        db.executescript(SCHEMA)
        for unit in ("stale:1", "read:1"):
            db.execute("INSERT INTO corpus_units(id,character,family,shuffle,object,offset,size) "
                       "VALUES(?,'a','U+0061',1,'o',0,1)", (unit,))
        assert export.drop_stale(db, stale) == 1
        assert [r[0] for r in db.execute("SELECT id FROM corpus_units")] == ["read:1"]


def test_an_export_drops_the_glyphs_of_two_lines_that_hold_one_box(scripts, tmp_path):
    import pyarrow as pa
    import pyarrow.dataset as ds
    import pyarrow.parquet as pq

    export = importlib.import_module("export_cloudflare_corpus")
    rows = []
    for line, x, ys in (("right", 200, (10, 50, 90)), ("left", 100, (10, 50, 90)), ("apart", 0, (10, 50, 90))):
        for seq, y in enumerate(ys, start=1):
            rows.append({"id": f"{line}:{seq}", "page_id": "p:0", "line_id": line, "seq": seq,
                         "box": {"x": x, "y": y, "w": 30, "h": 28}, "method": "detect-align", "kind": "char"})
    # The left line's box was wide enough to take the right line's last character too.
    rows.append({"id": "left:4", "page_id": "p:0", "line_id": "left", "seq": 4,
                 "box": {"x": 200, "y": 90, "w": 30, "h": 28}, "method": "detect-align", "kind": "char"})
    pq.write_table(pa.Table.from_pylist(rows), tmp_path / "units.parquet")
    stale = export.stale_units(ds.dataset(tmp_path / "units.parquet"), {"right": True, "left": True, "apart": True})
    assert stale == {f"right:{seq}" for seq in range(1, 4)} | {f"left:{seq}" for seq in range(1, 5)}
