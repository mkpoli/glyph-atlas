import pytest

from glyph_atlas import tables
from glyph_atlas.extraction_queue import Queue, quality_reason, run, scorable_chars, unique_units
from glyph_atlas.schema import Box, Candidate, Document, Line, Page, ReviewState, Unit


def unit(**kwargs):
    return Unit(id="test", text_source="字", reading="字", box=Box(x=10,y=10,w=30,h=35), **kwargs)


def votes(text="字"):
    return [{"engine":"Atlas classifier","text":text,"score":.97},
            {"engine":"NDLkotenOCR","text":text,"score":.98}]


def test_gate_rejects_joined_disagreement_and_unaligned():
    assert quality_reason(unit(),votes(),(200,200)) is None
    assert quality_reason(unit(),votes("字字"),(200,200)) == "visual-disagreement"
    assert quality_reason(unit(review=ReviewState.REJECTED),votes(),(200,200)) == "alignment-uncertain"
    faint=votes(); faint[1]["score"]=.12
    assert quality_reason(unit(),faint,(200,200)) is None, "NDLkotenOCR's reading of the character counts at .10"
    low=votes(); low[1]["score"]=.09
    assert quality_reason(unit(),low,(200,200)) == "visual-uncertain"
    unsure=votes(); unsure[0]["score"]=.79
    assert quality_reason(unit(),unsure,(200,200)) == "visual-uncertain"
    assert quality_reason(unit(),votes(),(30,30)) == "invalid-geometry"


def test_dedup_rejects_both_same_ink_even_if_labels_disagree():
    a=unit()
    b=a.model_copy(update={"id":"second","text_source":"宇"})
    c=a.model_copy(update={"id":"third","box":Box(x=80,y=10,w=30,h=35)})
    kept,count=unique_units([a,b,c])
    assert [u.id for u in kept] == ["third"]
    assert count == 2


def test_nonfinite_or_invalid_model_scores_are_withheld():
    for score in (float('nan'), float('inf'), -1, 1.01, None, '0.99', True):
        result=votes()
        result[0]['score']=score
        assert quality_reason(unit(),result,(200,200)) == 'visual-uncertain'


def test_family_score_is_not_an_exact_character_vote():
    result = votes()
    result[0]["identity_scope"] = "family"
    assert quality_reason(unit(), result, (200, 200)) == "visual-disagreement"


def source(tmp_path):
    directory=tmp_path/"source"; directory.mkdir()
    docs=[Document(id="a",title="暦"),Document(id="b",title="天文"),Document(id="c",title="蝦夷")]
    pages=[Page(id=f"{d.id}:{i}",document_id=d.id,seq=i,image="https://example.org/x.jpg",width=100,height=100)
           for d in docs for i in range(2)]
    lines=[Line(id=f"{p.id}:L0",page_id=p.id,seq=0,box=Box(x=1,y=1,w=10,h=50),text_raw="字",text="字")
           for p in pages]
    tables.write(directory/"documents.parquet",docs,Document)
    tables.write(directory/"pages.parquet",pages,Page)
    tables.write(directory/"lines.parquet",lines,Line)
    return directory


def test_queue_round_robin_resumes_and_seed_is_idempotent(tmp_path,monkeypatch):
    monkeypatch.setattr("glyph_atlas.images.index_path",lambda:tmp_path/"missing")
    queue=Queue(tmp_path/"queue")
    assert queue.seed(source(tmp_path)) == 4
    assert queue.seed(tmp_path/"source") == 0
    assert queue.claim("w")["id"] == "a:0"
    assert queue.claim("stopped", lease=-1)["id"] == "b:0"  # a worker that stopped: its lease has lapsed
    assert queue.claim("w")["id"] == "b:0"
    assert queue.claim("w")["id"] == "a:1"


def test_seed_withdraws_the_unfinished_pages_of_a_withdrawn_document(tmp_path,monkeypatch):
    monkeypatch.setattr("glyph_atlas.images.index_path",lambda:tmp_path/"missing")
    queue=Queue(tmp_path/"queue")
    queue.seed(source(tmp_path))
    with queue.db:
        queue.db.execute("UPDATE pages SET status='complete' WHERE id='b:0'")
    monkeypatch.setattr("glyph_atlas.withdrawn.documents",lambda:frozenset({"b"}))
    assert queue.seed(tmp_path/"source") == 0
    assert dict(queue.db.execute("SELECT id,status FROM pages WHERE document_id='b'")) == {
        "b:0":"complete","b:1":"withdrawn"}
    assert [queue.claim("w")["id"] for _ in range(2)] == ["a:0","a:1"]
    assert queue.claim("w") is None


def test_seed_leaves_out_a_page_without_a_located_line(tmp_path,monkeypatch):
    monkeypatch.setattr("glyph_atlas.images.index_path",lambda:tmp_path/"missing")
    directory=tmp_path/"source"; directory.mkdir()
    doc=Document(id="a",title="暦")
    pages=[Page(id=f"a:{i}",document_id="a",seq=i,image="https://example.org/x.jpg",width=100,height=100)
           for i in range(3)]
    lines=[Line(id="a:0:L0",page_id="a:0",seq=0,box=Box(x=1,y=1,w=10,h=50),text_raw="字",text="字"),
           Line(id="a:1:L0",page_id="a:1",seq=0,box=None,text_raw="字",text="字"),
           Line(id="a:2:L0",page_id="a:2",seq=0,box=Box(x=0,y=0,w=100,h=100),text_raw="",text="",
                meta={"scope":"page"})]
    tables.write(directory/"documents.parquet",[doc],Document)
    tables.write(directory/"pages.parquet",pages,Page)
    tables.write(directory/"lines.parquet",lines,Line)
    queue=Queue(tmp_path/"queue")
    assert queue.seed(directory) == 1
    assert [r[0] for r in queue.db.execute("SELECT id FROM pages")] == ["a:0"]


def test_failed_page_is_not_completed_or_importable(tmp_path, monkeypatch):
    monkeypatch.setattr("glyph_atlas.extraction_queue.require_storage", lambda _: None)
    queue=Queue(tmp_path/"queue")
    queue.db.execute("INSERT INTO pages(id,document_id,title,source,cached,rank) VALUES('a','a','A','s',1,0)")
    queue.db.commit()
    class Broken:
        def extract(self,*args,**kwargs):
            raise ValueError("unavailable")
    result=run(queue,Broken(),pages=1)
    assert result["counts"] == {"failed":1}
    assert result["character_crops"] == 0
    assert queue.db.execute("SELECT output FROM pages").fetchone()[0] is None


def test_publication_retries_after_import_failure_without_repeating_extraction(tmp_path, monkeypatch):
    from glyph_atlas.extraction_queue import publish_completed
    from glyph_atlas.review import media
    prepared = []
    monkeypatch.setattr(media, "prepare_dataset", lambda path: prepared.append(path))
    queue=Queue(tmp_path/"queue")
    queue.db.execute("INSERT INTO pages(id,document_id,title,source,cached,rank,status,output,accepted) VALUES('a','a','A','s',1,0,'complete','pages/a',3)")
    queue.db.commit()
    class Bridge:
        fail=True
        calls=0
        def import_dataset(self,path):
            assert prepared and prepared[-1] == path
            self.calls += 1
            if self.fail:
                raise ValueError("unavailable")
    bridge=Bridge()
    assert publish_completed(queue,bridge) == 0
    assert queue.db.execute("SELECT published_at FROM pages").fetchone()[0] is None
    bridge.fail=False
    assert publish_completed(queue,bridge) == 1
    assert publish_completed(queue,bridge) == 0
    assert bridge.calls == 2
    assert queue.status()["published_crops"] == 3


