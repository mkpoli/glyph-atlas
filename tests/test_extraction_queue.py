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
    assert queue.claim()["id"] == "a:0"
    assert queue.claim()["id"] == "b:0"
    queue.recover()
    assert queue.claim()["id"] == "a:0"


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
    result=run(queue,Broken(),pages=1,pause=0)
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
    monkeypatch.setattr("glyph_atlas.extraction_queue.time.time",lambda:1000)
    assert queue.claim()["id"] == "a"
    queue.fail("a","timeout",retryable=True)
    assert queue.claim() is None
    monkeypatch.setattr("glyph_atlas.extraction_queue.time.time",lambda:1100)
    assert queue.claim()["id"] == "a"
    queue.fail("a","timeout",retryable=True)
    monkeypatch.setattr("glyph_atlas.extraction_queue.time.time",lambda:1300)
    assert queue.claim()["id"] == "a"
    queue.fail("a","timeout",retryable=True)
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
        assert queue.claim()["id"] == "a:0"
        queue.recover()  # the worker died while extracting a:0
    assert queue.claim()["id"] == "a:1"
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
    assert queue.claim()["id"] == "b"


def test_prioritize_scores_each_page_from_the_dataset_it_was_seeded_from(tmp_path, monkeypatch):
    monkeypatch.setattr("glyph_atlas.images.index_path", lambda: tmp_path / "missing")
    queue = Queue(tmp_path / "queue")
    common = lines_source(tmp_path, [Line(id="l1", page_id="a", seq=0, text_raw="の", text="の")], name="common")
    rare = lines_source(tmp_path, [Line(id="l2", page_id="b", seq=0, text_raw="ヿ", text="ヿ")], name="rare")
    seeded(queue, [("a", common, 0), ("b", rare, 1)])
    assert queue.prioritize({"の": 1000}) == 2
    assert queue.db.execute("SELECT priority FROM pages WHERE id='b'").fetchone()[0] == 1.0
    assert queue.claim()["id"] == "b"


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
    assert queue.claim()["id"] == "b"


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
    assert queue.claim()["id"] == "a"


def test_the_pause_follows_only_a_page_that_fetched_its_image(tmp_path, monkeypatch):
    monkeypatch.setattr("glyph_atlas.extraction_queue.require_storage", lambda _: None)
    slept=[]
    monkeypatch.setattr("glyph_atlas.extraction_queue.time.sleep", slept.append)
    queue=Queue(tmp_path/"queue")
    # Claimed a, c (cached), then b, d: only b both fetched its image and has a page after it.
    for ident,cached,rank in (("a",1,0),("b",0,1),("c",1,2),("d",0,3)):
        queue.db.execute("INSERT INTO pages(id,document_id,title,source,cached,rank) VALUES(?,?,?,?,?,?)",
                         (ident,ident,ident.upper(),"s",cached,rank))
    queue.db.commit()
    class Broken:
        def extract(self,*args,**kwargs):
            raise ValueError("unavailable")
    run(queue,Broken(),pages=4,pause=7)
    assert slept == [7]


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
    assert {queue.claim_supplement()["output"], queue.claim_supplement()["output"]} == {"pages/old-rare", "pages/old-known"}
    assert queue.claim_supplement() is None
    queue.recover()
    assert {r[0] for r in queue.db.execute("SELECT status FROM supplements")} == {"pending"}


def test_a_supplement_keeps_new_units_off_published_crops(tmp_path):
    from glyph_atlas.extraction_queue import commit, supplement
    root = tmp_path/"queue"
    document, page = Document(id="d", title="D"), Page(id="d:0", document_id="d", seq=0, image="x", width=400, height=100)
    line = Line(id="d:0:L", page_id="d:0", seq=0, text_raw="字飍飍", text="字飍飍", box=Box(x=0, y=0, w=400, h=100))

    def placed(ident, x, gate):
        return Unit(id=ident, document_id="d", page_id="d:0", line_id="d:0:L", reading="飍", text_source="飍",
                    box=Box(x=x, y=10, w=40, h=45), meta={"extraction": {"gate": gate}})
    records = {"documents": [document], "pages": [page], "lines": [line]}
    commit(root/"pages"/"old", "old", {**records, "units": [placed("old:1", 10, "consensus")]}, {})

    class Engine:
        def extract(self, job, into, max_lines=64):
            units = [placed("new:1", 10, "consensus"), placed("new:2", 12, "unconfirmed"),
                     placed("new:3", 200, "unconfirmed"), placed("new:4", 300, "consensus")]
            return {"generation": "new"}, commit(into/"pages"/"new", "new", {**records, "units": units}, {})

    report, output = supplement(Engine(), {"id": "d:0", "output": "pages/old"}, root)
    assert report["added"] == 2 and output.parent.name == "supplements"
    # Either gate adds a crop the earlier output did not publish; nothing lands on a published one.
    assert [u.id for u in tables.Dataset(output).read("units")] == ["new:3", "new:4"]
    assert not tables.Dataset(output).validate()
    # A later policy's supplement also leaves alone what an earlier supplement published.
    commit(root/"supplements"/"prior", "prior", {**records, "units": [placed("prior:1", 205, "unconfirmed"),
                                                                       placed("prior:2", 302, "consensus")]}, {})
    report, output = supplement(
        Engine(), {"id": "d:0", "output": "pages/old", "earlier_supplements": ["supplements/prior"]}, root)
    assert report["added"] == 0 and tables.Dataset(output).read("units") == []


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
            return {"accepted": 1, "examined": 1}, root/"pages"/job["id"]

    def fake_supplement(engine, job, root, max_lines=64):
        order.append(("supplement", job["id"]))
        return {"added": 2}, root/"supplements"/job["id"]
    monkeypatch.setattr(extraction_queue, "supplement", fake_supplement)
    result = run(queue, Engine(), pages=4, pause=0)
    assert order == [("page", "p1"), ("supplement", "s1"), ("page", "p2"), ("supplement", "s2")]
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
    monkeypatch.setattr(extraction_queue.time, "time", lambda: now[0])
    for attempt in range(extraction_queue.MAX_ATTEMPTS):
        assert queue.claim_supplement()["id"] == "a"
        queue.fail_supplement("a", "DownloadError: unavailable")
        assert queue.claim_supplement() is None
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
    assert queue.claim_supplement() is None


def test_the_extraction_run_is_the_pilot_run_judging_each_character_on_its_own_margin():
    from pathlib import Path

    from glyph_atlas import align
    runs = Path(__file__).resolve().parents[1] / "models" / "align" / "runs"
    extraction, pilot = align.load_run(runs / "collection-v2.yaml"), align.load_run(runs / "pilot-v1.yaml")
    assert extraction.margin_scope == "character"
    assert extraction.model_copy(update={"name": pilot.name, "margin_scope": None}) == pilot
