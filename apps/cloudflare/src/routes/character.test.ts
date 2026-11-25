import { beforeAll, beforeEach, describe, expect, it, setSystemTime } from 'bun:test';
import { Database } from 'bun:sqlite';
import { readdirSync, readFileSync } from 'node:fs';
import { gzipSync } from 'node:zlib';
import { IDS_DIGITS, referenceShard } from '../atlas3Reference';
import { d1 } from '../forms.test';
import { bundleKey, cropId, publicId } from '../atlas3';
import { currentPointer } from '../atlas3Answers';
import { SHARDS, phasesOf, rangeCountQuery, rangeQuery, type GalleryQuery, type Phase, type Source } from '../atlas3Gallery';
import { corpusFormsQuery, datingQuery } from '../atlas3Listing';
import { cropSlotsQuery } from '../cropPanels';
import { carriedFormsQuery, bookCellsQuery, bookCropsQuery, booksOfQuery, composedCountQuery, composedQuery, chronologyQueries, copyOf, graphemeIdsQuery, groupKeysQuery, membersQuery, productionsQuery, visualQuery, writeFamiliesQuery,
  wordSpellingsQuery, writtenVariants } from './character';
import { publish } from './browse.fixture.test';
import worker from '../index';

// The Workers cache.
const edge = new Map<string, Response>();
(globalThis as any).caches = { default: {
  match: async (request: Request) => edge.get(request.url)?.clone(),
  put: async (request: Request, response: Response) => { edge.set(request.url, response.clone()); },
  delete: async (request: Request) => edge.delete(request.url),
} };
beforeEach(() => edge.clear());

// One small collection for every test, since reference objects, bundles and count shards are held per
// isolate: 時 (with 时 and 旹 in its family, 峕 a variant), and ね with a hentaigana and corpus glyphs
// filed under it with no character. Grapheme ids stay off the shards other test files read (7, 9).
const G = { 時: 300, 时: 301, 旹: 302, 峕: 303, ね: 400, '𛂒': 401, 事: 500, 亊: 501, 'ヿ': 510, 丸: 530, 凡: 531 } as Record<string, number>;
// Each character's family head, which its crops' records name as their family.
const HEAD = { 時: '時', 时: '時', 旹: '時', 峕: '峕', ね: 'ね', '𛂒': 'ね', 丸: '丸', 凡: '凡' } as Record<string, string>;
const BOOK = { a: 1, b: 2, c: 3, d: 4, e: 5, f: 6, dataset: 7, unnamed: 8, honkoku: 9, lines: 10, corpus: 11, copy: 12, hng: 13, cut: 14 };
const PAGE_A = 7001, PAGE_B = 7002, PAGE_C = 7003, PAGE_D = 7004, PAGE_E = 7005, PAGE_F = 7006;
// A page of each book of 凡's, from its book's id.
const pageOf = (book: number) => 7100 + book;
const PACK = 'packs/test.bin';
type Crop = { slot: number; page: number; kind: number; char: string | null; family?: string; style: number; state?: number; quiz?: number;
  production?: number; visual?: number; shuffle: number };
const BOOK_OF = { [PAGE_A]: BOOK.a, [PAGE_B]: BOOK.b, [PAGE_C]: BOOK.c, [PAGE_D]: BOOK.d, [PAGE_E]: BOOK.e, [PAGE_F]: BOOK.f, ...Object.fromEntries([7, 8, 9, 10, 11, 12, 13, 14].map(book => [pageOf(book), book])) } as Record<number, number>;
const YEAR = { [BOOK.a]: 1700, [BOOK.b]: 1813, [BOOK.c]: null, [BOOK.d]: 1890, [BOOK.e]: 1900, [BOOK.f]: 1901, [BOOK.dataset]: null, [BOOK.unnamed]: null, [BOOK.honkoku]: 1800, [BOOK.lines]: 1800, [BOOK.corpus]: 1750, [BOOK.copy]: null, [BOOK.hng]: 600, [BOOK.cut]: 600 } as Record<number, number | null>;
const CROPS: Crop[] = [
  // 時: cursive, unassessed and formal, one withheld, one flagged.
  { slot: 0, page: PAGE_A, kind: 0, char: '時', style: 0, shuffle: 50, production: 2 },
  { slot: 1, page: PAGE_A, kind: 0, char: '時', style: 1, shuffle: 10, production: 2 },
  { slot: 2, page: PAGE_A, kind: 0, char: '時', style: 2, shuffle: 30, quiz: 0, production: 2 },
  { slot: 0, page: PAGE_B, kind: 0, char: '時', style: 2, shuffle: 20, state: 2, production: 3 },
  { slot: 1, page: PAGE_B, kind: 0, char: '時', style: 4, shuffle: 40, production: 3 },
  { slot: 2, page: PAGE_B, kind: 0, char: '时', style: 0, shuffle: 5, production: 3 },
  { slot: 3, page: PAGE_B, kind: 0, char: '旹', style: 5, shuffle: 25, production: 3 },
  { slot: 4, page: PAGE_B, kind: 0, char: '峕', style: 2, shuffle: 15, production: 3 },
  // Corpus glyphs: 時 and 𛂒, and three glyphs of the ね family with no character.
  { slot: 0, page: PAGE_C, kind: 1, char: '時', style: 2, shuffle: 60, production: 1 },
  { slot: 1, page: PAGE_C, kind: 1, char: '𛂒', style: 2, shuffle: 70, production: 1 },
  { slot: 2, page: PAGE_C, kind: 1, char: null, family: 'ね', style: 2, shuffle: 80, production: 1, visual: 1 },
  { slot: 3, page: PAGE_C, kind: 1, char: null, family: 'ね', style: 0, shuffle: 65, production: 1, visual: 1 },
  { slot: 4, page: PAGE_C, kind: 1, char: null, family: 'ね', style: 2, shuffle: 90, production: 1 },
  // A corpus glyph of 時 in book a, so the decades place one corpus glyph.
  { slot: 3, page: PAGE_A, kind: 1, char: '時', style: 2, shuffle: 35, production: 1 },
  // 丸: a book of two styles with corpus glyphs, one of them withheld, and two books of one crop each.
  { slot: 0, page: PAGE_D, kind: 0, char: '丸', style: 0, shuffle: 10 },
  { slot: 1, page: PAGE_D, kind: 0, char: '丸', style: 4, shuffle: 20 },
  { slot: 2, page: PAGE_D, kind: 1, char: '丸', style: 2, shuffle: 5, quiz: 0 },
  { slot: 3, page: PAGE_D, kind: 1, char: '丸', style: 2, shuffle: 6 },
  { slot: 0, page: PAGE_E, kind: 0, char: '丸', style: 2, shuffle: 30 },
  { slot: 0, page: PAGE_F, kind: 0, char: '丸', style: 2, shuffle: 25 },
  // 凡: a dataset that names no document, crops of no named book, and three copies two records each hold.
  ...[10, 20, 30, 40].map((shuffle, slot) => ({ slot, page: pageOf(BOOK.dataset), kind: 1, char: '凡', style: 2, shuffle })),
  { slot: 0, page: pageOf(BOOK.unnamed), kind: 1, char: '凡', style: 2, shuffle: 15 },
  { slot: 0, page: pageOf(BOOK.honkoku), kind: 0, char: '凡', style: 2, shuffle: 10 },
  { slot: 0, page: pageOf(BOOK.lines), kind: 0, char: '凡', style: 2, shuffle: 5 },
  { slot: 1, page: pageOf(BOOK.lines), kind: 1, char: '凡', style: 2, shuffle: 1 },
  { slot: 0, page: pageOf(BOOK.corpus), kind: 1, char: '凡', style: 2, shuffle: 10 },
  { slot: 1, page: pageOf(BOOK.corpus), kind: 1, char: '凡', style: 2, shuffle: 20 },
  { slot: 0, page: pageOf(BOOK.copy), kind: 0, char: '凡', style: 2, shuffle: 30 },
  { slot: 0, page: pageOf(BOOK.hng), kind: 1, char: '凡', style: 2, shuffle: 20 },
  { slot: 0, page: pageOf(BOOK.cut), kind: 1, char: '凡', style: 2, shuffle: 10 },
];
const id = (c: Crop) => cropId(c.page, c.slot);
const sourceId = (c: Crop) => `${c.kind ? 'codh' : 'hk'}:${c.page}:${c.slot}`;

let db: Database, env: Env;
const ctx = { waitUntil: () => {}, passThroughOnException() {} } as unknown as ExecutionContext;
const objects = new Map<string, Uint8Array>();
const json = (value: unknown) => gzipSync(JSON.stringify(value));

function bucket() {
  return { get: async (name: string, options?: { range?: { offset: number; length: number } }) => {
    // Any publication's counts are this collection's, so a pointer another test file left held still finds them.
    const counts = /^v3\/pub\/[^/]+\/counts\/(\d+)\.json\.gz$/.exec(name);
    let bytes = objects.get(counts ? `v3/pub/T/counts/${counts[1]}.json.gz` : name);
    if (!bytes) return null;
    if (options?.range) bytes = bytes.subarray(options.range.offset, options.range.offset + options.range.length);
    const copy = new Uint8Array(bytes);
    return { body: new Response(copy).body, arrayBuffer: async () => copy.buffer };
  } };
}
const card = (char: string, extra: object = {}) => {
  const code_point = 'U+' + char.codePointAt(0)!.toString(16).toUpperCase().padStart(4, '0');
  return { code_point, char, name: null, url: `/layers/characters/${code_point}`, default_scope: 'character', candidates: { status: 'ok', known: true }, ...extra };
};

