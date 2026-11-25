# /// script
# requires-python = ">=3.11"
# dependencies = ["fonttools[woff]==4.63.0", "brotli==1.2.0"]
# ///
"""Build Atlas Sans and Atlas Serif: the two faces every character on the site is set in.

  uv run scripts/build_atlas_fonts.py [--only 4e,200] [--out cache/atlas-fonts]

Each face draws every Han ideograph Unicode assigns, the radicals, strokes, kana, hangul and CJK
punctuation, and the ideographic variation sequences its sources map, from one design per range:

  Atlas Sans   Noto Sans CJK JP, then Plangothic P1 and P2 for the ideographs past it
  Atlas Serif  Noto Serif CJK JP, then Jigmo, Jigmo2 and Jigmo3 (and Plangothic for 𫠞 U+2B81E)

A face is cut into small files, so a page downloads the files of the characters it shows and no
others: eight for the 2,048 characters the corpus writes most (data/vocab/typeface-core.txt), three
for the rest of the interface's own text (data/vocab/typeface-interface.txt), then one per 256 code
points. Those eleven and the kana and punctuation files have a bold cut beside them, as far as Noto
draws them,
from the Noto fonts at weight 700. The registered sequences of each 256 code points are a file of
their own.
`index.json` names each file's characters, as ranges, and the sequences it draws by collection;
`sans.css` and `serif.css` declare the files with those ranges, and `sans-figures.css` and
`serif-figures.css` the files of the sequences; beside each of those files a JSON of the same name
lists the sequences it draws, by base character. `NOTICE.txt` carries each source's copyright notice
beside `OFL.txt`, and each file's name table those of the sources it draws from.

The folder is named by the hash of the sources, the core list and FORMAT, which a change to what
this script draws raises, so a file never changes under a name; a rerun writes the files still
missing. the site serves it from R2 under /atlas/fonts/<version>/ (docs/typefaces.md).
"""
import argparse
import hashlib
import json
import multiprocessing
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.cu2quPen import Cu2QuPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / 'cache/fonts'
CORE = ROOT / 'data/vocab/typeface-core.txt'
INTERFACE = ROOT / 'data/vocab/typeface-interface.txt'
# The licence's text, below the copyright line of the copy that ships with the IDS editor's fonts.
OFL = ROOT / 'apps/review/static/fonts/OFL-Plangothic.txt'
UPEM, ASCENT, DESCENT = 1000, 880, -120
FORMAT = 3

