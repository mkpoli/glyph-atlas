import { nextPage } from './fixtures/pageIds';
import { afterEach, beforeEach, describe, expect, it } from 'bun:test';
import { Database } from 'bun:sqlite';
import { createHash } from 'node:crypto';
import { readdirSync, readFileSync } from 'node:fs';
import { gzipSync } from 'node:zlib';
import { d1 } from '../forms.test';
import { bundleKey, cropId, publicId } from '../atlas3';
import { votesKey } from '../atlas3Votes';
import { versionToken } from '../citation';
import { cropSlotsQuery, formNamesQuery, wordMethodsQuery, wordNamesQuery } from '../cropPanels';
import { cropVersion, cropVersionNow, recordQuery } from '../cropRecords';
import { bookDatesQuery, dateClaimsQuery, lineQuery, listedCropsQuery, samePixels } from './crop';
import worker from '../index';

// The Workers cache.
const edge = new Map<string, Response>();
(globalThis as any).caches = { default: {
  match: async (request: Request) => edge.get(request.url)?.clone(),
  put: async (request: Request, response: Response) => { edge.set(request.url, response.clone()); },
} };
beforeEach(() => edge.clear());
const databases: Database[] = [];
afterEach(() => { for (const db of databases.splice(0)) db.close(); });

function schema(folder: string) {
  const db = new Database(':memory:'); databases.push(db);
  const dir = new URL(`../../${folder}/`, import.meta.url);
  for (const file of readdirSync(dir).filter(f => f.endsWith('.sql')).sort()) db.exec(readFileSync(new URL(file, dir), 'utf8'));
  return db;
}
function atlasSchema() {
  const db = schema('migrations-atlas');
  return db;
}

// What build.py leaves out of a bundle entry (`ROW_FIELDS`), and null values.
const ROW_FIELDS = new Set(['id', 'state', 'revision', 'production', 'category', 'style', 'quiz', 'suspect', 'page_id', 'visual_group', 'issue',
  'char', 'code_point', 'written_character', 'grapheme', 'identity_status', 'identity_basis', 'form_decision', 'form_cluster', 'crop_version', 'crop_editable']);
const bundleEntry = (data: Record<string, any>) => Object.fromEntries(Object.entries(data).filter(([k, v]) => v !== null && !ROW_FIELDS.has(k)));
const withoutNulls = (data: Record<string, any>) => Object.fromEntries(Object.entries(data).filter(([, v]) => v !== null));

const hash = 'a'.repeat(64), other = 'b'.repeat(64);
// A collected crop's record as the old database held it (`units.data`).
const old = (id: string, label: string, x: number, extra: object = {}) => ({ id, label, production: 'handwritten', production_label: 'Handwritten',
  production_evidence: [], script: 'han', jibo: null, revision: 0, state: 'pending', page_id: 'hk:0123456789abcdef0123456789abcdef:4',
  line_id: 'hk:0123456789abcdef0123456789abcdef:4:L1', repair: null, box: { x, y: 10, w: 20, h: 30 }, image_sha256: hash,
  image: `/atlas/media/${label.codePointAt(0)}.webp`, context_image: '/atlas/media/ctx.webp', source: 'Book', text: '一二三', page_number: 5,
  context: true, context_box: { x: 0, y: 0, w: 400, h: 400 }, line: null, source_scale: [1, 1], crop_editable: false,
  crop_box: { x, y: 10, w: 20, h: 30 }, licence: 'CC-BY-4.0', holder: 'Holder', attribution: 'Holder', rights_url: null,
  full_page_available: false, tone: '#c7b9a2', image_size: [20, 30], ...extra });

