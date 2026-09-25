"""A vocabulary whose values form a tree, each value written as its path from the root.

`data/vocab/production.yaml` is one: `printed/type/metal` narrows `printed/type`, and a record takes
the deepest node its evidence states. A flat list such as `data/vocab/style.yaml` is a tree of one
level. A scope selects values by
the tree: `all`, a node and everything under it, or `not:` and a node for everything outside it.
"""
from functools import cached_property
from pathlib import Path

import yaml


class Tree:
    def __init__(self, path: Path):
        self.path = path

    @cached_property
    def nodes(self) -> dict[str, dict]:
        """Every node by id, in the file's order; a node's parent is the id less its last segment."""
        rows = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        nodes = {row["id"]: row for row in rows}
        if len(nodes) != len(rows):
            raise ValueError(f"{self.path}: an id is listed twice")
        for node in nodes:
            parent = node.rpartition("/")[0]
            if parent and parent not in nodes:
                raise ValueError(f"{self.path}: {node} has no parent {parent}")
        return nodes

    def check(self, value: str) -> str:
        """`value` if the vocabulary has it."""
        if value not in self.nodes:
            raise ValueError(f"{value!r} is not in {self.path.name}")
        return value

    def label(self, value: str) -> str:
        return self.nodes[self.check(value)]["en"]

    @staticmethod
    def within(value: str, node: str) -> bool:
        """Whether `value` is `node` or lies under it."""
        return value == node or value.startswith(node + "/")

    def in_scope(self, value: str, scope: str) -> bool:
        if scope == "all":
            return True
        if scope.startswith("not:"):
            return not self.within(value, scope[4:])
        return self.within(value, scope)

    def check_scope(self, scope: str) -> str:
        if scope != "all":
            self.check(scope.removeprefix("not:"))
        return scope
