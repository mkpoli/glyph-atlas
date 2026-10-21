import { beforeAll, beforeEach, describe, expect, it } from 'bun:test';
import { Database } from 'bun:sqlite';
import { readdirSync, readFileSync } from 'node:fs';
import { gzipSync } from 'node:zlib';
import { d1 } from '../forms.test';
import { bundleKey, cropId, publicId } from '../atlas3';
import { SHARDS, currentPointer, phasesOf, rangeCountQuery, rangeQuery, type GalleryQuery, type Phase, type Source } from '../atlas3Gallery';
import { corpusFormsQuery, datingQuery } from '../atlas3Listing';
import { cropSlotsQuery } from '../cropPanels';
import { composedCountQuery, composedQuery, chronologyQueries, graphemeIdsQuery, groupKeysQuery, membersQuery, productionsQuery, visualQuery,
  wordSpellingsQuery, writtenVariants } from './character';
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
const G = { 時: 300, 时: 301, 旹: 302, 峕: 303, ね: 400, '𛂒': 401 } as Record<string, number>;
// Each character's family head, which its crops' records name as their family.
const HEAD = { 時: '時', 时: '時', 旹: '時', 峕: '峕', ね: 'ね', '𛂒': 'ね' } as Record<string, string>;
const BOOK = { a: 1, b: 2, c: 3 };
const PAGE_A = 7001, PAGE_B = 7002, PAGE_C = 7003;
const PACK = 'packs/test.bin';
type Crop = { slot: number; page: number; kind: number; char: string | null; family?: string; style: number; state?: number; quiz?: number;
  production?: number; visual?: number; shuffle: number };
const BOOK_OF = { [PAGE_A]: BOOK.a, [PAGE_B]: BOOK.b, [PAGE_C]: BOOK.c } as Record<number, number>;
const YEAR = { [BOOK.a]: 1700, [BOOK.b]: 1813, [BOOK.c]: null } as Record<number, number | null>;
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
  db.exec(`INSERT INTO books(id,key,title) VALUES(1,'hk:a','A'),(2,'hk:b','B'),(3,'codh:c','C');
    INSERT INTO productions VALUES(1,'printed',NULL),(2,'printed/woodblock',1),(3,'handwritten',NULL);
    INSERT INTO codes VALUES('state',1,'checked'),('state',2,'flagged'),('style',1,'running'),('style',4,'formal'),('style',5,'printed'),
      ('visual_group',1,'U+306D:abc'),('category',2,'kanji');
    INSERT INTO pages(id,key,book,rev,next_slot) VALUES(${PAGE_A},'hk:a:1',1,1,9),(${PAGE_B},'hk:b:1',2,1,9),(${PAGE_C},'corpus:codh:c:0',3,1,9);
    UPDATE generation SET gen=6;
    INSERT INTO book_dates(book,axis,kind,start,end,precision,text,label,status,calendar,source) VALUES
      (1,'witness','copied',1700,1700,'year','1700','1700','single','gregorian','s'),
      (1,'composed','composed',1653,1653,'year','1653','1653','single','gregorian','s'),
      (2,'witness','printed',1813,1813,'year','1813','1813','single','gregorian','s');
    INSERT INTO site VALUES('variant_sources','{"src1":"Source One"}'),('word_sources','{"dict":"A dictionary"}');
    INSERT INTO words VALUES('w:toki','ja','とき','noun');
    INSERT INTO word_spellings VALUES('w:toki','時','dict','p1','attested','dict','b','',' ',3,9),('w:toki','刻','dict','p2','attested','dict','b','','',1,2);`);
  for (const [text, gid] of Object.entries(G)) db.query('INSERT INTO graphemes(id,text,head) VALUES(?,?,?)')
    .run(gid, text, ['時', '时', '旹'].includes(text) ? G.時 : ['ね', '𛂒'].includes(text) ? G.ね : gid);
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
    data: card('時', { grapheme: { code_point: 'U+6642', char: '時' } }), detail: card('時', { grapheme: { code_point: 'U+6642', char: '時' }, visual_analysis: { groups: [] } }),
    variants: [['旹', 'variant', 'src1', '', 0, 1], ['旹', 'variant', 'src2', '', 0, 1], ['峕', 'variant', 'src1', '', 1, 1], ['时', 'simplified', 'src2', '', 0, 1]],
    derived: [['⿰日寺', [[['日', '目']]]]], substitutions: [['日', '目', 4, [{ a: '昨', b: '眲', sources: ['src3'] }]]],
    writes: [], writers: [['时', 1]], ids: { 時: G.時, 旹: G.旹, 峕: G.峕, 时: G.时 } }));
  objects.set('v3/reference/char/U+306D.json.gz', json({ grapheme: G.ね, data: card('ね', { grapheme: { code_point: 'U+306D', char: 'ね' } }),
    detail: card('ね', { grapheme: { code_point: 'U+306D', char: 'ね' }, visual_analysis: { status: 'ready', groups: [{ code: 1, label: 'Group 1', count: 2,
      representatives: [{ crop: id(filed[0]), source_label: 'ね' }], anchors: [{ crop: id(filed[1]), distance: 0.1 }] }] } }),
    variants: [], derived: [], substitutions: [], writes: [], writers: [], ids: { ね: G.ね, '𛂒': G['𛂒'] } }));
  objects.set('v3/reference/char/U+1B092.json.gz', json({ grapheme: G['𛂒'], data: card('𛂒', { grapheme: { code_point: 'U+306D', char: 'ね' } }),
    detail: card('𛂒', { grapheme: { code_point: 'U+306D', char: 'ね' } }), variants: [], derived: [], substitutions: [], writes: [], writers: [],
    ids: { ね: G.ね, '𛂒': G['𛂒'] } }));
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
    expect(body.words.items[0].spellings.map((s: any) => [s.spelling, s.current])).toEqual([['時', true], ['刻', false]]);
    expect(body.words.sources).toEqual({ dict: 'A dictionary' });
    // Four crops shown, one withheld.
    expect(body.occurrences).toEqual({ total: 4, exact: 4, exact_total: 4, filtered: 4 });
    expect(ids(body.samples)).toEqual([crop(PAGE_A, 0), crop(PAGE_A, 1), crop(PAGE_B, 0), crop(PAGE_B, 1)]);
  });

  it('answers 404 for a character no card has, and a card for a description sequence', async () => {
    expect((await get('/layers/characters/U+9F98')).status).toBe(404);
    const { status, body } = await get('/layers/characters/' + encodeURIComponent('⿰日寺'));
    expect([status, body.char, body.samples]).toEqual([200, '⿰日寺', []]);
  });
});