// An isolate holds the layout's build id for a minute; every test file names the same one.
const BUILD = '20261020T120000';
function setup() {
  const db = atlasSchema(), runs = schema('migrations-atlas-runs');
  const page = nextPage(), corpusPage = nextPage(), book = 'hk:0123456789abcdef0123456789abcdef', pageKey = `${book}:4`;
  const [a, b, c, d, r, x] = [0, 1, 2, 3, 4, 5].map(slot => cropId(page, slot));
  const glyph = cropId(corpusPage, 0);
  const text = { a: `hk:${page}:a`, b: `hk:${page}:b`, c: `hk:${page}:c`, d: `hk:${page}:d`, r: `hk:${page}:r`, x: `hk:${page}:x`, glyph: `codh:${page}:g` };
  const records = { a: old(text.a, '一', 10), b: old(text.b, '二', 40), c: old(text.c, 'ム', 70), d: old(text.d, '四', 100) };
  db.exec(`INSERT INTO books(id,key,title) VALUES(1,'${book}','Book'),(2,'codh:200014685','南総里見八犬伝');
    INSERT INTO pages(id,key,book,rev,next_slot) VALUES(${page},'${pageKey}',1,3,6),(${corpusPage},'corpus:codh:x:0',2,1,1);
    INSERT INTO productions(id,key) VALUES(1,'handwritten'),(2,'printed');
    INSERT INTO graphemes(id,text,head) VALUES(1,'一',NULL),(2,'二',NULL),(3,'む',NULL),(4,'ム',3),(5,'四',NULL),(6,'翁',NULL),(7,'三',NULL);
    INSERT INTO codes(field,code,key) VALUES('state',0,'pending'),('state',1,'checked'),('state',2,'flagged'),('category',1,'kanji'),
      ('models',3,'[{"name":"NDLkotenOCR","sha256":"fd75"}]');
    INSERT INTO crops(id,kind,book,char,production,category,style,state,quiz,shuffle,cut,revision) VALUES
      (${a},0,1,1,1,1,0,0,1,1,1,0),(${b},0,1,2,1,1,0,2,1,2,0,2),(${c},0,1,4,1,1,0,1,1,3,0,1),(${d},0,1,5,1,1,0,3,1,4,0,2),
      (${x},2,1,7,1,1,0,0,1,6,0,0),(${glyph},1,2,6,2,NULL,0,1,1,5,0,1);
    INSERT INTO book_dates(book,axis,kind,start,end,precision,text,label,status,calendar,conversion,source)
      VALUES(1,'witness','copied',1790,1790,'year','寛政二年','1790','single','japanese','{"query":"q"}','src');
    INSERT INTO assertions(id,subject,predicate,value,tier,asserted_by,asserted_at) VALUES
      ('d1','${book}','date_copied','{"of":"witness","text":"寛政二年","start":1790,"end":1790,"precision":"year","calendar":"japanese"}','attested','x','t');
    INSERT INTO assertion_evidence(assertion,kind,ref,locator) VALUES('d1','source','src','p. 1');
    INSERT INTO forms(id,anchor,created_by,created_at) VALUES('form:1','rep:1','x','t');
    INSERT INTO representations(id,scheme,value) VALUES('rep:1','unicode','弌');
    INSERT INTO words(id,language,reading,class) VALUES('ja/いち','ja','いち','noun');
    INSERT INTO assertions(id,subject,crop,cut,predicate,object,tier,asserted_by,asserted_at,method) VALUES
      ('w1','${text.a}',${a},1,'writes_word','ja/いち','observed','x','t','ruby');
    INSERT INTO current_claims(subject,predicate,scope,slot,crop,cut,status,members,supporting,claims,resolver,at) VALUES
      ('${text.a}','has_form','','',${a},1,'single','[{"object":"form:1","value":null,"confidence":null,"assertion":"f1"}]','["f1"]',
        '[{"asserted_by":"x","members":[{"assertion":"f1"}]}]','r','t'),
      ('${text.d}','has_form','','',${d},1,'single','[{"object":"form:1","value":null,"confidence":null,"assertion":"f2"}]','["f2"]','[]','r','t'),
      ('${text.b}','has_form','','',${b},5,'single','[{"object":"form:0","value":null,"confidence":null,"assertion":"f0"}]','["f0"]','[]','r','t'),
      ('${text.a}','writes_word','','',${a},1,'single','[{"object":"ja/いち","value":null,"confidence":null,"assertion":"w1"}]','["w1"]',
        '[{"asserted_by":"x","members":[{"assertion":"w1"}]}]','r','t');
    -- b: flagged for its crop, then marked unclear, then that undone: its issue is its last event's.
    INSERT INTO events(id,crop,revision,cut,gen,actor,submission,at,kind,label,state,issue) VALUES
      (1,${b},1,0,0,1,1,1,1,2,2,2),(2,${b},2,0,0,1,2,2,1,2,2,5),(3,${b},3,0,0,1,3,3,5,2,2,2);
    UPDATE events SET reverses=2 WHERE id=3;
    -- c: written as ム in review, and a later match keeps it.
    INSERT INTO events(id,crop,revision,cut,gen,actor,submission,at,kind,label,state,issue,named) VALUES (4,${c},1,0,0,1,4,4,1,4,1,0,4);
    INSERT INTO events(id,crop,revision,cut,gen,actor,submission,at,kind,label,state) VALUES (5,${glyph},1,0,0,1,5,5,0,6,1);
    -- d: a review redrew its box, then a round marked it seen, which moved its revision; the old site's
    -- revision did not count marks.
    INSERT INTO events(id,crop,revision,cut,gen,actor,submission,at,kind,label,state,x,y,w,h) VALUES
      (8,${d},0,-1,0,1,8,1750000000000,0,5,0,101,11,22,33),(7,${d},1,0,0,1,7,1760000000000,0,5,0,101,11,22,33),(6,${d},2,0,0,1,6,1760000100000,3,5,3,NULL,NULL,NULL,NULL);
    INSERT INTO reviews(crop,gen,at,state,x,y,w,h) VALUES(${d},1,1,3,101,11,22,33);
    INSERT INTO form_clusters(id,key,family,label,count,coherence,shape_position,size_position,list,form,issue,character,written_family,decision)
      VALUES(9,'U+7FC1:35ced6e49b','U+7FC1','翁',1,1,0,0,'9-x','𦐈',NULL,NULL,'U+7FC1','dec');
    UPDATE crops SET cluster=9 WHERE id=${glyph};`);
  runs.exec(`INSERT INTO runs(size,graphemes,hand,shuffle,first,second,book) VALUES
    (2,'一二',0,1,${a},${b},1),(2,'二ム',0,2,${b},${c},1),(2,'ム四',0,3,${c},${d},1)`);
  // The corpus glyph's published record, in a pack.
  const corpus = { id: text.glyph, unit_id: text.glyph, char: '翁', label: '翁', code_point: 'U+7FC1', box: { x: 1, y: 2, w: 3, h: 4 },
    image: '/atlas/media/glyph.webp', source: { corpus: 'codh-full', document_id: 'codh:200014685', title: '南総里見八犬伝' },
    source_revision: 'rev1', written_character: '翁', identity_basis: 'normalized_transcription', identity_status: 'assigned', grapheme: 'U+7FC1',
    visual_group: null, origin: 'corpus', state: 'pending', revision: 0, proxyable: true, licence: 'CC-BY-SA-4.0' };
  const packed = new TextEncoder().encode('xx' + JSON.stringify(corpus));
  const cutA = { cut: 0, box: { x: 5, y: 10, w: 20, h: 30 }, image_sha256: other, image: '/atlas/media/old.webp', at: '2026-10-03T15:27:32.712Z' };
  const encodingA = { pixels: 'c'.repeat(64), image: '/atlas/media/enc.webp', at: '2026-10-05T00:00:00.000Z' };
  const pageFacts = { id: pageKey, document: book, title: 'Book', page_number: 5, width: 400, height: 400, service: 'https://x.org/iiif/p',
    previous: `${book}:3`, next: null, crops: [text.c, text.a, 'hk:gone', text.r, text.b, text.x] };
  const objects = new Map<string, Uint8Array>([
    [bundleKey(page, 3), gzipSync(JSON.stringify({ key: pageKey, book: 1, rev: 3, page: pageFacts, crops: [
      { slot: 0, id: text.a, ...bundleEntry(records.a), cuts: [cutA], encodings: [encodingA], cut_at: '2026-10-13T16:53:07.985Z', image_place: ['packs/p', 0, 1] },
      { slot: 1, id: text.b, ...bundleEntry(records.b) }, { slot: 2, id: text.c, ...bundleEntry({ ...records.c, label: 'ム' }) },
      { slot: 3, id: text.d, ...bundleEntry(records.d), cut_at: '2026-10-01T00:00:00.000Z' }, { slot: 4, id: text.r, retired: true, replaced_by: a, ...bundleEntry(old(text.r, '五', 130)) },
      { slot: 5, id: text.x, ...bundleEntry(old(text.x, '三', 160)) }] }))],
    [bundleKey(corpusPage, 1), gzipSync(JSON.stringify({ key: 'corpus:codh:x:0', book: 2, rev: 1, page: null,
      crops: [{ slot: 0, id: text.glyph, label: '翁', record_place: ['packs/records', 2, packed.length - 2] }] }))],
    ['packs/records', packed],
    [votesKey(BUILD, page), gzipSync(JSON.stringify({ 0: { cut: 1, models: 3,
      visual: { candidates: [{ text: '一', score: 0.9, engine: 0 }], votes: [] },
      context: { line_id: 'l', anchor: 1, source_text: '一', candidates: [['一', 0, '', '二']] } } }))],
  ]);
  const alias = (t: string) => `v3/aliases/${createHash('sha256').update(t).digest('hex').slice(0, 3)}.json.gz`;
  const shards = new Map<string, Record<string, number>>();
  for (const [t, id] of [[text.a, a], [text.r, r], [text.glyph, glyph]] as const) shards.set(alias(t), { ...shards.get(alias(t)), [t]: id });
  for (const [name, shard] of shards) objects.set(name, gzipSync(JSON.stringify(shard)));
  db.exec(`INSERT INTO codes(field,code,key) VALUES('build',0,'${BUILD}')`);
  const MEDIA = { get: async (name: string, options?: { range?: { offset: number; length: number } }) => {
    const whole = objects.get(name);
    if (!whole) return null;
    const bytes = options?.range ? whole.slice(options.range.offset, options.range.offset + options.range.length) : whole;
    return { body: new Response(bytes).body!, arrayBuffer: async () => new Uint8Array(bytes).buffer, json: async () => JSON.parse(new TextDecoder().decode(bytes)) };
  } };
  const env = { ATLAS: d1(db), ATLAS_RUNS: d1(runs), MEDIA, CATALOGUE_LAG: '0' } as unknown as Env;
  const waits: Promise<unknown>[] = [];
  const ctx = { waitUntil: (p: Promise<unknown>) => waits.push(p), passThroughOnException() {} } as unknown as ExecutionContext;
  const get = async (path: string) => { const response = await worker.fetch(new Request('https://glyphatlas.org' + path), env, ctx); await Promise.all(waits); return response; };
  return { db, runs, env, get, ids: { a, b, c, d, r, x, glyph }, text, records, pageKey, book };
}

