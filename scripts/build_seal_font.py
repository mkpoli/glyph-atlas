"""Build the small seal label font for the review interface, split into small files.

Sealie draws the small seal characters of Unicode 18.0 (U+3D000 to U+3FC3F). Its outlines are heavy,
about 1 KB a character in WOFF2 and 11 MB in all, and a page shows a few seal characters at most:
every script legend shows one, the seal form of 篆, U+3DD16. So that one gets a file of its own, and
the rest are cut, in code point order, into files of CHUNK characters, each with its own
`unicode-range`, so a page downloads only the files for the characters it shows.

Sealie keeps its forms narrow and well inside the em: a seal character fills about half the width and
three quarters of the height of its square, where a Han character of the site's faces fills nine
tenths of both, and its Regular strokes are lighter than theirs. Beside Han text at the same size it
reads as a smaller and thinner letter. So the build takes the Medium weight and enlarges every
outline by SCALE about the middle of the square, to the height of the ideographic em box, leaving the
advance as it is.

The files are too many and too heavy for the app's own assets, so a build is a folder served from R2
like the Atlas faces (docs/typefaces.md): named by the hash of the source, the licence and how the
font is cut, uploaded once, and never changed under that name.

  uv run scripts/build_seal_font.py
  rclone copy cache/seal-font/<version> r2:glyph-atlas/fonts/<version>

Writes cache/seal-font/<version>/ (seal/s.woff2 for the sample, seal/000.woff2 onwards, OFL.txt),
apps/review/src/seal-font.css, and the version in apps/cloudflare/src/typefaceVersions.ts, first in its list.
"""
import hashlib
import io
import json
import re
import urllib.request
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'apps/review'
BUILDS = ROOT / 'cache/seal-font'
CSS = APP / 'src/seal-font.css'
VERSIONS = ROOT / 'apps/cloudflare/src/typefaceVersions.ts'
CACHE = ROOT / 'cache/fonts'
FORMAT = 2   # raised when BLOCK or the way the font is cut or named changes
CHUNK = 64   # characters a file
SAMPLE = 0x3DD16   # the script legend's sample, the seal form of 篆
# Sealie at commit c8d4a98 of iebb/sealie, 38 minutes after release 1.3.1: upstream's own workflow build of
# that commit (run 44912812191), which differs from 1.3.1 in U+3EA9E alone, the left 匕 of 比 drawn with both
# strokes. No release holds it yet, so the files are kept as a snapshot release of a fork. Return to
# iebb/sealie's releases with the first after 1.3.1.
BASE = 'https://github.com/mkpoli/sealie/releases/download/v1.3.1-c8d4a98/'
FONT = {'url': BASE + 'Sealie-Medium.ttf', 'sha256': '4c1a5f2e118c47bb7bd604848e65278d412b1876bc52145850b2e36d43e60027'}
SCALE = 1.3   # the tallest form then stands 0.99 em and the widest 0.96 em
BOX = (-0.12, 0.88)   # the ideographic em box the enlarged forms are centred in, in em above the baseline
LICENCE = {'url': BASE + 'LICENSE-OFL.txt', 'sha256': 'e81b6564eca2b29417c198627fc843d86067b9230785d2c6344608e3f4cf0719'}
BLOCK = (0x3D000, 0x3FC3F)


def fetch(url, sha256):
    path = CACHE / url.rsplit('/', 1)[1]
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == sha256:
        return path.read_bytes()
    data = urllib.request.urlopen(url, timeout=300).read()
    if hashlib.sha256(data).hexdigest() != sha256:
        raise SystemExit(f'{url} does not match its pinned hash')
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def ranges(codes):
    out = []
    for c in sorted(codes):
        if out and out[-1][1] == c - 1:
            out[-1][1] = c
        else:
            out.append([c, c])
    return ', '.join(f'U+{a:X}' if a == b else f'U+{a:X}-{b:X}' for a, b in out)


