"""Grapheme families from shape-variant relations: one character written in another shape.

`scripts/build_character_table.py` calls `families` to group the kanji that `data/vocab/graphemes.yaml`
does not. A grapheme is one character however it is drawn, so only relations whose source defines
them as the same character in another shape join two code points:

- `compatibility`: a CJK compatibility ideograph and the unified ideograph it decomposes to
  (UnicodeData.txt). The two are canonically equivalent, and NFC already makes them one.
- `z`: one abstract character encoded twice (Unihan kZVariant, cjkvi 重複漢字).
- `itaiji`: two characters JIS X 0213 unifies at one 面区点 (包摂), from the MJ文字情報一覧表's X0213
  column (`data/vocab/kanji-equivalents.tsv`).
- `reduction`: the MJ縮退マップ's 戸籍統一文字情報 親字・正字 at ホップ数 1, which names the 正字 a
  character is registered under in the 戸籍統一文字.

Simplified, semantic, specialized-semantic and the other relations of `kanji-variants.tsv` join
nothing, and the relations `refs` keeps apart block a merge (`BLOCKING`): no family holds two
characters that any source calls a simplification, a variant in some senses only, a loan (通假),
a substitute, a non-cognate homograph or a look-alike of one another. The check is made on the
whole of both groups before they are joined, so a chain cannot carry a family across a pair it
could not join directly.

The MJ縮退マップ is a table for 戸籍 administration and joins more than shapes, so a reduction
counts only when:

1. it is stated for the code point's own figure, the one MJ implements as its default glyph
   (実装したUCS in `mj-kanji.tsv`). 師's default figure reduces to nothing; three of its non-default
   figures reduce to 帥, and they say what those drawings are, not what 師 is.
2. that figure has one 正字 at ホップ数 1. 𠆤 names both 丁 and 介, and joins neither.
3. where it would put two JIS X 0208 characters (Unihan kIRG_JSource J0) into one family, a second
   source calls each such pair variants: a `variant`, `equivalent`, `shinjitai` or `z` row other than
   the MJ縮退マップ's own and cjkvi's copy of the 戸籍統一文字 table (`koseki/…`). JIS X 0208 holds
   each of its characters as one in its own right, so 傅 and 伝, 誥 and 詰, 逑 and 述 stay apart, and
   苿, which Unihan unifies with 茉, does not bring 茉 to 味; 嶋 and 島, 渕 and 淵 join.
4. it does not join two groups that each hold a 正字 of their own (a reduction target that is not
   itself reduced), which would make one character a variant of two.

Edges are applied in a fixed order (compatibility and `z` first, then 包摂, then reductions; within a
kind by code point), so the build gives the same families on every run.

The head of a family is its curated representative when `graphemes.yaml` names the family; otherwise
the 正字 the family's directed edges point at (the member that is a reduction or decomposition
target and is reduced to nothing else); among several, or none, the JIS X 0208 member, then a
unified ideograph over a compatibility one, then the lowest code point. The 正字 is the character a
source names as the one the others are written for; JIS X 0208 is the repertoire a Japanese reader
knows; the code point is only there to make the choice total.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from . import refs

#: Relations of the 異体字 graph that keep a pair in separate families.
DIFFERENT = frozenset({"borrowed", "substitute", "non-cognate", "spoofing"})
BLOCKING = refs.KEPT_APART | refs.NOT_WIDENED | DIFFERENT
#: Relations another source uses for the same character in another shape; they corroborate a
#: reduction between two JIS X 0208 characters and join nothing themselves.
CORROBORATING = frozenset({"variant", "equivalent", "shinjitai", "z"})
#: The claim of a reduction row that names a 戸籍統一文字 親字・正字 one hop away.
OYAJI = re.compile(r"^(?P<figure>MJ\d+): 法務省戸籍法関連通達・通知, 種別 戸籍統一文字情報 親字・正字, ホップ数 1$")
#: The order edges are applied in: the kinds whose sources define an identity first.
RANK = {"compatibility": 0, "z": 0, "itaiji": 1, "reduction": 2}
#: Kinds whose edge points from a variant to the character it is written for.
DIRECTED = frozenset({"compatibility", "reduction"})


@dataclass(frozen=True)
class Edge:
    """One statement a family rests on. `role` is `merge` for the edge that joins, `corroborates`
    for the second source a reduction between two JIS X 0208 characters needs."""

    a: str
    b: str
    relation: str
    source: str
    detail: str
    role: str = "merge"


@dataclass
class Family:
    head: str
    members: set[str]
    edges: list[Edge] = field(default_factory=list)
    curated: bool = False


def _canonical(char: str) -> str:
    return unicodedata.normalize("NFC", char)


def _oyaji_claims(row: Mapping[str, str], default_figure: Mapping[str, str]) -> list[str]:
    """The claims of a reduction row that name the 親字・正字 of the code point's own figure."""
    return [claim for claim in row["detail"].split(" | ")
            if (match := OYAJI.match(claim)) and default_figure.get(match["figure"]) == row["a"]]


def _corroborating(row: Mapping[str, str]) -> Mapping[str, str] | None:
    """The row with its independent claims only, or `None` when it has none."""
    if row["relation"] not in CORROBORATING or row["source"] == "mj-shrink-map":
        return None
    if row["source"] != "cjkvi-variants":
        return row
    claims = [claim for claim in row["detail"].split(" | ") if "koseki/" not in claim]
    return {**row, "detail": " | ".join(claims)} if claims else None