describe('a crop\'s record', () => {
  it('is the old record field for field, apart from its public id and the null fields a bundle leaves out', async () => {
    const { get, ids, records, book } = setup();
    const response = await get(`/atlas/characters/${publicId(ids.a)}`);
    expect(response.status).toBe(200);
    const pub = publicId(ids.a);
    const want = { ...withoutNulls(records.a), id: pub, source_id: records.a.id, crop_version: `${pub}@${hash}@10,10,20,30`, crop_editable: true,
      form: { status: 'single', values: [{ form: 'form:1', scheme: 'unicode', text: '弌', confidence: null }], supporting: ['f1'], by: ['x'] },
      word: { status: 'single', values: [{ word: 'ja/いち', reading: 'いち', class: 'noun', form: null }], methods: ['ruby'], supporting: ['w1'], by: ['x'] },
      honkoku_url: `https://app.honkoku.org/transcription/${book.slice(3)}/5`,
      dating: { witness: { kind: 'copied', start: 1790, end: 1790, precision: 'year', qualifier: null, uncertain: false, text: '寛政二年',
        label: '1790', status: 'single', calendar: 'japanese', source: 'src', hutime: 'q' } },
      dates: [{ id: 'd1', kind: 'copied', of: 'witness', text: '寛政二年', start: 1790, end: 1790, precision: 'year', qualifier: null,
        uncertain: false, day: null, calendar: 'japanese', hutime: null, note: null, tier: 'attested', source: 'src', locator: 'p. 1' }] };
    expect(await response.json()).toEqual(want);
  });

  it('takes a flagged crop\'s issue from its last event, an undo included', async () => {
    const { get, ids } = setup();
    const record = await (await get(`/atlas/characters/${publicId(ids.b)}`)).json() as any;
    expect([record.state, record.issue, record.revision, record.category]).toEqual(['flagged', 'crop', 2, 'kanji']);
    // A form claimed on another cut than the crop's is not live.
    expect(record.form).toBeNull();
  });

  it('keeps the issue a batch correction left a reported crop with', async () => {
    const { db, get, ids } = setup();
    db.exec('UPDATE events SET kept_issue=3 WHERE id=3');
    expect((await (await get(`/atlas/characters/${publicId(ids.b)}`)).json() as any).issue).toBe('blank');
    const line = await (await get(`/atlas/characters/${publicId(ids.b)}/line`)).json() as any;
    expect(line.items.find((item: any) => item.id === publicId(ids.b)).issue).toBe('blank');
  });

  it('shows a character a review wrote as its written identity', async () => {
    const { get, ids } = setup();
    const record = await (await get(`/atlas/characters/${publicId(ids.c)}`)).json() as any;
    expect(record).toMatchObject({ label: 'ム', char: 'ム', code_point: 'U+30E0', written_character: 'ム', identity_status: 'assigned',
      identity_basis: 'human_review', script: 'katakana', grapheme: 'U+3080', visual_group: null, state: 'checked', issue: null });
  });

  it('shows a marked crop as pending at the revision before its mark, with its redrawn box until a publication cuts it', async () => {
    const { get, ids } = setup();
    const record = await (await get(`/atlas/characters/${publicId(ids.d)}`)).json() as any;
    const pub = publicId(ids.d);
    expect(record).toMatchObject({ state: 'pending', revision: 1, box: { x: 101, y: 11, w: 22, h: 33 }, box_pending: true, crop_version: `${pub}@${hash}@101,11,22,33` });
    // Its claims name the cut the redraw will be: the next one.
    expect(record.form?.values[0].form).toBe('form:1');
  });

  it('reads a corpus glyph\'s published record under its row and its cluster\'s form', async () => {
    const { get, ids, text } = setup();
    const record = await (await get(`/atlas/characters/${publicId(ids.glyph)}`)).json() as any;
    expect(record).toMatchObject({ id: publicId(ids.glyph), unit_id: text.glyph, state: 'checked', revision: 1,
      form_cluster: { id: 'U+7FC1:35ced6e49b' }, form_decision: { form: '𦐈', basis: 'form_cluster' }, written_character: '𦐈', label: '𦐈',
      identity_basis: 'form_cluster', crop_editable: false, crop_version: `${publicId(ids.glyph)}@rev1@1,2,3,4`, dating: {} });
    expect(record.honkoku_url).toBeUndefined();
    const corpus = await get(`/atlas/corpus/character?id=${publicId(ids.glyph)}`);
    expect(await corpus.json()).toEqual(record);
  });

  it('reads a corpus glyph outside the clustering under the cluster decision it carries from an earlier one', async () => {
    const { db, get, ids } = setup();
    db.exec(`UPDATE crops SET cluster=NULL WHERE id=${ids.glyph};
      INSERT INTO form_glyphs(crop,family,cluster,form,issue,character,written_family,decision,carried)
        VALUES(${ids.glyph},'U+7FC1',NULL,'𦐈',NULL,NULL,'U+7FC1','old','old')`);
    const record = await (await get(`/atlas/characters/${publicId(ids.glyph)}`)).json() as any;
    expect(record).toMatchObject({ form_decision: { form: '𦐈', basis: 'form_cluster' }, written_character: '𦐈', label: '𦐈',
      identity_status: 'assigned', identity_basis: 'form_cluster', grapheme: 'U+7FC1' });
    expect(record.form_cluster).toBeUndefined();
  });

  it('redirects an old text id and a retired crop to the crop\'s public id, and answers 404 for a candidate or nothing', async () => {
    const { get, ids, text } = setup();
    const moved = await get(`/atlas/characters/${encodeURIComponent(text.a)}`);
    expect([moved.status, moved.headers.get('location')]).toEqual([302, `https://glyphatlas.org/atlas/characters/${publicId(ids.a)}`]);
    expect((await get(`/atlas/characters/${publicId(ids.r)}/versions`)).headers.get('location'))
      .toBe(`https://glyphatlas.org/atlas/characters/${publicId(ids.a)}/versions`);
    const query = await get(`/atlas/corpus/character?id=${encodeURIComponent(text.glyph)}`);
    expect(query.headers.get('location')).toBe(`https://glyphatlas.org/atlas/corpus/character?id=${publicId(ids.glyph)}`);
    for (const id of [publicId(ids.x), 'zzzzzz', 'hk:none', '%E0'])
      expect(await (await get(`/atlas/characters/${id}`)).json()).toEqual({ detail: 'This character is not in the published collection.' });
  });

  it('gives the claims port the version and cut a claim is checked against', async () => {
    const { env, ids, text } = setup();
    expect(await cropVersionNow(env, ids.a)).toEqual({ cut: 1, version: `${publicId(ids.a)}@${hash}@10,10,20,30`, text: text.a, kind: 0 });
    expect((await cropVersionNow(env, ids.d))?.cut).toBe(1);
    expect(await cropVersionNow(env, ids.x)).toBeNull();
  });
});

