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
7. two curated families are never joined, nor brought together through a family both join.

A third pass (`_join_standard_forms`) takes in the form a national standard prescribes for one character:
a 简体字 of the 通用规范汉字表 (2013) and a 新字体 of the 常用漢字表 (2010). The second pass counts every
relation of a form, and a form in wide use has relations to more than the character it is written for:
动 is the simplified 動, and 異体字 tables also relate it to 働; 对 and 备 are related to others in some
senses, 枪 is the standard form of the 異體 鎗 too. A standard's own table says which character a form
is for, so here only what the standards, Unihan and OpenCC give as its full forms counts. The form
joins the family of its full form when it writes that one character, and stays apart (`Decision.reasons`)
when

1. the table gives it for itself too (后 for 后 and 後, 干, 里, 谷), or OpenCC's STCharacters does
   (`STANDALONE`: 价, 广, 愿): it is a character in use beside the one it simplifies;
2. its full forms are of several families (发 for 發 and 髮, 弁 for 辨, 瓣 and 辯), or Unihan, OpenCC or
   the 新字体 tables give it a further one that is no form of the first: one no source calls a variant of it
   and the table does not print as its 异体字 (录 for 錄 and 彔, 钥 for 鑰 and 鈅), or one both dictionaries of 4
   hold apart from it (闲 for 閑 and 閒);
3. the table lists its full form too, so the simplification is partial (着 and 著, 了 and 瞭, 乾 and 干);
4. it is a headword of the 說文解字 or the 廣韻 with no reading its full form's family has there (听 宜引切
   and 聽, 胜 and 勝, 从 疾容切 and 從 慈用切 in the 說文解字), or a 重文 there of another character that the 廣韻
   reads alike (箓 of 簏, not 籙). A form the 說文解字 gives as a 重文 of the full
   form is that character (达 and 達, 网 and 網, 无 and 無), whatever the 廣韻 says. Rules 1 and 4 are of the
   通用规范汉字表 alone: a pair of the 常用漢字表 is one character by that table, as the curated families are
   (戻 and 戾, though 戻 他計切 is also a 說文解字 headword);
5. a source calls the pair a loan or a substitute (离 and 離), or a non-cognate homograph. cjkvi's
   non-cognate list also holds shapes ISO/IEC 10646 encodes apart for the source separation rule, and
   the tag counts for nothing where the same database calls the pair a 異体字, a simplification or
   separated at the source (状 and 狀, 温 and 溫);
6. the family of its full form is headed by another character of the table that no curated family
   states one with the full form (铀: 鈾 stands under 宙, its 古文);
7. what it would bring along is different from the family in one of the senses of 5, or the two are curated.

A form the second pass put elsewhere leaves that family with what hangs on it alone (备 under 留, 壳 under
売), and a form that heads its own takes the whole of it along (黄). The joined family keeps the head of
its full form's, unless the form's is better known to a Japanese reader (`familiarity`: 没 over 沒), or known as
well and the 新字体 of the two (遥 over 遙).

A form with no character of its own, no reading or definition, that neither pass puts in a family
stands for each family the sources state it a whole variant of (role `stands-for`): 𠮮 stands for 后
and for 𠯑, and has no grapheme of its own.

A reduced form left out because it writes several characters is still recorded as a writer of each
family it writes (`Family.writers`, rows of role `writes`): a simplified, 新字体, regional or 隸定
form that the sources reduce two characters or more to, in two families or more, counting its own
when OpenCC's STCharacters lists it as its own traditional too. 闲 writes 閑 and 閒, 弁 writes 辨,
辯 and 瓣, 后 writes 後 as well as 后. A crop of such a form belongs to one of them by its text. A
plain variant row says which character a form is a variant of, not which of the two is reduced, so
寸 writes nothing here.

The pass looks at the groups as the first pass left them and joins them all at once, so a chain
cannot carry a family on: 閑 is tied to 閒 alone, but once joined the pair would be tied to 間 alone,
and 閑 and 間 are not one character. Several groups may join one family; each is checked again, as
it joins, against everything the family has taken in so far, so two of them a source keeps apart
(卥 and 𠧧, non-cognate) or two of one standard (踩 and 踹, GB 2312, both tied to 跴) never meet
there.

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
0208 level 1, then level 2), the 正字 the family's directed edges point at (the member that is a
reduction or decomposition target and is reduced to nothing else) first among several.

