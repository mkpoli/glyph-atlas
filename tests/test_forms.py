"""Form decisions: what a cluster or glyph decision assigns, and what is refused."""
import json
import os
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from glyph_atlas import forms
from glyph_atlas.corpus.identity import identity_fields

A, B, C, D = (f"codh:book:page:B0001:C000{i}" for i in range(4))


@pytest.fixture
def clustering():
    directory = Path(os.environ["ATLAS_FORM_CLUSTERS"])
    directory.mkdir(parents=True)
    clusters = [{"id": "U+306F:one", "label": "Cluster 1", "count": 3, "coherence": .9, "representatives": [A, B, C]},
                {"id": "U+306F:two", "label": "Cluster 2", "count": 1, "coherence": .9, "representatives": [D]}]
    (directory / "clusters.json").write_text(json.dumps({"revision": "r1", "families": {"U+306F": {
        "family": "U+306F", "char": "は", "label": "は", "members": [], "count": 4, "clusters": clusters}}}))
    pq.write_table(pa.table({"id": [A, B, C, D], "family": ["U+306F"] * 4,
                             "cluster": ["U+306F:one"] * 3 + ["U+306F:two"],
                             "similarity": [.95, .9, .8, 1.0], "rank": [0, 1, 2, 0]}), directory / "units.parquet")
    return directory


def test_a_cluster_decision_names_every_member_and_a_glyph_decision_overrides_it(clustering):
    forms.record("cluster", cluster="U+306F:one", form="𛂥")
    forms.record("glyph", units=[B], form="𛂞")
    forms.record("glyph", units=[C], form=None)
    assert forms.form_for(A)["form"] == "𛂥" and forms.form_for(A)["basis"] == "form_cluster"
    assert forms.form_for(B) == {**forms.form_for(B), "form": "𛂞", "basis": "form_glyph"}
    assert forms.form_for(C)["form"] is None
    assert forms.form_for(D) is None
    # A later cluster decision does not undo a glyph's own decision; following the cluster does.
    forms.record("cluster", cluster="U+306F:one", form="𛂦")
    assert forms.form_for(B)["form"] == "𛂞"
    forms.record("inherit", units=[B])
    assert forms.form_for(B)["form"] == "𛂦"
    forms.record("cluster", cluster="U+306F:one", form=None)
    assert forms.form_for(A) is None and forms.form_for(C)["form"] is None


def test_a_decision_names_the_glyphs_it_covered(clustering):
    event = forms.record("cluster", cluster="U+306F:one", form="𛂥")
    line = json.loads(Path(os.environ["ATLAS_FORM_DECISIONS"]).read_text().splitlines()[-1])
    assert line["units"] == [A, B, C] == event["units"] and line["revision"] == "r1"


@pytest.mark.parametrize(("kind", "arguments", "message"), [
    ("cluster", {"cluster": "U+306F:none", "form": "𛂥"}, "Unknown cluster"),
    ("cluster", {"cluster": "U+306F:one", "form": "あ"}, "not a form"),
    ("glyph", {"units": ["codh:other"], "form": "𛂥"}, "not in the current clustering"),
    ("glyph", {"units": [], "form": "𛂥"}, "Choose between"),
    ("inherit", {"units": [A], "form": "𛂥"}, "takes no form"),
])
def test_invalid_decisions_are_refused_and_not_written(clustering, kind, arguments, message):
    with pytest.raises(forms.DecisionError, match=message):
        forms.record(kind, **arguments)
    assert not Path(os.environ["ATLAS_FORM_DECISIONS"]).exists()


