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
nothing in this pass, and the relations `refs` keeps apart block a merge (`BLOCKING`): no merge here
puts together two characters that any source calls a simplification, a variant in some senses only,
a loan (通假), a substitute, a non-cognate homograph or a look-alike of one another. The check is
made on the whole of both groups before they are joined, so a chain cannot carry a family across a
pair it could not join directly.

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

A second pass takes in the 異寫字: a character, or a family built above, that the sources tie to
one family only and call that family's character written another way. The simplified 时 of 時,
the 新字体 竜 of 龍 and a 略字 one table lists are each 異寫字, since each writes one character and
nothing else. A character tied to two families is a variant of both and joins neither: 寸 is a
variant of 時 and of 算, 弁 the 新字体 of 辨, 辯 and 瓣, and both stay apart. The pass joins a
group to the one family it is tied to when

1. every relation of `refs.WRITTEN_FOR` it has with a character outside itself leads into that
   family, so it is a variant of nothing else;
2. at least one of those is a statement of the whole character (`WHOLE`): a variant, a full
   equivalent, a semantic, `z` or compatibility variant, a simplified, 新字体, regional or 隸定 form.
   cjkvi's 関連字 and 原規格分離 tables (`RELATED_ONLY`) say two characters are related, not that
   one is written for the other, and a variant in some senses, a fallback reduction or a 韻典網
   group joins nothing;
3. no source calls the two a variant in some senses only (`specialized-semantic`) or relates them
   as different characters (`DIFFERENT`);
4. a simplified form OpenCC's STCharacters also lists as its own traditional (`STANDALONE`: 云 for
   雲 and 云, 后 for 後 and 后) is the merger of two characters, and the pair stays apart whatever
   else relates it: 衚 is also a plain variant of 胡 in one table, and stays apart;
5. a group joins the simplified, 新字体, regional or 隸定 form of its own character only when that
   form is tied to nothing else: 閑 is tied to 闲 alone, and 闲 is also the simplified 閒;
6. where it would put two characters one national standard holds as two (JIS X 0208, GB 2312,
   CNS 11643 plane 1) into one family, that standard's own tradition states them one character:
   for JIS X 0208 a 新字体 or a 異体字 row of the Japanese tables cjkvi carries (`JAPANESE_TABLES`:
   戸籍統一文字, 常用漢字表, 人名用漢字, 表外漢字字体表). The simplification and 異体字 tables of the
   Chinese standards keep their pairs as two, so a GB 2312 or CNS pair joins only as one character
   encoded twice (`z`, compatibility). 躰 and 體, 邉 and 邊 join on the 戸籍統一文字; 咲 and 笑, 果
   and 菓, 辟 and 避 stay apart, as do 余 and 馀;
7. two curated families are never joined.

The pass looks at the groups as the first pass left them and joins them all at once, so a chain
cannot carry a family on: 閑 is tied to 閒 alone, but once joined the pair would be tied to 間 alone,
and 閑 and 間 are not one character.