With no such member, a simplified form that CNS 11643 does not hold never heads (璉 heads 琏; 凳,
which cjkvi gives as the simplified 櫈, is CNS 11643's own and heads), and the head is, in order:

1. a 正字 a reduction names in the main CJK block that CNS 11643 plane 1 or GB 2312 holds (坻 over 泜);
2. a member of CNS 11643 plane 1, then of plane 2 and GB 2312 both;
3. any 正字 a reduction names in the main block;
4. a member of GB 2312, then of CNS 11643 plane 2 alone (吕 heads 郘);
5. the full form a simplified member simplifies (戇 heads 戆 and 戅);
6. any 正字, a compatibility ideograph's decomposition included.

Among equals the full form of a simplification wins (靚 over 靘, both of plane 2 alone); then a
unified ideograph over a compatibility one, one of the main block over the extensions, then the
lowest code point. So 群 heads the family 羣 joins, 村 the family of 邨, 厲 the family of 厉, 墊 the
family of 垫, 妤 the family of 伃, 氯 the family of 㲶, and 篙 the family of 㰏, the 正字 the 戸籍統一文字
registers it under. The 正字 is the character a source names as the one the others are written for;
the code point is only there to make the choice total.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from . import refs

#: The source records of the standards and dictionaries the third pass reads.
TONGYONG, JOYO = "tongyong-guifan", "joyo"
SHUOWEN, GUANGYUN = "shuowen", "guangyun"
#: Relations of the 異体字 graph that keep a pair in separate families.
DIFFERENT = frozenset({"borrowed", "substitute", "non-cognate", "spoofing"})
#: Of those, the relations of two characters that only look or sound alike, which no form writes both of.
UNLIKE = frozenset({"non-cognate", "spoofing"})
BLOCKING = refs.KEPT_APART | refs.NOT_WIDENED | DIFFERENT
#: Relations that state one character written whole in another shape: a 異寫字 joins its family by
#: one of these.
WHOLE = frozenset({"variant", "equivalent", "semantic", "z", "simplified", "shinjitai", "regional",
                   "shuowen", "compatibility"})
#: Relations by which a source calls two full forms one character (rule 2 of the third pass).
VARIANT = (WHOLE - {"simplified"}) | {"reduction"}
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
#: The role of an edge to a form outside a family that writes it among other characters.
WRITES = "writes"
#: The role of an edge by which a form with no character of its own stands for a family (`_add_stand_ins`).
STANDS_FOR = "stands-for"
#: The role of an edge by which a small seal character (`b`) is a member by the Han character Unicode
#: gives as its modern equivalent (`a`); `scripts/build_character_table.py` adds them.
SEAL = "seal"
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
    #: The members `graphemes.yaml` states.
    stated: frozenset[str] = frozenset()
    #: The head a joined standard form leaves the family, one of the two it had; None before any joined.
    kept_head: str | None = None
    #: The characters of other families that also write this one, each with the edges that say so
    #: (role `writes`): 后 writes 後 as well as 后, and 闲 writes 閑 and 閒. A form with no character
    #: of its own stands for each family it is a variant of (role `stands-for`): 𠮮 for 后 and 𠯑.
    writers: dict[str, list[Edge]] = field(default_factory=dict)


class Headwords:
    """The headwords of the 說文解字 and the 廣韻 with their readings (`data/vocab/headword-readings.tsv`)."""

    def __init__(self, rows: Iterable[Mapping[str, str]] = ()) -> None:
        self._entries: dict[str, dict[str, set[str]]] = {}
        self._own: dict[str, dict[str, set[str]]] = {}
        self._read: dict[str, dict[str, set[str]]] = {}
        #: The 親字 of each 說文解字 entry, and the entries each 重文 is of.
        self._heads: dict[str, set[str]] = {}
        self._forms: dict[str, set[str]] = {}
        for row in rows:
            name, char = row["dictionary"], row["char"]
            self._entries.setdefault(name, {}).setdefault(char, set()).add(row["entry"])
            if name == SHUOWEN:
                if row["role"] == "head":
                    self._heads.setdefault(row["entry"], set()).add(char)
                else:
                    self._forms.setdefault(char, set()).add(row["entry"])
            if row["reading"]:
                self._read.setdefault(name, {}).setdefault(char, set()).add(row["reading"])
                if row["role"] == "head":
                    self._own.setdefault(name, {}).setdefault(char, set()).add(row["reading"])

    @staticmethod
    def _all(found: Mapping[str, set[str]], chars: Iterable[str]) -> set[str]:
        return set().union(*(found.get(char, set()) for char in chars))

    def one_entry(self, chars: Iterable[str], others: Iterable[str]) -> bool:
        """Whether the 說文解字 gives one of `others` in one entry with one of `chars`, as its 重文."""
        entries = self._entries.get(SHUOWEN, {})
        return bool(self._all(entries, chars) & self._all(entries, others))

    def writes(self, char: str) -> str:
        """The 親字 the 說文解字 gives `char` as a 重文 of and the 廣韻 reads alike; empty for none."""
        own = self._own.get(GUANGYUN, {})
        return "".join(sorted(head for entry in self._forms.get(char, ()) for head in self._heads.get(entry, ())
                              if own.get(char, set()) & own.get(head, set())))

    def apart_in(self, chars: Iterable[str], others: Iterable[str]) -> list[str]:
        """The dictionaries that hold both, `chars` as headwords, and share no reading between them."""
        found = []
        for name in (SHUOWEN, GUANGYUN):
            own, read = self._all(self._own.get(name, {}), chars), self._all(self._read.get(name, {}), others)
            if own and read and not own & read:
                found.append(name)
        return found


@dataclass(frozen=True)
class StandardForm:
    """One character of the 通用规范汉字表 with what the table prints beside it: its full forms (繁体字), whether
    it stands for itself among them (～), and the 异体字."""

    char: str
    position: int
    full: frozenset[str] = frozenset()
    itself: bool = False
    others: frozenset[str] = frozenset()


@dataclass
class Decision:
    """What the third pass found of one standard form: its full forms of other families, the edges that state them and, where it
    stays apart, why."""

    form: str
    full: frozenset[str]
    edges: list[Edge]
    reasons: list[str] = field(default_factory=list)


def standard_forms(rows: Iterable[Mapping[str, str]], listed: Mapping[str, int]) -> dict[str, StandardForm]:
    """The 通用规范汉字表 by character, from its table of forms (`data/vocab/tongyong-guifan-forms.tsv`) and its
    characters with their numbers."""
    full: dict[str, set[str]] = {}
    others: dict[str, set[str]] = {}
    for row in rows:
        full.setdefault(row["char"], set()).add(row["full"])
        others.setdefault(row["char"], set()).add(row["variant"])
    return {char: StandardForm(char, position, frozenset(full.get(char, set()) - {char}), char in full.get(char, set()),
                               frozenset(others.get(char, set()) - full.get(char, set()) - {char}))
            for char, position in listed.items()}


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
    readings: set[str] | None = None,
    standard: Mapping[str, StandardForm] | None = None,
    headwords: Headwords | None = None,
    decisions: list[Decision] | None = None,
) -> list[Family]:
    """Every family of more than one character or with a writer, curated ones included, in head code
    point order.

    `curated` maps a head character to the members `graphemes.yaml` states for it, head first. A
    curated family keeps its head and may take in further members by a shape edge; two curated
    families are never joined. `refused`, when given, collects the merges a rule turned down — as
    (reason, edge) pairs in edge order — for the build's summary. `gb2312` and `cns1` are the GB 2312
    and CNS 11643 plane 1 repertoires (rule 6 of the 異寫字 pass), `familiarity` ranks the characters
    a reader knows, lowest first, for the head. `readings` holds the ideographs that are characters of
    their own, with a reading or a definition; any other one a family takes in no shape stands for
    the families it is a variant of (`_add_stand_ins`). Without it, none does. `standard` is the
    通用规范汉字表 and `headwords` the two dictionaries of the third pass, whose findings `decisions`,
    when given, collects; without `standard` the pass does not run.
    """
    variants = list(variants)
    edges, corroboration, blocked = candidate_edges(
        variants, equivalents, ideographs, default_figure, jis0208)
    reduced = {edge.a for edge in edges if edge.relation == "reduction"}
    seiji = {edge.b for edge in edges if edge.relation == "reduction"} - reduced
    group: dict[str, Family] = {}
    for head, members in (curated or {}).items():
        family = Family(head, set(members), curated=True, stated=frozenset(members))
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
    headwords = headwords or Headwords()
    reduced_forms, unlike, standalone, stated = _join_yixiezi(
        variants, ideographs, {"jis": jis0208, "gb": gb2312 or set(), "cns": cns1 or set()}, group, refuse)
    if standard is not None:
        found = _join_standard_forms(variants, ideographs, group, refuse, standard, headwords, standalone, familiarity or {})
        if decisions is not None:
            decisions.extend(found)
    _add_writers(reduced_forms, unlike, standalone, group)
    if readings is not None:
        _add_stand_ins(stated, unlike, readings, group)
    found = {id(family): family for family in group.values() if len(family.members) > 1 or family.writers}
    for family in found.values():
        if not family.curated:
            family.head = _head(family, familiarity or {})
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
    variants: list[Mapping[str, str]], ideographs: set[str], standards: Mapping[str, set[str]],
    group: dict[str, Family], refuse,
) -> tuple[dict[str, list[Edge]], dict[str, set[str]], set[str], dict[str, list[Edge]]]:
    """The 異寫字 pass (see the module docstring): join each group tied to one family only. Answers with what the
    recording of writers reads: each reduced form's statements, the look-alikes, the forms that also write
    themselves, and each character's statements of the whole of another."""
    standalone = {row["b"] for row in variants if row["relation"] == "simplified" and row["source"] == "opencc"
                  and STANDALONE in row["detail"].split(" | ")}
    units: dict[str, Family] = {}

    def unit(char: str) -> Family:
        if char not in group:
            group[char] = Family(char, {char})
        return group[char]

    ties: dict[int, set[int]] = {}
    whole: dict[frozenset[int], list[Edge]] = {}
    # Each reduced form's statements of the characters it is written for, a merger's too (后 for 後).
    reduced: dict[str, list[Edge]] = {}
    apart: set[frozenset[int]] = set()
    merged: set[frozenset[int]] = set()
    different: dict[str, set[str]] = {}
    unlike: dict[str, set[str]] = {}
    # Each character's statements of the whole of another (`_whole`), for the forms that stand for one.
    stated: dict[str, list[Edge]] = {}
    for row in variants:
        a, b, relation = row["a"], row["b"], row["relation"]
        if a not in ideographs or b not in ideographs or _canonical(a) == _canonical(b):
            continue
        left, right = unit(a), unit(b)
        # Before the skip below: a reduction inside one family still counts toward what its form writes.
        if relation in REDUCED_FORM and _whole(row, set()):
            reduced.setdefault(b, []).append(Edge(a, b, relation, row["source"], row["detail"], WRITES))
        if left is right:
            continue
        units[id(left)], units[id(right)] = left, right
        pair = frozenset((id(left), id(right)))
        if relation in SPLIT or relation in DIFFERENT:
            apart.add(pair)
            different.setdefault(a, set()).add(b)
            different.setdefault(b, set()).add(a)
        if relation in UNLIKE:
            unlike.setdefault(a, set()).add(b)
            unlike.setdefault(b, set()).add(a)
        if relation == "simplified" and b in standalone:
            merged.add(pair)
        if relation not in refs.WRITTEN_FOR or not any(
                refs._counts(relation, claim) for claim in row["detail"].split(" | ")):
            continue
        ties.setdefault(id(left), set()).add(id(right))
        ties.setdefault(id(right), set()).add(id(left))
        if _whole(row, standalone):
            whole.setdefault(pair, []).append(Edge(a, b, relation, row["source"], row["detail"]))
            for char in (a, b):
                stated.setdefault(char, []).append(Edge(a, b, relation, row["source"], row["detail"], STANDS_FOR))
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
            joins.append((frozenset(units[key].members), frozenset(units[other].members), edges))
    for leaf, target, edges in joins:
        gone, keep = group[min(leaf, key=ord)], group[min(target, key=ord)]
        if gone is keep:
            continue  # two groups tied to each other only, joined from the other side already
        if any(different.get(char, set()) & keep.members for char in gone.members):
            refuse("a pair the relations keep apart, through the family", edges[0])
            continue
        if gone.curated and keep.curated:
            refuse("two curated families, through the family", edges[0])
            continue
        # The pairs this join makes; its edges speak only for the leaf and its target as they were.
        if any(not (x in leaf and y in target)
               for standard in standards.values()
               for x in gone.members & standard for y in keep.members & standard):
            refuse("two characters of one standard, through the family", edges[0])
            continue
        if gone.curated:
            gone, keep = keep, gone
        keep.members |= gone.members
        keep.edges += gone.edges + edges
        for char in keep.members:
            group[char] = keep
    return reduced, unlike, standalone, stated