def test_unknown_source_dimensions_require_canonical_info_and_reject_scaled_cache():
    import pytest

    from glyph_atlas.extraction_queue import check_coordinate_space, source_dimensions
    page=Page(id="p",document_id="d",seq=0,image="https://example.org/iiif/image",width=0,height=0)
    size=source_dimensions(page,info_loader=lambda _: {"width":4000,"height":6000})
    assert size == (4000,6000)
    check_coordinate_space(size,(4000,6000))
    with pytest.raises(ValueError,match="differ from source"):
        check_coordinate_space(size,(2000,3000))
    with pytest.raises(ValueError,match="40 megapixel"):
        source_dimensions(page,info_loader=lambda _: {"width":10000,"height":10000})


def test_noniiif_without_coordinate_dimensions_is_withheld():
    import pytest

    from glyph_atlas.extraction_queue import source_dimensions
    page=Page(id="p",document_id="d",seq=0,image="https://example.org/image.jpg",width=0,height=0)
    with pytest.raises(ValueError,match="dimensions unavailable"):
        source_dimensions(page)


def test_retryable_errors_have_bounded_backoff(tmp_path,monkeypatch):
    queue=Queue(tmp_path/"queue")
    queue.db.execute("INSERT INTO pages(id,document_id,title,source,cached,rank) VALUES('a','a','A','s',1,0)")
    queue.db.commit()
    queue.wall = lambda: 1000
    assert queue.claim("w")["id"] == "a"
    queue.fail("a","w","timeout",retryable=True)
    assert queue.claim("w") is None
    queue.wall = lambda: 1100
    assert queue.claim("w")["id"] == "a"
    queue.fail("a","w","timeout",retryable=True)
    queue.wall = lambda: 1300
    assert queue.claim("w")["id"] == "a"
    queue.fail("a","w","timeout",retryable=True)
    assert queue.db.execute("SELECT status FROM pages").fetchone()[0] == "failed"


def test_storage_floor_pauses_before_claim_or_inference(tmp_path,monkeypatch):
    queue=Queue(tmp_path/"queue")
    def full(_):
        raise OSError("no free space")
    monkeypatch.setattr("glyph_atlas.extraction_queue.require_storage",full)
    assert run(queue,object())["state"] == "paused-low-storage"


def test_committed_report_is_authoritative_and_validated(tmp_path):
    import json

    import pytest

    from glyph_atlas.extraction_queue import committed_report
    from glyph_atlas.schema import Line
    document=Document(id="d",title="Book")
    page=Page(id="p",document_id="d",seq=0,image="https://example.org/iiif/image",width=100,height=100,sha256="a"*64)
    line=Line(id="l",page_id="p",seq=0,text_raw="字",text="字",box=Box(x=0,y=0,w=100,h=100))
    one=unit(document_id="d",page_id="p",line_id="l")
    for name, rows in {"documents":[document],"pages":[page],"lines":[line],"units":[one]}.items():
        tables.write(tmp_path/f"{name}.parquet",rows,tables.TABLES[name])
    report={"generation":"g","page_id":"p","page_sha256":"a"*64,"accepted":1}
    (tmp_path/"report.json").write_text(json.dumps(report))
    assert committed_report(tmp_path,"g",page) == report
    report["accepted"]=2
    (tmp_path/"report.json").write_text(json.dumps(report))
    with pytest.raises(ValueError,match="tables mismatch"):
        committed_report(tmp_path,"g",page)
    with pytest.raises(ValueError,match="identity mismatch"):
        committed_report(tmp_path,"changed",page)


def test_a_page_that_keeps_stopping_the_worker_is_left_failed(tmp_path):
    """A page that kills the process never reaches `fail`; recovery counts it so the queue moves on."""
    from glyph_atlas.extraction_queue import MAX_ATTEMPTS

    queue = Queue(tmp_path / "queue")
    with queue.db:
        for rank, ident in enumerate(("a:0", "a:1")):
            queue.db.execute("INSERT INTO pages (id,document_id,title,source,cached,rank) VALUES(?,?,?,?,?,?)",
                             (ident, "a", "t", "s", 1, rank))
    for _ in range(MAX_ATTEMPTS):
        assert queue.claim("w", lease=-1)["id"] == "a:0"  # the worker died while extracting a:0
    assert queue.claim("w", lease=-1)["id"] == "a:1"  # a lease later
    assert queue.db.execute("SELECT status FROM pages WHERE id='a:0'").fetchone()[0] == "failed"


def test_a_page_the_store_refuses_is_not_published_again(tmp_path, monkeypatch):
    """A refused import is final: the crops are not rendered again on every pass."""
    from glyph_atlas.extraction_queue import publish_completed
    from glyph_atlas.review import media
    from glyph_atlas.review.store import BadRequest

    queue = Queue(tmp_path / "queue")
    with queue.db:
        queue.db.execute("""INSERT INTO pages (id,document_id,title,source,cached,rank,status,output)
            VALUES('a:0','a','t','s',1,0,'complete','pages/a0')""")
    prepared = []
    monkeypatch.setattr(media, "prepare_dataset", lambda path: prepared.append(path))

    class RefusingStore:
        def import_dataset(self, path):
            raise BadRequest("Conflicting imported pages record")

    for _ in range(3):
        assert publish_completed(queue, RefusingStore()) == 0
    assert len(prepared) == 1


def lines_source(tmp_path, lines, name="lines-source"):
    directory = tmp_path / name
    directory.mkdir()
    tables.write(directory / "lines.parquet", lines, Line)
    return directory


def test_scorable_chars_skips_whitespace_punctuation_and_marks():
    assert scorable_chars("あ、　ヿ゙") == {"あ", "ヿ"}


def seeded(queue, rows):
    """Queue pages as `seed` would, each row `(id, source, rank)`."""
    with queue.db:
        for page_id, source, rank in rows:
            queue.db.execute("INSERT INTO pages(id,document_id,title,source,cached,rank) VALUES(?,'d','T',?,0,?)",
                             (page_id, str(source), rank))


def test_prioritize_claims_a_zero_crop_character_before_an_earlier_common_page(tmp_path, monkeypatch):
    monkeypatch.setattr("glyph_atlas.images.index_path", lambda: tmp_path / "missing")
    queue = Queue(tmp_path / "queue")
    source = lines_source(tmp_path, [
        Line(id="l1", page_id="a", seq=0, text_raw="のの", text="のの"),
        Line(id="l2", page_id="b", seq=0, text_raw="ヿ", text="ヿ"),
    ])
    seeded(queue, [("a", source, 0), ("b", source, 1)])
    assert queue.prioritize({"の": 1000}) == 2
    assert queue.claim("w")["id"] == "b"


def test_prioritize_scores_each_page_from_the_dataset_it_was_seeded_from(tmp_path, monkeypatch):
    monkeypatch.setattr("glyph_atlas.images.index_path", lambda: tmp_path / "missing")
    queue = Queue(tmp_path / "queue")
    common = lines_source(tmp_path, [Line(id="l1", page_id="a", seq=0, text_raw="の", text="の")], name="common")
    rare = lines_source(tmp_path, [Line(id="l2", page_id="b", seq=0, text_raw="ヿ", text="ヿ")], name="rare")
    seeded(queue, [("a", common, 0), ("b", rare, 1)])
    assert queue.prioritize({"の": 1000}) == 2
    assert queue.db.execute("SELECT priority FROM pages WHERE id='b'").fetchone()[0] == 1.0
    assert queue.claim("w")["id"] == "b"


