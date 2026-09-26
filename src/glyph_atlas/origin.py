"""Where a document's exemplar was made, a node of `data/vocab/origin.yaml`."""
from pathlib import Path

from .vocab_tree import Tree

VOCAB = Path(__file__).resolve().parents[2] / "data/vocab/origin.yaml"
TREE = Tree(VOCAB)
check, label = TREE.check, TREE.label