# Every source, pinned. Noto's Reserved Font Name is `Source`; Plangothic declares none; Jigmo is CC0.
SOURCES = {
    'noto-sans': {'file': 'NotoSansCJKjp-VF-2.004.ttf', 'name': 'Noto Sans CJK JP 2.004', 'licence': 'SIL OFL 1.1',
                  'sha256': '240c9b83bf7b386edbae39995ae7e068ed4583f484d92e4a74c34158b5f27b1a', 'at': {'wght': 400},
                  'url': 'https://github.com/notofonts/noto-cjk/raw/Sans2.004/Sans/Variable/TTF/NotoSansCJKjp-VF.ttf'},
    'noto-serif': {'file': 'NotoSerifCJKjp-VF-2.003.ttf', 'name': 'Noto Serif CJK JP 2.003', 'licence': 'SIL OFL 1.1', 'at': {'wght': 400},
                   'url': 'https://github.com/notofonts/noto-cjk/raw/Serif2.003/Serif/Variable/TTF/NotoSerifCJKjp-VF.ttf'},
    # The same two files at weight 700, for the bold cut.
    'noto-sans-bold': {'file': 'NotoSansCJKjp-VF-2.004.ttf', 'name': 'Noto Sans CJK JP 2.004', 'licence': 'SIL OFL 1.1', 'at': {'wght': 700},
                       'url': 'https://github.com/notofonts/noto-cjk/raw/Sans2.004/Sans/Variable/TTF/NotoSansCJKjp-VF.ttf'},
    'noto-serif-bold': {'file': 'NotoSerifCJKjp-VF-2.003.ttf', 'name': 'Noto Serif CJK JP 2.003', 'licence': 'SIL OFL 1.1', 'at': {'wght': 700},
                        'url': 'https://github.com/notofonts/noto-cjk/raw/Serif2.003/Serif/Variable/TTF/NotoSerifCJKjp-VF.ttf'},
    'plangothic-p1': {'file': 'PlangothicP1-Regular.ttf', 'name': 'Plangothic P1 V2.9.5795', 'licence': 'SIL OFL 1.1',
                      'sha256': '550b5d0775b15405946b18f4843df439a51e69508d7e6778d94c1f7a53dc5ad6',
                      'url': 'https://github.com/Fitzgerald-Porthmouth-Koenigsegg/Plangothic-Project/releases/download/V2.9.5795/PlangothicP1-Regular.ttf'},
    'plangothic-p2': {'file': 'PlangothicP2-Regular.ttf', 'name': 'Plangothic P2 V2.9.5795', 'licence': 'SIL OFL 1.1',
                      'sha256': '681933370adfe0fc7253f77735275a82fea09fe4f8adba907bdeb46c110daf8f',
                      'url': 'https://github.com/Fitzgerald-Porthmouth-Koenigsegg/Plangothic-Project/releases/download/V2.9.5795/PlangothicP2-Regular.ttf'},
    'jigmo': {'file': 'Jigmo-20250912-Jigmo.ttf', 'name': 'Jigmo 20250912', 'licence': 'CC0 1.0', 'url': 'https://kamichikoichi.github.io/jigmo/'},
    'jigmo2': {'file': 'Jigmo-20250912-Jigmo2.ttf', 'name': 'Jigmo2 20250912', 'licence': 'CC0 1.0', 'url': 'https://kamichikoichi.github.io/jigmo/'},
    'jigmo3': {'file': 'Jigmo-20250912-Jigmo3.ttf', 'name': 'Jigmo3 20250912', 'licence': 'CC0 1.0', 'url': 'https://kamichikoichi.github.io/jigmo/'},
}
FACES = {
    'sans': {'family': 'Atlas Sans', 'sources': ['noto-sans', 'plangothic-p1', 'plangothic-p2'], 'bold': 'noto-sans-bold'},
    # Plangothic comes last for the one ideograph the serif sources lack, 𫠞 U+2B81E.
    'serif': {'family': 'Atlas Serif', 'sources': ['noto-serif', 'jigmo', 'jigmo2', 'jigmo3', 'plangothic-p1'], 'bold': 'noto-serif-bold'},
}
# What a face draws: the Han ideographs and what is written beside them. Latin, and the scripts with
# a face of their own (Tangut, the historic kana), stay with those faces. So do the conjoining
# hangul jamo: a font joins them into a syllable with its shaping tables, which these files leave out.
SCOPE = [(0x2E80, 0x2FDF), (0x2FF0, 0x33FF), (0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xAC00, 0xD7A3), (0xF900, 0xFAFF), (0xFE10, 0xFE1F), (0xFE30, 0xFE4F), (0xFF00, 0xFFEF),
         (0x20000, 0x3347F)]
# The bold cut: the core and interface files and the kana, punctuation and full-width forms, which headings and
# labels are written in. A character outside them is set in the regular weight.
BOLD = {'weight': 700, 'blocks': ['030', '0ff']}
IVD = {'url': 'https://www.unicode.org/ivd/data/2026-08-03/IVD_Sequences.txt', 'version': '2026-08-03'}
SELECTORS = (0xE0100, 0xE01EF)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source(key):
    record = SOURCES[key]
    path = CACHE / record['file']
    if not path.exists():
        if not record['url'].endswith('.ttf'):
            sys.exit(f"{record['file']} is not in {CACHE}: fetch it from {record['url']}")
        CACHE.mkdir(parents=True, exist_ok=True)
        path.write_bytes(urllib.request.urlopen(record['url'], timeout=600).read())
    found = digest(path)
    assert record.setdefault('sha256', found) == found, f"{record['file']} does not match its pinned hash"
    return path


def registered():
    """The IVD's sequences, (base, selector) -> its collections."""
    path = CACHE / f"IVD_Sequences-{IVD['version']}.txt"
    if not path.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        path.write_bytes(urllib.request.urlopen(IVD['url'], timeout=120).read())
    found = defaultdict(list)
    for line in path.read_text(encoding='utf-8').splitlines():
        if line and not line.startswith('#'):
            sequence, collection, _ = (part.strip() for part in line.split(';'))
            base, selector = (int(code, 16) for code in sequence.split())
            found[base, selector].append(collection)
    return found