#: cjkvi tags by which its database calls a pair one character, whatever its non-cognate list says: a 異体字 table,
#: a simplification, or separated at the source (原規格分離).
SAME_TAGS = ("hydzd/", "twedu/", "koseki/", "joyo/", "jinmei1/", "jinmei2/", "hyogai/", "dypytz/", "ucs-scs/",
             "cjkvi/duplicate", "cjkvi/simplified", "cjkvi/traditional")


def _is_different(rows: Iterable[Mapping[str, str]]) -> str | None:
    """Why the rows between two characters make them different ones, or None (rule 5 of the third pass)."""
    rows = list(rows)
    if any(row["relation"] in ("borrowed", "substitute") for row in rows):
        return "a loan or a substitute"
    tags = [claim.split()[1] for row in rows if row["source"] == "cjkvi-variants" and row["relation"] != "non-cognate"
            for claim in row["detail"].split(" | ") if len(claim.split()) > 1]
    if any(row["relation"] == "non-cognate" for row in rows) and not any(tag.startswith(SAME_TAGS) for tag in tags):
        return "a non-cognate homograph"
    return None


def _join_standard_forms(
    variants: list[Mapping[str, str]], ideographs: set[str], group: dict[str, Family], refuse,
    standard: Mapping[str, StandardForm], headwords: Headwords, standalone: set[str], familiarity: Mapping[str, int],
) -> list[Decision]:
    """The third pass (see the module docstring): join each form a national standard prescribes for one character."""
    between: dict[frozenset[str], list[Mapping[str, str]]] = {}
    #: The full forms the sources reduce to each form: by Unihan, by OpenCC's STCharacters, as a 新字体, and the
    #: 旧字体 the 常用漢字表 prints beside it.
    unihan: dict[str, set[str]] = {}
    opencc: dict[str, set[str]] = {}
    shinjitai: dict[str, set[str]] = {}
    joyo: dict[str, set[str]] = {}
    for row in variants:
        a, b, relation, source = row["a"], row["b"], row["relation"], row["source"]
        if a not in ideographs or b not in ideographs or _canonical(a) == _canonical(b):
            continue
        between.setdefault(frozenset((a, b)), []).append(row)
        claims = row["detail"].split(" | ")
        if relation == "simplified" and source == "unihan":
            unihan.setdefault(b, set()).add(a)
        elif relation == "simplified" and source == "opencc" and any(claim.startswith("STCharacters") for claim in claims):
            opencc.setdefault(b, set()).add(a)
        elif relation == "shinjitai" and source != "opencc":
            shinjitai.setdefault(b, set()).add(a)
        for claim in claims:
            if claim.split()[1:2] == ["joyo/variant"]:
                new, _, old = claim.split()[0].partition("→")
                joyo.setdefault(new, set()).add(old)

    def unit(char: str) -> Family:
        return group.get(char) or Family(char, {char})

    def two_characters(a: str, b: str) -> bool:
        return not headwords.one_entry({a}, {b}) and len(headwords.apart_in({a}, {b})) == 2

    def related(a: str, b: str) -> bool:
        return not two_characters(a, b) and any(row["relation"] in VARIANT for row in between.get(frozenset((a, b)), ()))

    def decide(form: str, full: set[str], further: set[str], edges: list[Edge], table: StandardForm | None) -> Decision | None:
        families = {id(unit(char)) for char in full}
        if form in group and families == {id(group[form])}:
            return None
        edges += [Edge(row["a"], row["b"], row["relation"], row["source"], row["detail"])
                  for char in sorted(full) for row in between.get(frozenset((form, char)), ())
                  if row["b"] == form and row["relation"] in REDUCED_FORM or "joyo/variant" in row["detail"]]
        found = Decision(form, frozenset(full), edges)
        if table is not None and table.full and table.itself:
            found.reasons.append("the table gives it for itself too")
        if form in standalone and table is not None:
            found.reasons.append("OpenCC gives it for itself too")
        if len(families) > 1:
            found.reasons.append("several characters: " + "".join(sorted(full)))
        outside = sorted(char for char in further - full - {form} if char in ideographs and unit(char) is not unit(form)
                         and id(unit(char)) not in families
                         and not any(related(char, one) or (table is not None and char in table.others
                                                            and not two_characters(char, one)) for one in full))
        if outside:
            found.reasons.append("a further character: " + "".join(outside))
        listed = sorted(char for char in full if char in standard and table is not None)
        if listed:
            found.reasons.append("the table lists the full form too: " + "".join(listed))
        members = set().union(*(unit(char).members for char in full)) - {form}
        if table is not None and not headwords.one_entry({form}, members):
            found.reasons += [f"a headword of the {name} with another reading" for name in headwords.apart_in({form}, members)]
            if other := headwords.writes(form):
                found.reasons.append(f"a 重文 of another character: {other}")
        for char in sorted(full):
            if why := _is_different(between.get(frozenset((form, char)), ())):
                found.reasons.append(why)
        if len(families) == 1:
            target = unit(min(full))
            head = _head(target, familiarity)
            curated = any(char in target.stated for char in full)
            if table is not None and head in standard and head != form and head not in full and not curated:
                found.reasons.append(f"its full form is of the grapheme of {head}, another character of the table")
        found.full = frozenset(full - unit(form).members)
        return found

    decided: list[Decision] = []
    for char, table in sorted(standard.items(), key=lambda item: item[1].position):
        full = {one for one in table.full if one in ideographs}
        told = {one for one in unihan.get(char, set()) if one != char}
        others = {one for one in table.others if one in ideographs}
        if not full and not told:
            continue
        # With no 繁体字 printed, the full form is the one Unihan gives; where the table prints one 异体字 and
        # Unihan another character, it is the table's (净［淨］, Unihan 凈).
        target = full or (others if told.isdisjoint(others) and len(others) == 1 else told)
        further = told | opencc.get(char, set()) | shinjitai.get(char, set())
        edges = [Edge(one, char, "simplified", TONGYONG, f"{table.position:04d} {char}（{one}）") for one in sorted(full)]
        edges += [Edge(one, char, "variant", TONGYONG, f"{table.position:04d} {char}［{one}］") for one in sorted(target & others)]
        if (found := decide(char, target, further, edges, table)) is not None:
            decided.append(found)
    for char in sorted(joyo, key=ord):
        full = {one for one in joyo[char] if one in ideographs and _canonical(one) == one}
        if not full or any(found.form == char and full <= found.full for found in decided):
            continue
        if (found := decide(char, full, shinjitai.get(char, set()), [], None)) is not None:
            decided.append(found)
    for found in decided:
        edge = found.edges[0]
        if found.reasons:
            refuse("a standard form: " + found.reasons[0].partition(":")[0], edge)
        elif why := _take_in(found, group, between, familiarity):
            found.reasons.append(why)
            refuse("a standard form: " + why, edge)
    return decided


