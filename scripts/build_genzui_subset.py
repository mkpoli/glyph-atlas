# /// script
# requires-python = ">=3.12"
# dependencies = ["fonttools[woff]>=4.55", "httpx>=0.28"]
# ///
"""Cut the review interface's rare-kana webfont from GenZui Sans.

The subset holds the kana almost no installed font draws: every character GenZui Sans has in Kana
Extended-B, Kana Supplement, Kana Extended-A and Small Kana Extension (U+1AFF0–U+1B16F), and the
four kana ligatures encoded as CJK ideographs (𪜈 U+2A708, 𬻿 U+2CEFF, 𬼀 U+2CF00, 𬼂 U+2CF02).
The release is fetched from the GenZui repository at a pinned commit, checked against its SHA-256
and cached under `cache/fonts/`; the output is `apps/review/static/fonts/GenZuiSans-Kana.woff2`.
The unicode-range in `apps/review/src/layers.css` and the covered ranges in `ReferenceGlyph.svelte`
are the ranges this script prints.

    uv run scripts/build_genzui_subset.py
"""

import hashlib
import itertools
from pathlib import Path

import httpx
from fontTools import subset
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent.parent
COMMIT = "705e182e573410ec381d805f01e2c90578ec45af"  # chore: release GenZui Sans 0.103
URL = f"https://raw.githubusercontent.com/mkpoli/GenZui/{COMMIT}/releases/sans-v0.103/GenZuiSans-Regular.ttf"
SHA256 = "cba129c9c7584c0632335f8cb98ec486d37353d5cfcf38bfbf9ea93f593a03e8"
CACHE = ROOT / "cache" / "fonts" / "GenZuiSans-Regular-0.103.ttf"
OUTPUT = ROOT / "apps" / "review" / "static" / "fonts" / "GenZuiSans-Kana.woff2"

KANA_BLOCKS = (0x1AFF0, 0x1B16F)
LIGATURES = (0x2A708, 0x2CEFF, 0x2CF00, 0x2CF02)


def fetch() -> Path:
    if not CACHE.exists():
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        response = httpx.get(URL, follow_redirects=True, timeout=120)
        response.raise_for_status()
        CACHE.write_bytes(response.content)
    digest = hashlib.sha256(CACHE.read_bytes()).hexdigest()
    if digest != SHA256:
        raise SystemExit(f"{CACHE}: sha256 {digest}, expected {SHA256}")
    return CACHE


def ranges(points: list[int]) -> str:
    runs = []
    for _, run in itertools.groupby(enumerate(sorted(points)), lambda item: item[1] - item[0]):
        run = [point for _, point in run]
        runs.append(f"U+{run[0]:X}" if len(run) == 1 else f"U+{run[0]:X}-{run[-1]:X}")
    return ", ".join(runs)


def main() -> None:
    source = fetch()
    cmap = TTFont(source).getBestCmap()
    low, high = KANA_BLOCKS
    points = [p for p in cmap if low <= p <= high] + [p for p in LIGATURES if p in cmap]
    missing = [f"U+{p:X}" for p in LIGATURES if p not in cmap]
    if missing:
        raise SystemExit(f"GenZui Sans lacks {', '.join(missing)}")

    options = subset.Options()
    options.flavor = "woff2"
    options.name_IDs = ["*"]
    options.name_languages = ["*"]
    options.hinting = False
    font = TTFont(source)
    subsetter = subset.Subsetter(options)
    subsetter.populate(unicodes=points)
    subsetter.subset(font)
    font.flavor = "woff2"
    font.save(OUTPUT)
    print(f"{OUTPUT.relative_to(ROOT)}: {len(points)} characters, {OUTPUT.stat().st_size:,} bytes")
    print(ranges(points))


if __name__ == "__main__":
    main()