beforeAll(() => {
  db = new Database(':memory:');
  const migrations = new URL('../../migrations-atlas/', import.meta.url);
  for (const file of readdirSync(migrations).filter(f => f.endsWith('.sql')).sort()) db.exec(readFileSync(new URL(file, migrations), 'utf8'));
  db.exec(`INSERT INTO books(id,key,title) VALUES(1,'hk:a','A'),(2,'hk:b','B'),(3,'codh:c','C'),(4,'hk:d','D'),(5,'hk:e','E'),(6,'hk:f','F'),(7,'hi:kuzushiji-2023','データセット'),(8,'hk',NULL),
      (9,'hk:055a6724cf2e402237834afa675596ac','物'),(10,'hl:055A6724CF2E402237834AFA675596AC','物類'),(11,'codh:200022050','料理'),
      (12,'hl:1793909603875BE815ED30CF80427250','料理本'),(13,'hng:fhs','経'),(14,'hng-kiridashi:fhs','経');
    INSERT INTO productions VALUES(1,'printed',NULL),(2,'printed/woodblock',1),(3,'handwritten',NULL);
    INSERT INTO codes VALUES('state',1,'checked'),('state',2,'flagged'),('style',1,'running'),('style',4,'formal'),('style',5,'printed'),
      ('visual_group',1,'U+306D:abc'),('category',2,'kanji');
    INSERT INTO pages(id,key,book,rev,next_slot) VALUES(${PAGE_A},'hk:a:1',1,1,9),(${PAGE_B},'hk:b:1',2,1,9),(${PAGE_C},'corpus:codh:c:0',3,1,9),(${PAGE_D},'hk:d:1',4,1,9),(${PAGE_E},'hk:e:1',5,1,9),(${PAGE_F},'hk:f:1',6,1,9),${[7, 8, 9, 10, 11, 12, 13, 14].map(book => `(${pageOf(book)},'page:${book}:1',${book},1,9)`).join()};
    UPDATE generation SET gen=6;
    INSERT INTO book_dates(book,axis,kind,start,end,precision,text,label,status,calendar,source) VALUES
      (1,'witness','copied',1700,1700,'year','1700','1700','single','gregorian','s'),
      (1,'composed','composed',1653,1653,'year','1653','1653','single','gregorian','s'),
      (2,'witness','printed',1813,1813,'year','1813','1813','single','gregorian','s');
    INSERT INTO site VALUES('variant_sources','{"src1":"Source One"}'),('word_sources','{"dict":"A dictionary"}');
    INSERT INTO words VALUES('w:toki','ja','とき','noun');
    INSERT INTO word_spellings VALUES('w:toki','時','dict','p1','attested','dict','b','',' ',3,9),('w:toki','刻','dict','p2','attested','dict','b','','',1,2);`);
  for (const [text, gid] of Object.entries(G)) db.query('INSERT INTO graphemes(id,text,head) VALUES(?,?,?)')
    .run(gid, text, ['時', '时', '旹'].includes(text) ? G.時 : ['ね', '𛂒'].includes(text) ? G.ね : text === '亊' ? G.事 : gid);
  const pack: Uint8Array[] = [];
  let offset = 0;
  const entries = new Map<number, object[]>();
  for (const c of CROPS) {
    const book = BOOK_OF[c.page];
    db.query(`INSERT INTO crops(id,kind,book,char,family,visual_group,production,category,style,state,quiz,year,shuffle) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)`)
      .run(id(c), c.kind, book, c.char ? G[c.char] : null, G[c.family ?? HEAD[c.char!]], c.visual ?? null, c.production ?? null, c.kind ? null : 2,
        c.style, c.state ?? 1, c.quiz ?? 1, YEAR[book], c.shuffle);
    const record = { id: sourceId(c), label: c.char ?? c.family, image: `/atlas/media/${sourceId(c).replace(/:/g, '')}.webp` };
    let entry: object = { slot: c.slot, id: sourceId(c), ...record };
    if (c.kind) {
      const bytes = new TextEncoder().encode(JSON.stringify(record));
      entry = { slot: c.slot, id: sourceId(c), label: record.label, record_place: [PACK, offset, bytes.length] };
      pack.push(bytes); offset += bytes.length;
    }
    entries.set(c.page, [...(entries.get(c.page) ?? []), entry]);
  }
  objects.set(PACK, new Uint8Array(Buffer.concat(pack)));
  for (const [page, crops] of entries) objects.set(bundleKey(page, 1), json({ key: `p${page}`, book: BOOK_OF[page], rev: 1, page: null, crops }));
  // The answers, counted as answers.py counts them.
  const DECADE = 'CASE WHEN year IS NULL THEN NULL WHEN year < 0 THEN (year - 9) / 10 * 10  ELSE year / 10 * 10 END';
  const cells = (key: string, where: string) => {
    const out: Record<string, unknown[][]> = {};
    for (const row of db.query(`SELECT ${key} AS k,kind,style,state,quiz,${DECADE} AS decade,visual_group,count(*) AS n FROM crops WHERE kind<2 AND ${where}
      GROUP BY 1,2,3,4,5,6,7`).all() as any[]) (out[row.k] ??= []).push([row.kind, row.style, row.state, row.quiz, row.decade, row.visual_group, row.n]);
    return out;
  };
  const chars = cells('char', 'char IS NOT NULL'), families = cells('family', 'char IS NULL AND family IS NOT NULL');
  for (let shard = 0; shard < SHARDS; shard++) {
    const pick = (all: Record<string, unknown>) => Object.fromEntries(Object.entries(all).filter(([k]) => Number(k) % SHARDS === shard));
    objects.set(`v3/pub/T/counts/${shard}.json.gz`, json({ pub: 'T', chars: pick(chars), families: pick(families) }));
  }
  objects.set('v3/pointer.json', json({ pub: 'T', through_gen: 4 }));
  const filed = CROPS.filter(c => c.visual);
  objects.set('v3/reference/char/U+6642.json.gz', json({ grapheme: G.時,
    data: card('時', { grapheme: { code_point: 'U+6642', char: '時' } }), detail: card('時', { grapheme: { code_point: 'U+6642', char: '時',
      members: [{ code_point: 'U+6642', char: '時' }, { code_point: 'U+65F6', char: '时' }, { code_point: 'U+65F9', char: '旹' }, { code_point: 'U+5446', char: '呆' }] },
      visual_analysis: { groups: [] } }),
    variants: [['旹', 'variant', 'src1', '', 0, 1], ['旹', 'variant', 'src2', '', 0, 1], ['峕', 'variant', 'src1', '', 1, 1], ['时', 'simplified', 'src2', '', 0, 1]],
    derived: [['⿰日寺', [[['日', '目']]]]], substitutions: [['日', '目', 4, [{ a: '昨', b: '眲', sources: ['src3'] }]]],
    writes: [['旹', 1], ['峕', 1]], writers: [['时', 1]], ids: { 時: G.時, 旹: G.旹, 峕: G.峕, 时: G.时 } }));
  objects.set('v3/reference/char/U+306D.json.gz', json({ grapheme: G.ね, data: card('ね', { grapheme: { code_point: 'U+306D', char: 'ね' } }),
    detail: card('ね', { grapheme: { code_point: 'U+306D', char: 'ね', members: [{ code_point: 'U+306D', char: 'ね' }, { code_point: 'U+1B092', char: '𛂒' }] }, visual_analysis: { status: 'ready', groups: [{ code: 1, label: 'Group 1', count: 2,
      representatives: [{ crop: id(filed[0]), source_label: 'ね' }], anchors: [{ crop: id(filed[1]), distance: 0.1 }] }] } }),
    variants: [], derived: [], substitutions: [], writes: [], writers: [], ids: { ね: G.ね, '𛂒': G['𛂒'] } }));
  objects.set('v3/reference/char/U+1B092.json.gz', json({ grapheme: G['𛂒'], data: card('𛂒', { grapheme: { code_point: 'U+306D', char: 'ね' } }),
    detail: card('𛂒', { grapheme: { code_point: 'U+306D', char: 'ね' } }), variants: [], derived: [], substitutions: [], writes: [], writers: [],
    ids: { ね: G.ね, '𛂒': G['𛂒'] } }));
  objects.set('v3/reference/char/U+5CD5.json.gz', json({ grapheme: G.峕, data: card('峕', { grapheme: { code_point: 'U+5CD5', char: '峕' } }),
    detail: card('峕', { grapheme: { code_point: 'U+5CD5', char: '峕', members: [{ code_point: 'U+5CD5', char: '峕' }] } }), variants: [], derived: [], substitutions: [], writes: [], writers: [], ids: { 峕: G.峕 } }));
  objects.set('v3/reference/char/U+4E38.json.gz', json({ grapheme: G.丸, data: card('丸', { grapheme: { code_point: 'U+4E38', char: '丸' } }),
    detail: card('丸', { grapheme: { code_point: 'U+4E38', char: '丸', members: [{ code_point: 'U+4E38', char: '丸' }] } }), variants: [], derived: [], substitutions: [], writes: [], writers: [], ids: { 丸: G.丸 } }));
  objects.set('v3/reference/char/U+51E1.json.gz', json({ grapheme: G.凡, data: card('凡', { grapheme: { code_point: 'U+51E1', char: '凡' } }),
    detail: card('凡', { grapheme: { code_point: 'U+51E1', char: '凡' } }), variants: [], derived: [], substitutions: [], writes: [], writers: [], ids: { 凡: G.凡 } }));
  // 事 and its member 亊, and ヿ, a katakana of its own grapheme: the reference relates none of them to ヿ,
  // and holds no drawn form.
  const shi = [{ code_point: 'U+4E8B', char: '事' }, { code_point: 'U+4E8A', char: '亊' }, { code_point: 'U+3D8B7', char: '\u{3D8B7}' }];
  for (const [char, head] of [['事', '事'], ['亊', '事'], ['ヿ', 'ヿ']])
    objects.set(`v3/reference/char/U+${char.codePointAt(0)!.toString(16).toUpperCase()}.json.gz`, json({ grapheme: G[head],
      data: card(char, { grapheme: { code_point: 'U+' + head.codePointAt(0)!.toString(16).toUpperCase(), char: head } }),
      detail: card(char, { grapheme: { code_point: 'U+' + head.codePointAt(0)!.toString(16).toUpperCase(), char: head, ...(head === '事' ? { members: shi } : {}) } }),
      variants: [], derived: [], substitutions: [], writes: [], writers: [], ids: head === '事' ? { 事: G.事, 亊: G.亊 } : { ヿ: G.ヿ } }));
  objects.set('v3/reference/list/ligatures/0.json.gz', json({ total: 2, offset: 0, items: [{ char: 'ね' }, { char: '時' }] }));
  env = { ATLAS: d1(db), MEDIA: bucket(), CATALOGUE_LAG: '0' } as unknown as Env;
});

const get = async (path: string) => {
  const response = await worker.fetch(new Request('https://glyphatlas.org' + path), env, ctx);
  return { status: response.status, body: await response.json() as any };
};
const crop = (page: number, slot: number) => publicId(cropId(page, slot));
const ids = (items: any[]) => items.map(item => item.id);
async function every(path: string): Promise<string[]> {
  const seen: string[] = [];
  let after = '';
  do {
    const { status, body } = await get(path + (after ? `&after=${after}` : ''));
    expect(status).toBe(200);
    seen.push(...ids(body.items ?? body.glyph_items));
    after = body.next ?? '';
  } while (after);
  return seen;
}