def _head(family: Family, familiarity: Mapping[str, int]) -> str:
    return family.head if family.curated else family.kept_head or head_of(family, familiarity)


def _part_of(form: str, family: Family, familiarity: Mapping[str, int]) -> set[str]:
    """What moves with `form` out of its family: the whole when it heads it, or else what hangs on it alone."""
    head = _head(family, familiarity)
    if len(family.members) == 1 or head == form:
        return set(family.members)
    reached, queue = {head}, [head]
    while queue:
        char = queue.pop()
        for edge in family.edges:
            for one, other in ((edge.a, edge.b), (edge.b, edge.a)):
                if one == char and other != form and other in family.members and other not in reached:
                    reached.add(other)
                    queue.append(other)
    return set(family.members) - reached


def _take_in(
    found: Decision, group: dict[str, Family], between: Mapping[frozenset[str], list[Mapping[str, str]]],
    familiarity: Mapping[str, int],
) -> str | None:
    """Join one standard form to the family of its full form, or say why what it brings along cannot."""
    form = found.form
    source = group.get(form) or Family(form, {form})
    target = group.get(min(found.full)) or Family(min(found.full), {min(found.full)})
    if source is target:
        return None
    moved = _part_of(form, source, familiarity)
    if form in source.stated and (target.curated or moved != source.members):
        return "two curated families"
    for char in moved - {form}:
        for member in target.members:
            if why := _is_different(between.get(frozenset((char, member)), ())):
                return f"{char}, which it brings, and {member}: {why}"
    heads = [_head(source, familiarity) if moved == source.members else form, _head(target, familiarity)]
    edges = [edge for edge in source.edges if edge.a in moved and edge.b in moved]
    rest = [edge for edge in source.edges if edge.a not in moved and edge.b not in moved]
    if moved != source.members:
        source.members -= moved
        source.edges = rest
        keep = target
        keep.members |= moved
        keep.edges += edges + found.edges
    else:
        keep, gone = (source, target) if source.curated else (target, source)
        keep.members |= gone.members
        keep.edges += gone.edges + found.edges
    if not keep.curated:
        # The full form's family keeps its head, unless the form's is one a Japanese reader knows better, or as
        # well and the 新字体 of the pair (遥 for 遙; 憐 stays over 怜).
        known = [familiarity.get(head, 9) if familiarity.get(head) in JAPANESE_RANKS else 9 for head in heads]
        newer = heads[0] == form and any(edge.relation == "shinjitai" or "joyo/variant" in edge.detail for edge in found.edges)
        keep.kept_head = heads[0] if known[0] < known[1] or (known[0] == known[1] < 9 and newer) else heads[1]
    for char in keep.members:
        group[char] = keep
    return None