class Source:
    def __init__(self, key):
        self.key = key
        self.font = TTFont(source(key), lazy=True)
        self.cmap = self.font.getBestCmap()
        self.glyphs = self.font.getGlyphSet(location=SOURCES[key].get('at'))
        self.scale = UPEM / self.font['head'].unitsPerEm
        self.copyright = self.font['name'].getDebugName(0) or ''
        self.cubic = 'glyf' not in self.font
        # A sequence the font maps: its own glyph, or the base character's where it is the default.
        self.sequences = {}
        for table in self.font['cmap'].tables:
            if table.format == 14:
                for selector, pairs in table.uvsDict.items():
                    for base, glyph in pairs:
                        if glyph or base in self.cmap:
                            self.sequences[base, selector] = glyph or self.cmap[base]

    def draw(self, name):
        pen = TTGlyphPen(None)
        to = TransformPen(pen, (self.scale, 0, 0, self.scale, 0, 0)) if self.scale != 1 else pen
        self.glyphs[name].draw(Cu2QuPen(to, 0.5, reverse_direction=True) if self.cubic else to)
        return pen.glyph(dropImpliedOnCurves=True), round(self.font['hmtx'][name][0] * self.scale)


def ranges(points):
    out = []
    for point in sorted(points):
        if out and out[-1][1] == point - 1:
            out[-1][1] = point
        else:
            out.append([point, point])
    return out


def lines(path):
    """A list file's characters, one list per font file."""
    return [list(line) for line in path.read_text(encoding='utf-8').splitlines() if line and not line.startswith('#')]


LOADED = {}


def loaded(key):
    """A source, opened once in each process."""
    if key not in LOADED:
        LOADED[key] = Source(key)
    return LOADED[key]


def write(task):
    """One font file from `glyphs`, name -> (source key, its glyph name); returns its size and hash.
    Its name table states the sources it draws from. A file already written under this version is kept."""
    path, family, glyphs, cmap, uvs, weight = task
    keys = sorted({key for key, _ in glyphs.values()}, key=list(SOURCES).index)
    if not path.exists():
        order, glyf, advances = ['.notdef'], {'.notdef': TTGlyphPen(None).glyph()}, {'.notdef': UPEM}
        for name, (key, source_name) in glyphs.items():
            order.append(name)
            glyf[name], advances[name] = loaded(key).draw(source_name)
        builder = FontBuilder(UPEM, isTTF=True)
        builder.setupGlyphOrder(order)
        builder.setupCharacterMap(cmap, uvs=uvs or None)
        builder.setupGlyf(glyf)
        bounds = builder.font['glyf']
        builder.setupHorizontalMetrics({name: (advance, getattr(bounds[name], 'xMin', 0)) for name, advance in advances.items()})
        builder.setupHorizontalHeader(ascent=ASCENT, descent=DESCENT)
        builder.setupNameTable({'familyName': family, 'styleName': 'Bold' if weight > 400 else 'Regular',
                                'copyright': ' '.join(f"{SOURCES[key]['name']}: {loaded(key).copyright.strip()} ({SOURCES[key]['licence']})." for key in keys),
                                'licenseDescription': 'This Font Software is licensed under the SIL Open Font License, Version 1.1 (OFL.txt beside these files, and https://openfontlicense.org).'
                                                      + (' The outlines from Jigmo are dedicated to the public domain under CC0 1.0.' if any(key.startswith('jigmo') for key in keys) else ''),
                                'licenseInfoURL': 'https://openfontlicense.org'})
        builder.setupOS2(usWeightClass=weight, sTypoAscender=ASCENT, sTypoDescender=DESCENT, sTypoLineGap=0, usWinAscent=ASCENT, usWinDescent=-DESCENT)
        builder.setupPost(keepGlyphNames=False)
        builder.font.flavor = 'woff2'
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix('.part')
        builder.save(partial)
        partial.rename(path)
    data = path.read_bytes()
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()[:16]}