def test_prioritize_refuses_a_source_without_lines(tmp_path, monkeypatch):
    import pytest

    monkeypatch.setattr("glyph_atlas.images.index_path", lambda: tmp_path / "missing")
    queue = Queue(tmp_path / "queue")
    seeded(queue, [("a", tmp_path / "moved", 0)])
    with pytest.raises(FileNotFoundError):
        queue.prioritize({})


def test_prioritize_ties_keep_the_original_claim_order(tmp_path, monkeypatch):
    monkeypatch.setattr("glyph_atlas.images.index_path", lambda: tmp_path / "missing")
    queue = Queue(tmp_path / "queue")
    source = lines_source(tmp_path, [
        Line(id="l1", page_id="a", seq=0, text_raw="字", text="字"),
        Line(id="l2", page_id="b", seq=0, text_raw="字", text="字"),
    ])
    seeded(queue, [("a", source, 1), ("b", source, 0)])
    queue.prioritize({})
    assert queue.claim("w")["id"] == "b"


def test_prioritize_leaves_complete_pages_alone(tmp_path, monkeypatch):
    monkeypatch.setattr("glyph_atlas.images.index_path", lambda: tmp_path / "missing")
    queue = Queue(tmp_path / "queue")
    source = lines_source(tmp_path, [Line(id="l1", page_id="a", seq=0, text_raw="ヿ", text="ヿ")])
    with queue.db:
        queue.db.execute("""INSERT INTO pages(id,document_id,title,source,cached,rank,status,priority)
            VALUES('a','d','A',?,0,0,'complete',5.0)""", (str(source),))
    assert queue.prioritize({}) == 0
    assert queue.db.execute("SELECT priority FROM pages WHERE id='a'").fetchone()[0] == 5.0


def test_priority_column_upgrades_an_old_queue_file_without_it(tmp_path):
    import sqlite3

    path = tmp_path / "queue"
    path.mkdir()
    db = sqlite3.connect(path / "queue.sqlite")
    db.execute("""CREATE TABLE pages (
        id TEXT PRIMARY KEY, document_id TEXT NOT NULL, title TEXT NOT NULL,
        source TEXT NOT NULL, cached INTEGER NOT NULL, rank INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
        output TEXT, accepted INTEGER NOT NULL DEFAULT 0, examined INTEGER NOT NULL DEFAULT 0,
        error TEXT, updated_at TEXT)""")
    db.execute("INSERT INTO pages(id,document_id,title,source,cached,rank) VALUES('a','d','A','s',1,0)")
    db.commit()
    db.close()
    queue = Queue(path)
    columns = {r[1] for r in queue.db.execute("PRAGMA table_info(pages)")}
    assert "priority" in columns
    assert queue.claim("w")["id"] == "a"


def test_gates_for_a_character_the_classifier_has_no_class_for():
    from glyph_atlas.extraction_queue import out_of_vocabulary, unconfirmed_reason
    rare = unit().model_copy(update={"text_source": "飍", "reading": "飍", "review": ReviewState.REJECTED})
    assert out_of_vocabulary(rare, {"U+5B57"}) and not out_of_vocabulary(unit(), {"U+5B57"})
    # Katakana is scored through its hiragana class; marks and punctuation have no ink of their own.
    yu = unit().model_copy(update={"text_source": "ユ", "candidates": [Candidate(unicode="U+3086", p=1.0)]})
    assert not out_of_vocabulary(yu, {"U+3086"})
    for mark in ("ー", "〳", "：", "-", "（"):
        assert not out_of_vocabulary(unit().model_copy(update={"text_source": mark}), set())
    assert unconfirmed_reason(rare, [], (200,200), alphabet=set()) is None
    assert unconfirmed_reason(rare, [], (30,30), alphabet=set()) == "invalid-geometry"
    # Where NDLkotenOCR knows the character, it must read it.
    assert unconfirmed_reason(rare, votes("尽"), (200,200), alphabet={"飍"}) == "visual-disagreement"
    assert unconfirmed_reason(rare, votes("飍"), (200,200), alphabet={"飍"}) is None
    # The classifier vetoes a box it confidently reads as a kana or as a neighbour.
    assert unconfirmed_reason(rare, votes("お"), (200,200), alphabet=set()) == "visual-disagreement"
    assert unconfirmed_reason(rare, votes("風"), (200,200), alphabet=set(), neighbours={"風"}) == "visual-disagreement"
    assert unconfirmed_reason(rare, votes("高"), (200,200), alphabet=set(), neighbours={"風"}) is None
    # NDLkotenOCR reading two characters surely means the box holds more than one.
    two = votes("高"); two[1]["text"] = "吉園"
    assert unconfirmed_reason(rare, two, (200,200), alphabet=set()) == "not-one-character"
    two[1]["score"] = .78
    assert unconfirmed_reason(rare, two, (200,200), alphabet=set()) is None
    unsure = votes("お"); unsure[0]["score"] = .5
    assert unconfirmed_reason(rare, unsure, (200,200), alphabet=set()) is None


def test_extraction_publishes_out_of_vocabulary_units_tagged_unconfirmed(tmp_path, monkeypatch):
    from PIL import Image

    from glyph_atlas import align as align_module
    from glyph_atlas import extraction_queue
    from glyph_atlas.extraction_queue import Engine

    directory = tmp_path/"source"; directory.mkdir()
    page = Page(id="d:0", document_id="d", seq=0, image="https://example.org/p.jpg", width=400, height=200)
    tables.write(directory/"documents.parquet", [Document(id="d", title="安政風聞集")], Document)
    tables.write(directory/"pages.parquet", [page], Page)
    tables.write(directory/"lines.parquet", [Line(id="d:0:l", page_id="d:0", seq=0, text="字飍字", text_raw="字飍字",
                 box=Box(x=0, y=0, w=400, h=200))], Line)
    image = tmp_path/"p.png"; Image.new("RGB", (400, 200), "white").save(image)
    monkeypatch.setattr(extraction_queue.images, "path_for", lambda _: image)

    def placed(text, x, review, box=True, w=40):
        return Unit(id=f"u{x}", page_id="d:0", line_id="d:0:l", text_source=text, reading=text, review=review,
                    box=Box(x=x, y=10, w=w, h=45) if box else None)
    # The classifier reads 字 on every 40-pixel crop, and 高 on the 50-pixel 飍 at x=100.
    found = [placed("字", 10, ReviewState.MACHINE), placed("飍", 100, ReviewState.REJECTED, w=50),
             placed("字", 200, ReviewState.REJECTED), placed("飍", 250, ReviewState.REJECTED),
             placed("飍", 300, ReviewState.REJECTED, box=False)]
    monkeypatch.setattr(align_module, "align_line", lambda *a, **k: (found, []))
    monkeypatch.setattr(align_module, "clear_crop_cache", lambda: None)

    engine = object.__new__(Engine)
    engine.run = type("Run", (), {"model_dump": lambda self: {}})()
    engine.models = {}
    engine.detector = type("Detector", (), {"boxes": lambda self, _: []})()
    engine.classifier = type("Classifier", (), {"classes": ["U+5B57", "other"]})()
    engine.reader = type("Reader", (), {"alphabet": set("字"),
                                        "read": lambda self, crop: {"votes": votes("高" if crop.width == 50 else "字")}})()

    report, output = engine.extract({"id": "d:0", "source": str(directory)}, tmp_path/"out")
    units = {u.meta["extraction"]["source_unit_id"]: u for u in tables.Dataset(output).read("units")}
    assert units["u10"].meta["extraction"]["gate"] == "consensus"
    assert units["u100"].meta["extraction"]["gate"] == "unconfirmed"
    assert units["u100"].review == ReviewState.MACHINE and units["u100"].unicode == "U+98CD"
    assert set(units) == {"u10", "u100"}
    assert report["unconfirmed"] == 1
    # The boxless 飍 and the in-vocabulary 字 stay withheld by the alignment; the 飍 at x=250 reads as
    # its neighbour 字.
    assert report["withheld"] == {"alignment-uncertain": 2, "visual-disagreement": 1, "overlapping-crops": 0}


