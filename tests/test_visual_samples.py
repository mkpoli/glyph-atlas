import hashlib
import importlib.util
from collections import Counter
from pathlib import Path

import httpx
import pytest

from glyph_atlas.visual_samples import BudgetClient, balanced, choose, families


def test_work_balancing_is_stable_under_source_order_and_caps_sources():
    rows = [{"id": f"{family}:{source}:{work}:{index}", "family": family, "corpus": source,
             "document_id": work, "sampling_stratum": work}
            for family in ("a", "b") for source in ("codh", "hi")
            for work, size in (("large", 50), ("small", 3)) for index in range(size)]
    result = choose(rows, per_source=8, maximum=24)
    assert [row["id"] for row in result] == [row["id"] for row in choose(rows[::-1], 8, 24)]
    assert Counter((row["family"], row["corpus"]) for row in result) == {
        (family, source): 6 for family in ("a", "b") for source in ("codh", "hi")}
    assert Counter(row["document_id"] for row in balanced(rows[:53], 6)) == {"large": 3, "small": 3}


def test_variant_families_use_shared_canonical_keys():
    assert families("仮假") == {"U+4EEE": ["U+4EEE", "U+5047"]}


class NeverRead(httpx.SyncByteStream):
    def __iter__(self):
        raise AssertionError("unbounded body must never be downloaded")


def test_ignored_zip_range_is_rejected_before_body_is_read():
    client = BudgetClient(maximum=100, pause=0)
    client.client.close()
    client.client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, stream=NeverRead(), request=request)))
    try:
        with pytest.raises(RuntimeError, match="did not honor"):
            client.get("https://example.org/archive.zip", headers={"Range": "bytes=0-9"})
        assert client.used == 0
    finally:
        client.close()


def test_range_larger_than_budget_is_rejected_without_a_request():
    client = BudgetClient(maximum=5, pause=0)
    try:
        with pytest.raises(RuntimeError, match="remaining network budget"):
            client.get("https://example.org/archive.zip", headers={"Range": "bytes=100-109"})
        assert client.requests == 0
    finally:
        client.close()


@pytest.mark.parametrize("headers", [
    {"Content-Range": "bytes 0-9/100"},
    {"Content-Range": "bytes 1-10/100", "Content-Length": "10"},
    {"Content-Range": "anything", "Content-Length": "10"},
])
def test_malformed_partial_response_is_rejected_before_body(headers):
    client = BudgetClient(maximum=10, pause=0)
    client.client.close()
    client.client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(206, headers=headers, stream=NeverRead(), request=request)))
    try:
        with pytest.raises(RuntimeError, match="body not downloaded"):
            client.get("https://example.org/archive.zip", headers={"Range": "bytes=0-9"})
        assert client.used == 0
    finally:
        client.close()


def test_embedding_refuses_changed_crop_or_escaped_path(tmp_path):
    spec = importlib.util.spec_from_file_location("embedding_script", Path("scripts/embed_variant_samples.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = tmp_path / "sample.jpg"
    path.write_bytes(b"original")
    row = {"row_index": 0, "image_path": path.name, "crop_sha256": hashlib.sha256(b"original").hexdigest()}
    assert module.image_paths(tmp_path, [row]) == [path]
    path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        module.image_paths(tmp_path, [row])
    row["image_path"] = "../outside.jpg"
    with pytest.raises(ValueError, match="outside"):
        module.image_paths(tmp_path, [row])