describe('a crop\'s versions', () => {
  it('lists its earlier cuts, then the one it has now', async () => {
    const { get, ids, book } = setup();
    const pub = publicId(ids.a);
    expect(await (await get(`/atlas/characters/${pub}/versions`)).json()).toEqual({ id: pub, current: `${pub}@${hash}@10,10,20,30`, versions: [
      { id: `${pub}@${other}@5,10,20,30`, pixels: other, box: '5,10,20,30', document: book, image: '/atlas/media/old.webp', at: '2026-10-03T15:27:32.712Z' },
      { id: `${pub}@${'c'.repeat(64)}@10,10,20,30`, pixels: 'c'.repeat(64), box: '10,10,20,30', document: book, image: '/atlas/media/enc.webp', at: '2026-10-05T00:00:00.000Z' },
      { id: `${pub}@${hash}@10,10,20,30`, pixels: hash, box: '10,10,20,30', document: book, image: '/atlas/media/19968.webp', at: '2026-10-13T16:53:07.985Z' }] });
  });
  it('lists a pending redraw after the cut it was drawn on, dated by its review', async () => {
    const { get, ids } = setup();
    const pub = publicId(ids.d), body = await (await get(`/atlas/characters/${pub}/versions`)).json() as any;
    expect(body.current).toBe(`${pub}@${hash}@101,11,22,33`);
    expect(body.versions.map((v: any) => [v.id, v.at])).toEqual([[`${pub}@${hash}@100,10,20,30`, '2026-10-01T00:00:00.000Z'],
      [`${pub}@${hash}@101,11,22,33`, new Date(1760000000000).toISOString()]]);
    // A citation of the cut before the redraw still resolves.
    const [cited] = await (await get(`/atlas/cite/crop/${pub}?v=${versionToken(pub, `${pub}@${hash}@100,10,20,30`)}`)).json() as any[];
    expect(cited.version).toBe(versionToken(pub, `${pub}@${hash}@100,10,20,30`));
  });
});

