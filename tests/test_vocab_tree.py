import pytest

from glyph_atlas.vocab_tree import Tree


def write(tmp_path, text):
    path = tmp_path / "tree.yaml"
    path.write_text(text, encoding="utf-8")
    return Tree(path)


def test_a_repeated_id_is_refused(tmp_path):
    with pytest.raises(ValueError, match="twice"):
        _ = write(tmp_path, "- {id: handwritten, en: A}\n- {id: handwritten, en: B}\n").nodes


def test_a_node_needs_its_parent(tmp_path):
    with pytest.raises(ValueError, match="no parent printed"):
        _ = write(tmp_path, "- {id: printed/type, en: A}\n").nodes


def test_an_unknown_value_is_refused_the_same_way_everywhere(tmp_path):
    tree = write(tmp_path, "- {id: a, en: A}\n")
    for call in (tree.check, tree.label, tree.check_scope):
        with pytest.raises(ValueError):
            call("b")


def test_two_trees_read_their_own_files(tmp_path):
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    one, two = write(tmp_path / "one", "- {id: a, en: A}\n"), write(tmp_path / "two", "- {id: b, en: B}\n")
    assert (list(one.nodes), list(two.nodes)) == (["a"], ["b"])