describe('a character page', () => {
  it('shows its card, its 異体字 with their crops counted, its words and the first page of its gallery', async () => {
    const { status, body } = await get('/layers/characters/U+6642');
    expect(status).toBe(200);
    expect([body.char, body.query]).toEqual(['時', 'U+6642']);
    // 旹 is attested twice, 峕 once; 时 is kept apart as a simplification.
    expect(body.variants.items.map((v: any) => [v.char, v.sources, v.count, v.written])).toEqual([['旹', ['src1', 'src2'], 1, false], ['峕', ['src1'], 1, true]]);
    expect(body.variants.related.map((v: any) => [v.char, v.count])).toEqual([['时', 1]]);
    expect(body.variants.derived).toEqual([{ char: '⿰日寺', code_point: null, encoded: false, count: 0, corpus_count: 0, sources: ['src3'],
      routes: [[{ was: '日', became: '目', count: 4, pairs: [{ a: '昨', b: '眲', sources: ['src3'] }] }]] }]);
    expect(body.variants.writers).toEqual([{ char: '时', code_point: 'U+65F6', shared: true }]);
    expect(body.variants.sources).toEqual({ 'derived-ids': 'derived-ids', src1: 'Source One', src2: 'src2', src3: 'src3' });
    // The 字体 row counts each form's images, the 字種 row its grapheme's: 時's family bills 7 crops and
    // 2 corpus glyphs, and 呆, with no grapheme row, reads as unused rather than failing the card.
    expect(body.grapheme.members.map((m: any) => [m.char, m.count, m.corpus_count]))
      .toEqual([['時', 5, 2], ['时', 1, 0], ['旹', 1, 0], ['呆', 0, 0], ['\u{F0003}', 0, 0], ['\u{F0004}', 0, 0]]);
    expect([body.grapheme.count, body.grapheme.corpus_count]).toEqual([7, 2]);
    // 峕 heads its own family row, so its one crop is counted once, not twice.
    expect(body.variants.writes.map((w: any) => [w.char, w.count, w.corpus_count])).toEqual([['旹', 1, 0], ['峕', 1, 0]]);
    expect(body.words.items[0].spellings.map((s: any) => [s.spelling, s.current])).toEqual([['時', true], ['刻', false]]);
    expect(body.words.sources).toEqual({ dict: 'A dictionary' });
  });

  it('counts in a grapheme the crops filed under it with no character', async () => {
    const { body } = await get('/layers/characters/U+306D');
    expect(body.grapheme.members.map((m: any) => [m.char, m.count, m.corpus_count])).toEqual([['ね', 0, 0], ['𛂒', 0, 1]]);
    expect([body.grapheme.count, body.grapheme.corpus_count]).toEqual([0, 4]);
  });

  it('answers 404 for a character no card has, and a card for a description sequence', async () => {
    expect((await get('/layers/characters/U+9F98')).status).toBe(404);
    const { status, body } = await get('/layers/characters/' + encodeURIComponent('⿰日寺'));
    expect([status, body.char]).toEqual([200, '⿰日寺']);
  });

  it('lists the described forms and figures the family\'s crops are claimed to have, counted by them', async () => {
    const forms: Record<string, [string, string, string]> = {};
    const named = (digit: string, scheme: string, value: string) => {
      forms[value] = ['fm:' + digit.repeat(32), 'rp:' + digit.repeat(32), scheme];
      db.query('INSERT INTO representations(id,scheme,value) VALUES(?,?,?)').run(forms[value][1], scheme, value);
      db.query("INSERT INTO forms(id,anchor,created_by,created_at) VALUES(?,?,'reader','now')").run(forms[value][0], forms[value][1]);
    };
    named('1', 'ids', '⿰木木'); named('3', 'ids', '⿰日寺'); named('5', 'ivs', '時\u{E0101}'); named('7', 'unicode', '旹');
    const claim = (page: number, slot: number, value: string, status = 'asserted', cut = 0) => {
      const target = cropId(page, slot);
      db.query("DELETE FROM current_claims WHERE subject=? AND predicate='has_form'").run(publicId(target));
      db.query(`INSERT INTO current_claims(subject,predicate,scope,slot,crop,cut,status,object,members,supporting,claims,resolver,at)
        VALUES(?,'has_form','','',?,?,?,?,'[]','[]','[]','r','now')`).run(publicId(target), target, cut, status, forms[value][0]);
    };
    let version = 0;
    // A claim is saved with its submission, which moves the catalogue's version.
    const listed = async (code = 'U+6642') => {
      db.query("INSERT INTO ledger_submissions(id,actor,request,response,at) VALUES(?,'reader','','','now')").run(`carried-${++version}`);
      return (await get('/layers/characters/' + code)).body.variants;
    };
    const row = (value: string, count: number, corpus_count = 0) => ({ char: value, code_point: null, form: forms[value][0], scheme: forms[value][2], count, corpus_count });
    claim(PAGE_A, 0, '⿰木木'); claim(PAGE_B, 0, '⿰木木'); claim(PAGE_C, 0, '時\u{E0101}'); claim(PAGE_A, 1, '⿰日寺');
    // A code point of its own is no such form; nor is one people dispute, or one claimed on an earlier cut.
    claim(PAGE_A, 2, '旹'); claim(PAGE_B, 2, '⿰日寺', 'disputed'); claim(PAGE_B, 3, '⿰日寺', 'asserted', -1);
    const found = await listed();
    expect(found.forms).toEqual([row('⿰木木', 2), row('⿰日寺', 1), row('時\u{E0101}', 0, 1)]);
    // The derived form is one of the family's 字体 by the crop that has it, and derived no more.
    expect(found.derived).toEqual([]);
    // Taken back on its crop, it is derived again.
    db.query("DELETE FROM current_claims WHERE subject=? AND predicate='has_form'").run(publicId(cropId(PAGE_A, 1)));
    const back = await listed();
    expect([back.forms.map((f: any) => f.char), back.derived.map((v: any) => v.char)]).toEqual([['⿰木木', '時\u{E0101}'], ['⿰日寺']]);
    // A crop given another character takes its form along, and back.
    const moved = cropId(PAGE_A, 0);
    db.query('UPDATE crops SET char=? WHERE id=?').run(G.峕, moved);
    expect([(await listed()).forms[0].count, (await listed('U+5CD5')).forms]).toEqual([1, [row('⿰木木', 1)]]);
    db.query('UPDATE crops SET char=? WHERE id=?').run(G.時, moved);
    expect([(await listed()).forms[0].count, (await listed('U+5CD5')).forms]).toEqual([2, []]);
    // A crop withdrawn and put back under another character takes it there.
    const held = db.query('SELECT * FROM crops WHERE id=?').get(moved) as Record<string, number>;
    db.query('DELETE FROM crops WHERE id=?').run(moved);
    expect((await listed()).forms[0].count).toBe(1);
    const put = { ...held, char: G.峕 };
    db.query(`INSERT INTO crops(${Object.keys(put).join(',')}) VALUES(${Object.keys(put).map(() => '?').join(',')})`).run(...Object.values(put));
    expect((await listed('U+5CD5')).forms).toEqual([row('⿰木木', 1)]);
    db.query('UPDATE crops SET char=? WHERE id=?').run(G.時, moved);
    // So does one a reader files under another grapheme.
    db.query(`INSERT INTO current_claims(subject,predicate,scope,slot,crop,cut,status,object,members,supporting,claims,resolver,at)
      VALUES(?,'has_grapheme','','',?,0,'asserted','U+5CD5','[]','[]','[]','r','now')`).run(publicId(moved), moved);
    expect([(await listed()).forms[0].count, (await listed('U+5CD5')).forms]).toEqual([1, [row('⿰木木', 1)]]);
    db.exec("DELETE FROM current_claims WHERE predicate IN ('has_form','has_grapheme')");
    expect(db.query('SELECT count(*) AS n FROM carried_forms').get()).toEqual({ n: 0 });
  });

  it('lists the drawn forms of a grapheme among its members, and gives each a page under that grapheme', async () => {
    const ru = '\u{F0000}', fang = '\u{F0001}', hook = '\u{F0002}';
    const { status, body } = await get('/layers/characters/U+4E8B');
    expect(status).toBe(200);
    expect(body.grapheme.members.map((m: any) => [m.char, m.drawn ?? false, m.count, m.corpus_count]))
      .toEqual([['事', false, 0, 0], ['亊', false, 0, 0], [ru, true, 0, 0], [fang, true, 0, 0], [hook, true, 0, 0], ['\u{3D8B7}', false, 0, 0]]);
    expect(body.grapheme.character_count).toBe(6);
    expect(body.variants.forms).toEqual([]);
    // No other family carries them.
    expect((await get('/layers/characters/U+5CD5')).body.grapheme.members.map((m: any) => m.char)).toEqual(['峕']);
    // A crop filed under a drawn form is counted for it and listed on its page and its family's.
    const target = cropId(PAGE_A, 0), was = db.query('SELECT char FROM crops WHERE id=?').get(target) as { char: number };
    db.prepare('INSERT INTO graphemes(id,text,head) VALUES(520,?,?)').run(ru, G.事);
    db.prepare('UPDATE crops SET char=520 WHERE id=?').run(target);
    try {
      const own = await get('/layers/characters/U+F0000');
      expect(own.status).toBe(200);
      expect([own.body.code_point, own.body.char, own.body.script, own.body.drawn, own.body.grapheme.code_point, own.body.grapheme.is_self])
        .toEqual(['U+F0000', ru, 'han', true, 'U+4E8B', false]);
      expect(own.body.grapheme.members.map((m: any) => m.char)).toEqual(['事', '亊', ru, fang, hook, '\u{3D8B7}']);
      // Its own edges are none; the characters its grapheme is also written with are listed, as on any member's card.
      expect(own.body.variants).toMatchObject({ items: [], forms: [] });
      expect(own.body.variants.related.map((v: any) => v.char)).toEqual(['ヿ', '\u{1B123}']);
      expect(ids((await get('/layers/occurrences?code_point=U%2BF0000')).body.items)).toEqual([publicId(target)]);
      expect(ids((await get('/layers/occurrences?code_point=U%2B4E8B&scope=grapheme')).body.items)).toContain(publicId(target));
    } finally {
      db.prepare('UPDATE crops SET char=? WHERE id=?').run(was.char, target);
      db.exec('DELETE FROM graphemes WHERE id=520');
    }
    // A private-use character the table does not give has no card.
    expect((await get('/layers/characters/U+F0100')).status).toBe(404);
  });

  it('lists a character of another script the table gives as writing the grapheme, as a shared form', async () => {
    const hiragana = '\u{1B123}';
    const { status, body } = await get('/layers/characters/U+4E8B');
    expect(status).toBe(200);
    // ヿ and the hiragana digraph are related and written for 事, and no gallery widens to them.
    expect(body.variants.items).toEqual([]);
    expect(body.variants.related.map((v: any) => [v.char, v.code_point, v.widens, v.written, v.sources, v.relations[0].relation])).toEqual([
      ['ヿ', 'U+30FF', false, true, ['en-wiktionary-koto', 'ja-wikipedia-koto'], 'semantic'],
      [hiragana, 'U+1B123', false, true, ['l2-24-150'], 'semantic']]);
    expect(body.variants.writers).toEqual([{ char: 'ヿ', code_point: 'U+30FF', shared: true }, { char: hiragana, code_point: 'U+1B123', shared: true }]);
    expect(body.variants.total).toBe(2);
    expect(body.variants.sources['l2-24-150']).toStartWith('Gen Kojitani');
    // A member of the family lists them too.
    expect((await get('/layers/characters/U+4E8A')).body.variants.writers.map((w: any) => w.char)).toEqual(['ヿ', hiragana]);
    // ヿ's own card names 事 as a grapheme it also writes, with what states it, and stays its own.
    const own = (await get('/layers/characters/U+30FF')).body;
    expect(own.variants.writes.map((w: any) => [w.char, w.code_point])).toEqual([['事', 'U+4E8B']]);
    expect(Object.keys(own.variants.sources)).toEqual(['en-wiktionary-koto', 'ja-wikipedia-koto']);
    expect([own.variants.own, own.variants.related, own.variants.writers]).toEqual([true, [], []]);
    // A grapheme the table does not name is as it was.
    expect((await get('/layers/characters/U+5CD5')).body.variants.writers).toEqual([]);
  });
});