def test_a_page_s_inputs_are_read_from_its_own_rows_without_loading_the_catalogue(tmp_path, monkeypatch):
    from PIL import Image

    from glyph_atlas import extraction_queue
    from glyph_atlas.extraction_queue import Engine

    directory = tmp_path/"source"; directory.mkdir()
    tables.write(directory/"documents.parquet", [Document(id="d", title="暦"), Document(id="e", title="天文")], Document)
    tables.write(directory/"pages.parquet", [
        Page(id=f"{d}:{i}", document_id=d, seq=i, image=f"https://example.org/{d}{i}.jpg", width=40, height=20)
        for d in "de" for i in range(3)], Page)
    tables.write(directory/"lines.parquet", [Line(id="e:1:l", page_id="e:1", seq=0, text="字", text_raw="字",
                 box=Box(x=0, y=0, w=40, h=20))], Line)
    image = tmp_path/"p.png"; Image.new("RGB", (40, 20), "white").save(image)
    monkeypatch.setattr(extraction_queue.images, "path_for", lambda _: image)
    loaded = []
    read = tables.Dataset.read
    monkeypatch.setattr(tables.Dataset, "read", lambda self, name, *a, **k: loaded.append(name) or read(self, name, *a, **k))

    engine = object.__new__(Engine)
    engine.run = type("Run", (), {"model_dump": lambda self: {}})()
    engine.models = {}
    work = engine.inputs({"id": "e:1", "source": str(directory)})
    assert (work["page"].id, work["document"].title, [line.id for line in work["lines"]]) == ("e:1", "天文", ["e:1:l"])
    assert loaded == []


def supplement_queue(tmp_path, pages):
    """A queue whose rows are `(id, status, policy of its output or None, line text)`."""
    from glyph_atlas.extraction_queue import atomic_json
    source = lines_source(tmp_path, [Line(id=f"{ident}:L", page_id=ident, seq=0, text_raw=text, text=text,
                                          box=Box(x=0, y=0, w=10, h=10)) for ident, _, _, text in pages])
    queue = Queue(tmp_path/"queue")
    for ident, status, policy, _ in pages:
        output = None
        if policy:
            output = f"pages/{ident}"
            (queue.root/output).mkdir(parents=True)
            atomic_json(queue.root/output/"report.json", {"policy": policy})
        queue.db.execute("""INSERT INTO pages(id,document_id,title,source,cached,rank,status,output)
            VALUES(?,?,?,?,1,0,?,?)""", (ident, "d", "D", str(source), status, output))
    queue.db.commit()
    return queue, source


def test_supplements_list_the_pages_an_earlier_policy_completed(tmp_path):
    from glyph_atlas.extraction_queue import POLICY
    queue, _ = supplement_queue(tmp_path, [
        ("old-rare", "complete", "single-character-consensus-v1", "風飍字"),
        ("old-known", "complete", "single-character-consensus-v0", "字：ー"),
        ("current", "complete", POLICY, "飍"),
        ("pending", "pending", None, "飍")])
    assert queue.seed_supplements() == 2
    assert queue.seed_supplements() == 0
    assert sorted(r[0] for r in queue.db.execute("SELECT page_id FROM supplements")) == ["old-known", "old-rare"]
    assert {queue.claim_supplement("w")["output"],
            queue.claim_supplement("w")["output"]} == {"pages/old-rare", "pages/old-known"}
    assert queue.claim_supplement("w") is None


class SupplementEngine:
    """An engine whose page extraction yields `units` and counts how often it ran."""

    def __init__(self, records, units):
        self.records, self.units, self.runs = records, units, 0

    def inputs(self, job, max_lines=64):
        return {"identity": "new"}

    def extract_page(self, work):
        self.runs += 1
        return {"generation": work["identity"]}, {**self.records, "units": self.units}


def supplement_records():
    document, page = Document(id="d", title="D"), Page(id="d:0", document_id="d", seq=0, image="x", width=400, height=100)
    line = Line(id="d:0:L", page_id="d:0", seq=0, text_raw="字飍飍", text="字飍飍", box=Box(x=0, y=0, w=400, h=100))
    return {"documents": [document], "pages": [page], "lines": [line]}


def placed(ident, x, gate):
    return Unit(id=ident, document_id="d", page_id="d:0", line_id="d:0:L", reading="飍", text_source="飍",
                box=Box(x=x, y=10, w=40, h=45), meta={"extraction": {"gate": gate}})


def test_a_supplement_keeps_new_units_off_published_crops(tmp_path):
    from glyph_atlas.extraction_queue import commit, supplement
    root = tmp_path/"queue"
    records = supplement_records()
    commit(root/"pages"/"old", "old", {**records, "units": [placed("old:1", 10, "consensus")]}, {})
    engine = SupplementEngine(records, [placed("new:1", 10, "consensus"), placed("new:2", 12, "unconfirmed"),
                                        placed("new:3", 200, "unconfirmed"), placed("new:4", 300, "consensus")])
    report, output = supplement(engine, {"id": "d:0", "output": "pages/old"}, root)
    assert report["added"] == 2 and output.parent.name == "supplements"
    # Either gate adds a crop the earlier output did not publish; nothing lands on a published one.
    assert [u.id for u in tables.Dataset(output).read("units")] == ["new:3", "new:4"]
    assert not tables.Dataset(output).validate()
    assert sorted(p.name for p in (root/"pages").iterdir()) == ["old"], "the page's full extraction is not kept"
    # A later policy's supplement also leaves alone what an earlier supplement published.
    commit(root/"supplements"/"prior", "prior", {**records, "units": [placed("prior:1", 205, "unconfirmed"),
                                                                       placed("prior:2", 302, "consensus")]}, {})
    report, output = supplement(engine, {"id": "d:0", "output": "pages/old",
                                         "earlier_supplements": ["supplements/prior"]}, root)
    assert report["added"] == 0 and tables.Dataset(output).read("units") == []


def test_a_supplement_committed_before_its_worker_stopped_is_reused(tmp_path):
    from glyph_atlas.extraction_queue import commit, supplement
    root = tmp_path/"queue"
    records = supplement_records()
    commit(root/"pages"/"old", "old", {**records, "units": []}, {})
    engine = SupplementEngine(records, [placed("new:1", 200, "consensus")])
    job = {"id": "d:0", "output": "pages/old"}
    first, output = supplement(engine, job, root)
    again, same = supplement(engine, job, root)
    assert engine.runs == 1 and same == output and again == first


