"""Form clustering: which families are clustered, into how many clusters, and how."""
import pytest

from glyph_atlas import form_clusters


def test_only_families_with_several_members_are_clustered():
    assert form_clusters.family_of("U+306F")["code_point"] == "U+306F"
    assert form_clusters.family_of("U+1B09E")["code_point"] == "U+306F"
    assert form_clusters.family_of(None) is None


def test_cluster_count_grows_slowly_and_is_capped():
    assert form_clusters.cluster_count(5) == 1
    assert form_clusters.cluster_count(40) == 2
    assert form_clusters.cluster_count(28518) == 42
    assert form_clusters.cluster_count(10**7) == form_clusters.MAX_CLUSTERS


def test_kmeans_separates_distinct_shapes_and_is_deterministic():
    torch = pytest.importorskip("torch")
    generator = torch.Generator().manual_seed(0)
    axes = torch.eye(8)[:3]
    points = torch.cat([axes[i] + 0.05 * torch.randn(50, 8, generator=generator) for i in range(3)])
    points = torch.nn.functional.normalize(points, dim=1)
    labels, centres = form_clusters._kmeans(points, 3, seed=5)
    again, _ = form_clusters._kmeans(points, 3, seed=5)
    assert torch.equal(labels, again)
    for i in range(3):
        assert len(set(labels[i * 50:(i + 1) * 50].tolist())) == 1
    assert len(set(labels.tolist())) == 3
    assert torch.allclose(centres.norm(dim=1), torch.ones(3), atol=1e-5)


def test_shape_order_puts_similar_centres_next_to_each_other():
    import numpy as np

    a, b = np.eye(4)[0], np.eye(4)[1]
    centres = np.stack([a, b, a + 0.1 * b, b + 0.1 * a, a + 0.2 * b])
    centres /= np.linalg.norm(centres, axis=1, keepdims=True)
    order = form_clusters.shape_order(centres)
    assert sorted(order) == [0, 1, 2, 3, 4]
    near_a = {order.index(i) for i in (0, 2, 4)}
    assert max(near_a) - min(near_a) == 2


def test_glyphs_pool_the_corpora_that_leave_the_form_unrecorded(form_corpora):
    root, ids = form_corpora
    found = {glyph["id"]: glyph for glyph in form_clusters.glyphs(root)}
    assert set(found) == {*ids["held"], ids["unheld"], ids["crop"]}, "古活字 names its forms and is left out"
    assert {glyph["family"] for glyph in found.values()} == {"U+306F"}
    pixels = form_clusters.Pixels(root)
    page, box = pixels(found[ids["held"][0]])
    assert page.is_file() and box == (0, 0, 50, 50)
    crop, none = pixels(found[ids["crop"]])
    assert crop.name == "1.jpg" and none is None
    assert pixels(found[ids["unheld"]]) is None


def test_a_crop_file_is_embedded_whole(form_corpora):
    pytest.importorskip("torch")
    root, _ = form_corpora
    pixels = form_clusters.Pixels(root)
    located = {g["id"]: pixels(g) for g in form_clusters.glyphs(root) if pixels(g)}
    jobs = form_clusters._file_jobs(located)
    assert sorted(len(boxes) for _, boxes in jobs) == [1, 2], "one job per page scan and per crop file"
    for job in jobs:
        ids_done, arrays = form_clusters._crops(job)
        assert len(ids_done) == len(job[1]) and arrays.shape[0] == len(ids_done)


def test_a_page_harvested_later_is_found(form_corpora, tmp_path):
    from PIL import Image

    from glyph_atlas import images

    root, ids = form_corpora
    pixels = form_clusters.Pixels(root)
    glyph = next(g for g in form_clusters.glyphs(root) if g["id"] == ids["unheld"])
    assert pixels(glyph) is None
    Image.new("RGB", (100, 100), "white").save(tmp_path / "other.png")
    images.register(tmp_path / "other.png", "https://example.org/iiif/other.tif")
    assert pixels(glyph) is not None


def test_workers_stay_a_bounded_distance_ahead_of_the_consumer():
    from concurrent.futures import Future

    class Pool:
        submitted = 0

        def submit(self, fn, job):
            self.submitted += 1
            future = Future()
            future.set_result(fn(job))
            return future

    pool = Pool()
    results = form_clusters._bounded_map(pool, lambda n: n * n, range(100), ahead=8)
    consumed = []
    for value in results:
        consumed.append(value)
        assert pool.submitted - len(consumed) < 8
    assert consumed == [n * n for n in range(100)]
