"""Build the handwriting fallback fonts for the review interface's character labels.

The interface draws a label in Kureedo Kata, then Klee One, then LXGW WenKai TC, then LXGW
WenKai: all four in the same hand. Klee One is served complete from @fontsource/klee-one. The two
LXGW fonts are large, so this script serves only the labels that need them: every character the
corpus has crops of that neither Kureedo Kata nor Klee One draws, subset from the LXGW releases
pinned below, WenKai TC first (its forms are the traditional ones older Japanese print uses) and
WenKai for the rest. Each font is split into small files by frequency, each with its own
`unicode-range`, so a page downloads only the files for the labels it shows.

  uv run scripts/build_fallback_fonts.py --labels work/corpus-index/chars.parquet

Without --labels the character list committed beside the fonts is reused. Writes
apps/review/static/fonts/fallback/ (fonts, the character list, the licences) and
apps/review/src/fallback-fonts.css.
"""
import argparse
import hashlib
import re
import urllib.request
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'apps/review'
OUT = APP / 'static/fonts/fallback'
CSS = APP / 'src/fallback-fonts.css'
CACHE = ROOT / 'cache/fonts'
LIST = OUT / 'characters.tsv'
CHUNK = 120   # characters per file

FONTS = [
    {'family': 'LXGW WenKai TC', 'key': 'wenkai-tc', 'release': 'v1.522',
     'url': 'https://github.com/lxgw/LxgwWenkaiTC/releases/download/v1.522/LXGWWenKaiTC-Regular.ttf',
     'sha256': 'b1a0795862c1415bf3f393ea50b2a4ea6275012cf5bad3f94feeb1222f555731',
     'licence': 'https://raw.githubusercontent.com/lxgw/LxgwWenkaiTC/v1.522/OFL.txt'},
    {'family': 'LXGW WenKai', 'key': 'wenkai', 'release': 'v1.522',
     'url': 'https://github.com/lxgw/LxgwWenKai/releases/download/v1.522/LXGWWenKai-Regular.ttf',
     'sha256': '39ad71264b588165b469e35e6afb162a378dacd1f95348160240ba9038ac3009',
     'licence': 'https://raw.githubusercontent.com/lxgw/LxgwWenKai/v1.522/OFL.txt'},
]


def fetch(url, sha256=None):
    path = CACHE / url.rsplit('/', 1)[1] if sha256 else None
    if path and path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == sha256:
        return path.read_bytes()
    data = urllib.request.urlopen(url, timeout=120).read()
    if sha256:
        assert hashlib.sha256(data).hexdigest() == sha256, f'{url} does not match its pinned hash'
        CACHE.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
    return data


def cmap_of(paths):
    out = set()
    for path in paths:
        out |= set(TTFont(path).getBestCmap())
    return out


def labels(parquet):
    """Characters with crops, most crops first, from the corpus index."""
    import pyarrow.parquet as pq
    # U+FFFD marks a label that was lost in decoding, not a character.
    rows = [r for r in pq.read_table(parquet, columns=['char', 'n_units']).to_pylist()
            if r['n_units'] > 0 and len(r['char']) == 1 and r['char'] != '\ufffd']
    return sorted(((r['char'], r['n_units']) for r in rows), key=lambda x: (-x[1], x[0]))


def ranges(codes):
    codes, out = sorted(codes), []
    for c in codes:
        if out and out[-1][1] == c - 1:
            out[-1][1] = c
        else:
            out.append([c, c])
    return ', '.join(f'U+{a:X}' if a == b else f'U+{a:X}-{b:X}' for a, b in out)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--labels', type=Path, help='the corpus index chars.parquet (default: reuse the committed list)')
    args = parser.parse_args()
    if args.labels:
        wanted = labels(args.labels)
    else:
        wanted = [(line.split('\t')[0], int(line.split('\t')[1])) for line in LIST.read_text().splitlines()[1:] if line]
    klee = APP / 'node_modules/@fontsource/klee-one'
    served = re.findall(r'url\(\./(files/[^)]+\.woff2)\)', (klee / '400.css').read_text())   # the files the page loads
    drawn = cmap_of([APP / 'static/fonts/KureedoKata-Regular.woff2', *(klee / f for f in served)])
    need = [(ch, n) for ch, n in wanted if ord(ch) not in drawn]
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob('*.woff2'):
        old.unlink()
    LIST.write_text('character\tcrops\n' + ''.join(f'{ch}\t{n}\n' for ch, n in wanted))
    faces, report = [], []
    for spec in FONTS:
        data = fetch(spec['url'], spec['sha256'])
        source = CACHE / spec['url'].rsplit('/', 1)[1]
        cmap = set(TTFont(source).getBestCmap())
        mine = [(ch, n) for ch, n in need if ord(ch) in cmap]
        need = [(ch, n) for ch, n in need if ord(ch) not in cmap]
        (OUT / f'{spec["key"]}-OFL.txt').write_bytes(fetch(spec['licence']))
        for i in range(0, len(mine), CHUNK):
            codes = [ord(ch) for ch, _ in mine[i:i + CHUNK]]
            font = TTFont(source, recalcTimestamp=False)
            options = subset.Options(); options.flavor = 'woff2'; options.hinting = False
            options.layout_features = ['vert', 'vrt2']   # upright forms in vertical text
            sub = subset.Subsetter(options=options); sub.populate(unicodes=codes); sub.subset(font)
            name = f'{spec["key"]}-{i // CHUNK:02d}.woff2'
            font.save(OUT / name)
            faces.append(f'@font-face {{\n  font-family: "{spec["family"]}";\n  src: url("/fonts/fallback/{name}") format("woff2");\n'
                         f'  font-display: swap;\n  unicode-range: {ranges(codes)};\n}}\n')
        report.append(f'{spec["family"]} {spec["release"]} ({hashlib.sha256(data).hexdigest()[:12]}): {len(mine)} characters')
    CSS.write_text('/* Generated by scripts/build_fallback_fonts.py: the labels Kureedo Kata and Klee One do not draw, from the\n'
                   '   LXGW WenKai TC and WenKai releases. Each file covers its own characters only. */\n' + ''.join(faces))
    print('\n'.join(report))
    kana = cmap_of([APP / 'static/fonts/GenZuiSans-Kana.woff2'])   # the last font in the stack
    need = [(ch, n) for ch, n in need if ord(ch) not in kana]
    print(f'{len(need)} labels no font in the stack draws:', ''.join(ch for ch, _ in need[:60]))


if __name__ == '__main__':
    main()
