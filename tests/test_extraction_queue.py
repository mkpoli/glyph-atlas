from glyph_atlas import tables
from glyph_atlas.extraction_queue import Queue, quality_reason, run, unique_units
from glyph_atlas.schema import Box, Document, Page, ReviewState, Unit


def unit(**kwargs):
    return Unit(id="test", text_source="字", reading="字", box=Box(x=10,y=10,w=30,h=35), **kwargs)


def votes(text="字"):
    return [{"engine":"Atlas classifier","text":text,"score":.97},
            {"engine":"NDLkotenOCR","text":text,"score":.98}]


def test_gate_rejects_joined_disagreement_and_unaligned():
    assert quality_reason(unit(),votes(),(200,200)) is None
    assert quality_reason(unit(),votes("字字"),(200,200)) == "visual-disagreement"
    assert quality_reason(unit(review=ReviewState.REJECTED),votes(),(200,200)) == "alignment-uncertain"
    low=votes(); low[1]["score"]=.69
    assert quality_reason(unit(),low,(200,200)) == "visual-uncertain"
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
    tables.write(directory/"documents.parquet",docs,Document)
    tables.write(directory/"pages.parquet",pages,Page)
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