describe('a crop\'s citation', () => {
  it('cites the current version, an earlier one by its new token or one issued under the old text id', async () => {
    const { get, ids, text } = setup();
    const pub = publicId(ids.a);
    const current = versionToken(pub, `${pub}@${hash}@10,10,20,30`)!, earlier = `${pub}@${other}@5,10,20,30`;
    const response = await get(`/atlas/cite/crop/${pub}`);
    expect([response.status, response.headers.get('access-control-allow-origin'), response.headers.get('cache-control')]).toEqual([200, '*', 'no-cache']);
    const [item] = await response.json() as any[];
    expect([item.version, item.URL, item.title]).toEqual([current, `https://glyphatlas.org/crop/${pub}?v=${current}`, '一']);
    const [first] = await (await get(`/atlas/cite/crop/${pub}?v=${versionToken(pub, earlier)}`)).json() as any[];
    expect([first.version, first.title]).toEqual([versionToken(pub, earlier), pub]);
    const issued = versionToken(text.a, cropVersion(text.a, { image_sha256: other, box: { x: 5, y: 10, w: 20, h: 30 } }))!;
    const [again] = await (await get(`/atlas/cite/crop/${pub}?v=${issued}`)).json() as any[];
    expect(again.version).toBe(versionToken(pub, earlier));
    expect((await get(`/atlas/cite/crop/${pub}?v=zzzzzz`)).status).toBe(404);
    expect((await get(`/atlas/cite/crop/${pub}?v=2c9afc05d11e-1`)).status).toBe(422);
    expect(await (await get(`/atlas/cite/crop/${pub}?format=bibtex`)).text()).toStartWith(`@misc{glyphatlas-一-${current},`);
    const moved = await get(`/atlas/cite/crop/${encodeURIComponent(text.a)}?v=${issued}`);
    expect([moved.status, moved.headers.get('location'), moved.headers.get('access-control-allow-origin')])
      .toEqual([302, `https://glyphatlas.org/atlas/cite/crop/${pub}?v=${issued}`, '*']);
  });
});