def plan(face, ivd, only, out):
    """The face's index entry without sizes, and the files to write as (group, name, task).

    A character is in one file: `c<n>` for the nth line of the core list, `u<n>` for the nth of the
    interface list, else the file of its 256 code points. The registered sequences of a block's characters are a file of their own under
    `figures/`, in the family `<family> Figures`, with the characters they vary: text with no
    variation selector never downloads them."""
    family, keys = FACES[face]['family'], FACES[face]['sources']
    drawn = {}        # code point -> (source key, glyph name)
    sequences = {}    # (base, selector) -> (source key, glyph name)
    for key in keys:
        item = loaded(key)
        for low, high in SCOPE:
            for point in range(low, high + 1):
                if point in item.cmap:
                    drawn.setdefault(point, (key, item.cmap[point]))
        for pair, glyph in item.sequences.items():
            if pair in ivd and pair[0] in drawn:
                sequences.setdefault(pair, (key, glyph))
    files = defaultdict(list)
    common = {}
    for prefix, path in (('c', CORE), ('u', INTERFACE)):
        for number, line in enumerate(lines(path)):
            for char in line:
                assert ord(char) in drawn, f'{face} does not draw {char} of {path.name}'
                common.setdefault(ord(char), f'{prefix}{number}')
    for point in drawn:
        files[common.get(point) or format(point >> 8, 'x').zfill(3)].append(point)
    varied = defaultdict(list)
    for pair in sequences:
        varied[format(pair[0] >> 8, 'x').zfill(3)].append(pair)
    total = defaultdict(int)
    for pair in sequences:
        for collection in ivd[pair]:
            total[collection] += 1
    entry = {'family': family, 'sources': keys, 'characters': len(drawn), 'sequences': dict(sorted(total.items())), 'files': {}, 'bold': {}, 'figures': {}}
    tasks, lists = [], {}   # lists: figures file -> {base in hex: its selectors, counted from U+E0100}
    for name in sorted(files):
        if only and name not in only:
            continue
        points = sorted(files[name])
        entry['files'][name] = {'ranges': ranges(points)}
        tasks.append((face, 'files', name, (out / face / f'{name}.woff2', family, {f'u{point:X}': drawn[point] for point in points},
                                            {point: f'u{point:X}' for point in points}, None, 400)))
        heavy = loaded(FACES[face]['bold'])
        if name[0] in 'cu' or name in BOLD['blocks']:
            points = [point for point in points if point in heavy.cmap]
            entry['bold'][name] = {'ranges': ranges(points)}
            tasks.append((face, 'bold', name, (out / face / 'bold' / f'{name}.woff2', family, {f'u{point:X}': (heavy.key, heavy.cmap[point]) for point in points},
                                               {point: f'u{point:X}' for point in points}, None, BOLD['weight'])))
    for name in sorted(varied):
        if only and name not in only:
            continue
        pairs = sorted(varied[name])
        bases = sorted({base for base, _ in pairs})
        glyphs = {f'u{base:X}': drawn[base] for base in bases}
        uvs = []
        for base, selector in pairs:
            same = sequences[base, selector] == drawn[base]
            if not same:
                glyphs[f'u{base:X}.{selector:X}'] = sequences[base, selector]
            uvs.append((base, selector, f'u{base:X}' if same else f'u{base:X}.{selector:X}'))
        by = defaultdict(int)
        for pair in pairs:
            for collection in ivd[pair]:
                by[collection] += 1
        entry['figures'][name] = {'ranges': ranges(bases), 'sequences': dict(sorted(by.items()))}
        listed = defaultdict(list)
        for base, selector in pairs:
            listed[f'{base:X}'].append(selector - SELECTORS[0])
        lists[name] = dict(listed)
        tasks.append((face, 'figures', name, (out / face / 'figures' / f'{name}.woff2', f'{family} Figures', glyphs,
                                              {base: f'u{base:X}' for base in bases}, uvs, 400)))
    return entry, tasks, lists


def sheet(family, folder, files, selectors=False, weight=400):
    """The style sheet that declares one family's files, each with the characters it draws, so a
    browser downloads a file when the page shows one of them."""
    def span(low, high):
        return f'U+{low:X}' if low == high else f'U+{low:X}-{high:X}'
    return ''.join(
        f'@font-face{{font-family:"{family}";src:url("{folder}{name}.woff2") format("woff2");font-weight:{weight};font-display:swap;'
        f'unicode-range:{",".join(span(*pair) for pair in file["ranges"] + ([list(SELECTORS)] if selectors else []))}}}\n'
        for name, file in files.items())