def _add_writers(
    reduced: dict[str, list[Edge]], unlike: dict[str, set[str]], standalone: set[str],
    group: dict[str, Family],
) -> None:
    """Record each reduced form that writes several characters as a writer of each family it writes.

    A simplified, 新字体, regional or 隸定 form is written for the characters the sources reduce to
    it. When those lie in two families or more, counting its own when it also writes itself
    (`STANDALONE`), it writes each, and each family it lies outside of lists it as a writer: 闲 writes
    閑 and 閒, 弁 writes 辨, 辯 and 瓣, 后 writes 後 as well as 后. A form that writes one family only
    is in it, or is a variant of it that the 異寫字 pass keeps apart for another reason. A loan or a
    substitute still writes the character (后 is also 後's loan); a non-cognate homograph or a
    look-alike (`UNLIKE`) writes nothing.
    """
    for writer, edges in sorted(reduced.items(), key=lambda item: ord(item[0])):
        own = group[writer]
        by_family: dict[int, tuple[Family, list[Edge]]] = {}
        for edge in edges:
            target = group[edge.a]
            if not unlike.get(writer, set()) & target.members:
                by_family.setdefault(id(target), (target, []))[1].append(edge)
        if len(by_family | ({id(own): None} if writer in standalone else {})) < 2:
            continue
        for target, found in by_family.values():
            if target is not own:
                target.writers[writer] = sorted(found, key=lambda edge: (ord(edge.a), edge.source, edge.relation))