describe('a gallery of the collection', () => {
  it('lists a character by style group, shuffle and id, leaving withheld crops out and counting them', async () => {
    const { body } = await get('/layers/occurrences?code_point=U%2B6642&limit=2');
    expect(ids(body.items)).toEqual([crop(PAGE_A, 0), crop(PAGE_A, 1)]);
    // The counts by style are of every crop, the withheld one among them.
    expect([body.total, body.styles, body.withheld, body.scope]).toEqual([4, { cursive: 2, unassessed: 2, formal: 1 }, 1, 'character']);
    expect(await every('/layers/occurrences?code_point=U%2B6642&limit=2')).toEqual([crop(PAGE_A, 0), crop(PAGE_A, 1), crop(PAGE_B, 0), crop(PAGE_B, 1)]);
    const shown = await get('/layers/occurrences?code_point=U%2B6642&withheld=show&style=unassessed');
    expect([ids(shown.body.items), shown.body.total, shown.body.withheld]).toEqual([[crop(PAGE_B, 0), crop(PAGE_A, 2)], 2, null]);
    const flagged = await get('/layers/occurrences?code_point=U%2B6642&state=flagged');
    expect([ids(flagged.body.items), flagged.body.total]).toEqual([[crop(PAGE_B, 0)], 1]);
    expect((await get('/layers/occurrences?code_point=U%2B6642&state=nothing')).body.total).toBe(0);
  });

  it('merges a family’s characters, and lists the variants one character after another', async () => {
    expect(await every('/layers/occurrences?code_point=U%2B6642&expand=grapheme&limit=2')).toEqual(
      [crop(PAGE_B, 2), crop(PAGE_A, 0), crop(PAGE_A, 1), crop(PAGE_B, 0), crop(PAGE_B, 1), crop(PAGE_B, 3)]);
    const variants = await get('/layers/occurrences?code_point=U%2B6642&scope=variants');
    // 時, then 旹 and 峕 in code point order (旹 U+65F9, 峕 U+5CD5 first).
    expect(ids(variants.body.items)).toEqual([crop(PAGE_B, 4), crop(PAGE_B, 3), crop(PAGE_A, 0), crop(PAGE_A, 1), crop(PAGE_B, 0), crop(PAGE_B, 1)]);
    expect([variants.body.total, variants.body.capped, variants.body.scope]).toEqual([6, false, 'variants']);
    expect(writtenVariants(null, '時')).toEqual(['時']);
  });

  it('orders by year with the undated last, and narrows to a range or to the undated', async () => {
    expect(await every('/layers/occurrences?code_point=U%2B6642&order=year&withheld=show&limit=2')).toEqual(
      [crop(PAGE_A, 1), crop(PAGE_A, 2), crop(PAGE_A, 0), crop(PAGE_B, 0), crop(PAGE_B, 1)]);
    const ranged = await get('/layers/occurrences?code_point=U%2B6642&years=1801-1900');
    expect([ids(ranged.body.items), ranged.body.styles, ranged.body.withheld, ranged.body.years]).toEqual(
      [[crop(PAGE_B, 0), crop(PAGE_B, 1)], { cursive: 0, unassessed: 1, formal: 1 }, 0, { from: 1801, to: 1900 }]);
    expect((await get('/layers/occurrences?code_point=U%2B6642&years=undated')).body.items).toEqual([]);
    // On the composed axis only book a is dated (1653); book b's crops have no composed year.
    expect(await every('/layers/occurrences?code_point=U%2B6642&order=year&axis=composed&limit=2')).toEqual(
      [crop(PAGE_A, 1), crop(PAGE_A, 0), crop(PAGE_B, 0), crop(PAGE_B, 1)]);
    const composed = await get('/layers/occurrences?code_point=U%2B6642&axis=composed&years=undated');
    expect([ids(composed.body.items), composed.body.total]).toEqual([[crop(PAGE_B, 0), crop(PAGE_B, 1)], 2]);
  });

  it('asks a tab paging by offset to reload, and refuses a cursor it did not write', async () => {
    const stale = await get('/layers/occurrences?code_point=U%2B6642&offset=60');
    expect([stale.status, stale.body.code]).toEqual([409, 'reload']);
    expect((await get('/layers/occurrences?code_point=U%2B6642&offset=0')).status).toBe(200);
    expect((await get('/layers/occurrences?code_point=U%2B6642&after=x')).status).toBe(422);
    expect((await get('/layers/occurrences?code_point=U%2B6642&after=9S0.1.' + crop(PAGE_A, 0))).status).toBe(422);
  });
});

describe('character gallery metadata', () => {
  it('shows named forms on the effective cut, pending redraws and book dates', async () => {
    const cid = cropId(PAGE_A, 0);
    db.exec(`INSERT INTO representations(id,scheme,value) VALUES('r:gallery','unicode','時');
      INSERT INTO forms(id,anchor,created_by,created_at) VALUES('f:gallery','r:gallery','u1','');
      INSERT INTO reviews(crop,gen,at,char,state,x,y,w,h) VALUES(${cid},6,0,300,1,1,2,3,4);
      INSERT INTO current_claims(subject,predicate,scope,slot,crop,cut,status,object,members,supporting,claims,resolver,at) VALUES
        ('gallery','has_form','','',${cid},1,'single','f:gallery',
         '[{"object":"f:gallery","confidence":null,"assertion":"c:gallery"}]','["c:gallery"]','[]','r','');`);
    const found = (await get('/layers/occurrences?code_point=U%2B6642')).body.items.find((item: any) => item.id === publicId(cid));
    expect(found.form.values).toEqual([{ form: 'f:gallery', scheme: 'unicode', text: '時', confidence: null }]);
    expect(found.box).toEqual({ x: 1, y: 2, w: 3, h: 4 });
    expect(found.box_pending).toBe(true);
    expect(found.dating.witness.start).toBe(1700);
  });
});

describe('a crop a grapheme claim files', () => {
  it('leaves its character\'s family for the one the claim names, in the gallery and its counts', async () => {
    // 时 (slot 2 of page B, in 時's family) read as 峕.
    const claim = (status: string) => db.query(`INSERT INTO current_claims(subject,predicate,scope,slot,crop,cut,status,object,value,members,supporting,claims,resolver,at)
      VALUES('x','has_grapheme','','',?,0,?,'U+5CD5',NULL,'[]','[]','[]','r','t')`).run(cropId(PAGE_B, 2), status);
    const time = '/layers/occurrences?code_point=U%2B6642&expand=grapheme', other = '/layers/occurrences?code_point=U%2B5CD5&expand=grapheme';
    claim('asserted');
    try {
      expect(await every(time + '&limit=2')).toEqual([crop(PAGE_A, 0), crop(PAGE_A, 1), crop(PAGE_B, 0), crop(PAGE_B, 1), crop(PAGE_B, 3)]);
      expect((await get(time)).body.total).toBe(5);
      const books = async (code: string) => (await get(`/layers/books?code_point=${code}&scope=grapheme`)).body.groups
        .filter((g: any) => g.document === 'hk:b').map((g: any) => [g.count.local, ids(g.items)]);
      expect(await books('U%2B6642')).toEqual([[3, [crop(PAGE_B, 0), crop(PAGE_B, 3), crop(PAGE_B, 1)]]]);
      expect(await books('U%2B5CD5')).toEqual([[2, [crop(PAGE_B, 2), crop(PAGE_B, 4)]]]);
      const filed = await get(other);
      expect([ids(filed.body.items), filed.body.total, filed.body.styles]).toEqual([[crop(PAGE_B, 2), crop(PAGE_B, 4)], 2, { cursive: 1, unassessed: 1, formal: 0 }]);
      // The claims are kept by the catalogue version, which every ledger write moves.
      db.exec(`UPDATE current_claims SET status='disputed' WHERE predicate='has_grapheme'; INSERT INTO ledger_submissions VALUES('disputed','u','{}','{}','t')`);
      expect((await get(time)).body.total).toBe(6);
      expect(ids((await get(other)).body.items)).toEqual([crop(PAGE_B, 4)]);
    } finally { db.exec(`DELETE FROM current_claims WHERE predicate='has_grapheme'; DELETE FROM ledger_submissions`) }
  });
});