def test_run_takes_a_supplement_every_other_page_and_then_the_rest(tmp_path, monkeypatch):
    from glyph_atlas import extraction_queue
    monkeypatch.setattr(extraction_queue, "require_storage", lambda _: None)
    queue, _ = supplement_queue(tmp_path, [
        ("s1", "complete", "single-character-consensus-v1", "飍"),
        ("s2", "complete", "single-character-consensus-v1", "飍"),
        ("p1", "pending", None, "字"), ("p2", "pending", None, "字")])
    assert queue.seed_supplements() == 2
    order = []

    class Engine:
        def extract(self, job, root, max_lines=64):
            order.append(("page", job["id"]))
            return {"accepted": 1, "examined": 1, "policy": extraction_queue.POLICY}, root/"pages"/job["id"]

    def fake_supplement(engine, job, root, max_lines=64):
        order.append(("supplement", job["id"]))
        return {"added": 2}, root/"supplements"/job["id"]
    monkeypatch.setattr(extraction_queue, "supplement", fake_supplement)
    result = run(queue, Engine(), pages=4)
    assert order == [("page", "p1"), ("supplement", "s1"), ("page", "p2"), ("supplement", "s2")]
    assert {r[0] for r in queue.db.execute("SELECT status FROM pages WHERE id IN ('p1','p2')")} == {"complete"}
    assert result["supplements"] == {"complete": 2, "added": 4, "published": 0, "publication_failures": 0}


def test_supplements_are_published_once_and_a_refusal_is_not_retried(tmp_path, monkeypatch):
    from glyph_atlas.extraction_queue import POLICY, publish_supplements
    from glyph_atlas.review import media
    from glyph_atlas.review.store import BadRequest
    monkeypatch.setattr(media, "prepare_dataset", lambda path: None)
    queue, _ = supplement_queue(tmp_path, [("a", "complete", "single-character-consensus-v1", "飍"),
                                           ("b", "complete", "single-character-consensus-v1", "飍")])
    queue.db.executemany("INSERT INTO supplements(page_id,policy,status,output,added) VALUES(?,?,'complete',?,1)",
                         [("a", POLICY, "supplements/a"), ("b", POLICY, "supplements/b")])
    queue.db.commit()

    imported = []

    class Store:
        def import_dataset(self, path):
            if path.name == "b":
                raise BadRequest("Conflicting imported pages record")
            imported.append(path.name)
    assert publish_supplements(queue, Store()) == 1
    assert publish_supplements(queue, Store()) == 0
    assert imported == ["a"]
    assert queue.status()["supplements"]["published"] == 1


def test_a_failed_supplement_waits_before_its_next_attempt(tmp_path, monkeypatch):
    from glyph_atlas import extraction_queue
    queue, _ = supplement_queue(tmp_path, [("a", "complete", "single-character-consensus-v1", "飍")])
    assert queue.seed_supplements() == 1
    now = [1000.0]
    queue.wall = lambda: now[0]
    for attempt in range(extraction_queue.MAX_ATTEMPTS):
        assert queue.claim_supplement("w")["id"] == "a"
        queue.fail_supplement("a", "w", "DownloadError: unavailable")
        assert queue.claim_supplement("w") is None
        now[0] += 3600
    assert queue.db.execute("SELECT status FROM supplements").fetchone()[0] == "failed"


def test_a_supplements_table_without_retry_after_is_upgraded(tmp_path):
    import sqlite3
    root = tmp_path/"queue"; root.mkdir()
    db = sqlite3.connect(root/"queue.sqlite")
    db.execute("""CREATE TABLE supplements (page_id TEXT NOT NULL, policy TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0, output TEXT, added INTEGER,
        error TEXT, published_at TEXT, publish_error TEXT, publish_attempts INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT, PRIMARY KEY (page_id, policy))""")
    db.commit(); db.close()
    queue = Queue(root)
    assert "retry_after" in {r[1] for r in queue.db.execute("PRAGMA table_info(supplements)")}
    assert queue.claim_supplement("w") is None


def test_the_extraction_run_is_the_pilot_run_judging_each_character_on_its_own_margin():
    from pathlib import Path

    from glyph_atlas import align
    runs = Path(__file__).resolve().parents[1] / "models" / "align" / "runs"
    extraction, pilot = align.load_run(runs / "collection-v2.yaml"), align.load_run(runs / "pilot-v1.yaml")
    assert extraction.margin_scope == "character"
    assert extraction.model_copy(update={"name": pilot.name, "margin_scope": None}) == pilot


def test_seeding_supersedes_an_earlier_policy_s_supplements_and_status_counts_only_the_current(tmp_path):
    from glyph_atlas.extraction_queue import POLICY
    queue, _ = supplement_queue(tmp_path, [("a", "complete", "single-character-consensus-v1", "飍"),
                                           ("b", "complete", "single-character-consensus-v1", "字")])
    old = "single-character-consensus-v2"
    queue.db.executemany("INSERT INTO supplements (page_id,policy,status,output,added) VALUES (?,?,?,?,?)",
                         [("a", old, "complete", "supplements/a-v2", 3), ("b", old, "pending", None, None)])
    queue.db.commit()
    assert queue.seed_supplements() == 2
    assert dict(queue.db.execute("SELECT page_id,status FROM supplements WHERE policy=?", (old,))) == {
        "a": "complete", "b": "superseded"}
    claimed = {}
    while (job := queue.claim_supplement("w")) is not None:
        assert job["policy"] == POLICY
        claimed[job["id"]] = job["earlier_supplements"]
    assert claimed == {"a": ["supplements/a-v2"], "b": []}, "a complete earlier supplement is kept off"
    status = queue.status()["supplements"]
    assert status["running"] == 2 and "superseded" not in status and "pending" not in status
    assert status["added"] == 3


def test_seeding_reads_a_page_s_report_once_and_a_page_finished_under_this_policy_needs_none(tmp_path):
    from glyph_atlas.extraction_queue import POLICY
    queue, _ = supplement_queue(tmp_path, [("a", "complete", "single-character-consensus-v1", "飍"),
                                           ("c", "pending", None, "字")])
    assert queue.seed_supplements() == 1
    (queue.root/"pages"/"a"/"report.json").unlink()
    queue.db.execute("DELETE FROM supplements")
    assert queue.seed_supplements() == 1, "the policy recorded on the page row is enough"
    queue.claim("w")
    queue.finish("c", "w", {"accepted": 0, "examined": 0, "policy": POLICY}, queue.root/"pages"/"c")
    assert queue.seed_supplements() == 0


@pytest.mark.parametrize("text,score,reason", [
    ("字", .10, None), ("字", .0999, "visual-uncertain"), (" 字\n", .5, None),
    ("子", .95, "visual-disagreement"), ("字字", .95, "visual-disagreement")])
def test_ndl_s_reading_counts_from_ten_hundredths(text, score, reason):
    ballot = votes(); ballot[1].update(text=text, score=score)
    assert quality_reason(unit(), ballot, (200, 200)) == reason


def test_a_small_kana_is_not_its_full_size_form_and_a_compatibility_ideograph_is_its_unified_one():
    kana = unit(); kana.text_source = "ゃ"
    ballot = [{"engine": "Atlas classifier", "text": "ゃ", "score": .97}, {"engine": "NDLkotenOCR", "text": "や", "score": .98}]
    assert quality_reason(kana, ballot, (200, 200)) == "visual-disagreement"
    han = unit(); han.text_source = "豈"  # U+8C48
    ballot = [{"engine": "Atlas classifier", "text": "豈", "score": .97}, {"engine": "NDLkotenOCR", "text": "豈", "score": .5}]
    assert quality_reason(han, ballot, (200, 200)) is None


def test_focused_documents_are_claimed_first_until_the_focus_is_cleared(tmp_path, monkeypatch):
    monkeypatch.setattr("glyph_atlas.images.index_path", lambda: tmp_path/"missing")
    queue = Queue(tmp_path/"queue")
    queue.seed(source(tmp_path))
    assert queue.focus({"b"}) == 2
    assert [queue.claim("w")["id"], queue.claim("w")["id"]] == ["b:0", "b:1"]
    assert queue.focus(set()) == 0
    assert queue.claim("w")["id"] == "a:0"


