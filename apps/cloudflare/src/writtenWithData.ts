// Written by scripts/build_written_with.py from data/vocab/written-with.yaml; edit neither here.
/** The characters of another script a grapheme is also written with: each with the head of that
 *  grapheme, whether a source states the link or the table makes it, and the statements, as rows
 *  of the variant graph. */
export const WRITERS: { char: string; grapheme: string; tier: string; relations: { relation: string; source: string; detail: string }[] }[] = [
  {
    "char": "\u30ff",
    "grapheme": "\u4e8b",
    "tier": "attested",
    "relations": [
      {
        "relation": "semantic",
        "source": "ja-wikipedia-koto",
        "detail": "\u5b57\u6e90\u300c\u300c\u30ff\u300d\u306f\u300c\u4e8b\u300d\u306e\u8349\u66f8\u306e\u4e0a\u90e8\u3001\u307e\u305f\u306f\u300c\u30b3\u30c8\u300d\u306e\u5408\u5b57\u3067\u3042\u308b\u300d"
      },
      {
        "relation": "semantic",
        "source": "en-wiktionary-koto",
        "detail": "Etymology \"Derived from \u4e8b.\"; noun \"abbreviation of \u4e8b (koto, \u201cthing, matter\u201d)\""
      }
    ]
  },
  {
    "char": "\ud82c\udd23",
    "grapheme": "\u4e8b",
    "tier": "editorial",
    "relations": [
      {
        "relation": "semantic",
        "source": "l2-24-150",
        "detail": "Editorial link; the proposal does not name \u4e8b. Table 1: \"representing a Japanese word \u201ckoto\u201d. This word means \u201cthing\u201d.\" and \"Even in vertically written documents, it is not used for any other purpose than \u201cthing\u201d.\""
      }
    ]
  }
];
/** The citation of each source those statements name. */
export const WRITER_SOURCES: Record<string, string> = {
  "l2-24-150": "Gen Kojitani, \"Proposal for missing three Kana-Ligatures\", L2/24-150, 2024-06-21 (https://www.unicode.org/L2/L2024/24150-three-kana-ligatures.pdf), read 2026-11-16",
  "ja-wikipedia-koto": "ja.wikipedia \u300c\u30ff\u300d, revision 111081382 of 2026-09-20 (https://ja.wikipedia.org/w/index.php?oldid=111081382), read 2026-11-16; a wiki article, which cites \u300e\u666e\u901a\u65e5\u672c\u6587\u5178\u300f and \u300e\u4eee\u5b57\u672c\u672b\u300f for the two origins it gives",
  "en-wiktionary-koto": "en.wiktionary \"\u30ff\", revision 89844048 of 2026-03-14 (https://en.wiktionary.org/w/index.php?oldid=89844048), read 2026-11-16; a wiki entry with no citation of its own"
};