describe('the total of a grapheme', () => {
  const cards = { 時: 'U%2B6642', 峕: 'U%2B5CD5', ね: 'U%2B306D', 丸: 'U%2B4E38' };
  // Every place that states it: the card, the list of every grapheme, the gallery with what it withholds,
  // and the counts by style and by decade.
  async function totals(char: keyof typeof cards) {
    edge.clear();
    const code = cards[char], sum = (counts: object) => Object.values(counts).reduce((a, b) => a + b, 0);
    const { grapheme } = (await get('/layers/characters/' + code)).body;
    const list = (await get('/atlas/graphemes/counts')).body.items.filter((row: any) => row[1] === decodeURIComponent(code));
    const own = (await get(`/layers/occurrences?code_point=${code}&expand=grapheme`)).body, glyphs = (await get(`/layers/candidates?code_point=${code}&scope=grapheme`)).body;
    const decades = (await get(`/layers/decades?code_point=${code}&scope=grapheme`)).body;
    const found = [[grapheme.count, grapheme.corpus_count], list.reduce(([a, b]: number[], row: any) => [a + row[2], b + row[3]], [0, 0]),
      [own.total + own.withheld, glyphs.glyphs], [sum(own.styles), sum(glyphs.styles)],
      [decades.local.reduce((n: number, d: any) => n + d[1], 0), decades.corpus.reduce((n: number, d: any) => n + d[1], 0)]];
    expect(found.map(String)).toEqual(found.map(() => String(found[0])));
    return { total: found[0], sole: grapheme.sole };
  }
  const claim = (crop: number, object: string) => db.query(`INSERT INTO current_claims(subject,predicate,scope,slot,crop,cut,status,object,value,members,supporting,claims,resolver,at)
    VALUES('c' || ?1,'has_grapheme','','',?1,0,'asserted',?2,NULL,'[]','[]','[]','r','t')`).run(crop, object);
  const unclaim = () => db.exec(`DELETE FROM current_claims WHERE predicate='has_grapheme'`);
  beforeAll(() => publish(db, objects, 'T', 4));

  it('counts withheld crops and the glyphs with no character, and is one number wherever it is stated', async () => {
    expect(await totals('時')).toEqual({ total: [7, 2], sole: null });
    expect(await totals('ね')).toEqual({ total: [0, 4], sole: null });
    expect(await totals('丸')).toEqual({ total: [4, 2], sole: 'U+4E38' });
  });

  it('takes the crops claims file into the grapheme, and leaves out those they file away', async () => {
    expect(await totals('峕')).toEqual({ total: [1, 0], sole: 'U+5CD5' });
    claim(cropId(PAGE_B, 2), 'U+5CD5');
    try {
      expect(await totals('時')).toEqual({ total: [6, 2], sole: null });
      // 峕's gallery holds a 时 now, which its character's page does not.
      expect(await totals('峕')).toEqual({ total: [2, 0], sole: null });
      // The 字種 a form writes is counted the same way on that form's card.
      const { writes } = (await get('/layers/characters/U+6642')).body.variants;
      expect(writes.map((w: any) => [w.char, w.count, w.corpus_count])).toEqual([['旹', 1, 0], ['峕', 2, 0]]);
    } finally { unclaim() }
    // A glyph with no character is taken from the family that names it, and one crop for another leaves
    // the total as it was and the two galleries different.
    claim(cropId(PAGE_C, 4), 'U+5CD5');
    claim(cropId(PAGE_B, 4), 'U+6642');
    try {
      expect(await totals('ね')).toEqual({ total: [0, 3], sole: null });
      expect(await totals('峕')).toEqual({ total: [0, 1], sole: null });
      expect(await totals('時')).toEqual({ total: [8, 2], sole: null });
    } finally { unclaim() }
  });

  it('counts a small seal member the card does not list yet, as the gallery reads it', async () => {
    db.exec(`INSERT INTO graphemes(id,text,head) VALUES(600,'\u{3D307}',${G.丸});
      INSERT INTO tally(gen,kind,char,family,book,production,state,style,quiz,decade,n) VALUES(5,0,600,${G.丸},1,-1,1,4,1,1700,2);
      INSERT INTO imports(stamp,at) VALUES('seal',0)`);
    try { expect(await totals('丸')).toEqual({ total: [6, 2], sole: null }) }
    finally { db.exec('DELETE FROM graphemes WHERE id=600; DELETE FROM tally WHERE char=600; DELETE FROM imports WHERE stamp=\'seal\'') }
  });

  it('counts no claim on a crop that is neither the collection\'s nor the corpus\'s', async () => {
    db.exec(`INSERT INTO crops(id,kind,book,char,family,style,state,quiz,shuffle) VALUES(${cropId(PAGE_E, 5)},2,5,${G.时},${G.時},0,1,1,1)`);
    claim(cropId(PAGE_E, 5), 'U+5CD5');
    try { expect([await totals('時'), await totals('峕')]).toEqual([{ total: [7, 2], sole: null }, { total: [1, 0], sole: 'U+5CD5' }]) }
    finally { unclaim(); db.exec(`DELETE FROM crops WHERE id=${cropId(PAGE_E, 5)}`) }
  });

  it('counts what the gallery of a character with no card in the table lists', async () => {
    const text = '⿰木木', asked = encodeURIComponent(text), read = async (path: string) => { edge.clear(); return (await get(path)).body };
    expect((await read('/layers/characters/' + asked)).grapheme).toMatchObject({ count: 0, corpus_count: 0 });
    db.exec(`INSERT INTO graphemes(id,text,head) VALUES(610,'${text}',NULL);
      INSERT INTO tally(gen,kind,char,family,book,production,state,style,quiz,decade,n) VALUES(5,0,610,-1,1,-1,1,4,1,1700,3)`);
    try {
      expect((await read('/layers/characters/' + asked)).grapheme).toMatchObject({ count: 3, corpus_count: 0 });
      for (const expand of ['none', 'grapheme']) expect((await read(`/layers/occurrences?code_point=${asked}&expand=${expand}`)).total).toBe(3);
    } finally { db.exec('DELETE FROM graphemes WHERE id=610; DELETE FROM tally WHERE char=610') }
  });

  it('stands beside decades of the same version, whatever was reviewed since', async () => {
    const both = async () => {
      const { grapheme } = (await get('/layers/characters/U+4E38')).body, { local } = (await get('/layers/decades?code_point=U%2B4E38&scope=grapheme')).body;
      return [grapheme.count, local.reduce((n: number, d: any) => n + d[1], 0)];
    };
    expect(await both()).toEqual([4, 4]);
    db.exec(`INSERT INTO tally(gen,kind,char,family,book,production,state,style,quiz,decade,n) VALUES(5,0,${G.丸},${G.丸},1,-1,1,4,1,1700,1);
      INSERT INTO events(crop,revision,cut,gen,actor,submission,at,kind) VALUES(1,1,0,5,1,1,1,0)`);
    try { expect(await both()).toEqual([5, 5]) } finally { db.exec(`DELETE FROM tally WHERE char=${G.丸}; DELETE FROM events`) }
  });

  it('is the same in every view, with the withheld crops said beside it', async () => {
    const sum = (counts: object) => Object.values(counts).reduce((a, b) => a + b, 0);
    for (const show of ['', '&withheld=show']) {
      const gallery = async (query: string) => { const { body } = await get('/layers/occurrences?code_point=U%2B6642' + query + show); return [body.total, body.withheld, sum(body.styles)] };
      const books = async (query: string) => { const { body } = await get('/layers/books?code_point=U%2B6642' + query + show); return [body.listed.local, body.withheld, sum(body.styles.local)] };
      const want = show ? [5, null, 5] : [4, 1, 5];
      for (const query of ['', '&order=year', '&years=1601-1900', '&order=year&axis=composed']) expect(await gallery(query)).toEqual(want as any);
      for (const query of ['', '&years=1601-1900']) expect(await books(query)).toEqual(want as any);
      // Narrowed to a style, the total and the withheld are that style's; the chips keep every style's.
      expect(await gallery('&style=unassessed&years=1601-1900')).toEqual((show ? [2, null, 5] : [1, 1, 5]) as any);
      expect(await books('&style=unassessed')).toEqual((show ? [2, null, 5] : [1, 1, 5]) as any);
    }
    const { local } = (await get('/layers/decades?code_point=U%2B6642')).body;
    expect(local.reduce((n: number, d: any) => n + d[1], 0)).toBe(5);
  });

  it('is the same on the card and in the list where a count is below nothing, or a claimed crop\'s place counts none', async () => {
    // A review took two crops of 峕 where the publication counts one; a claim files a glyph of a family
    // no grapheme of the list heads.
    db.exec(`INSERT INTO tally(gen,kind,char,family,book,production,state,style,quiz,decade,n) VALUES(5,0,${G.峕},${G.峕},1,-1,1,4,1,1700,-2);
      INSERT INTO imports(stamp,at) VALUES('below',0)`);
    const both = async (code: string) => {
      edge.clear();
      const { grapheme } = (await get('/layers/characters/' + encodeURIComponent(code))).body;
      const rows = (await get('/atlas/graphemes/counts')).body.items.filter((row: any) => row[1] === code);
      expect([grapheme.count, grapheme.corpus_count]).toEqual(rows.reduce(([a, b]: number[], row: any) => [a + row[2], b + row[3]], [0, 0]));
      return [grapheme.count, grapheme.corpus_count];
    };
    claim(cropId(PAGE_C, 4), 'U+5CD5');
    try {
      expect(await both('U+5CD5')).toEqual([0, 1]);
      db.exec(`UPDATE crops SET family=NULL WHERE id=${cropId(PAGE_C, 4)}; INSERT INTO imports(stamp,at) VALUES('placeless',0)`);
      expect(await both('U+5CD5')).toEqual([0, 1]);
    } finally { unclaim(); db.exec(`UPDATE crops SET family=${G.ね} WHERE id=${cropId(PAGE_C, 4)}; DELETE FROM tally WHERE char=${G.峕}; DELETE FROM imports WHERE stamp IN ('below','placeless')`) }
  });

  it('lists by book what its books hold, and reads the counts by style once, on the first page', async () => {
    const path = '/layers/books?code_point=U%2B4E38', sum = (groups: any[]) => groups.reduce((n: number, g: any) => n + g.count.local, 0);
    const before = (await get(path)).body;
    expect([before.listed.local, sum(before.groups), before.styles.local]).toEqual([4, 4, { cursive: 1, unassessed: 2, formal: 1 }]);
    // Within the hour the books stand as they are, and what they list with them; the counts by style follow the review.
    db.exec(`INSERT INTO tally(gen,kind,char,family,book,production,state,style,quiz,decade,n) VALUES(5,0,${G.丸},${G.丸},1,-1,1,4,1,1700,1); INSERT INTO imports(stamp,at) VALUES('book',0); DELETE FROM imports WHERE stamp='book';
      INSERT INTO events(crop,revision,cut,gen,actor,submission,at,kind) VALUES(1,1,0,5,1,1,1,0)`);
    let tallies = 0;
    const counting = { ...env, ATLAS: Object.assign(Object.create(env.ATLAS), { prepare: (sql: string) => { if (sql.includes('CROSS JOIN tally')) tallies++; return env.ATLAS.prepare(sql) } }) } as unknown as Env;
    const ask = async (more = '') => (await worker.fetch(new Request('https://glyphatlas.org' + path + more), counting, ctx)).json() as Promise<any>;
    try {
      const after = await ask();
      expect([after.listed.local, sum(after.groups), after.styles.local]).toEqual([4, 4, { cursive: 1, unassessed: 2, formal: 2 }]);
      const first = tallies;
      await ask('&document=hk:d&per=1'); await ask('&limit=1&after=1.5');
      expect([first, tallies]).toEqual([first, first]);
      expect(first).toBeLessThanOrEqual(2);
    } finally { db.exec(`DELETE FROM tally WHERE char=${G.丸}; DELETE FROM events`) }
  });

  it('keeps the decades of the copies\' dates by the catalogue and those of the texts\' by the hour', async () => {
    const keys = () => [...edge.keys()].filter(url => url.includes('/layers/decades'));
    const ask = async (axis: string) => { await get('/layers/decades?code_point=U%2B4E38&axis=' + axis); return keys().length };
    expect([await ask('witness'), await ask('composed')]).toEqual([1, 2]);
    db.exec(`INSERT INTO events(crop,revision,cut,gen,actor,submission,at,kind) VALUES(1,1,0,5,1,1,1,0)`);
    try { expect([await ask('composed'), await ask('witness')]).toEqual([2, 3]) } finally { db.exec('DELETE FROM events') }
  });

  it('reads the claims once for every card, gallery and list of a catalogue version', async () => {
    let reads = 0;
    const counting = { ...env, ATLAS: Object.assign(Object.create(env.ATLAS), { prepare: (sql: string) => { if (sql.includes("'has_grapheme'")) reads++; return env.ATLAS.prepare(sql) } }) } as unknown as Env;
    const paths = ['U+6642', 'U+306D', 'U+4E38'].flatMap(code => [`/layers/characters/${code}`, `/layers/occurrences?code_point=${encodeURIComponent(code)}&expand=grapheme`,
      `/layers/candidates?code_point=${encodeURIComponent(code)}&scope=grapheme`, `/layers/decades?code_point=${encodeURIComponent(code)}&scope=grapheme`, `/layers/books?code_point=${encodeURIComponent(code)}&scope=grapheme`]);
    for (const path of [...paths, '/atlas/graphemes/counts', '/atlas?grapheme=U%2B6642&limit=1']) expect((await worker.fetch(new Request('https://glyphatlas.org' + path), counting, ctx)).status).toBe(200);
    expect(reads).toBe(1);
  });
});