def candidate_edges(
    variants: Iterable[Mapping[str, str]],
    equivalents: Iterable[Mapping[str, str]],
    ideographs: set[str],
    default_figure: Mapping[str, str],
    jis0208: set[str],
) -> tuple[list[Edge], dict[frozenset[str], list[Edge]], dict[str, set[str]]]:
    """The shape edges in the order they are applied, the corroboration of each JIS X 0208 pair,
    and every character's blocking partners."""
    variants = list(variants)
    blocked: dict[str, set[str]] = {}
    corroboration: dict[frozenset[str], list[Edge]] = {}
    targets: dict[str, dict[str, list[str]]] = {}
    edges: list[Edge] = []
    for row in variants:
        a, b, relation = row["a"], row["b"], row["relation"]
        if a == b:
            continue
        if relation in BLOCKING and _canonical(a) != _canonical(b):
            blocked.setdefault(a, set()).add(b)
            blocked.setdefault(b, set()).add(a)
        if a not in ideographs or b not in ideographs:
            continue
        if (independent := _corroborating(row)) is not None:
            corroboration.setdefault(frozenset((a, b)), []).append(
                Edge(a, b, relation, row["source"], independent["detail"], "corroborates"))
        if ((relation == "compatibility" and row["source"] == "unicode-ucd")
                or (relation == "z" and row["source"] in ("unihan", "cjkvi-variants"))):
            edges.append(Edge(a, b, relation, row["source"], row["detail"]))
        elif relation == "reduction" and row["source"] == "mj-shrink-map":
            claims = _oyaji_claims(row, default_figure)
            if claims:
                # A repeated a→b row adds its claims rather than replacing them, so no statement
                # a figure makes about its 親字 is lost to a later row's copy of another.
                targets.setdefault(a, {}).setdefault(b, []).extend(claims)
    for a, found in targets.items():
        if len(found) != 1:
            continue  # a figure registered under two 正字 is a variant of neither
        ((b, claims),) = found.items()
        edges.append(Edge(a, b, "reduction", "mj-shrink-map", " | ".join(dict.fromkeys(claims))))
    for row in equivalents:
        if row["kind"] == "itaiji" and row["a"] in ideographs and row["b"] in ideographs:
            edges.append(Edge(row["a"], row["b"], "itaiji", "mj-kanji", "X0213 包摂"))
    edges.sort(key=lambda edge: (RANK[edge.relation], ord(edge.a), ord(edge.b), edge.source))
    for found in corroboration.values():
        found.sort(key=lambda edge: (edge.source, edge.relation, edge.a, edge.b, edge.detail))
    return edges, corroboration, blocked


def families(
    variants: Iterable[Mapping[str, str]],
    equivalents: Iterable[Mapping[str, str]],
    ideographs: set[str],
    default_figure: Mapping[str, str],
    jis0208: set[str],
    curated: Mapping[str, list[str]] | None = None,
    refused: list[tuple[str, Edge]] | None = None,
) -> list[Family]:
    """Every family of more than one character, curated ones included, in head code point order.

    `curated` maps a head character to the members `graphemes.yaml` states for it, head first. A
    curated family keeps its head and may take in further members by a shape edge; two curated
    families are never joined. `refused`, when given, collects the merges a rule turned down — as
    (reason, edge) pairs in edge order — for the build's summary.
    """
    edges, corroboration, blocked = candidate_edges(
        variants, equivalents, ideographs, default_figure, jis0208)
    reduced = {edge.a for edge in edges if edge.relation == "reduction"}
    seiji = {edge.b for edge in edges if edge.relation == "reduction"} - reduced
    group: dict[str, Family] = {}
    for head, members in (curated or {}).items():
        family = Family(head, set(members), curated=True)
        for member in members:
            group[member] = family

    def of(char: str) -> Family:
        return group.get(char) or Family(char, {char})

    def refuse(reason: str, edge: Edge) -> None:
        if refused is not None:
            refused.append((reason, edge))

    for edge in edges:
        left, right = of(edge.a), of(edge.b)
        if left is right:
            left.edges.append(edge)
            continue
        if left.curated and right.curated:
            refuse("two curated families", edge)
            continue
        small, large = sorted((left, right), key=lambda family: len(family.members))
        if any(blocked.get(char, set()) & large.members for char in small.members):
            refuse("a pair the relations keep apart", edge)
            continue
        seconds: list[Edge] = []
        if edge.relation == "reduction":
            ends = [{_canonical(char) for char in family.members & seiji} for family in (left, right)]
            if ends[0] and ends[1] and ends[0] != ends[1]:
                refuse("a 正字 of a family each", edge)
                continue
            pairs = [frozenset((x, y)) for x in sorted(left.members & jis0208)
                     for y in sorted(right.members & jis0208)]
            if any(pair not in corroboration for pair in pairs):
                refuse("no second source for a JIS X 0208 pair", edge)
                continue
            seconds = [second for pair in pairs for second in corroboration[pair]]
        keep, gone = (left, right) if left.curated or (not right.curated and left is large) else (right, left)
        keep.members |= gone.members
        keep.edges += gone.edges + [edge] + seconds
        for char in keep.members:
            group[char] = keep
    found = {id(family): family for family in group.values() if len(family.members) > 1}
    for family in found.values():
        if not family.curated:
            family.head = head_of(family, jis0208)
    return sorted(found.values(), key=lambda family: ord(family.head))


def head_of(family: Family, jis0208: set[str]) -> str:
    """The representative of a derived family (see the module docstring)."""
    directed = [edge for edge in family.edges if edge.relation in DIRECTED and edge.role == "merge"]
    sources = {edge.a for edge in directed}
    sinks = {edge.b for edge in directed} - sources
    pool = sinks or family.members
    return min(pool, key=lambda char: (char not in jis0208, _canonical(char) != char, ord(char)))