def _add_stand_ins(
    stated: dict[str, list[Edge]], unlike: dict[str, set[str]], readings: set[str], group: dict[str, Family],
) -> None:
    """Record each form with no character of its own as standing for the families it is a variant of.

    An ideograph with no reading or definition (not in `readings`) is a way of writing other
    characters. When neither pass has put it in a family, each family the sources state it a whole
    variant of (`_whole`) lists it, by the edges that say so: 𠮮 stands for 后 and for 𠯑, and so has
    no grapheme of its own. It stands only for a family that holds a character of its own, never for
    another such form. A reduced form already writing families keeps that role, and a non-cognate
    homograph or a look-alike (`UNLIKE`) stands for nothing.
    """
    writing = {writer for family in group.values() for writer in family.writers}
    for form, edges in sorted(stated.items(), key=lambda item: ord(item[0])):
        own = group.get(form)
        if form in readings or form in writing or (own is not None and len(own.members) > 1):
            continue
        by_family: dict[int, tuple[Family, list[Edge]]] = {}
        for edge in edges:
            other = edge.b if edge.a == form else edge.a
            target = group.get(other)
            if target is not None and target is not own and target.members & readings \
                    and not unlike.get(form, set()) & target.members:
                by_family.setdefault(id(target), (target, []))[1].append(edge)
        for target, found in by_family.values():
            target.writers[form] = sorted(found, key=lambda edge: (ord(edge.a), ord(edge.b), edge.source, edge.relation))