describe('a gallery of the corpus', () => {
  it('lists a family’s glyphs with those filed under it with no character, and counts the family', async () => {
    const own = await get('/layers/candidates?code_point=U%2B306D');
    expect([own.body.glyph_items, own.body.family_total, own.body.unassigned_count]).toEqual([[], 4, 3]);
    const family = await get('/layers/candidates?code_point=U%2B306D&scope=grapheme');
    expect(ids(family.body.glyph_items)).toEqual([crop(PAGE_C, 3), crop(PAGE_C, 1), crop(PAGE_C, 2), crop(PAGE_C, 4)]);
    expect([family.body.glyphs, family.body.styles, family.body.scope]).toEqual([4, { cursive: 1, unassessed: 3, formal: 0 }, 'grapheme']);
    const unassigned = await get('/layers/candidates?code_point=U%2B306D&scope=grapheme&visual_group=unassigned');
    expect(ids(unassigned.body.glyph_items)).toEqual([crop(PAGE_C, 3), crop(PAGE_C, 2), crop(PAGE_C, 4)]);
    const grouped = await get('/layers/candidates?code_point=U%2B306D&scope=grapheme&visual_group=U%2B306D%3Aabc&limit=1');
    expect([ids(grouped.body.glyph_items), grouped.body.styles]).toEqual([[crop(PAGE_C, 3)], { cursive: 1, unassessed: 1, formal: 0 }]);
    const next = await get(`/layers/candidates?code_point=U%2B306D&scope=grapheme&visual_group=U%2B306D%3Aabc&limit=1&after=${grouped.body.next}`);
    expect([ids(next.body.glyph_items), next.body.next]).toEqual([[crop(PAGE_C, 2)], null]);
  });

  it('names a visual group by its key and each sample by its public id', async () => {
    const { body } = await get('/layers/characters/U+306D');
    const [group] = body.visual_analysis.groups;
    expect(group.id).toBe('U+306D:abc');
    expect(group.representatives).toEqual([{ id: crop(PAGE_C, 2), image: `/layers/visual-groups/samples/${crop(PAGE_C, 2)}/image`, source_label: 'ね' }]);
    expect(group.anchors).toEqual([{ id: crop(PAGE_C, 3), distance: 0.1 }]);
  });
});

describe('a gallery grouped by book', () => {
  const shape = (body: any) => body.groups.map((g: any) => [g.document, g.title, g.count.local, g.count.corpus, g.items.map((i: any) => [i.id, i.origin])]);
  it('lists the books with the most crops first, each with its first crops, the collection’s first', async () => {
    const { body } = await get('/layers/books?code_point=U%2B6642&per=2');
    expect(shape(body)).toEqual([
      ['hk:a', 'A', 2, 1, [[crop(PAGE_A, 1), 'collection'], [crop(PAGE_A, 0), 'collection']]],
      ['hk:b', 'B', 2, 0, [[crop(PAGE_B, 0), 'collection'], [crop(PAGE_B, 1), 'collection']]],
      ['codh:c', 'C', 0, 1, [[crop(PAGE_C, 0), 'corpus']]]]);
    expect([body.total, body.next, body.withheld]).toEqual([3, null, 1]);
    expect(body.styles).toEqual({ local: { cursive: 2, unassessed: 2, formal: 1 }, corpus: { cursive: 0, unassessed: 2, formal: 0 } });
    expect(body.groups.map((g: any) => g.next)).toEqual([`0.50.${crop(PAGE_A, 0)}`, null, null]);
  });

  it('goes on through one book’s crops, its collected ones and then its corpus glyphs', async () => {
    for (const per of [1, 2]) {
      const seen: string[] = [];
      let after = '';
      do {
        const { body } = await get(`/layers/books?code_point=U%2B6642&document=hk:a&withheld=show&per=${per}${after && '&after=' + after}`);
        expect(body.groups.map((g: any) => g.document)).toEqual(['hk:a']);
        seen.push(...ids(body.groups[0].items)); after = body.groups[0].next ?? '';
      } while (after);
      expect(seen).toEqual([crop(PAGE_A, 1), crop(PAGE_A, 2), crop(PAGE_A, 0), crop(PAGE_A, 3)]);
    }
  });

  it('goes on from a corpus glyph to the corpus glyphs after it, the withheld among them', async () => {
    const found = [];
    let after = '';
    do {
      const { body } = await get('/layers/books?code_point=U%2B4E38&document=hk:d&per=1' + (after && '&after=' + after));
      found.push([...ids(body.groups[0].items), body.groups[0].items[0].origin]); after = body.groups[0].next ?? '';
    } while (after && found.length < 9);
    expect(found).toEqual([[crop(PAGE_D, 0), 'collection'], [crop(PAGE_D, 1), 'collection'], [crop(PAGE_D, 2), 'corpus'], [crop(PAGE_D, 3), 'corpus']]);
  });

  it('pages the books, and refuses a cursor it did not write', async () => {
    const seen: string[] = [];
    let after = '';
    do {
      const { body } = await get('/layers/books?code_point=U%2B6642&limit=1' + (after && '&after=' + after));
      expect(body.groups).toHaveLength(1);
      seen.push(body.groups[0].document); after = body.next ?? '';
    } while (after);
    expect(seen).toEqual(['hk:a', 'hk:b', 'codh:c']);
    // Books of as many crops go by id, and a page holds the books that reach its crops.
    const books = async (more: string) => { const { body } = await get('/layers/books?code_point=U%2B4E38' + more); return [body.groups.map((g: any) => g.document), body.next] };
    expect(await books('&limit=1')).toEqual([['hk:d'], '4.4']);
    expect(await books('&limit=1&after=4.4')).toEqual([['hk:e'], '1.5']);
    expect(await books('&limit=1&after=1.5')).toEqual([['hk:f'], null]);
    expect(await books('&limit=5')).toEqual([['hk:d', 'hk:e'], '1.5']);
    expect(await books('&limit=5&per=2')).toEqual([['hk:d', 'hk:e', 'hk:f'], null]);
    expect((await get('/layers/books?code_point=U%2B6642&after=0S1.2.3')).status).toBe(422);
    expect((await get('/layers/books?code_point=U%2B6642&document=hk:a&after=3.1')).status).toBe(422);
  });

  it('narrows to a style group and to years, the withheld shown when asked for', async () => {
    const cursive = await get('/layers/books?code_point=U%2B6642&style=cursive');
    expect(shape(cursive.body)).toEqual([['hk:a', 'A', 2, 0, [[crop(PAGE_A, 1), 'collection'], [crop(PAGE_A, 0), 'collection']]]]);
    expect([cursive.body.styles.local.formal, cursive.body.withheld]).toEqual([1, 0]);
    expect(shape((await get('/layers/books?code_point=U%2B4E38&style=formal')).body)).toEqual([['hk:d', 'D', 1, 0, [[crop(PAGE_D, 1), 'collection']]]]);
    const ranged = (await get('/layers/books?code_point=U%2B4E38&years=1890-1900')).body;
    expect([ranged.groups.map((g: any) => g.document), ranged.withheld]).toEqual([['hk:d', 'hk:e'], 0]);
    const books = async (more: string) => (await get('/layers/books?code_point=U%2B6642' + more)).body.groups.map((g: any) => [g.document, g.count.local, g.count.corpus]);
    expect(await books('&years=1801-1900')).toEqual([['hk:b', 2, 0]]);
    expect(await books('&years=undated')).toEqual([['codh:c', 0, 1]]);
    expect(await books('&withheld=show')).toEqual([['hk:a', 3, 1], ['hk:b', 2, 0], ['codh:c', 0, 1]]);
    expect((await get('/layers/books?code_point=U%2B6642&axis=composed')).status).toBe(422);
  });

  it('merges a family’s characters and the glyphs filed under it with no character', async () => {
    // The character's own books are kept apart from its family's.
    expect((await get('/layers/books?code_point=U%2B6642')).body.groups[0].document).toBe('hk:a');
    const { body } = await get('/layers/books?code_point=U%2B6642&scope=grapheme');
    expect(shape(body)[0]).toEqual(['hk:b', 'B', 4, 0, [2, 0, 3, 1].map(slot => [crop(PAGE_B, slot), 'collection'])]);
    const ne = await get('/layers/books?code_point=U%2B306D&scope=grapheme&per=2');
    expect(shape(ne.body)).toEqual([['codh:c', 'C', 0, 4, [[crop(PAGE_C, 3), 'corpus'], [crop(PAGE_C, 1), 'corpus']]]]);
  });

  it('keeps two records of one copy as one book, and the crops of no recorded book last', async () => {
    const at = (book: number, slot: number) => crop(pageOf(book), slot), { body } = await get('/layers/books?code_point=U%2B51E1');
    expect(body.groups.map((g: any) => [g.document, g.records, g.title, g.page, g.count, ids(g.items)])).toEqual([
      ['hl:055A6724CF2E402237834AFA675596AC', ['hl:055A6724CF2E402237834AFA675596AC', 'hk:055a6724cf2e402237834afa675596ac'], '物類', 'hl:055A6724CF2E402237834AFA675596AC',
        { local: 2, corpus: 1 }, [at(BOOK.lines, 0), at(BOOK.honkoku, 0), at(BOOK.lines, 1)]],
      ['codh:200022050', ['codh:200022050', 'hl:1793909603875BE815ED30CF80427250'], '料理', 'hl:1793909603875BE815ED30CF80427250',
        { local: 1, corpus: 2 }, [at(BOOK.copy, 0), at(BOOK.corpus, 0), at(BOOK.corpus, 1)]],
      ['hng:fhs', ['hng:fhs', 'hng-kiridashi:fhs'], '経', null, { local: 0, corpus: 2 }, [at(BOOK.cut, 0), at(BOOK.hng, 0)]],
      ['unrecorded', ['hi:kuzushiji-2023', 'hk'], null, null, { local: 0, corpus: 5 },
        [at(BOOK.dataset, 0), at(BOOK.unnamed, 0), at(BOOK.dataset, 1), at(BOOK.dataset, 2), at(BOOK.dataset, 3)]]]);
    expect([body.total, body.next]).toEqual([4, null]);
    const seen: string[] = [];
    let after = '';
    do {
      const page = (await get('/layers/books?code_point=U%2B51E1&limit=1' + (after && '&after=' + after))).body;
      seen.push(...page.groups.map((g: any) => g.document)); after = page.next ?? '';
    } while (after);
    expect(seen).toEqual(['hl:055A6724CF2E402237834AFA675596AC', 'codh:200022050', 'hng:fhs', 'unrecorded']);
    for (const group of body.groups) {
      const crops: string[] = [];
      after = '';
      do {
        const page = (await get(`/layers/books?code_point=U%2B51E1&per=1&document=${group.document}` + (after && '&after=' + after))).body;
        crops.push(...ids(page.groups[0].items)); after = page.groups[0].next ?? '';
      } while (after && crops.length < 9);
      expect(crops).toEqual(ids(group.items));
    }
    const read = async (document: string) => (await get('/layers/books?code_point=U%2B51E1&document=' + document)).body.groups.map((g: any) => [g.document, g.items.length]);
    expect(await read('hl:1793909603875BE815ED30CF80427250')).toEqual([['codh:200022050', 3]]);
    expect(await read('hk:none')).toEqual([]);
    const dated = (await get('/layers/books?code_point=U%2B51E1&years=undated')).body.groups.map((g: any) => [g.document, g.count]);
    expect(dated).toEqual([['hl:1793909603875BE815ED30CF80427250', { local: 1, corpus: 0 }], ['unrecorded', { local: 0, corpus: 5 }]]);
  });

  it('names the copy a record is of', () => {
    const copies = (...keys: string[]) => new Set(keys.map(key => copyOf({ key, title: 'T' }))).size;
    expect(copies('hl:7970d9964e0cfeafe30321827a62beae', 'hl:6E29B9B929C04E82EA49008079FA75D8')).toBe(1);
    expect(copies('hl:48ADDF6AFC3EC1002E472AA1137F87AE', 'codh:100241706')).toBe(1);
    expect(copies('hl:055A6724CF2E402237834AFA675596AC', 'hk:055a6724cf2e402237834afa675596ac')).toBe(1);
    expect(copies('hng:fhs', 'hng-kiridashi:fhs', 'hng:myz', 'codh:100241706', 'codh:100249476', 'hk:a')).toBe(5);
  });

  it('reads the books along the book indexes and each book’s crops in order from its own range', () => {
    const plan = (sql: string, values: unknown[]) => (db.query(`EXPLAIN QUERY PLAN ${sql}`).all(...(values as any[])) as { detail: string }[]).map(r => r.detail).join(' | ');
    for (const source of [{ by: 'char', key: G.時 }, { by: 'family', key: G.ね }, { by: 'char', key: G.時, except: [1, 2] }] as Source[]) for (const kind of [0, 1] as const) {
      const index = source.by === 'char' ? 'crop_char_book' : 'crop_family_book';
      const cells = bookCellsQuery(source, kind);
      expect(plan(cells.sql, cells.values)).toContain(`COVERING INDEX ${index} `);
      for (const group of [null, 'cursive', 'formal'] as const) for (const after of [null, [1, 2] as [number, number]]) {
        const crops = bookCropsQuery(source, kind, [1, 2], { group, withheld: false }, after, 3), found = plan(crops.sql, crops.values);
        expect(found).toContain('SEARCH k USING INTEGER PRIMARY KEY');
        expect(found).toMatch(new RegExp(`SEARCH crops USING COVERING INDEX ${index} \\([a-z]+=\\? AND kind=\\? AND book=\\?`));
        expect(found).not.toMatch(/TEMP B-TREE FOR ORDER BY|SCAN (crops|k)(?! USING)/);
      }
    }
    expect(plan(booksOfQuery(), ['[1]'])).not.toMatch(/SCAN (crops|books|k|b)(?! USING)/);
  });
});

