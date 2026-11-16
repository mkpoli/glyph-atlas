from glyph_atlas.site import PASS_HEADER, pass_headers


def test_the_pass_goes_to_the_site(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PASS", "secret")
    assert pass_headers("https://glyphatlas.org/atlas/reviews.json") == {PASS_HEADER: "secret"}


def test_the_pass_goes_to_no_other_host(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PASS", "secret")
    assert pass_headers("https://example.org/atlas/reviews.json") == {}
    assert pass_headers("https://glyphatlas.org.example.org/") == {}
    assert pass_headers("http://localhost:8787/atlas/reviews.json") == {}


def test_no_secret_sends_nothing(monkeypatch):
    monkeypatch.delenv("RATE_LIMIT_PASS", raising=False)
    assert pass_headers("https://glyphatlas.org/") == {}
    monkeypatch.setenv("RATE_LIMIT_PASS", "")
    assert pass_headers("https://glyphatlas.org/") == {}