describe('a crop\'s line', () => {
  it('reads its neighbours in reading order, as the review path judges them', async () => {
    const { get, ids } = setup();
    const body = await (await get(`/atlas/characters/${publicId(ids.b)}/line`)).json() as any;
    expect(body.id).toBe(publicId(ids.b));
    expect(body.items.map((i: any) => [i.offset, i.label, i.state, i.issue])).toEqual([[-1, '一', 'pending', null], [0, '二', 'flagged', 'crop'],
      [1, 'ム', 'checked', null], [2, '四', 'pending', null]]);
    expect(body.items[3].revision).toBe(1);
    expect(body.items[0]).toEqual({ id: publicId(ids.a), offset: -1, label: '一', image: '/atlas/media/19968.webp', revision: 0, image_sha256: hash,
      state: 'pending', issue: null, tone: '#c7b9a2', image_size: [20, 30] });
  });
  it('is empty for a crop with no neighbour or no crop, and redirects an old id', async () => {
    const { get, ids, text } = setup();
    expect(await (await get(`/atlas/characters/${publicId(ids.glyph)}/line`)).json()).toEqual({ id: publicId(ids.glyph), reach: 4, items: [] });
    expect(await (await get('/atlas/characters/zzzzzz/line')).json()).toEqual({ id: 'zzzzzz', reach: 4, items: [] });
    expect((await get(`/atlas/characters/${encodeURIComponent(text.a)}/line`)).status).toBe(302);
  });
});