describe('decades and the time axis', () => {
  it('count a character’s crops per decade on either axis', async () => {
    const witness = await get('/layers/decades?code_point=U%2B6642');
    expect([witness.body.local, witness.body.corpus]).toEqual([[[1700, 3], [1810, 2]], [[null, 1], [1700, 1]]]);
    const composed = await get('/layers/decades?code_point=U%2B6642&axis=composed');
    expect([composed.body.local, composed.body.corpus]).toEqual([[[null, 2], [1650, 3]], [[null, 1], [1650, 1]]]);
    const family = await get('/layers/decades?code_point=U%2B6642&scope=grapheme');
    expect(family.body.local).toEqual([[1700, 3], [1810, 4]]);
  });

  it('draw a few crops per decade, the collection’s first, narrowed by production or character', async () => {
    const { body } = await get('/layers/chronology?code_point=U%2B6642&per=2');
    expect(body.buckets.map((b: any) => [b.decade, b.local, b.corpus, b.items.map((i: any) => [i.id, i.origin])])).toEqual([
      [1700, 2, 1, [[crop(PAGE_A, 1), 'collection'], [crop(PAGE_A, 0), 'collection']]],
      [1810, 2, 0, [[crop(PAGE_B, 0), 'collection'], [crop(PAGE_B, 1), 'collection']]]]);
    expect([body.undated.local, body.undated.corpus, ids(body.undated.items)]).toEqual([0, 1, [crop(PAGE_C, 0)]]);
    const printed = await get('/layers/chronology?code_point=U%2B6642&production=printed&scope=grapheme');
    expect(printed.body.buckets.map((b: any) => [b.decade, b.local, b.corpus])).toEqual([[1700, 2, 1]]);
    const some = await get('/layers/chronology?code_point=U%2B6642&scope=grapheme&chars=' + encodeURIComponent('时,旹'));
    expect(some.body.buckets.map((b: any) => [b.decade, ids(b.items)])).toEqual([[1810, [crop(PAGE_B, 2), crop(PAGE_B, 3)]]]);
    // 殺's 36 Han forms, named apart from its small seal characters: more than a score of characters.
    const han = readFileSync(new URL('../../../../data/vocab/characters.tsv', import.meta.url), 'utf8').split('\n').map(line => line.split('\t'))
      .filter(row => row[10] === 'U+6BBA' && row[4] === 'han').map(row => row[1]);
    expect(han.length).toBe(36);
    expect((await get('/layers/chronology?code_point=U%2B6642&scope=grapheme&chars=' + encodeURIComponent(han.join(',')))).status).toBe(200);
    const composed = await get('/layers/chronology?code_point=U%2B6642&axis=composed');
    expect(composed.body.buckets.map((b: any) => [b.decade, b.local, b.corpus])).toEqual([[1650, 2, 1]]);
    expect([composed.body.undated.local, composed.body.undated.corpus]).toEqual([2, 1]);
  });
});

describe('the card lists', () => {
  it('page the ligatures, `total` being the page', async () => {
    const { body } = await get('/layers/ligatures?limit=1&offset=1');
    expect(body).toEqual({ items: [{ char: '時' }], total: 1 });
  });
});

describe('every query of the character routes', () => {
  const plan = (sql: string, values: unknown[]) => (db.query(`EXPLAIN QUERY PLAN ${sql}`).all(...(values as any[])) as { detail: string }[]).map(r => r.detail).join(' | ');
  const sources: Source[] = [{ by: 'char', key: G.時 }, { by: 'family', key: G.ね }];
  const composed = new Map([[BOOK.a, 1653]]);
  const shapes: [Partial<GalleryQuery>, 0 | 1][] = [];
  for (const kind of [0, 1] as const) for (const order of ['style', 'year'] as const) for (const years of [null, { from: 1600, to: 1699 }, { from: 1700, to: 1799 }, 'undated'] as const)
    for (const axis of ['witness', 'composed'] as const) shapes.push([{ order, years, axis, composed, group: 'cursive', state: 1 }, kind]);
  // A range ordered by its index's own key reads no more than the page; one ordered otherwise sorts the range it names.
  const sorted: Phase[] = ['R', 'C', 'K'];
  const INDEX: Record<Phase, [string, string]> = { S: ['crop_char', 'crop_family'], Y: ['crop_year', 'crop_family_year'], U: ['crop_undated', 'crop_family'],
    R: ['crop_year', 'crop_family_year'], C: ['crop_char_book', 'crop_family_book'], K: ['crop_char_book', 'crop_family_book'], N: ['crop_char', 'crop_family'] };
  it('reads each gallery range along its index', () => {
    for (const [shape, kind] of shapes) {
      const q = { kind, segments: [sources], limit: 3, ...shape } as GalleryQuery;
      for (const phase of phasesOf(q)) for (const source of sources) for (const after of [null, [1, 2, 3] as [number, number, number]]) {
        const range = rangeQuery(q, phase, source, after, 4);
        if (!range) { expect(['C', 'K']).toContain(phase); continue }
        const found = plan(range.sql, range.values);
        const index = phase === 'U' && kind === 1 && source.by === 'char' ? 'crop_undated_corpus' : INDEX[phase][source.by === 'char' ? 0 : 1];
        expect(found).toContain(`INDEX ${index} `);
        expect(found).not.toMatch(/SCAN crops(?! USING)/);
        // Only a crop outside the composed books reads its row.
        const readsRow = source.by === 'char' && phase === 'N';
        if (!readsRow) expect(found).toContain('COVERING INDEX');
        if (!sorted.includes(phase)) expect(found).not.toContain('TEMP B-TREE');
        const count = phase === 'R' || phase === 'K' ? rangeCountQuery(q, phase, source) : null;
        if (count) expect(plan(count.sql, count.values)).toContain(`INDEX ${index} `);
      }
    }
  });

  it('reads the decades, the time axis and the rest by key or index', () => {
    for (const source of sources) for (const kind of [0, 1] as const) {
      const composed = composedCountQuery(source, kind, [1, 2], false);
      expect(plan(composed.sql, composed.values)).toContain(source.by === 'char' ? 'crop_char_book' : 'crop_family_book');
      // The time axis ranks the crops it counts, so it sorts them; it reads them along an index.
      for (const axis of ['witness', 'composed'] as const) for (const statement of chronologyQueries(source, kind, axis, { style: 'cursive', productions: [1, 2], withheld: false }, [[1, 1653]], 6)) {
        const found = plan(statement.sql, statement.values);
        expect(found).toMatch(/USING (COVERING )?INDEX crop_/);
        expect(found).not.toMatch(/SCAN crops(?! USING)/);
      }
    }
    for (const [sql, values, index] of [[membersQuery(), [G.時], 'grapheme_head'], [groupKeysQuery(), ['[1]'], 'PRIMARY KEY'],
      [visualQuery(), [1], 'crop_visual'], [composedQuery(), [], 'book_date_axis'], [graphemeIdsQuery(), ['["時"]'], 'sqlite_autoindex_graphemes'], [writeFamiliesQuery(), ['[302]'], 'grapheme_head'],
      [corpusFormsQuery(), ['[1]'], 'INTEGER PRIMARY KEY'], [datingQuery(), ['["a"]'], 'PRIMARY KEY'], [cropSlotsQuery(), ['[1]', 'has_form'], 'current_claim_crop'],
      [wordSpellingsQuery(), ['時'], 'word_spelling_spelling'], [productionsQuery(), ['printed', 8, 'printed/'], 'SCAN productions'],
      [carriedFormsQuery(), ['[300,301,302]', '[]', '[1]'], 'carried_form_grapheme']] as [string, unknown[], string][]) {
      const found = plan(sql, values);
      expect(found).toContain(index);
      // The productions are a few dozen rows; every other table is read by key or index.
      expect(found.replace('SCAN productions', '')).not.toMatch(/SCAN (crops|graphemes|codes|book_dates|books|current_claims|word_spellings|words)(?! USING)/);
    }
  });
});