The word 異寫字 for a variant that differs in writing only, set against the broader 異體字, follows
字統网 (zi.tools, https://zi.tools/, read 2026-10-17), whose variant graph also links a variant to
a whole character (`時-*`) or to one of its senses; the pages read state no definition of the term.
A one-to-many simplification or 新字体 as the test of ambiguity follows the join of the 常用漢字表
with the 通用规范汉字表 in Ryusei Yamaguchi (mandel59), 「文字情報ハックのすすめ」, undated slides
(https://biangbiangcon.ryusei.dev/, read 2026-10-17), where 弁 meets 瓣, 辨 and 辩 and 乾 meets 乾
and 干; a talk's slides, not a reviewed publication.

Edges are applied in a fixed order (compatibility and `z` first, then 包摂, then reductions; within a
kind by code point), so the build gives the same families on every run.

The head of a family is its curated representative when `graphemes.yaml` names the family; otherwise
the member a Japanese reader knows best (`familiarity`: a 常用漢字, then a 人名用漢字, then JIS X
0208 level 1, then level 2), the 正字 the family's directed edges point at (the
member that is a reduction or decomposition target and is reduced to nothing else) first among
several; with none of those, that 正字; then a unified ideograph over a compatibility one, then the
lowest code point. So 群 heads the family 羣 joins, and 村 the family of 邨. The 正字 is the
character a source names as the one the others are written for; the code point is only there to
make the choice total.
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
#: Relations that state one character written whole in another shape: a 異寫字 joins its family by
#: one of these.
WHOLE = frozenset({"variant", "equivalent", "semantic", "z", "simplified", "shinjitai", "regional",
                   "shuowen", "compatibility"})
#: Relations that tie a pair as a variant in some senses only, and keep it apart.
SPLIT = frozenset({"specialized-semantic"})
#: Directed relations whose `b` is the form written for `a`'s character: a reduced form that may
#: write more than one.
REDUCED_FORM = frozenset({"simplified", "shinjitai", "regional", "shuowen"})
#: cjkvi tag prefixes of the Japanese 異体字 tables.
JAPANESE_TABLES = ("koseki/", "joyo/", "jinmei1/", "jinmei2/", "hyogai/")
#: Relations that state one character encoded twice, which every standard accepts.
ENCODED_TWICE = frozenset({"z", "compatibility"})
#: The claim of an OpenCC row whose simplified form STCharacters lists among its own traditional forms.
STANDALONE = "STCharacters (also itself)"
#: cjkvi tables whose rows only say two characters are related (関連字, 原規格分離).
RELATED_ONLY = frozenset({"cjkvi/variant", "jisx0212/variant", "jisx0213/variant", "ucs-scs/variant"})
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
    gb2312: set[str] | None = None,
    cns1: set[str] | None = None,
    familiarity: Mapping[str, int] | None = None,
) -> list[Family]:
    """Every family of more than one character, curated ones included, in head code point order.

    `curated` maps a head character to the members `graphemes.yaml` states for it, head first. A
    curated family keeps its head and may take in further members by a shape edge; two curated
    families are never joined. `refused`, when given, collects the merges a rule turned down — as
    (reason, edge) pairs in edge order — for the build's summary. `gb2312` and `cns1` are the GB 2312
    and CNS 11643 plane 1 repertoires (rule 6 of the 異寫字 pass), `familiarity` ranks the characters
    a Japanese reader knows, lowest first, for the head.
    """
    variants = list(variants)
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
        if refused is not None and (reason, edge) not in refused:
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
    _join_yixiezi(variants, ideographs, {"jis": jis0208, "gb": gb2312 or set(), "cns": cns1 or set()}, group, refuse)
    found = {id(family): family for family in group.values() if len(family.members) > 1}
    for family in found.values():
        if not family.curated:
            family.head = head_of(family, familiarity or {})
    return sorted(found.values(), key=lambda family: ord(family.head))


def _whole(row: Mapping[str, str], standalone: set[str]) -> bool:
    """Whether a row states one character written whole for the other (rules 2 and 4)."""
    if row["relation"] not in WHOLE or (row["relation"] == "simplified" and row["b"] in standalone):
        return False
    claims = [claim for claim in row["detail"].split(" | ") if refs._counts(row["relation"], claim)]
    if row["source"] == "cjkvi-variants":
        claims = [claim for claim in claims if claim.split()[1:2] and claim.split()[1] not in RELATED_ONLY]
    return bool(claims)


def _stated_by(standard: str, edge: Edge) -> bool:
    """Whether a standard's own tradition states a pair one character (rule 6)."""
    if edge.relation in ENCODED_TWICE:
        return True
    if standard != "jis":
        return False
    return edge.relation == "shinjitai" or (edge.source == "cjkvi-variants" and any(
        claim.split()[1].startswith(JAPANESE_TABLES) for claim in edge.detail.split(" | ") if len(claim.split()) > 1))