describe('a source page', () => {
  it('holds its collected crops in the record\'s order, as the site has them now', async () => {
    const { get, ids, pageKey, book } = setup();
    const view = await (await get(`/atlas/pages/${encodeURIComponent(pageKey)}`)).json() as any;
    expect(view.units.map((u: any) => [u.id, u.character, u.state])).toEqual([[publicId(ids.c), 'ム', 'checked'], [publicId(ids.a), '一', 'pending'],
      [publicId(ids.b), '二', 'flagged']]);
    expect(view.units[2]).toMatchObject({ issue: 'crop', box: { x: 40, y: 10, w: 20, h: 30 }, line_id: `${book}:4:L1`, code_point: null });
    expect(view).toMatchObject({ id: pageKey, document_id: book, document_title: 'Book', seq: 4, previous: `${book}:3`, next: null, drawable: false,
      image_url: 'https://x.org/iiif/p/full/!3000,3000/0/default.jpg' });
    expect(view.crops).toBeUndefined();
  });
  it('answers 404 for a page it does not hold, or one with no page record', async () => {
    const { get } = setup();
    expect((await get('/atlas/pages/nope')).status).toBe(404);
    expect((await get('/atlas/pages/corpus:codh:x:0')).status).toBe(404);
  });
});

describe('a crop\'s model votes', () => {
  it('are answered for the pixels the crop holds now, with their engines named', async () => {
    const { get, ids } = setup();
    const pub = publicId(ids.a), q = `?revision=0&image_sha256=${hash}`;
    expect(await (await get(`/atlas/characters/${pub}/suggestions${q}`)).json()).toEqual({ status: 'ready',
      engines: [{ name: 'NDLkotenOCR', sha256: 'fd75' }], verified: false, candidates: [{ text: '一', score: 0.9, engine: 'NDLkotenOCR' }], votes: [] });
    expect(await (await get(`/atlas/characters/${pub}/suggestions/context${q}`)).json()).toEqual({ status: 'ready', basis: 'transcription',
      line_id: 'l', anchor: 1, source_text: '一', candidates: [{ text: '一', engine: 'Nearby transcription', offset: 0, before: '', after: '二' }] });
    expect((await get(`/atlas/characters/${pub}/suggestions?revision=1&image_sha256=${hash}`)).status).toBe(409);
    // A marked crop is asked for at its revision before the mark, or after it.
    for (const revision of [1, 2]) expect((await get(`/atlas/characters/${publicId(ids.d)}/suggestions?revision=${revision}&image_sha256=${hash}`)).status).toBe(200);
    expect((await get(`/atlas/characters/${publicId(ids.d)}/suggestions?revision=0&image_sha256=${hash}`)).status).toBe(409);
    expect(await (await get(`/atlas/characters/${publicId(ids.b)}/suggestions?revision=2&image_sha256=${hash}`)).json())
      .toEqual({ status: 'unavailable', candidates: [] });
  });
  it('are asked for by the pixels a crop or a corpus glyph holds', () => {
    expect(samePixels(new URLSearchParams({ image_sha256: hash }), 'local', { image_sha256: hash })).toBe(true);
    expect(samePixels(new URLSearchParams({ image: '/i.webp' }), 'local', { image_sha256: null, image: '/i.webp' })).toBe(true);
    expect(samePixels(new URLSearchParams({ source_revision: 'r1' }), 'corpus', { source_revision: 'r2' })).toBe(false);
  });
});