describe('a gallery of the collection', () => {
  it('lists a character by style group, shuffle and id, leaving withheld crops out and counting them', async () => {
    const { body } = await get('/layers/occurrences?code_point=U%2B6642&limit=2');
    expect(ids(body.items)).toEqual([crop(PAGE_A, 0), crop(PAGE_A, 1)]);
    expect([body.total, body.styles, body.withheld, body.scope]).toEqual([4, { cursive: 2, unassessed: 1, formal: 1 }, 1, 'character']);
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
      [[crop(PAGE_B, 0), crop(PAGE_B, 1)], { cursive: 0, unassessed: 1, formal: 1 }, null, { from: 1801, to: 1900 }]);
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

describe('decades and the time axis', () => {
  it('count a character’s crops per decade on either axis', async () => {
    const witness = await get('/layers/decades?code_point=U%2B6642');
    expect([witness.body.local, witness.body.corpus]).toEqual([[[1700, 2], [1810, 2]], [[null, 1], [1700, 1]]]);
    const composed = await get('/layers/decades?code_point=U%2B6642&axis=composed&withheld=show');
    expect([composed.body.local, composed.body.corpus]).toEqual([[[null, 2], [1650, 3]], [[null, 1], [1650, 1]]]);
    const family = await get('/layers/decades?code_point=U%2B6642&scope=grapheme');
    expect(family.body.local).toEqual([[1700, 2], [1810, 4]]);
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
      expect(plan(composedCountQuery(source, kind, [1, 2], false), [source.key])).toContain(source.by === 'char' ? 'crop_char_book' : 'crop_family_book');
      // The time axis ranks the crops it counts, so it sorts them; it reads them along an index.
      for (const axis of ['witness', 'composed'] as const) for (const statement of chronologyQueries(source, kind, axis, { style: 'cursive', productions: [1, 2], withheld: false }, [[1, 1653]], 6)) {
        const found = plan(statement.sql, statement.values);
        expect(found).toMatch(/USING (COVERING )?INDEX crop_/);
        expect(found).not.toMatch(/SCAN crops(?! USING)/);
      }
    }
    for (const [sql, values, index] of [[membersQuery(), [G.時], 'grapheme_head'], [groupKeysQuery(), ['[1]'], 'PRIMARY KEY'],
      [visualQuery(), [1], 'crop_visual'], [composedQuery(), [], 'book_date_axis'], [graphemeIdsQuery(), ['["時"]'], 'sqlite_autoindex_graphemes'],
      [corpusFormsQuery(), ['[1]'], 'INTEGER PRIMARY KEY'], [datingQuery(), ['["a"]'], 'PRIMARY KEY'], [cropSlotsQuery(), ['[1]', 'has_form'], 'current_claim_crop'],
      [wordSpellingsQuery(), ['時'], 'word_spelling_spelling'], [productionsQuery(), ['printed', 8, 'printed/'], 'SCAN productions']] as [string, unknown[], string][]) {
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