def _join_yixiezi(
    variants: list[Mapping[str, str]], ideographs: set[str], standards: tuple[set[str], ...],
    group: dict[str, Family], refuse,
) -> None:
    """The 異寫字 pass (see the module docstring): join each group tied to one family only."""
    standalone = {row["b"] for row in variants if row["relation"] == "simplified" and row["source"] == "opencc"
                  and STANDALONE in row["detail"].split(" | ")}
    units: dict[str, Family] = {}

    def unit(char: str) -> Family:
        if char not in group:
            group[char] = Family(char, {char})
        return group[char]

    ties: dict[int, set[int]] = {}
    whole: dict[frozenset[int], list[Edge]] = {}
    apart: set[frozenset[int]] = set()
    merged: set[frozenset[int]] = set()
    for row in variants:
        a, b, relation = row["a"], row["b"], row["relation"]
        if a not in ideographs or b not in ideographs or _canonical(a) == _canonical(b):
            continue
        left, right = unit(a), unit(b)
        if left is right:
            continue
        units[id(left)], units[id(right)] = left, right
        pair = frozenset((id(left), id(right)))
        if relation in SPLIT or relation in DIFFERENT:
            apart.add(pair)
        if relation == "simplified" and b in standalone:
            merged.add(pair)
        if relation not in refs.WRITTEN_FOR or not any(
                refs._counts(relation, claim) for claim in row["detail"].split(" | ")):
            continue
        ties.setdefault(id(left), set()).add(id(right))
        ties.setdefault(id(right), set()).add(id(left))
        if _whole(row, standalone):
            whole.setdefault(pair, []).append(Edge(a, b, relation, row["source"], row["detail"]))
    joins = []
    for key in sorted(ties, key=lambda key: ord(min(units[key].members, key=ord))):
        if len(ties[key]) != 1:
            continue
        (other,) = ties[key]
        pair = frozenset((key, other))
        if pair not in whole:
            continue
        edges = sorted(whole[pair], key=lambda edge: (ord(edge.a), ord(edge.b), edge.source, edge.relation))
        full = all(edge.relation in REDUCED_FORM and edge.a in units[key].members for edge in edges)
        if pair in apart:
            refuse("a variant in some senses only, or a different character", edges[0])
        elif pair in merged:
            refuse("a simplified form that also writes itself", edges[0])
        elif full and ties[other] != {key}:
            refuse("the full form of a reduced form that writes another character too", edges[0])
        elif any(units[key].members & standard and units[other].members & standard
                 and not any(_stated_by(name, edge) for edge in edges) for name, standard in standards.items()):
            refuse("two characters of one standard its tradition does not join", edges[0])
        elif units[key].curated and units[other].curated:
            refuse("two curated families", edges[0])
        else:
            joins.append((units[key], units[other], edges))
    for gone, keep, edges in joins:
        gone, keep = group[min(gone.members, key=ord)], group[min(keep.members, key=ord)]
        if gone is keep:
            continue  # two groups tied to each other only, joined from the other side already
        if gone.curated:
            gone, keep = keep, gone
        keep.members |= gone.members
        keep.edges += gone.edges + edges
        for char in keep.members:
            group[char] = keep


def head_of(family: Family, familiarity: Mapping[str, int]) -> str:
    """The representative of a derived family (see the module docstring)."""
    directed = [edge for edge in family.edges if edge.relation in DIRECTED and edge.role == "merge"]
    sources = {edge.a for edge in directed}
    sinks = {edge.b for edge in directed} - sources
    known = {char for char in family.members if char in familiarity}
    if known:
        best = min(familiarity[char] for char in known)
        known = {char for char in known if familiarity[char] == best}
    pool = (known & sinks) or known or sinks or family.members
    return min(pool, key=lambda char: (_canonical(char) != char, ord(char)))