describe('a visual group sample\'s image', () => {
  it('redirects to the crop\'s image', async () => {
    const { get, ids } = setup();
    const response = await get(`/layers/visual-groups/samples/${publicId(ids.glyph)}/image`);
    expect([response.status, response.headers.get('location')]).toEqual([302, 'https://glyphatlas.org/atlas/media/glyph.webp']);
  });
});

describe('the crop routes\' queries', () => {
  // Only json_each and the recursive walk's own steps may be scanned; every table is read by a key.
  const plan = (db: Database, sql: string, ...args: unknown[]) => {
    const q = db.query(`EXPLAIN QUERY PLAN ${sql}`);
    const rows = (q.all(...(args as any[])) as { detail: string }[]).map(r => r.detail); q.finalize();
    return rows.filter(d => /TEMP B-TREE FOR (?!ORDER BY.*events)|AUTOMATIC/.test(d) || (/^SCAN /.test(d) && !/^SCAN (j|json_each|CONSTANT ROW|ahead|behind|a|b)\b/.test(d)));
  };
  it('read keys only', () => {
    const { db, runs } = setup();
    expect(plan(db, recordQuery(), 1)).toEqual([]);
    expect(plan(db, listedCropsQuery(), '[1]')).toEqual([]);
    expect(plan(db, cropSlotsQuery(), '[1]', 'has_form')).toEqual([]);
    expect(plan(db, formNamesQuery(), '[]')).toEqual([]);
    expect(plan(db, wordNamesQuery(), '[]')).toEqual([]);
    expect(plan(db, wordMethodsQuery(), '[]')).toEqual([]);
    expect(plan(db, bookDatesQuery(), 'b')).toEqual([]);
    // A book's few date claims are sorted by predicate after the subject's range is read.
    expect(plan(db, dateClaimsQuery(), 'b')).toEqual(['USE TEMP B-TREE FOR LAST TERM OF ORDER BY']);
    expect(plan(runs, lineQuery(), 1)).toEqual([]);
    const line = (runs.query(`EXPLAIN QUERY PLAN ${lineQuery()}`).all(1) as { detail: string }[]).map(r => r.detail).join('\n');
    expect(line).toContain('run_first');
    expect(line).toContain('run_second');
  });
});