describe('a review of a glyph filed under its family', () => {
  // Runs last: it changes the collection the other tests read.
  const [, filed] = [0, CROPS.find(c => c.slot === 4 && c.page === PAGE_C)!];
  const review = async (crop: Crop, char: number | null) => {
    // Other fixtures may have warmed the shared pointer; these events belong after that publication.
    const gen = (await currentPointer(env)).through_gen + 1;
    db.query('UPDATE generation SET gen=?').run(gen);
    db.query('UPDATE crops SET char=? WHERE id=?').run(char, id(crop));
    // The cell moves from the crop's old character to its new one, both under its family.
    const cell = (c: number | null, n: number) => db.query(`INSERT INTO tally(gen,kind,char,family,book,production,style,state,quiz,decade,n)
      VALUES(?,1,?,?,3,1,2,1,1,-1,?) ON CONFLICT DO UPDATE SET n=n+excluded.n`).run(gen, c ?? -1, G['ね'], n);
    const before = crop.char ? G[crop.char] : null;
    cell(before, -1); cell(char, 1);
    crop.char = char === null ? null : Object.keys(G).find(k => G[k] === char)!;
  };
  it('moves it to its character once, and counts it there and not also as unassigned', async () => {
    await review(filed, G['𛂒']);
    const family = await get('/layers/candidates?code_point=U%2B306D&scope=grapheme');
    expect(ids(family.body.glyph_items).filter(i => i === crop(PAGE_C, 4))).toHaveLength(1);
    expect([family.body.glyphs, family.body.family_total, family.body.unassigned_count]).toEqual([4, 4, 2]);
    const own = await get('/layers/candidates?code_point=U%2B1B092');
    expect([ids(own.body.glyph_items).sort(), own.body.glyphs]).toEqual([[crop(PAGE_C, 1), crop(PAGE_C, 4)].sort(), 2]);
  });
  it('keeps a crop whose character a review clears in its family, as unassigned', async () => {
    const glyph = CROPS.find(c => c.slot === 1 && c.page === PAGE_C)!;
    await review(glyph, null);
    const unassigned = await get('/layers/candidates?code_point=U%2B306D&scope=grapheme&visual_group=unassigned');
    expect(ids(unassigned.body.glyph_items)).toContain(crop(PAGE_C, 1));
    expect(unassigned.body.unassigned_count).toBe(3);
  });
});

describe('the forms of a whole grapheme', () => {
  const point = (char: string) => 'U+' + char.codePointAt(0)!.toString(16).toUpperCase();
  const T = 951, TU = 952;
  async function family() {
    db.exec(`INSERT OR IGNORE INTO graphemes(id,text,head) VALUES(${T},'図',${T}),(${TU},'圖',${T}),(953,'圗',${T}),(954,'图',${T})`);
    const members = ['図', '圖', '圗', '图', '\uFA44'].map(char => ({ code_point: point(char), char }));
    const edges: Record<string, unknown[]> = { 図: [['圖', 'shinjitai', 's', '', 1, 1]],
      圖: [['図', 'shinjitai', 's', '', 1, 1], ['啚', 'variant', 's', '', 0, 1], ['度', 'non-cognate', 's', '', 0, 0], ['谋', 'variant', 's', '', 0, 1]], 图: [['啚', 'variant', 's', '', 0, 1], ['徒', 'borrowed', 's', '', 0, 0]] };
    for (const [i, { char }] of members.slice(0, 4).entries()) objects.set(`v3/reference/char/${point(char)}.json.gz`, json({ grapheme: T + i, data: card(char, { grapheme: { code_point: 'U+56F3', char: '図' } }),
      detail: card(char, { grapheme: { code_point: 'U+56F3', char: '図', members } }), variants: edges[char] ?? [], derived: char === '図' ? [['⿰日寺', [[['日', '目']]]]] : [],
      substitutions: char === '図' ? [['日', '目', 4, [{ a: '昨', b: '眲', sources: ['src3'] }]]] : [], writes: char === '圖' ? [['谋', 1]] : [], writers: char === '图' ? [['团', 1]] : [], ids: { 図: T, 圖: TU } }));
    const shapes: Record<string, unknown> = { 図: [['⿴囗乂'], [{ text: '⿴囗乂', regions: 'J' }, { text: '⿴囗丷', regions: 'G' }], null], 圖: [['⿴囗啚'], [{ text: '⿴囗啚', regions: 'GJ' }, { text: '⿴囗口㐭', regions: 'H' }], null],
      圗: [['⿱口回'], [{ text: '⿱口回', regions: 'G' }, { text: '⿴囗丷', regions: 'T' }], null] };
    const shards = new Map<string, Record<string, unknown>>();
    for (const [char, value] of Object.entries(shapes)) { const name = `v3/reference/ids/${await referenceShard(char, IDS_DIGITS)}.json.gz`; shards.set(name, { ...shards.get(name), [char]: value }); }
    for (const [name, shard] of shards) objects.set(name, json(shard));
  }
  const forms = async (char: string, as: Env = env) => (await (await worker.fetch(new Request(`https://glyphatlas.org/layers/graphemes/${encodeURIComponent(point(char))}/forms`), as, ctx)).json()) as any;
  it('are every member with its shapes, each shape of one member alone its own, and the variants of any', async () => {
    await family();
    const found = await forms('図');
    expect([found.head, found.members.map((m: any) => m.char)]).toEqual(['図', ['図', '圖', '圗', '图']]);
    expect(found.members.map((m: any) => m.shapes.map((shape: any) => [shape.text, shape.base]))).toEqual([
      [['⿴囗乂', '図'], ['⿴囗丷', null]], [['⿴囗啚', '圖'], ['⿴囗口㐭', '圖']], [['⿱口回', '圗'], ['⿴囗丷', null]], []]);
    expect(found.variants.map((v: any) => [v.char, v.via])).toEqual([['啚', ['圖', '图']]]);
    // 谋 is another grapheme 圖 writes; 度 and 徒 are no variants, and neither is listed as a member.
    expect(found.related.map((v: any) => [v.char, v.via])).toEqual([['谋', ['圖']], ['度', ['圖']], ['徒', ['图']]]);
    expect(found.derived).toEqual([{ char: '⿰日寺', code_point: null, encoded: false, via: ['図'], route: [['日', '目']] }]);
  });
  it('are the same from any member, and kept, what the crops carry apart', async () => {
    await family();
    edge.clear();
    const read: string[] = [], get = env.MEDIA.get.bind(env.MEDIA);
    const counting = { ...env, MEDIA: { ...env.MEDIA, get: async (name: string, options?: any) => { read.push(name); return get(name, options); } } } as unknown as Env;
    const first = await forms('圖', counting);
    expect(first.forms).toEqual([]);
    const crop = id(CROPS.find(c => c.char === '凡' && c.kind === 0)!);
    db.exec(`INSERT INTO representations(id,scheme,value) VALUES('rp:t','ids','⿴囗斗'); INSERT INTO forms(id,anchor,created_by,created_at) VALUES('fm:t','rp:t','u','t');
      INSERT INTO carried_forms(crop,grapheme,form,cut) VALUES(${crop},${TU},'fm:t',0); INSERT INTO ledger_submissions VALUES('carried','u','{}','{}','t')`);
    try {
      read.length = 0;
      const second = await forms('図', counting);
      expect({ ...second, forms: [] }).toEqual(first);
      expect(second.forms.map((f: any) => [f.char, f.count])).toEqual([['⿴囗斗', 1]]);
      expect(read.filter(name => name.includes('/reference/ids/'))).toEqual([]);
    } finally { db.exec(`DELETE FROM carried_forms; DELETE FROM ledger_submissions WHERE id='carried'`); }
  });
  // 隆 in two shapes, the first 𨺓's only description; 逸 in two, the second encoded again as 𨓜.
  async function owned(second = '⿰阝⿳夂一生') {
    const families: [string, string[], Record<string, unknown>][] = [['隆', ['隆', '𨺓'], { 隆: [['⿰阝⿱夂生', second], [{ text: '⿰阝⿱夂生', regions: 'J' }, { text: second, regions: 'G' }], null], 𨺓: [['⿰阝⿱夂生'], null, null] }],
      ['逸', ['逸', '𨓜'], { 逸: [['⿺辶兔', '⿺辶免'], [{ text: '⿺辶兔', regions: 'GT' }, { text: '⿺辶免', regions: 'J', chars: ['𨓜'] }], null] }]];
    for (const [i, [head, chars, ids]] of families.entries()) {
      db.exec(chars.map((char, j) => `INSERT OR IGNORE INTO graphemes(id,text,head) VALUES(${960 + i * 10 + j},'${char}',${960 + i * 10});`).join(''));
      const grapheme = { code_point: point(head), char: head, members: chars.map(char => ({ code_point: point(char), char })) };
      for (const char of chars) objects.set(`v3/reference/char/${point(char)}.json.gz`, json({ grapheme: 960, data: card(char, { grapheme: { code_point: point(head), char: head } }),
        detail: card(char, { grapheme }), variants: [], derived: [], substitutions: [], writes: [], writers: [], ids: {} }));
      for (const [char, value] of Object.entries(ids)) objects.set(`v3/reference/ids/${await referenceShard(char, IDS_DIGITS)}.json.gz`, json({ [char]: value }));
    }
  }
  const bases = (found: any) => found.members.map((m: any) => m.shapes.map((shape: any) => [shape.text, shape.base]));
  it('name no member for a shape another is described by, though it has that shape alone or is encoded for it', async () => {
    await owned();
    expect(bases(await forms('隆'))).toEqual([[['⿰阝⿱夂生', null], ['⿰阝⿳夂一生', '隆']], []]);
    expect(bases(await forms('逸'))).toEqual([[['⿺辶兔', '逸'], ['⿺辶免', null]], []]);
  });
  it('are kept as long as the objects they were read from', async () => {
    const start = Date.now() + 3_600_000, kept = () => Number(/max-age=(\d+)/.exec([...edge].find(([url]) => url.includes('grapheme-forms?'))![1].headers.get('cache-control')!)![1]);
    const drop = (part: string) => { for (const url of [...edge.keys()]) if (url.includes(part)) edge.delete(url) };
    try {
      setSystemTime(start);
      await owned();
      const before = bases(await forms('隆'));
      expect(kept()).toBe(300);
      // The reference data changes, and 200 seconds on an answer is made from what is still held.
      await owned('⿰阝⿱夊生');
      setSystemTime(start + 200_000);
      drop('grapheme-forms');
      db.exec("INSERT INTO imports(stamp,at) VALUES('reference-forms',1)");
      expect(bases(await forms('隆'))).toEqual(before);
      expect(kept()).toBe(100);
      setSystemTime(start + 300_001);
      drop('grapheme-forms');
      expect(bases(await forms('隆'))[0]).toContainEqual(['⿰阝⿱夊生', '隆']);
    } finally { setSystemTime(); db.exec("DELETE FROM imports WHERE stamp='reference-forms'"); edge.clear() }
  });
  it('are kept no longer than a shard read without a member of theirs', async () => {
    const start = Date.now() + 7_200_000, name = `v3/reference/ids/${await referenceShard('𨺓', IDS_DIGITS)}.json.gz`;
    try {
      setSystemTime(start);
      await owned();
      objects.set(name, json({}));
      await get('/layers/ids?c=𨺓');
      // The rest is read afresh: the answer is due when the shard held since the start is.
      setSystemTime(start + 250_000);
      for (const url of [...edge.keys()]) if (!url.endsWith(name)) edge.delete(url);
      expect(bases(await forms('隆'))[0][0]).toEqual(['⿰阝⿱夂生', '隆']);
      expect(/max-age=(\d+)/.exec([...edge].find(([url]) => url.includes('grapheme-forms?'))![1].headers.get('cache-control')!)![1]).toBe('50');
    } finally { setSystemTime(); edge.clear() }
  });
  it('are a kana’s members, and none for a character with no card', async () => {
    expect((await forms('𛂒')).members.map((m: any) => [m.char, m.shapes])).toEqual([['ね', []], ['𛂒', []]]);
    expect((await worker.fetch(new Request('https://glyphatlas.org/layers/graphemes/U%2B4E00/forms'), env, ctx)).status).toBe(404);
  });
});