#: The ranks of `familiarity` a Japanese reader knows (常用, 人名用, JIS X 0208 levels 1 and 2), the
#: traditional standard's (CNS 11643 planes 1 and 2) and the simplified standard's (GB 2312).
JAPANESE_RANKS, TRADITIONAL_RANKS, SIMPLIFIED_RANKS = range(4), frozenset({4, 5, 7}), frozenset({6})
#: CNS 11643 plane 1, and plane 2 where GB 2312 holds the character too; plane 2 alone comes after
#: GB 2312, so a live GB form is not headed by an obscure one (吕, not 郘).
TAIWAN_RANKS, TRADITIONAL_ONLY_RANKS = frozenset({4, 5}), frozenset({7})

#: The two standards' common sets, CNS 11643 plane 1 and GB 2312: a 正字 in one of them heads first.
COMMON_CHINESE_RANKS = frozenset({4, 5, 6})


def head_of(family: Family, familiarity: Mapping[str, int]) -> str:
    """The representative of a derived family (see the module docstring)."""
    directed = [edge for edge in family.edges if edge.relation in DIRECTED and edge.role == "merge"]
    sources = {edge.a for edge in directed}
    sinks = {edge.b for edge in directed} - sources
    # A 正字 a reduction names: a compatibility ideograph's decomposition is the same character in
    # another code point, not a form the others are written for.
    seiji = {edge.b for edge in directed if edge.relation == "reduction"} - sources
    # Past the Japanese ranks, a member that is another member's simplified form and no character of
    # the traditional standard never heads; 凳, which cjkvi gives as the simplified 櫈, is CNS 11643's own.
    reduced = [edge for edge in family.edges if edge.relation == "simplified" and edge.a in family.members
               and familiarity.get(edge.b) not in TRADITIONAL_RANKS]
    simplified = {edge.b for edge in reduced}
    candidates = (family.members - simplified) or family.members
    # The forms those simplify, which head before any other member left unranked: 戇 for 戆, not 戅.
    full = {edge.a for edge in reduced} & candidates

    def best(ranks: Iterable[int], among: set[str]) -> set[str]:
        known = {char for char in among if familiarity.get(char) in ranks}
        top = min((familiarity[char] for char in known), default=None)
        return {char for char in known if familiarity[char] == top}

    japanese = best(JAPANESE_RANKS, family.members)
    if japanese:
        pool = (japanese & sinks) or japanese
    else:
        main = {char for char in seiji & candidates if 0x4E00 <= ord(char) <= 0x9FFF}
        ranked = {char for char in main if familiarity.get(char) in COMMON_CHINESE_RANKS}
        pool = (ranked or best(TAIWAN_RANKS, candidates) or main or best(SIMPLIFIED_RANKS, candidates)
                or best(TRADITIONAL_ONLY_RANKS, candidates) or full or (sinks & candidates) or candidates)
        pool = (pool & full) or pool  # among equals, the form a simplification simplifies: 靚 over 靘
    return min(pool, key=lambda char: (_canonical(char) != char, not 0x4E00 <= ord(char) <= 0x9FFF, ord(char)))