def test_a_focused_document_s_supplements_are_taken_first(tmp_path):
    queue, _ = supplement_queue(tmp_path, [
        ("a", "complete", "single-character-consensus-v1", "飍"),
        ("b", "complete", "single-character-consensus-v1", "飍")])
    queue.db.execute("UPDATE pages SET document_id=id")
    queue.db.commit()
    assert queue.seed_supplements() == 2
    queue.focus({"b"})
    assert queue.claim_supplement("w")["id"] == "b"


def test_a_queue_keeps_the_ndl_provider_its_first_worker_brought(tmp_path):
    queue = Queue(tmp_path / "queue")
    queue.pin("ndl_provider", "CPUExecutionProvider")
    queue.pin("ndl_provider", "CPUExecutionProvider")
    with pytest.raises(RuntimeError, match="CPUExecutionProvider, not CUDAExecutionProvider"):
        Queue(tmp_path / "queue").pin("ndl_provider", "CUDAExecutionProvider")
    queue.pin("ndl_provider", "CUDAExecutionProvider", replace=True)
    assert queue.db.execute("SELECT value FROM settings WHERE key='ndl_provider'").fetchone()[0] == "CUDAExecutionProvider"


def test_where_ndl_runs_is_part_of_a_page_identity(tmp_path):
    from types import SimpleNamespace

    from glyph_atlas.extraction_queue import engine_models, page_identity

    detector = tmp_path / "detector.onnx"
    detector.write_bytes(b"model")

    def reader(provider):
        return SimpleNamespace(engines=[{"name": "NDLkotenOCR", "sha256": "s", "provider": provider}],
                               classifier=SimpleNamespace(classes=["あ"]), alphabet="あい")

    run = SimpleNamespace(model_dump=lambda: {"detector": "d"})
    page = Page(id="a:0", document_id="a", seq=0, image="https://example.org/x.jpg", width=100, height=100)
    document = Document(id="a", title="暦")
    on_gpu, on_cpu = (page_identity(engine_models(detector, reader(p)), run, page, document, [])
                      for p in ("CUDAExecutionProvider", "CPUExecutionProvider"))
    assert on_gpu != on_cpu
def test_workers_sharing_a_queue_never_claim_the_same_page(tmp_path):
    """Claims from separate connections, as separate worker processes make them, take distinct pages."""
    import threading

    queue = Queue(tmp_path / "queue")
    with queue.db:
        queue.db.executemany("INSERT INTO pages (id,document_id,title,source,cached,rank) VALUES(?,?,?,?,?,?)",
                             [(f"p:{i}", "p", "t", "s", 1, i) for i in range(60)])
    taken, lock = [], threading.Lock()

    def worker(name):
        own = Queue(tmp_path / "queue")
        while (job := own.claim(name)) is not None:
            with lock:
                taken.append(job["id"])

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(taken) == sorted(f"p:{i}" for i in range(60))