def test_a_decided_form_becomes_the_written_character_below_a_human_review(clustering):
    row = {"id": A, "unicode": "U+306F", "text_source": "は", "page_id": "codh:book:page"}
    assert identity_fields(row, "codh-full")["identity_status"] == "unassigned"
    forms.record("cluster", cluster="U+306F:one", form="𛂥")
    fields = identity_fields(row, "codh-full")
    assert (fields["written_character"], fields["identity_basis"]) == ("𛂥", "form_cluster")
    assert fields["form_cluster"]["id"] == "U+306F:one"
    reviewed = identity_fields(row, "codh-full", human_character="𛂞")
    assert (reviewed["written_character"], reviewed["identity_basis"]) == ("𛂞", "human_review")
    forms.record("glyph", units=[A], form=None)
    excluded = identity_fields(row, "codh-full")
    assert excluded["written_character"] is None and excluded["identity_basis"] == "form_glyph"


def test_the_api_lists_clusters_and_records_decisions(clustering, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from glyph_atlas.review.forms import router

    app = FastAPI()
    app.include_router(router(media=None, corpus_root=tmp_path))
    client = TestClient(app)
    assert client.get("/atlas/forms/families").json()["items"][0] == {
        "code_point": "U+306F", "char": "は", "label": "は", "count": 4, "clusters": 2, "assigned": 0, "rejected": 0}
    family = client.get("/atlas/forms/families/U+306F").json()
    assert [c["id"] for c in family["items"]] == ["U+306F:one", "U+306F:two"]
    assert {"char": "𛂥", "code_point": "U+1B0A5"}.items() <= next(f for f in family["forms"] if f["char"] == "𛂥").items()
    response = client.post("/atlas/forms/decisions", json={"kind": "cluster", "cluster": "U+306F:one", "form": "𛂥"})
    assert response.status_code == 200 and response.json()["count"] == 3 and "units" not in response.json()
    assert client.post("/atlas/forms/decisions", json={"kind": "glyph", "units": [D], "form": "あ"}).status_code == 422
    members = client.get("/atlas/forms/clusters/U+306F:one", params={"limit": 2}).json()
    assert members["form"] == "𛂥" and members["total"] == 3
    assert [(m["id"], m["form"], m["basis"]) for m in members["items"]] == [(A, "𛂥", "form_cluster"), (B, "𛂥", "form_cluster")]
    assert client.get("/atlas/forms/families").json()["items"][0]["assigned"] == 3
    # The majority form comes with how many glyphs have it, so a cluster is accepted only when it covers most of them.
    client.post("/atlas/forms/decisions", json={"kind": "glyph", "units": [B], "form": "𛂞"})
    one = client.get("/atlas/forms/families/U+306F").json()["items"][0]
    assert (one["majority"], one["majority_count"], one["count"]) == ("𛂥", 2, 3)


def test_an_unfinished_last_line_is_not_a_decision_but_a_broken_one_is_reported(clustering):
    forms.record("cluster", cluster="U+306F:one", form="𛂥")
    log = Path(os.environ["ATLAS_FORM_DECISIONS"])
    with log.open("a") as handle:
        handle.write('{"kind":"glyph","units":["' + A)
    forms._DECISIONS.invalidate()
    assert forms.form_for(A)["form"] == "𛂥"
    log.write_text('{"kind": broken}\n' + log.read_text())
    forms._DECISIONS.invalidate()
    with pytest.raises(forms.DecisionLogError, match=":1 is not a decision"):
        forms.form_for(A)


def test_a_cluster_is_labelled_only_by_decisions_of_the_current_clustering(clustering):
    forms.record("cluster", cluster="U+306F:one", form="𛂥")
    assert forms.cluster_decisions() == {"U+306F:one": {"form": "𛂥", "issue": None}}
    summary = json.loads((clustering / "clusters.json").read_text())
    summary["revision"] = "r2"
    (clustering / "clusters.json").write_text(json.dumps(summary))
    forms._CLUSTERS.invalidate()
    # The glyphs keep their form; the new clustering's cluster has not been named.
    assert forms.cluster_decisions() == {} and forms.form_for(A)["form"] == "𛂥"
def test_cluster_members_can_be_listed_least_typical_first(clustering, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from glyph_atlas.review.forms import router

    app = FastAPI()
    app.include_router(router(media=None, corpus_root=tmp_path))
    client = TestClient(app)
    unusual = client.get("/atlas/forms/clusters/U+306F:one", params={"order": "unusual", "limit": 2}).json()
    assert [m["id"] for m in unusual["items"]] == [C, B] and unusual["total"] == 3


def test_glyphs_reported_as_another_character_leave_the_family(clustering, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from glyph_atlas.review.forms import router

    app = FastAPI()
    app.include_router(router(media=None, corpus_root=tmp_path))
    client = TestClient(app)
    forms.record("cluster", cluster="U+306F:one", form="𛂥")
    response = client.post("/atlas/forms/decisions", json={"kind": "glyph", "units": [B], "issue": "character",
                                                     "character": "テ", "client_id": "me"})
    assert response.status_code == 200 and response.json()["count"] == 1
    assert (forms.form_for(B)["form"], forms.form_for(B)["issue"], forms.form_for(B)["character"]) == (None, "character", "テ")
    members = client.get("/atlas/forms/clusters/U+306F:one").json()["items"]
    assert {m["id"]: (m["reported"], m["character"]) for m in members} == {
        A: (None, None), B: ("character", "テ"), C: (None, None)}
    family = client.get("/atlas/forms/families/U+306F").json()
    assert (family["assigned"], family["rejected"], family["items"][0]["rejected"]) == (2, 1, 1)
    # Following the cluster again takes the report back.
    client.post("/atlas/forms/decisions", json={"kind": "inherit", "units": [B]})
    assert forms.form_for(B)["form"] == "𛂥" and forms.form_for(B).get("issue") is None


def test_a_report_is_only_a_glyph_or_cluster_without_a_form(clustering):
    import pytest as _pytest

    for kind, fields in (("cluster", {"cluster": "U+306F:one", "form": "𛂥"}), ("glyph", {"units": [A], "form": "𛂥"}),
                         ("glyph", {"units": [A], "issue": "mixed"}), ("inherit", {"units": [A]}),
                         ("cluster", {"cluster": "U+306F:one", "issue": "mixed", "character": "テ"}),
                         ("glyph", {"units": [A], "issue": "other"}), ("glyph", {"units": [A], "issue": "crop", "character": "テ"})):
        with _pytest.raises(forms.DecisionError):
            forms.record(kind, **fields, **({"issue": "character"} if "issue" not in fields else {}))


def test_a_character_reported_for_a_glyph_is_what_it_shows(clustering):
    from glyph_atlas.corpus.identity import identity_fields

    forms.record("glyph", units=[A], issue="character", character="テ")
    fields = identity_fields({"id": A, "unicode": "U+306F"}, "codh-full")
    assert (fields["written_character"], fields["identity_basis"], fields["grapheme"]) == ("テ", "form_glyph", "U+3066")


def test_clusters_can_be_listed_with_similar_shapes_together(clustering, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from glyph_atlas.review.forms import router

    (clustering / "neighbours.json").write_text(json.dumps({"U+306F": {
        "order": ["U+306F:two", "U+306F:one"],
        "nearest": {"U+306F:one": {"id": "U+306F:two", "similarity": 0.8},
                    "U+306F:two": {"id": "U+306F:one", "similarity": 0.8}}}}))
    forms._CLUSTERS.invalidate()
    app = FastAPI()
    app.include_router(router(media=None, corpus_root=tmp_path))
    client = TestClient(app)
    shape = client.get("/atlas/forms/families/U+306F").json()
    assert [c["id"] for c in shape["items"]] == ["U+306F:two", "U+306F:one"]
    assert shape["items"][1]["nearest"] == {"id": "U+306F:two", "similarity": 0.8, "label": "Cluster 2"}
    size = client.get("/atlas/forms/families/U+306F", params={"order": "size"}).json()
    assert [c["id"] for c in size["items"]] == ["U+306F:one", "U+306F:two"]


def test_shape_runs_put_the_heaviest_run_of_similar_clusters_first():
    from glyph_atlas.review.forms import shape_runs

    clusters = [{"id": i, "count": n} for i, n in (("odd", 2), ("faint", 3), ("big", 900), ("near", 400), ("far", 50))]
    near = {"order": ["odd", "faint", "near", "big", "far"], "adjacent": [0.2, 0.3, 0.95, 0.4]}
    assert shape_runs(near, clusters) == ["big", "near", "far", "faint", "odd"]


def test_a_cluster_splits_by_shape_into_groups_of_its_own_glyphs(clustering, tmp_path):
    import numpy as np
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from glyph_atlas.review.forms import router

    # Rows follow units.parquet: A and B share a shape, C differs; D is the other cluster.
    np.save(clustering / "embeddings.npy", np.array([[1, 0, 0], [0.99, 0.1, 0], [0, 1, 0], [0, 0, 1]], np.float16))
    forms._CLUSTERS.invalidate()
    app = FastAPI()
    app.include_router(router(media=None, corpus_root=tmp_path))
    client = TestClient(app)
    split = client.get("/atlas/forms/split/U+306F:one", params={"k": 2}).json()
    assert [sorted(g["ids"]) for g in split["groups"]] == [[A, B], [C]]
    assert split == client.get("/atlas/forms/split/U+306F:one", params={"k": 2}).json()
    assert client.get("/atlas/forms/split/U+306F:none", params={"k": 2}).status_code == 404


def test_a_cluster_is_marked_mixed_or_reported_whole(clustering, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from glyph_atlas.review.forms import router

    app = FastAPI()
    app.include_router(router(media=None, corpus_root=tmp_path))
    client = TestClient(app)
    forms.record("cluster", cluster="U+306F:one", form="𛂥")
    forms.record("glyph", units=[B], form="𛂞")
    # Mixed withdraws the cluster's form and names nothing; a glyph's own decision stays.
    assert client.post("/atlas/forms/decisions", json={"kind": "cluster", "cluster": "U+306F:one", "issue": "mixed"}).status_code == 200
    assert forms.form_for(A) is None and forms.form_for(B)["form"] == "𛂞"
    family = client.get("/atlas/forms/families/U+306F").json()
    assert (family["items"][0]["form"], family["items"][0]["issue"], family["rejected"]) == (None, "mixed", 0)
    # A cluster reported as another character reports every glyph that follows it.
    forms.record("cluster", cluster="U+306F:one", issue="character", character="テ")
    assert (forms.form_for(A)["issue"], forms.form_for(A)["character"], forms.form_for(A)["basis"]) == ("character", "テ", "form_cluster")
    assert forms.form_for(B)["form"] == "𛂞" and forms.form_for(B).get("issue") is None
    fields = identity_fields({"id": A, "unicode": "U+306F"}, "codh-full")
    assert (fields["written_character"], fields["grapheme"]) == ("テ", "U+3066")
    members = client.get("/atlas/forms/clusters/U+306F:one").json()
    assert members["issue"] == "character" and {m["id"]: m["reported"] for m in members["items"]} == {A: "character", B: None, C: "character"}
    assert client.get("/atlas/forms/families/U+306F").json()["rejected"] == 2
    # Clearing the cluster takes the report back.
    forms.record("cluster", cluster="U+306F:one", form=None)
    assert forms.form_for(A) is None


def test_a_decision_answers_with_the_decisions_that_restore_what_it_changed(clustering, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from glyph_atlas.review.forms import router

    app = FastAPI()
    app.include_router(router(media=None, corpus_root=tmp_path))
    client = TestClient(app)
    decide = lambda decision: client.post("/atlas/forms/decisions", json=decision).json()
    replay = lambda undo: [decide(decision) for decision in undo]
    # A cluster never named is cleared again; a named one gets its form back.
    first = decide({"kind": "cluster", "cluster": "U+306F:one", "form": "𛂥"})
    assert first["undo"] == [{"kind": "cluster", "cluster": "U+306F:one", "form": None}]
    second = decide({"kind": "cluster", "cluster": "U+306F:one", "issue": "character", "character": "テ"})
    replay(second["undo"])
    assert forms.form_for(A)["form"] == "𛂥" and forms.form_for(A).get("issue") is None
    replay(decide({"kind": "cluster", "cluster": "U+306F:one", "form": None})["undo"])
    assert forms.form_for(A)["form"] == "𛂥"
    # Glyphs get back their own decisions, and those that followed the cluster follow it again.
    decide({"kind": "glyph", "units": [B], "form": "𛂞"})
    decide({"kind": "glyph", "units": [C], "issue": "crop"})
    changed = decide({"kind": "glyph", "units": [A, B, C], "form": None})
    assert sorted(len(d["units"]) for d in changed["undo"]) == [1, 1, 1]
    replay(changed["undo"])
    assert forms.form_for(A)["basis"] == "form_cluster" and forms.form_for(A)["form"] == "𛂥"
    assert forms.form_for(B)["form"] == "𛂞" and forms.form_for(C)["issue"] == "crop"


def test_undo_restores_a_review_inherited_by_a_filtered_clustering(clustering, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from glyph_atlas.review.forms import router

    forms.record("cluster", cluster="U+306F:one", issue="mixed")
    manifest = clustering / "clusters.json"
    data = json.loads(manifest.read_text())
    manifest.write_text(json.dumps({**data, "revision": "filtered", "parents": ["r1"]}))
    forms._CLUSTERS.invalidate()
    assert forms.cluster_decisions()["U+306F:one"]["issue"] == "mixed"

    app = FastAPI()
    app.include_router(router(media=None, corpus_root=tmp_path))
    client = TestClient(app)
    response = client.post("/atlas/forms/decisions", json={
        "kind": "cluster", "cluster": "U+306F:one", "form": "𛂥",
    })
    assert response.status_code == 200
    assert forms.form_for(A)["form"] == "𛂥"
    for decision in response.json()["undo"]:
        assert client.post("/atlas/forms/decisions", json=decision).status_code == 200
    assert forms.cluster_decisions()["U+306F:one"] == {"form": None, "issue": "mixed"}
    assert forms.form_for(A) is None


def test_a_cluster_lists_its_least_typical_glyphs_past_the_typical_twelve(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from glyph_atlas.review.forms import router

    directory = Path(os.environ["ATLAS_FORM_CLUSTERS"])
    directory.mkdir(parents=True)
    ids = [f"codh:book:page:B0001:C{i:04d}" for i in range(30)]
    clusters = [{"id": "U+306F:one", "label": "Cluster 1", "count": 30, "coherence": .9, "representatives": ids[:12]}]
    (directory / "clusters.json").write_text(json.dumps({"revision": "r1", "families": {"U+306F": {
        "family": "U+306F", "char": "は", "label": "は", "members": [], "count": 30, "clusters": clusters}}}))
    pq.write_table(pa.table({"id": ids, "family": ["U+306F"] * 30, "cluster": ["U+306F:one"] * 30,
                             "similarity": [1 - i / 100 for i in range(30)], "rank": list(range(30))}),
                   directory / "units.parquet")
    forms.record("glyph", units=[ids[28]], issue="crop")
    app = FastAPI()
    app.include_router(router(media=None, corpus_root=tmp_path))
    unusual = TestClient(app).get("/atlas/forms/families/U+306F").json()["items"][0]["unusual"]
    # Least typical first, without the reported glyph, and never one of the twelve the card already shows.
    assert [g["id"] for g in unusual] == [ids[29], *ids[27:16:-1]]