def enlarge(source):
    """The font with every outline SCALE times its size, centred in the square and in BOX."""
    font = TTFont(io.BytesIO(source), recalcTimestamp=False)
    em, glyf, widths = font['head'].unitsPerEm, font['glyf'], font['hmtx']
    drawn = [glyf[name] for name in font.getGlyphOrder() if glyf[name].numberOfContours > 0]
    middle = (min(g.yMin for g in drawn) + max(g.yMax for g in drawn)) / 2   # of the band the forms share
    target = (BOX[0] + BOX[1]) / 2 * em
    for name in font.getGlyphOrder():
        glyph = glyf[name]
        if glyph.numberOfContours < 0:
            raise SystemExit(f'{name} is a composite glyph, which the enlargement does not handle')
        if glyph.numberOfContours == 0:
            continue
        advance = widths[name][0]
        glyph.coordinates.translate((-advance / 2, -middle))
        glyph.coordinates.scale((SCALE, SCALE))
        glyph.coordinates.translate((advance / 2, target))
        glyph.coordinates.toInt()
        glyph.recalcBounds(glyf)
        if glyph.xMin < 0 or glyph.xMax > advance:
            raise SystemExit(f'{name} is wider than its advance once enlarged')
        widths[name] = (advance, glyph.xMin)
    buffer = io.BytesIO(); font.save(buffer)
    return buffer.getvalue()


def cut(source, codes, out, version, name):
    font = TTFont(io.BytesIO(source), recalcTimestamp=False)
    options = subset.Options(); options.flavor = 'woff2'; options.layout_features = ['*']
    sub = subset.Subsetter(options=options); sub.populate(unicodes=codes); sub.subset(font)
    buffer = io.BytesIO(); subset.save_font(font, buffer, options)
    data = buffer.getvalue()
    (out / 'seal' / f'{name}.woff2').write_bytes(data)
    return (f'@font-face {{\n  font-family: "Sealie";\n  src: url("/atlas/fonts/{version}/seal/{name}.woff2") format("woff2");\n'
            f'  font-display: swap;\n  unicode-range: {ranges(codes)};\n}}\n'), len(data)


def main():
    source = fetch(FONT['url'], FONT['sha256'])
    drawn = sorted(c for c in TTFont(io.BytesIO(source)).getBestCmap() if BLOCK[0] <= c <= BLOCK[1])
    version = hashlib.sha256(json.dumps([FORMAT, FONT['sha256'], LICENCE['sha256'], CHUNK, SAMPLE, SCALE, BOX]).encode()).hexdigest()[:12]
    source = enlarge(source)
    out = BUILDS / version
    (out / 'seal').mkdir(parents=True, exist_ok=True)
    for old in (out / 'seal').glob('*.woff2'):
        old.unlink()
    (out / 'OFL.txt').write_bytes(fetch(LICENCE['url'], LICENCE['sha256']))
    rest = [c for c in drawn if c != SAMPLE]
    cuts = [cut(source, [SAMPLE], out, version, 's')] + [
        cut(source, rest[a:a + CHUNK], out, version, f'{a // CHUNK:03x}') for a in range(0, len(rest), CHUNK)]
    named = VERSIONS.read_text()
    if f"'{version}'" not in named:   # a new build goes first; the earlier ones stay served
        named = re.sub(r"SEAL_FONT_VERSIONS = \[", f"SEAL_FONT_VERSIONS = ['{version}', ", named)
        if f"'{version}'" not in named:
            raise SystemExit(f'{VERSIONS} has no SEAL_FONT_VERSIONS list')
        VERSIONS.write_text(named)
    CSS.write_text('/* Generated by scripts/build_seal_font.py: Sealie 1.3.1 Medium with the correction of U+3EA9E, enlarged, the legend\'s sample alone and the rest in\n'
                   f'   files of {CHUNK} characters in code point order, so a page downloads only the ones it shows. */\n'
                   + ''.join(face for face, _ in cuts))
    sizes = [size for _, size in cuts]
    print(f'{len(drawn)} characters in {len(cuts)} files: the sample {sizes[0]} B, the rest '
          f'{min(sizes[1:]) // 1024}–{max(sizes[1:]) // 1024} KB, {sum(sizes) // 1024} KB in all\n-> {out}')


if __name__ == '__main__':
    main()