def test_workers_starting_together_on_an_older_queue_add_each_column_once(tmp_path):
    """Opening a queue made before the lease columns, from several processes at once, migrates it once."""
    import multiprocessing
    import sqlite3

    root = tmp_path / "queue"
    root.mkdir()
    with sqlite3.connect(root / "queue.sqlite") as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("""CREATE TABLE pages (id TEXT PRIMARY KEY, document_id TEXT NOT NULL, title TEXT NOT NULL,
            source TEXT NOT NULL, cached INTEGER NOT NULL, rank INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0, output TEXT,
            accepted INTEGER NOT NULL DEFAULT 0, examined INTEGER NOT NULL DEFAULT 0, error TEXT, updated_at TEXT)""")
    context = multiprocessing.get_context("fork")
    start = context.Barrier(8)
    workers = [context.Process(target=_open_after, args=(start, root)) for _ in range(8)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert [worker.exitcode for worker in workers] == [0] * 8
    assert "lease_until" in {r[1] for r in Queue(root).db.execute("PRAGMA table_info(pages)")}


def _open_after(start, root):
    start.wait()
    Queue(root)


def test_a_claim_leaves_a_live_lease_and_takes_back_a_lapsed_one(tmp_path):
    queue = Queue(tmp_path / "queue")
    with queue.db:
        queue.db.executemany("INSERT INTO pages (id,document_id,title,source,cached,rank) VALUES(?,?,?,?,?,?)",
                             [("live", "d", "t", "s", 1, 0), ("dead", "d", "t", "s", 1, 1)])
    assert queue.claim("alive")["id"] == "live"
    assert queue.claim("stopped", lease=-1)["id"] == "dead"
    assert queue.status()["workers"] == {"alive": 1}
    taken = queue.claim("next")
    assert (taken["id"], taken["attempts"]) == ("dead", 2)
    assert queue.claim("another") is None


def test_a_lease_is_renewed_only_by_the_worker_holding_it(tmp_path):
    queue = Queue(tmp_path / "queue")
    with queue.db:
        queue.db.execute("INSERT INTO pages (id,document_id,title,source,cached,rank) VALUES('p','d','t','s',1,0)")
    queue.claim("w1", lease=-1)
    assert not queue.renew("page", "p", "w2")
    assert queue.renew("page", "p", "w1")
    assert queue.claim("w3") is None


def test_a_worker_whose_lease_was_taken_over_writes_nothing(tmp_path):
    from glyph_atlas.extraction_queue import POLICY

    queue = Queue(tmp_path / "queue")
    with queue.db:
        queue.db.execute("INSERT INTO pages (id,document_id,title,source,cached,rank) VALUES('p','d','t','s',1,0)")
    queue.claim("slow", lease=-1)
    queue.claim("fresh")
    report = {"accepted": 1, "examined": 1, "policy": POLICY}
    assert not queue.finish("p", "slow", report, queue.root/"pages"/"x")
    assert not queue.fail("p", "slow", "timeout", retryable=True)
    assert tuple(queue.db.execute("SELECT status, worker FROM pages").fetchone()) == ("running", "fresh")
    assert queue.finish("p", "fresh", report, queue.root/"pages"/"x")
    assert tuple(queue.db.execute("SELECT status, worker FROM pages").fetchone()) == ("complete", None)


def test_a_supplement_whose_lease_was_taken_over_is_left_to_its_new_worker(tmp_path):
    queue, _ = supplement_queue(tmp_path, [("a", "complete", "single-character-consensus-v1", "飍")])
    queue.seed_supplements()
    queue.claim_supplement("slow", lease=-1)
    assert queue.claim_supplement("fresh")["id"] == "a"
    assert queue.status()["workers"] == {"fresh": 1}
    assert not queue.finish_supplement("a", "slow", {"added": 0}, queue.root/"supplements"/"x")
    assert not queue.fail_supplement("a", "slow", "timeout")
    assert queue.finish_supplement("a", "fresh", {"added": 0}, queue.root/"supplements"/"x")


def test_an_uncached_page_on_a_host_another_worker_uses_waits_behind_other_pages(tmp_path):
    queue = Queue(tmp_path / "queue")
    with queue.db:
        queue.db.executemany("INSERT INTO pages (id,document_id,title,source,cached,rank,host) VALUES(?,?,?,?,?,?,?)",
                             [("a0", "a", "t", "s", 0, 0, "a.example"), ("a1", "a", "t", "s", 0, 1, "a.example"),
                              ("a2", "a", "t", "s", 1, 2, "a.example"), ("b0", "b", "t", "s", 0, 3, "b.example")])
    assert queue.claim("w1")["id"] == "a2", "a cached page goes first"
    assert queue.claim("w2")["id"] == "a0", "and asks its host for nothing"
    assert queue.claim("w3")["id"] == "b0"
    assert queue.claim("w4")["id"] == "a1", "with nothing else left, the busy host's page is taken"


def test_seeding_records_each_page_s_image_host(tmp_path, monkeypatch):
    monkeypatch.setattr("glyph_atlas.images.index_path", lambda: tmp_path/"missing")
    queue = Queue(tmp_path/"queue")
    queue.seed(source(tmp_path))
    assert {r[0] for r in queue.db.execute("SELECT host FROM pages")} == {"example.org"}


def test_a_heartbeat_retries_a_busy_database_and_stops_once_the_claim_is_lost():
    import sqlite3
    import time

    from glyph_atlas.extraction_queue import heartbeat

    class Renewals:
        def __init__(self, answers):
            self.answers, self.calls = list(answers), 0

        def renew(self, *args, **kwargs):
            self.calls += 1
            answer = self.answers.pop(0) if self.answers else True
            if isinstance(answer, Exception):
                raise answer
            return answer

    busy = Renewals([sqlite3.OperationalError("database is locked"), True, True])
    with heartbeat(busy, "page", "p", "w", lease=0.08) as beat:
        time.sleep(0.1)
    assert busy.calls >= 3 and not beat.lost.is_set()
    lost = Renewals([True, False])
    with heartbeat(lost, "page", "p", "w", lease=0.08) as beat:
        time.sleep(0.1)
    assert beat.lost.is_set() and lost.calls == 2


class CommittingEngine:
    """An engine that commits an empty extraction for each page, as a real one would, a little slowly."""

    def extract(self, job, root, *, max_lines=64):
        import time

        from glyph_atlas.extraction_queue import POLICY, commit
        time.sleep(0.01)
        report = {"accepted": 0, "examined": 0, "policy": POLICY, "page_id": job["id"]}
        return report, commit(root/"pages"/job["id"], job["id"], {"documents": [Document(id="d", title="t")]}, report)


def _work(root, name):
    run(Queue(root), CommittingEngine(), pages=100, seconds=60, supplement_every=0, worker=name)


def test_workers_running_at_once_extract_every_page_once(tmp_path, monkeypatch):
    """Four worker processes share one queue: each page is extracted and finished once, status stays readable."""
    import json
    import multiprocessing

    monkeypatch.setattr("glyph_atlas.extraction_queue.require_storage", lambda _: None)
    queue = Queue(tmp_path / "queue")
    with queue.db:
        queue.db.executemany("INSERT INTO pages (id,document_id,title,source,cached,rank) VALUES(?,?,?,?,?,?)",
                             [(f"p{i}", "d", "t", "s", 1, i) for i in range(40)])
    context = multiprocessing.get_context("fork")
    workers = [context.Process(target=_work, args=(queue.root, f"w{i}")) for i in range(4)]
    for worker in workers:
        worker.start()
    reads = 0
    while any(worker.is_alive() for worker in workers):
        if (queue.root/"status.json").exists():
            json.loads((queue.root/"status.json").read_text())
            reads += 1
    for worker in workers:
        worker.join(120)
    assert [worker.exitcode for worker in workers] == [0] * 4
    assert reads, "status.json was read while the workers wrote it"
    assert dict(queue.db.execute("SELECT status, count(*) FROM pages GROUP BY status")) == {"complete": 40}
    assert sorted(p.name for p in (queue.root/"pages").iterdir()) == sorted(f"p{i}" for i in range(40))
    assert json.loads((queue.root/"status.json").read_text())["counts"] == {"complete": 40}
    assert not list(queue.root.glob("status.json.*.tmp")) and not list((queue.root/".staging").iterdir())


def _commit_same(root, start):
    from glyph_atlas.extraction_queue import POLICY, commit
    start.wait()
    commit(root/"pages"/"same", "same", {"documents": [Document(id="d", title="t")]}, {"accepted": 0, "policy": POLICY})


def test_workers_committing_one_identity_at_once_leave_one_output(tmp_path):
    import multiprocessing

    context = multiprocessing.get_context("fork")
    start = context.Barrier(4)
    workers = [context.Process(target=_commit_same, args=(tmp_path, start)) for _ in range(4)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(60)
    assert [worker.exitcode for worker in workers] == [0] * 4
    assert sorted(p.name for p in (tmp_path/"pages"/"same").glob("[!.]*")) == ["documents.parquet", "report.json"]
    assert not list((tmp_path/".staging").iterdir())


class SlowEngine(CommittingEngine):
    """Takes longer over each page than a lease lasts, and counts every extraction of a page."""

    def extract(self, job, root, *, max_lines=64):
        import time
        (root/"extracted").mkdir(exist_ok=True)
        with open(root/"extracted"/job["id"], "a") as log:
            log.write("x")
        time.sleep(0.6)
        return super().extract(job, root, max_lines=max_lines)


def _work_slowly(root, name):
    run(Queue(root), SlowEngine(), pages=100, seconds=60, supplement_every=0, worker=name, lease=0.3)


def test_heartbeats_keep_pages_that_outlast_their_lease_with_their_workers(tmp_path, monkeypatch):
    """Two worker processes each hold a page twice as long as a lease: the heartbeat renews it, so no
    page is taken over and extracted twice."""
    import multiprocessing

    monkeypatch.setattr("glyph_atlas.extraction_queue.require_storage", lambda _: None)
    queue = Queue(tmp_path / "queue")
    with queue.db:
        queue.db.executemany("INSERT INTO pages (id,document_id,title,source,cached,rank) VALUES(?,?,?,?,?,?)",
                             [(f"p{i}", "d", "t", "s", 1, i) for i in range(4)])
    context = multiprocessing.get_context("fork")
    workers = [context.Process(target=_work_slowly, args=(queue.root, f"w{i}")) for i in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(60)
    assert [worker.exitcode for worker in workers] == [0, 0]
    assert {p.name: p.read_text() for p in (queue.root/"extracted").iterdir()} == {f"p{i}": "x" for i in range(4)}
    assert {tuple(r) for r in queue.db.execute("SELECT status, attempts FROM pages")} == {("complete", 1)}


class GatedEngine:
    """Signals that it holds its page, then waits for `gate` and either commits 7 crops or fails."""

    def __init__(self, outcome):
        self.outcome = outcome

    def extract(self, job, root, *, max_lines=64):
        import time

        from glyph_atlas.extraction_queue import POLICY, commit
        (root/"claimed").touch()
        while not (root/"gate").exists():
            time.sleep(0.01)
        if self.outcome == "fail":
            raise OSError("the stalled worker's download failed")
        report = {"accepted": 7, "examined": 7, "policy": POLICY, "page_id": job["id"]}
        return report, commit(root/"pages"/"stale", job["id"], {"documents": [Document(id="d", title="t")]}, report)


def _stall(root, outcome):
    run(Queue(root), GatedEngine(outcome), pages=1, seconds=60, supplement_every=0, worker="stalled", lease=0.5)


def _take_over(root):
    run(Queue(root), CommittingEngine(), pages=1, seconds=60, supplement_every=0, worker="fresh")


@pytest.mark.parametrize("outcome", ["finish", "fail"])
def test_a_frozen_worker_s_late_result_leaves_the_page_to_the_worker_that_took_it_over(tmp_path, monkeypatch, outcome):
    """A worker process frozen past its lease loses the page to another; thawed, its finish or fail
    writes nothing over the other worker's result."""
    import multiprocessing
    import os
    import signal
    import sqlite3
    import time

    monkeypatch.setattr("glyph_atlas.extraction_queue.require_storage", lambda _: None)
    queue = Queue(tmp_path / "queue")
    with queue.db:
        queue.db.execute("INSERT INTO pages (id,document_id,title,source,cached,rank) VALUES('p','d','t','s',1,0)")
    context = multiprocessing.get_context("fork")
    stalled = context.Process(target=_stall, args=(queue.root, outcome))
    stalled.start()
    while not (queue.root/"claimed").exists():
        time.sleep(0.01)
    # Freeze the worker outside a write of its heartbeat, or the frozen write would lock the queue.
    while True:
        os.kill(stalled.pid, signal.SIGSTOP)
        try:
            with sqlite3.connect(queue.root/"queue.sqlite", timeout=0.2) as db:
                db.execute("BEGIN IMMEDIATE")
            break
        except sqlite3.OperationalError:
            os.kill(stalled.pid, signal.SIGCONT)
            time.sleep(0.01)
    time.sleep(0.8)
    fresh = context.Process(target=_take_over, args=(queue.root,))
    fresh.start()
    fresh.join(60)
    assert fresh.exitcode == 0
    os.kill(stalled.pid, signal.SIGCONT)
    (queue.root/"gate").touch()
    stalled.join(60)
    assert stalled.exitcode == 0
    row = queue.db.execute("SELECT status, accepted, attempts, error, worker FROM pages").fetchone()
    assert tuple(row) == ("complete", 0, 2, None, None)


def test_an_unseeded_uncached_page_keeps_its_place_behind_focus_while_a_host_is_busy(tmp_path):
    """A page without a host sorts as a page on no busy host, so focus and priority still decide."""
    queue = Queue(tmp_path / "queue")
    with queue.db:
        queue.db.executemany("""INSERT INTO pages (id,document_id,title,source,cached,rank,host,focus,priority)
            VALUES(?,?,?,?,?,?,?,?,?)""", [("busy", "a", "t", "s", 0, 0, "a.example", 0, 0),
                                          ("unseeded", "b", "t", "s", 0, 1, None, 0, 0),
                                          ("focused", "c", "t", "s", 0, 2, "c.example", 1, 5)])
    assert queue.claim("w1")["id"] == "focused"
    assert queue.claim("w2")["id"] == "busy"
    queue.db.execute("UPDATE pages SET status='pending', worker=NULL WHERE id='focused'")
    queue.db.commit()
    assert queue.claim("w3")["id"] == "focused"


class Interleaved:
    """A connection that lets another worker take over page or supplement `p` right after the holder
    was checked, as a second process could between two statements."""

    def __init__(self, db, root):
        self.db, self.root = db, root

    def __getattr__(self, name):
        return getattr(self.db, name)

    def __enter__(self):
        return self.db.__enter__()

    def __exit__(self, *exc):
        return self.db.__exit__(*exc)

    def execute(self, sql, *args):
        import sqlite3
        result = self.db.execute(sql, *args)
        if sql.lstrip().startswith("SELECT attempts FROM"):
            table = "supplements" if "supplements" in sql else "pages"
            key = "page_id" if table == "supplements" else "id"
            other = sqlite3.connect(self.root / "queue.sqlite", timeout=5)
            with other:
                other.execute(f"UPDATE {table} SET worker='fresh' WHERE {key}='p'")
            other.close()
        return result


def test_a_fail_whose_claim_is_taken_over_after_the_check_writes_nothing(tmp_path):
    queue = Queue(tmp_path / "queue")
    with queue.db:
        queue.db.execute("INSERT INTO pages (id,document_id,title,source,cached,rank) VALUES('p','d','t','s',1,0)")
    queue.claim("slow")
    queue.db = Interleaved(queue.db, queue.root)
    assert not queue.fail("p", "slow", "timeout", retryable=True)
    assert tuple(queue.db.execute("SELECT status, worker, error FROM pages").fetchone()) == ("running", "fresh", None)


def test_a_supplement_fail_whose_claim_is_taken_over_after_the_check_writes_nothing(tmp_path):
    queue, _ = supplement_queue(tmp_path, [("p", "complete", "single-character-consensus-v1", "飍")])
    queue.seed_supplements()
    queue.claim_supplement("slow")
    queue.db = Interleaved(queue.db, queue.root)
    assert not queue.fail_supplement("p", "slow", "timeout")
    assert tuple(queue.db.execute("SELECT status, worker, error FROM supplements").fetchone()) == ("running", "fresh", None)


class Wall:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def test_a_page_that_stopped_its_workers_is_failed_only_once_its_lease_is_well_past(tmp_path):
    from glyph_atlas.extraction_queue import MAX_ATTEMPTS

    queue = Queue(tmp_path / "queue")
    queue.wall = wall = Wall()
    with queue.db:
        queue.db.execute("""INSERT INTO pages (id,document_id,title,source,cached,rank,status,attempts,worker,lease_until)
            VALUES('p','d','t','s',1,0,'running',?,'gone',?)""", (MAX_ATTEMPTS, wall.now - 1))
    for _ in range(3):
        assert queue.claim("w", lease=10) is None
        assert queue.db.execute("SELECT status FROM pages").fetchone()[0] == "running"
        wall.now += 4
    queue.claim("w", lease=10)
    assert queue.db.execute("SELECT status FROM pages").fetchone()[0] == "failed"


def test_after_a_clock_jump_a_worker_leaves_lapsed_leases_to_their_heartbeats_for_a_while(tmp_path):
    """A machine that slept wakes with every lease lapsed; its workers' heartbeats renew them within
    an eighth of a lease, so a worker that notices the jump claims only pending pages meanwhile."""
    queue = Queue(tmp_path / "queue")
    queue.wall = wall = Wall()
    with queue.db:
        queue.db.executemany("INSERT INTO pages (id,document_id,title,source,cached,rank) VALUES(?,?,?,?,?,?)",
                             [("held", "d", "t", "s", 1, 0), ("next", "d", "t", "s", 1, 1), ("last", "d", "t", "s", 1, 2)])
    assert queue.claim("sleeper", lease=10)["id"] == "held"
    wall.now += 3600
    assert queue.claim("other", lease=10)["id"] == "next"
    wall.now += 3
    assert queue.claim("other", lease=10)["id"] == "held"


def test_a_heartbeat_stops_renewing_a_page_that_ran_past_its_deadline():
    import time

    from glyph_atlas.extraction_queue import heartbeat

    class Renewals:
        calls = 0

        def renew(self, *args, **kwargs):
            self.calls += 1
            return True

    hung = Renewals()
    with heartbeat(hung, "page", "p", "w", lease=0.08, deadline=0.2) as beat:
        time.sleep(0.5)
        calls = hung.calls
        time.sleep(0.2)
    assert beat.lost.is_set() and hung.calls == calls


def test_a_worker_start_sweeps_scratch_files_older_than_a_lease(tmp_path):
    import os
    import time

    queue = Queue(tmp_path / "queue")
    stale = queue.root/".staging"/"x.dead"
    fresh = queue.root/".staging"/"y.live"
    for directory in (stale, fresh):
        directory.mkdir(parents=True)
        (directory/"report.json").write_text("{}")
    (queue.root/"status.json.dead.tmp").write_text("{")
    (queue.root/"status.json.live.tmp").write_text("{")
    old = time.time() - 2 * 600
    for path in (stale, stale/"report.json", queue.root/"status.json.dead.tmp"):
        os.utime(path, (old, old))
    queue.sweep(age=600)
    assert sorted(p.name for p in (queue.root/".staging").iterdir()) == ["y.live"]
    assert [p.name for p in queue.root.glob("*.tmp")] == ["status.json.live.tmp"]