def made(job):
    face, group, name, task = job
    return face, group, name, write(task)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--only', help='comma-separated file names (4e,200), for a trial')
    parser.add_argument('--out', type=Path, default=ROOT / 'cache/atlas-fonts')
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()
    only = {name if name[0] in 'cu' else name.zfill(3) for name in args.only.split(',')} if args.only else None
    ivd = registered()
    for key in SOURCES:
        source(key)
    version = hashlib.sha256(json.dumps([FORMAT, IVD['version'], SCOPE, FACES, BOLD, {key: SOURCES[key]['sha256'] for key in sorted(SOURCES)},
                                         digest(CORE), digest(INTERFACE)], sort_keys=True).encode()).hexdigest()[:12]
    out = args.out / version
    faces, jobs, lists = {}, [], {}
    for face in FACES:
        faces[face], tasks, lists[face] = plan(face, ivd, only, out)
        jobs += tasks
    notices = ''.join(f"{SOURCES[key]['name']} ({SOURCES[key]['licence']}, {SOURCES[key]['url']})\n{loaded(key).copyright.strip()}\n\n"
                      for key in SOURCES if not key.endswith('-bold'))
    LOADED.clear()   # each worker opens its own
    with multiprocessing.Pool(args.workers) as pool:
        for count, (face, group, name, size) in enumerate(pool.imap_unordered(made, jobs), 1):
            faces[face][group][name] = {**size, **faces[face][group][name]}
            print(f'{count}/{len(jobs)}', face, group, name, size['bytes'], flush=True)
    for entry in faces.values():
        entry['bytes'] = sum(file['bytes'] for group in ('files', 'bold', 'figures') for file in entry[group].values())
    index = {'format': FORMAT, 'version': version, 'upem': UPEM, 'ascent': ASCENT, 'descent': DESCENT, 'ivd': IVD['version'],
             'registered': dict(sorted((collection, sum(1 for collections in ivd.values() if collection in collections))
                                       for collection in {c for cs in ivd.values() for c in cs})),
             'sources': {key: {field: SOURCES[key][field] for field in ('name', 'licence', 'sha256', 'url')} for key in SOURCES},
             'faces': faces}
    # A trial leaves the folder's index, sheets and notices as a whole build wrote them.
    if not only:
        for face, entry in faces.items():
            # A browser draws bold text from the bold faces alone and leaves a character they lack to
            # the next family, so every file with no bold cut is declared at the bold weight as well.
            plain = {name: file for name, file in entry['files'].items() if name not in entry['bold']}
            (out / f'{face}.css').write_text(sheet(entry['family'], f'{face}/', entry['files'])
                                             + sheet(entry['family'], f'{face}/bold/', entry['bold'], weight=BOLD['weight'])
                                             + sheet(entry['family'], f'{face}/', plain, weight=BOLD['weight']), encoding='utf-8')
            (out / f'{face}-figures.css').write_text(sheet(f"{entry['family']} Figures", f'{face}/figures/', entry['figures'], selectors=True), encoding='utf-8')
            for name, listed in lists[face].items():
                (out / face / 'figures' / f'{name}.json').write_text(json.dumps(listed, separators=(',', ':')) + '\n', encoding='utf-8')
        (out / 'index.json').write_text(json.dumps(index, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
        (out / 'NOTICE.txt').write_text('Atlas Sans and Atlas Serif are built from the fonts below and are licensed under the SIL Open Font '
                                        'License, Version 1.1 (OFL.txt); the outlines from Jigmo are under CC0 1.0.\n\n' + notices, encoding='utf-8')
        text = OFL.read_text(encoding='utf-8')
        (out / 'OFL.txt').write_text(text[text.index('This Font Software is licensed'):], encoding='utf-8')
    for face, entry in index['faces'].items():
        print(face, entry['characters'], 'characters', entry['sequences'], f"{entry['bytes'] / 1e6:.1f} MB in {len(entry['files'])} + {len(entry['figures'])} files")
    print(out)


if __name__ == '__main__':
    main()
