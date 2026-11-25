import { cropSlotsQuery } from '../cropPanels';
import { afterEach, beforeEach, describe, expect, it, setSystemTime } from 'bun:test';
import { bundle, bundleKey, cropId, cropOf, publicId } from '../atlas3';
import { charsQuery, tallyDeltas, tallyDeltasQuery } from '../atlas3Answers';
import { corpusFormsQuery, datingQuery, marksQuery, reviewedQuery } from '../atlas3Listing';
import { REVIEWED_IN_INSPECTOR, bookDatesQuery, booksQuery, cursorOf, cursorText, dateStats, doubtedQuery, flaggedQuery, byCodePoint, gallery, graphemeCounts, groupStats, page, type Stage,
  catalogue, familyQuery, graphemeQuery, health, keptFor, membersQuery, othersSkipsQuery, ownSkipsQuery, publishedAt, ranges, sideQuery, siteValue, type Filter, bookGlyphsQuery, BOOK_ROWS } from './browse';
import { Timing } from '../timing';
import worker from '../index';
import { ctx, databases, every, get, gz, setup } from './browse.fixture.test';

// The Workers cache, as far as these routes use it.
const edge = new Map<string, Response>();
beforeEach(() => {
  edge.clear();
  (globalThis as any).caches = { default: {
    match: async (request: Request) => edge.get(request.url)?.clone(),
    put: async (request: Request, response: Response) => { edge.set(request.url, response.clone()); },
  } };
});
afterEach(() => { setSystemTime(); for (const db of databases.splice(0)) db.close(); });

describe('site values', () => {
  it('answer the publication time, the collection status and the corpus summary', async () => {
    const { env, objects, pub } = await setup();
    const now = 2e13 + Math.random() * 1e9;
    expect(await health(env, now)).toEqual({ ok: true, published_at: publishedAt(pub) });
    expect(publishedAt('20261019T130411Z')).toBe('2026-10-19T13:04:11+00:00');
    expect(await siteValue(env, 'collection')).toEqual({ crops: 18 });
    expect(await siteValue(env, 'corpus_index')).toEqual({ sources: 2 });
    expect(await siteValue(env, 'variant_sources')).toBeNull();
    objects.delete('v3/pointer.json');
    // An isolate holds the answer for five seconds.
    expect((await health(env, now + 4000)).published_at).toBe(publishedAt(pub));
    expect(await health(env, now + 6000)).toEqual({ ok: true, published_at: null });
  });
});

describe('the listing\'s timing', () => {
  it('names each step a listing waits on', async () => {
    const { env } = await setup();
    const timing = new Timing();
    await catalogue(env, ctx, new URL('https://glyphatlas.org/atlas?seed=5&limit=2'), null, timing);
    const steps = timing.header().split(', ').map(step => step.split(';')[0]);
    // Steps that run side by side end in either order. A read again (`codes-again`, `productions-again`)
    // is named only when the isolate met a code it does not hold, which depends on what ran before.
    expect(steps.filter(step => !step.endsWith('-again')).sort()).toEqual(['bundles', 'bundles-from', 'chars', 'codes', 'counts', 'crops', 'deltas',
      'deltas-read', 'entries', 'facets', 'facets-answer', 'items', 'marks', 'page', 'pointer', 'productions', 'records', 'reviews', 'rows', 'stamps', 'total']);
    expect(timing.header()).toMatch(/bundles-from;desc="kept \d+ held \d+ edge \d+ r2 \d+"/);
  });
});

describe('a kept listing', () => {
  it('answers a browse listing asked for again from the edge, and a round and a writer afresh', async () => {
    // Within one minute, so the kept answers stand throughout.
    setSystemTime(new Date('2026-10-22T12:00:20Z'));
    const { env: writer } = await setup();
    // A visitor's isolate lags the catalogue; a writer's requests come with the lag at 0.
    const env = { ...writer, CATALOGUE_LAG: '60' } as Env;
    const steps = async (path: string, as = env) => {
      const timing = new Timing(), body = await catalogue(as, ctx, new URL(`https://glyphatlas.org${path}`), null, timing);
      return { body, steps: timing.header().split(', ').map(step => step.split(';')[0]) };
    };
    const first = await steps('/atlas?seed=5&limit=2'), again = await steps('/atlas?limit=2&seed=5');
    expect(first.steps).not.toContain('listing-hit');
    expect(again.steps).toEqual(['stamps', 'listing-hit', 'total']);
    expect(again.body).toEqual(first.body);
    expect((await steps('/atlas?seed=6&limit=2')).steps).not.toContain('listing-hit');
    // The Flagged view, asked for twice, is read twice.
    await steps('/atlas?seed=5&limit=2&state=flagged');
    expect((await steps('/atlas?seed=5&limit=2&state=flagged')).steps).not.toContain('listing-hit');
    expect((await steps('/atlas?seed=5&limit=2', writer)).steps).not.toContain('listing-hit');
  });
  it('keeps answers for a minute of a publication, whatever is reviewed in it', () => {
    const env = { CATALOGUE_LAG: '60' } as unknown as Env, minute = 60_000;
    const stamps = { gen: 3, import: 7, event: 10, submission: 4, undos: 0, ledger: null, decision: null };
    const at = (now: number, s = stamps, pub = 'p1') => keptFor(env, s, pub, now);
    expect(at(5 * minute + 1)).toBe(at(6 * minute - 1));
    expect(at(5 * minute + 1, { ...stamps, event: 11 })).toBe(at(5 * minute + 1));
    expect(at(6 * minute)).not.toBe(at(6 * minute - 1));
    expect(at(5 * minute, { ...stamps, gen: 4 })).not.toBe(at(5 * minute));
    expect(at(5 * minute, stamps, 'p2')).not.toBe(at(5 * minute));
    expect(keptFor({ CATALOGUE_LAG: '0' } as unknown as Env, stamps, 'p1')).toBeNull();
  });
});

describe('the statistics of the dates', () => {
  it('count each book once per list, by its witness year and its composed date', async () => {
    const { env } = await setup();
    const stats = await dateStats(env, ctx, new URL('https://glyphatlas.org/atlas/dates/stats'));
    // hk:a: 12 crops dated 1712, composed; hk:b: 6 undated; codh:c: 10 corpus glyphs dated 1850.
    expect(stats.total).toEqual({ crops: 28, works: 3 });
    expect(stats.dated).toEqual({ crops: 22, works: 2 });
    expect(stats.composed).toEqual({ crops: 12, works: 1 });
    expect(stats.hundreds).toEqual([[1700, 12, 1], [1800, 10, 1]]);
    expect(stats.decades).toEqual([[1710, 12, 1], [1850, 10, 1]]);
    expect(stats.kinds).toEqual([['printed', 12, 1], ['copied', 10, 1]]);
  });
});

describe('the statistics by script group', () => {
  it('count collected crops and corpus glyphs together by script and review state, updating with tally deltas', async () => {
    const { env, db, objects, a, c, crop, pub } = await setup();
    objects.set(`v3/pub/${pub}/unread.json.gz`, gz({ pub, categories: { '-1': [[1, 0, 0, 1, null, null, 1]] } }));
    const initial = await groupStats(env, ctx, new URL('https://glyphatlas.org/atlas/groups/stats'));
    const byGroup = (res: any) => Object.fromEntries(res.groups.map((g: any) => [g.group, g]));
    const first = byGroup(initial);
    // Collected (18): 10 kana (1 checked, 1 flagged, 1 hard, 1 seen, 6 pending; 2 withheld), 7 kanji (1 checked, 1 flagged, 5 pending; 1 withheld), 1 other (pending).
    // Corpus (10): 7 kana (1 checked, 6 pending), 1 kanji (pending), 1 hangul (pending), 1 unread (other, pending).
    expect(first.all).toMatchObject({ total: 28, reviewed: 6, checked: 3, need_fixing: 3, flagged: 2, hard: 1, seen: 1, pending: 21, withheld: 3 });
    expect(first.all.collected).toMatchObject({ total: 18, reviewed: 5, checked: 2, need_fixing: 3, flagged: 2, hard: 1, seen: 1, pending: 12, withheld: 3 });
    expect(first.all.corpus).toMatchObject({ total: 10, reviewed: 1, checked: 1, need_fixing: 0, pending: 9, withheld: 0 });
    expect(first.kana).toMatchObject({ total: 17, reviewed: 4, checked: 2, need_fixing: 2, flagged: 1, hard: 1, seen: 1, pending: 12, withheld: 2 });
    expect(first.kanji).toMatchObject({ total: 8, reviewed: 2, checked: 1, need_fixing: 1, flagged: 1, hard: 0, pending: 6, withheld: 1 });
    expect(first.hangul).toMatchObject({ total: 1, reviewed: 0, checked: 0, need_fixing: 0, pending: 1 });
    expect(first.gugyeol).toMatchObject({ total: 0, reviewed: 0, checked: 0, need_fixing: 0, pending: 0 });
    expect(first.tangut).toMatchObject({ total: 0, reviewed: 0, checked: 0, need_fixing: 0, pending: 0 });
    expect(first.other).toMatchObject({ total: 2, reviewed: 0, checked: 0, need_fixing: 0, pending: 2 });

    // A review that relabels a kanji crop to Gugyeol (not in codes) and names the unread corpus glyph as Tangut updates counts at once.
    db.exec(`INSERT INTO graphemes(id,text,head) VALUES(7,'',NULL),(8,'𗀀',NULL);
      INSERT INTO tally VALUES(1,0,4,-1,1,2,0,0,1,-1,-1),(1,0,7,-1,1,2,0,1,1,-1,1);
      INSERT INTO tally VALUES(1,1,-1,-1,3,2,0,0,1,-1,-1),(1,1,8,-1,3,2,0,1,1,-1,1);
      INSERT INTO events(crop,revision,cut,gen,actor,submission,at,kind) VALUES(${crop(a, 5)},1,0,1,1,1,1,0);`);
    const updated = byGroup(await groupStats(env, ctx, new URL('https://glyphatlas.org/atlas/groups/stats')));
    expect(updated.gugyeol).toMatchObject({ total: 1, reviewed: 1, checked: 1, need_fixing: 0, pending: 0 });
    expect(updated.tangut).toMatchObject({ total: 1, reviewed: 1, checked: 1, need_fixing: 0, pending: 0 });
    expect(updated.kanji).toMatchObject({ total: 7, reviewed: 2, checked: 1, need_fixing: 1, pending: 5 });
    expect(updated.other).toMatchObject({ total: 1, reviewed: 0, pending: 1 });
    expect(updated.all).toMatchObject({ total: 28, reviewed: 8, checked: 5, need_fixing: 3, pending: 19 });
    // Cached under the catalogue version; an identical read returns the same result from the Workers cache.
    expect((await groupStats(env, ctx, new URL('https://glyphatlas.org/atlas/groups/stats'))).groups).toEqual(Object.values(updated));
    void c;
  });
});

const counted = async (env: Env): Promise<any[]> => JSON.parse((await graphemeCounts(env, ctx, new URL('https://glyphatlas.org/atlas/graphemes/counts'))).text).items
  .sort((a: any, b: any) => byCodePoint(a[0], b[0]) || byCodePoint(a[1], b[1]));
describe('crops by character and grapheme', () => {
  it('count every crop of each, withheld ones and those with no character among them', async () => {
    // Two corpus glyphs and a withheld crop of は's family name it with no character.
    const { env } = await setup((db, c) => db.exec(`INSERT INTO crops(id,kind,book,char,family,production,style,state,quiz,shuffle,cut,revision,flags)
      VALUES(${cropId(c, 20)},1,3,NULL,1,2,0,0,1,5,0,0,0),(${cropId(c, 21)},1,3,NULL,3,2,0,0,1,6,0,0,0),(${cropId(c, 22)},0,3,NULL,2,2,0,0,0,7,0,0,0)`));
    expect(await counted(env)).toEqual([['', 'U+306F', 1, 2], ['は', 'U+306F', 6, 4], ['ハ', 'U+306F', 3, 0], ['処', 'U+51E6', 1, 0], ['所', 'U+6240', 6, 1],
      ['한', 'U+D55C', 0, 1], ['𛂞', 'U+306F', 1, 3]]);
  });
  it('follow the reviews since the publication, also those that name a crop', async () => {
    const { env, db } = await setup((db, c) => db.exec(`INSERT INTO crops(id,kind,book,char,family,production,style,state,quiz,shuffle,cut,revision,flags)
      VALUES(${cropId(c, 20)},1,3,NULL,1,2,0,0,1,5,0,0,0)`));
    db.exec(`INSERT INTO tally(gen,kind,char,family,book,production,state,style,quiz,decade,n) VALUES(1,1,-1,1,3,2,0,0,1,-1,-1),(1,1,2,1,3,2,1,0,1,-1,1);
      INSERT INTO events(crop,revision,cut,gen,actor,submission,at,kind) VALUES(1,1,0,1,1,1,1,0);`);
    const rows = (await counted(env)).filter((row: any) => row[1] === 'U+306F');
    expect(rows).toEqual([['は', 'U+306F', 6, 4], ['ハ', 'U+306F', 3, 1], ['𛂞', 'U+306F', 1, 3]]);
  });
  it('count no crop with no character where the publication counts none, but for one a claim files', async () => {
    const { env, db, objects, pub } = await setup((db, c) => db.exec(`INSERT INTO crops(id,kind,book,char,family,production,style,state,quiz,shuffle,cut,revision,flags)
      VALUES(${cropId(c, 20)},1,3,NULL,1,2,0,0,1,5,0,0,0),(${cropId(c, 21)},1,3,NULL,3,2,0,0,1,6,0,0,0)`));
    const corpus = JSON.parse(new TextDecoder().decode(Bun.gunzipSync(objects.get(`v3/pub/${pub}/corpus.json.gz`)!)));
    expect(corpus.filed).toEqual([[1, 1, 1], [3, 1, 1]]);
    delete corpus.filed;
    objects.set(`v3/pub/${pub}/corpus.json.gz`, gz(corpus));
    db.exec(`INSERT INTO tally(gen,kind,char,family,book,production,state,style,quiz,decade,n) VALUES(1,1,-1,1,3,2,0,0,1,-1,-1),(1,1,2,1,3,2,1,0,1,-1,1);
      INSERT INTO current_claims(subject,predicate,scope,slot,crop,cut,status,object,members,supporting,claims,resolver,at) VALUES('x','has_grapheme','','',(SELECT max(id) FROM crops WHERE kind=1),0,'asserted','U+51E6','[]','[]','[]','r','t');
      INSERT INTO ledger_submissions VALUES('s','u','{}','{}','t');`);
    const rows = await counted(env);
    // The one a claim files under 処 is that grapheme's, whose gallery lists it.
    expect(rows.filter(row => !row[0])).toEqual([['', 'U+51E6', 0, 1, 1]]);
    expect(rows.every(row => row[2] >= 0 && row[3] >= 0)).toBe(true);
    expect(rows.find(row => row[0] === 'ハ')).toEqual(['ハ', 'U+306F', 3, 1]);
  });
  it('state no negative count', async () => {
    const { env, db } = await setup();
    db.exec(`INSERT INTO tally(gen,kind,char,family,book,production,state,style,quiz,decade,n) VALUES(1,0,5,-1,1,2,0,0,1,-1,-1),(1,1,-1,1,3,2,0,0,1,-1,-1);
      INSERT INTO current_claims(subject,predicate,scope,slot,crop,cut,status,object,members,supporting,claims,resolver,at)
        SELECT 'x','has_grapheme','','',id,0,'asserted','U+51E6','[]','[]','[]','r','t' FROM crops WHERE kind=0 AND char IS NULL LIMIT 1;`);
    const rows = await counted(env);
    expect(rows.every(row => row[2] >= 0 && row[3] >= 0 && row[2] + row[3] > 0)).toBe(true);
    expect(rows.find(row => row[0] === '한')).toEqual(['한', 'U+D55C', 0, 1]);
  });
  it('are asked for each time, and answered as unchanged while the counts are the same', async () => {
    const { env, db } = await setup();
    const ask = (headers = {}) => worker.fetch(new Request('https://glyphatlas.org/atlas/graphemes/counts', { headers }), env, { waitUntil: () => {}, passThroughOnException() {} } as unknown as ExecutionContext);
    const first = await ask(), tag = first.headers.get('etag')!, rows = await first.text();
    expect([first.status, first.headers.get('cache-control'), tag.startsWith('W/"')]).toEqual([200, 'private, no-cache', true]);
    // Another minute, or a save that moved none of them, leaves the copy standing; one that did does not.
    edge.clear();
    for (const _ of [0, 1]) {
      const again = await ask({ 'if-none-match': tag });
      expect([again.status, await again.text(), again.headers.get('etag')]).toEqual([304, '', tag]);
      db.exec(`INSERT OR IGNORE INTO ledger_submissions VALUES('tag','u','{}','{}','t')`);
    }
    db.exec(`INSERT INTO tally(gen,kind,char,family,book,production,state,style,quiz,decade,n) VALUES(1,1,5,-1,3,2,0,0,1,-1,1); INSERT INTO events(id,crop,revision,cut,gen,actor,submission,at,kind) VALUES(4343,1,1,0,1,1,1,1,0)`);
    const moved = await ask({ 'if-none-match': tag });
    expect([moved.status, moved.headers.get('etag') === tag, (await moved.text()) === rows]).toEqual([200, false, false]);
  });
  it('are kept a minute of a publication, and answered afresh to a browser that wrote', async () => {
    const { env: writer, db } = await setup(), env = { ...writer, CATALOGUE_LAG: '60' } as Env;
    const of = async (as: Env) => (await counted(as)).find(row => row[0] === '한')[3];
    expect([await of(env), await of(writer)]).toEqual([1, 1]);
    db.exec(`INSERT INTO tally(gen,kind,char,family,book,production,state,style,quiz,decade,n) VALUES(1,1,5,-1,3,2,0,0,1,-1,1); INSERT INTO events(id,crop,revision,cut,gen,actor,submission,at,kind) VALUES(4242,1,1,0,1,1,1,1,0);`);
    expect([await of(env), await of(writer)]).toEqual([1, 2]);
    expect([...edge.keys()].filter(url => url.includes('/graphemes/counts')).length).toBe(3);
  });
});

describe('the homepage gallery', () => {
  function sampled(objects: Map<string, Uint8Array>, c: number, records: Uint8Array[], pub: string, shards = 4) {
    const range = 2 ** 25, entries = records.map((r, slot) => [cropId(c, slot), Math.floor(slot * range / records.length) + 7,
      slot === 7 ? 'hangul' : slot === 9 ? 'other' : slot === 8 ? 'kanji' : 'kana', JSON.parse(new TextDecoder().decode(r))]);
    for (let k = 0; k < shards; k++) objects.set(`v3/pub/${pub}/gallery/sample/${k}.json.gz`,
      gz({ pub, entries: entries.filter(e => Math.floor((e[1] as number) * shards / range) === k) }));
    objects.set(`v3/pub/${pub}/gallery/hangul/0.json.gz`, gz({ pub, entries: entries.filter(e => e[2] === 'hangul') }));
    objects.set(`v3/pub/${pub}/gallery/gugyeol/0.json.gz`, gz({ pub, entries: [] }));
    objects.set(`v3/pub/${pub}/gallery/index.json.gz`, gz({ pub, pools: {
      sample: { range, shards, glyphs: 10, categories: { hangul: 1, kana: 7, kanji: 1, other: 1 } },
      hangul: { range: 2 ** 31, shards: 1, glyphs: 1, categories: { hangul: 1 } }, gugyeol: { range: 2 ** 31, shards: 1, glyphs: 0, categories: {} },
      tangut: { range: 2 ** 31, shards: 1, glyphs: 0, categories: {} } } }));
    return entries;
  }
  it('deal from the seed’s point, wrapping round, with each glyph’s row and dating', async () => {
    const { env, db, objects, c, records, pub } = await setup();
    const entries = sampled(objects, c, records, pub);
    const page = await gallery(env, ctx, new URLSearchParams({ seed: String(entries[6][1] as number), limit: '5' }));
    expect(page.items.map((i: any) => i.source_id)).toEqual(['codh:6', 'codh:7', 'codh:8', 'codh:9', 'codh:0']);
    expect(page.available).toBe(5);
    const first = page.items[0];
    expect(first.id).toBe(publicId(cropId(c, 6)));
    expect(first.state).toBe('checked');
    expect(first.dating.witness.start).toBe(1850);
    expect(first.text).toBeUndefined();
    // The glyph no one has read keeps its record's label, none.
    expect(page.items[3].label).toBe('');
    // A glyph a round showed is pending, as its record says.
    db.exec(`UPDATE crops SET state=3 WHERE id=${cropId(c, 7)}`);
    expect((await gallery(env, ctx, new URLSearchParams({ seed: String(entries[7][1] as number), limit: '1' }))).items[0].state).toBe('pending');
  });
  it('answer a sample asked for again from the edge', async () => {
    // Within one minute, so the kept answers stand throughout.
    setSystemTime(new Date('2026-10-22T12:00:20Z'));
    const { env: writer, objects, c, records, pub } = await setup();
    const env = { ...writer, CATALOGUE_LAG: '60' } as Env;
    const entries = sampled(objects, c, records, pub);
    const ask = async () => {
      const timing = new Timing(), body = await gallery(env, ctx, new URLSearchParams({ seed: String(entries[6][1] as number), limit: '5' }), timing);
      return { body, steps: timing.header().split(', ').map(step => step.split(';')[0]) };
    };
    const first = await ask(), again = await ask();
    expect(first.steps).not.toContain('sample-hit');
    expect(again.steps).toEqual(['stamps', 'sample-hit', 'total']);
    expect(again.body).toEqual(first.body);
  });
  it('deal a script group from its own glyphs, and leave out one a review moved to another script', async () => {
    const { env, db, objects, c, records, pub } = await setup();
    sampled(objects, c, records, pub);
    expect((await gallery(env, ctx, new URLSearchParams({ group: 'hangul' }))).items.map((i: any) => i.source_id)).toEqual(['codh:7']);
    expect((await gallery(env, ctx, new URLSearchParams({ group: 'gugyeol' }))).items).toEqual([]);
    // The publication's index names no pool for small seal.
    expect((await gallery(env, ctx, new URLSearchParams({ group: 'seal' }))).items).toEqual([]);
    expect((await gallery(env, ctx, new URLSearchParams({ group: 'kanji', limit: '3' }))).items.map((i: any) => i.source_id)).toEqual(['codh:8']);
    db.exec(`UPDATE crops SET char=1 WHERE id=${cropId(c, 8)}`);
    expect((await gallery(env, ctx, new URLSearchParams({ group: 'kanji' }))).items).toEqual([]);
    await expect(gallery(env, ctx, new URLSearchParams({ group: 'latin' }))).rejects.toThrow('Invalid group.');
  });
});

describe('the browse page', () => {
  it('count every collected crop by character, book and state', async () => {
    const { env } = await setup();
    const body = await get(env, '/atlas?limit=0');
    // Withheld crops are left out: 15 of the 18.
    expect(body.counts).toEqual({ pending: 10, seen: 1, flagged: 1, checked: 2, hard: 1, skipped: 0 });
    expect(body.available).toBe(15);
    expect(body.categories.slice(0, 3).map((c: any) => [c.label, c.grapheme, c.total])).toEqual([['は', 'U+306F', 5], ['所', 'U+6240', 5], ['ハ', 'U+306F', 2]]);
    expect(body.categories.slice(3).map((c: any) => [c.label, c.grapheme, c.total]).sort()).toEqual([['処', 'U+51E6', 1], ['𛂞', 'U+306F', 1]]);
    expect(body.categories[0]).toEqual({ label: 'は', grapheme: 'U+306F', total: 5, pending: 4, seen: 0, checked: 1, flagged: 0, hard: 0, skipped: 0 });
    // Every book is listed, the one of corpus glyphs by how many it holds, which count toward no state.
    expect(body.documents.map((d: any) => [d.id, d.title, d.total, d.pending])).toEqual([['hk:a', 'Book A', 10, 6], ['codh:c', 'Corpus C', 10, 0], ['hk:b', 'Book B', 5, 4]]);
    expect(body.withheld_count).toBe(3);
    expect(body.review_epoch).toBe(3);
    // Asked for its rows alone, a page keeps the counts by state and leaves out those by character and work.
    const light = await get(env, '/atlas?seed=5&limit=2&facets=counts');
    expect([light.counts, light.categories, light.documents, light.items.length]).toEqual([body.counts, [], [], 2]);
    const shown = await get(env, '/atlas?limit=0&withheld=show');
    expect(shown.documents.map((d: any) => d.total)).toEqual([12, 10, 6]);
    expect(shown.withheld_count).toBeNull();
  });

  it('count a crop a grapheme claim reads as another grapheme under that grapheme, as its gallery lists it', async () => {
    const { db, env, a, c, crop } = await setup();
    // A pending 所 of book A and the corpus glyph 所 of movable type, each read as 処.
    const claim = db.prepare(`INSERT INTO current_claims(subject,predicate,scope,slot,crop,cut,status,object,members,supporting,claims,resolver,at)
      VALUES(?,'has_grapheme','','',?,0,'asserted','U+51E6','[]','[]','[]','r','2026-10-23')`);
    claim.run('hk:a5', crop(a, 5)); claim.run('codh:8', crop(c, 8));
    const body = await get(env, '/atlas?limit=0');
    const of = (label: string) => body.categories.filter((x: any) => x.label === label).map((x: any) => [x.grapheme, x.total, x.pending]).sort();
    expect(of('所')).toEqual([['U+51E6', 1, 1], ['U+6240', 4, 2]]);
    expect(body.counts).toEqual({ pending: 10, seen: 1, flagged: 1, checked: 2, hard: 1, skipped: 0 });
    // 所 keeps five of its six crops and no glyph; the claimed two are 処's, in rows marked as moved.
    expect((await counted(env)).filter((row: any) => ['U+51E6', 'U+6240'].includes(row[1]))).toEqual([['処', 'U+51E6', 1, 0], ['所', 'U+51E6', 1, 1, 1], ['所', 'U+6240', 5, 0, 1]]);
    // Each family's listing agrees with its count: 処 lists the claimed 所, and 所 leaves it out.
    const ids = (body: any) => body.items.map((item: any) => item.id).sort();
    const sho = await get(env, '/atlas?grapheme=U%2B51E6&limit=60'), so = await get(env, '/atlas?grapheme=U%2B6240&limit=60');
    expect([sho.total, sho.items.length]).toEqual([2, 2]);
    expect(ids(sho)).toContain(publicId(crop(a, 5)));
    expect([so.total, so.items.length]).toEqual([4, 4]);
    expect(ids(so)).not.toContain(publicId(crop(a, 5)));
    const flagged = await get(env, '/atlas?grapheme=U%2B6240&state=attention&limit=60');
    expect(flagged.items.map((item: any) => item.id)).toEqual([publicId(crop(a, 6))]);
    // A crop with no character is counted under no character, and listed under no family.
    claim.run('hk:a8', crop(a, 8));
    const unnamed = await get(env, '/atlas?grapheme=U%2B51E6&limit=60&v=2');
    expect(ids(unnamed)).not.toContain(publicId(crop(a, 8)));
    // A character narrows the claimed crops as it narrows the rest.
    const narrowed = await get(env, '/atlas?grapheme=U%2B51E6&character=%E6%89%80&limit=60');
    expect([narrowed.total, narrowed.items.map((item: any) => item.id)]).toEqual([1, [publicId(crop(a, 5))]]);
    // A round deals by character, and counts so.
    const round = await get(env, '/atlas?purpose=review&limit=0', 'me');
    expect(round.categories.filter((x: any) => x.label === '所').map((x: any) => x.grapheme)).toEqual(['U+6240']);
  });

  it('lists a grapheme under its key and under its members’, naming it', async () => {
    const { env } = await setup(db => db.exec(`INSERT INTO graphemes(id,text,head) VALUES(7,'𛂟',1)`));
    const of = async (key: string) => { const body = await get(env, `/atlas?grapheme=${key}&seed=3&limit=40`); return [body.head, body.total, body.items.map((i: any) => i.id)] };
    const whole = await of('U%2B306F');
    expect(whole[0]).toBe('U+306F');
    // ハ's key, and that of a member the collection holds no crop of.
    expect(await of('U%2B30CF')).toEqual(whole);
    expect(await of('U%2B1B09F')).toEqual(whole);
    expect(await of('U%2B9999')).toEqual(['U+9999', 0, []]);
    // A book's listing under it too, and one narrowed to a character of it.
    const ids = async (key: string, more: string) => (await get(env, `/atlas?grapheme=${key}&seed=3&limit=40&${more}`)).items.map((i: any) => i.id);
    for (const more of ['document=hk:a', 'character=ハ']) expect(await ids('U%2B30CF', more)).toEqual(await ids('U%2B306F', more));
    expect((await ids('U%2B30CF', 'document=hk:a')).length).toBeGreaterThan(0);
  });

  it('lists a family as a load left it, before the publication that counts it so', async () => {
    const { env, db } = await setup();
    const of = async (key: string, more = '') => { const body = await get(env, `/atlas?grapheme=${key}&seed=3&limit=40${more}`);
      return [body.head, body.total, body.items.map((i: any) => i.id).sort(), [...new Set(body.items.map((i: any) => i.label))].sort()] as const; };
    const [ha, sho] = [await of('U%2B306F'), await of('U%2B6240')];
    const katakana = (await get(env, '/atlas?character=ハ&seed=3&limit=40')).items.map((i: any) => i.id);
    expect(katakana.length).toBeGreaterThan(0);
    // The load files ハ under 所; the counts file it under は until they are published again.
    db.exec(`UPDATE graphemes SET head=(SELECT id FROM graphemes WHERE text='所') WHERE text='ハ'; INSERT INTO imports(stamp, at) VALUES('reference-5-x.rows', 1)`);
    const now = await of('U%2B6240');
    expect(now).toEqual(['U+6240', sho[1] + katakana.length, [...sho[2], ...katakana].sort(), [...sho[3], 'ハ'].sort()]);
    expect(await of('U%2B30CF')).toEqual(now);
    expect(await of('U%2B306F')).toEqual(['U+306F', ha[1] - katakana.length, ha[2].filter((id: string) => !katakana.includes(id)), ha[3].filter((label: any) => label !== 'ハ')]);
    expect((await every(env, '/atlas?seed=7&grapheme=U%2B6240', 2)).seen.sort()).toEqual(now[2]);
    // The grapheme browser's counts are the publication's.
    expect((await get(env, '/atlas?limit=0')).categories.find((c: any) => c.label === 'ハ').grapheme).toBe('U+306F');
  });

  it('goes on under the key of a member where a page of its own listing left off', async () => {
    const { env } = await setup();
    const { seen } = await every(env, '/atlas?seed=7&grapheme=U%2B306F', 3);
    expect((await every(env, '/atlas?seed=7&grapheme=U%2B30CF', 3)).seen).toEqual(seen);
  });

  it('lists a small seal character alone under its key, whatever claims file its crops under', async () => {
    // ハ's id takes the character, which keeps its crops.
    const SEAL = '\u{3FA6A}', { env, db } = await setup(db => db.exec(`UPDATE graphemes SET text='${SEAL}' WHERE text='ハ'`));
    const of = async (key: string) => (await get(env, `/atlas?grapheme=${key}&seed=3&limit=40`));
    const whole = await of('U%2B306F'), member = await of('U%2B3FA6A');
    expect([member.head, [...new Set(member.items.map((i: any) => i.label))]]).toEqual(['U+3FA6A', [SEAL]]);
    expect([member.total, member.items.length]).toEqual([whole.items.filter((i: any) => i.label === SEAL).length, member.total]);
    // A crop claimed for 所 leaves は's listing, and is of the character still.
    const id = member.items.find((i: any) => i.origin !== 'corpus').id;
    db.prepare(`INSERT INTO current_claims(subject,predicate,scope,slot,crop,cut,status,object,members,supporting,claims,resolver,at)
      VALUES(?,'has_grapheme','','',?,0,'asserted','U+6240','[]','[]','[]','r','2026-11-24')`).run(id, cropOf(id));
    db.exec(`INSERT INTO ledger_submissions VALUES('s','u','{}','{}','t')`);
    const ids = async (key: string) => (await of(key)).items.map((i: any) => i.id);
    expect([(await ids('U%2B306F')).includes(id), (await ids('U%2B3FA6A')).includes(id)]).toEqual([false, true]);
  });

  it('page through every crop once from the seed’s point, the cursor ending where the index does', async () => {
    const { env } = await setup();
    for (const seed of [0, 1234567, 2147483647]) {
      const { seen, body } = await every(env, `/atlas?seed=${seed}`, 4);
      expect(seen.length).toBe(15);
      expect(new Set(seen).size).toBe(15);
      expect(body.total).toBe(15);
    }
    const { seen } = await every(env, '/atlas?seed=99&withheld=show', 5);
    expect(seen.length).toBe(18);
  });

  it('narrow by character, family, book, script and state, each counted', async () => {
    const { env, a, b, crop } = await setup();
    const ids = async (path: string) => (await every(env, path, 3)).seen.sort();
    const pub = (...list: number[]) => list.map(publicId).sort();
    expect(await ids('/atlas?seed=5&character=は')).toEqual(pub(crop(a, 0), crop(a, 1), crop(a, 2), crop(b, 0), crop(b, 4)));
    expect(await ids('/atlas?seed=5&q=U%2B306F')).toEqual(pub(crop(a, 0), crop(a, 1), crop(a, 2), crop(b, 0), crop(b, 4)));
    expect((await ids('/atlas?seed=5&grapheme=U%2B306F')).length).toBe(8);
    expect(await ids('/atlas?seed=5&document=hk:b')).toEqual(pub(crop(b, 0), crop(b, 1), crop(b, 2), crop(b, 4), crop(b, 5)));
    expect(await ids('/atlas?seed=5&group=kanji')).toEqual(pub(crop(a, 5), crop(a, 6), crop(a, 11), crop(b, 1), crop(b, 2), crop(b, 5)));
    expect(await ids('/atlas?seed=5&state=checked')).toEqual(pub(crop(a, 2), crop(b, 1)));
    expect(await ids('/atlas?seed=5&production=printed/type')).toEqual(pub(crop(a, 3), crop(a, 4), crop(b, 4), crop(b, 5)));
    expect(await ids('/atlas?seed=5&character=は&document=hk:a')).toEqual(pub(crop(a, 0), crop(a, 1), crop(a, 2)));
    const narrowed = await get(env, '/atlas?seed=5&group=kanji&limit=2');
    expect(narrowed.total).toBe(6);
    expect(narrowed.withheld_count).toBeNull();
    expect((await get(env, '/atlas?character=は&limit=1')).withheld_count).toBe(1);
    // A book of corpus glyphs is browsed like any other: its glyphs, less the one this site may not serve.
    const glyphs = await get(env, '/atlas?seed=5&document=codh:c&limit=20');
    expect([glyphs.total, glyphs.items.length, new Set(glyphs.items.map((item: any) => item.origin))]).toEqual([10, 9, new Set(['corpus'])]);
    const named = await get(env, '/atlas?seed=5&document=codh:c&character=は&limit=20');
    expect([named.total, named.items.length, new Set(named.items.map((item: any) => item.label))]).toEqual([4, 3, new Set(['は'])]);
    const family = await get(env, '/atlas?seed=5&document=codh:c&grapheme=U%2B306F&limit=20');
    expect([family.total, family.items.length]).toEqual([7, 6]);
    // Under a choice of material the books of corpus glyphs are not listed, their counts being by book alone.
    expect((await get(env, '/atlas?limit=0&production=printed/type')).documents.map((d: any) => d.id)).not.toContain('codh:c');
    expect((await get(env, '/atlas?seed=5&document=codh:c&state=checked&limit=20')).total).toBe(1);
    // A round narrowed to a book deals its collected crops alone.
    for (const seed of [5, 6]) expect((await get(env, `/atlas?purpose=review&seed=${seed}&document=codh:c&limit=5`)).items).toEqual([]);
    expect((await get(env, '/atlas?document=nowhere&limit=5')).items).toEqual([]);
    expect((await get(env, '/atlas?character=X&limit=5')).total).toBe(0);
  });

  it('show each crop with its form, dating, suspect mark and shape order', async () => {
    const { env, db, a, crop } = await setup();
    db.exec(`INSERT INTO suspects VALUES(${crop(a, 0)},0.9,'ほ','は','{"x":0,"y":0,"w":1,"h":1}'),(${crop(a, 1)},0.8,'ほ','あ',NULL);
      INSERT INTO shapes VALUES(${crop(a, 0)},4);
      INSERT INTO representations(id,scheme,value) VALUES('r:1','unicode','𛂞');
      INSERT INTO forms(id,anchor,created_by,created_at) VALUES('f:1','r:1','u1','');
      INSERT INTO current_claims(subject,predicate,scope,slot,crop,cut,status,object,members,supporting,claims,resolver,at) VALUES
        ('x','has_form','','',${crop(a, 0)},0,'single','f:1','[{"object":"f:1","confidence":null,"assertion":"c1"}]','["c1"]',
         '[{"asserted_by":"u1","standing":true,"members":[{"assertion":"c1"}]}]','r',''),
        ('y','has_form','','',${crop(a, 1)},1,'single','f:2','[{"object":"f:2","confidence":null,"assertion":"c2"}]','["c2"]','[]','r','');`);
    const items = (await get(env, '/atlas?character=は&limit=10')).items;
    const first = items.find((i: any) => i.id === publicId(crop(a, 0))), second = items.find((i: any) => i.id === publicId(crop(a, 1)));
    expect(first.suspect).toEqual({ p: 0.9, reads_as: 'ほ' });
    expect(first.shape_order).toBe(4);
    expect(first.form).toEqual({ status: 'single', values: [{ form: 'f:1', scheme: 'unicode', text: '𛂞', confidence: null }], supporting: ['c1'], by: ['u1'] });
    expect(first.dating.witness.label).toBe('1712');
    expect(first.dating.composed.label).toBe('1600-1650');
    // A mark on another label, and a claim on an earlier cut, no longer hold.
    expect(second.suspect).toBeNull();
    expect(second.form).toBeNull();
    expect(first.document).toBe('hk:a');
    expect(first.page_id).toBe('hk:a:1');
    // A redraw exposes the next cut immediately; only claims made on that cut remain visible.
    db.exec(`INSERT INTO reviews(crop,gen,at,state,x,y,w,h) VALUES(${crop(a, 0)},1,0,0,2,2,3,3);
      UPDATE current_claims SET cut=1 WHERE crop=${crop(a, 0)}`);
    const redrawn = (await get(env, '/atlas?character=は&limit=10')).items.find((i: any) => i.id === publicId(crop(a, 0)));
    expect(redrawn.form).toEqual(first.form);
  });

  it('hold a suspect mark against the crop as it shows now, relabelled or redrawn, as the old site did', async () => {
    const { env, db, a, crop } = await setup();
    // The old site's rule (index.ts before the cutover), held against the crop's record as it stood.
    const oldSuspectOf = (mark: any, item: any) => {
      if (!mark || mark.label !== item.label) return null;
      const x = mark.box, y = item.box ?? null;
      const same = x === null || y === null ? x === y : ['x', 'y', 'w', 'h'].every(k => Math.abs(Number(x[k]) - Number(y[k])) < 1e-6);
      return same ? { p: mark.p, reads_as: mark.reads_as } : null;
    };
    const cut = (slot: number) => ({ x: slot, y: 0, w: 1, h: 1 });
    const marks = [[0, 'は', cut(0)], [1, 'は', cut(1)], [2, 'は', { x: 9, y: 9, w: 3, h: 3 }], [3, 'ハ', cut(3)]] as const;
    for (const [slot, label, box] of marks) db.query('INSERT INTO suspects VALUES(?,0.7,?,?,?)').run(crop(a, slot), 'ほ', label, JSON.stringify(box));
    // a0 relabelled 処; a1 redrawn elsewhere; a2 redrawn onto the box the classifier was shown; a3 as cut.
    db.exec(`UPDATE crops SET char=6, revision=1 WHERE id=${crop(a, 0)};
      INSERT INTO reviews(crop,gen,at,char,state,x,y,w,h) VALUES(${crop(a, 1)},1,0,1,0,5,5,2,2),(${crop(a, 2)},1,0,1,1,9,9,3,3);`);
    const items = (await get(env, '/atlas?document=hk:a&withheld=show&limit=20')).items;
    const shown = (slot: number) => items.find((i: any) => i.id === publicId(crop(a, slot)));
    const was = { 0: { label: '処', box: cut(0) }, 1: { label: 'は', box: { x: 5, y: 5, w: 2, h: 2 } },
      2: { label: 'は', box: { x: 9, y: 9, w: 3, h: 3 } }, 3: { label: 'ハ', box: cut(3) } } as Record<number, any>;
    for (const [slot, label, box] of marks) expect(shown(slot).suspect).toEqual(oldSuspectOf({ p: 0.7, reads_as: 'ほ', label, box }, was[slot]));
    expect([0, 1, 2, 3].map(slot => shown(slot).suspect === null)).toEqual([true, true, false, false]);
    expect(shown(1)).toMatchObject({ box: { x: 5, y: 5, w: 2, h: 2 }, box_pending: true });
    expect(shown(3).box_pending).toBeUndefined();
  });

  it('show a flagged crop with the issue its last review gave it', async () => {
    const { env, db, a, b, crop } = await setup();
    // a6 flagged for its crop, then for its reading (an older name of the character issue); b3 merged.
    db.exec(`INSERT INTO events(crop,revision,cut,gen,actor,submission,at,kind,issue) VALUES(${crop(a, 6)},1,0,1,2,3,0,1,2),
      (${crop(a, 6)},2,0,1,2,3,0,1,6),(${crop(b, 3)},1,0,1,2,3,0,1,1),(${crop(a, 2)},1,0,1,2,3,0,0,NULL)`);
    const items = (await get(env, '/atlas?state=attention&limit=10')).items;
    const issue = (id: number) => items.find((i: any) => i.id === publicId(id)).issue;
    expect([issue(crop(a, 6)), issue(crop(b, 3)), issue(crop(a, 9))]).toEqual(['character', 'merged', null]);
    expect((await get(env, '/atlas?character=は&state=checked&limit=10')).items[0].issue).toBeNull();
    db.exec(`UPDATE events SET issue=NULL,kept_issue=3 WHERE crop=${crop(b, 3)}`);
    const kept = (await get(env, '/atlas?state=attention&limit=10')).items.find((i: any) => i.id === publicId(crop(b, 3)));
    expect(kept.issue).toBe('blank');
  });

  it('count a review at once, before the next publication', async () => {
    const { env, db, a, crop } = await setup();
    db.exec(`UPDATE crops SET state=1, revision=1 WHERE id=${crop(a, 0)};
      INSERT INTO tally VALUES(1,0,1,-1,1,2,0,0,1,-1,-1),(1,0,1,-1,1,2,0,1,1,-1,1);
      UPDATE crops SET char=6, revision=1 WHERE id=${crop(a, 8)};
      INSERT INTO tally VALUES(1,0,-1,-1,1,2,0,0,1,-1,-1),(1,0,6,-1,1,2,0,0,1,-1,1);`);
    const body = await get(env, '/atlas?limit=0');
    expect(body.counts.checked).toBe(3);
    expect(body.categories.find((c: any) => c.label === 'は')).toMatchObject({ total: 5, pending: 3, checked: 2 });
    expect(body.categories.find((c: any) => c.label === '処').total).toBe(2);
    expect((await get(env, '/atlas?character=処&limit=10')).total).toBe(2);
  });
});

describe('the reviews since the publication', () => {
  it('are read from D1 once for a key, and from the edge by an isolate that does not hold it', async () => {
    const { env, db, pub } = await setup();
    let reads = 0;
    const counted = { ...env, ATLAS: { ...env.ATLAS, prepare: (sql: string) => { if (sql === tallyDeltasQuery()) reads++; return env.ATLAS.prepare(sql); } } } as Env;
    db.exec('INSERT INTO tally VALUES(1,0,1,-1,1,2,0,1,1,-1,1)');
    const first = await tallyDeltas(counted, pub, 0, 'k1');
    expect(first.map(d => [d.char, d.state, d.n])).toEqual([[1, 1, 1]]);
    expect(await tallyDeltas(counted, pub, 0, 'k1')).toBe(first);
    expect(reads).toBe(1);
    // A save moves the stamps, and its key is read afresh.
    db.exec('INSERT INTO tally VALUES(1,0,6,-1,1,2,0,0,1,-1,1)');
    expect((await tallyDeltas(counted, pub, 0, 'k2')).map(d => d.char)).toEqual([1, 6]);
    expect(reads).toBe(2);
    // The isolate holds the last key alone; the first comes back from the edge as it was read.
    expect(await tallyDeltas(counted, pub, 0, 'k1')).toEqual(first);
    expect(reads).toBe(2);
    expect([...edge.keys()].filter(url => url.includes('/_deltas/'))).toEqual(['k1', 'k2'].map(k => `https://glyphatlas.org/_deltas/${encodeURIComponent(`${pub}|0|${k}`)}?s=2`));
  });
});

describe('the Flagged view', () => {
  it('list every crop flagged or hard to read, withheld too, those already looked at in the inspector last', async () => {
    const { env, db, a, b, crop } = await setup();
    // An inspector review of a1-6 counts; one from an undone submission, or from a round, does not.
    db.exec(`INSERT INTO events(crop,revision,cut,gen,actor,submission,at,kind) VALUES(${crop(a, 6)},1,0,1,2,3,0,1),
      (${crop(a, 9)},1,0,1,2,4,0,1),(${crop(b, 3)},1,0,1,2,2,0,1)`);
    const body = await get(env, '/atlas?state=attention&limit=10&seed=3');
    expect(body.items.map((i: any) => i.id).sort()).toEqual([crop(a, 6), crop(a, 9), crop(b, 3)].map(publicId).sort());
    expect(body.items[2].id).toBe(publicId(crop(a, 6)));
    expect(body.total).toBe(3);
    expect(body.reported_count).toBe(1);
    const hidden = await get(env, '/atlas?state=attention&reported=hide&limit=10');
    expect(hidden.total).toBe(2);
    expect(hidden.reported_count).toBe(1);
    expect((await get(env, '/atlas?state=flagged&limit=10')).items.map((i: any) => i.id).sort()).toEqual([crop(a, 6), crop(b, 3)].map(publicId).sort());
    expect((await get(env, '/atlas?state=attention&character=所&limit=10')).items.map((i: any) => i.id)).toEqual([publicId(crop(a, 6))]);
  });
});

describe('review rounds', () => {
  it('count what a round may deal: named, not withheld, in its material, with the corpus glyphs nothing has decided', async () => {
    const { env } = await setup();
    const body = await get(env, '/atlas?purpose=review&limit=0&state=pending', 'u1');
    expect(body.production).toBe('not:printed/type');
    // は: 3 pending (a0, a1, b0) + 4 corpus; 所: a5 + b2 pending, a6 flagged, b1 checked; ハ and b4, b5 are movable type.
    const of = (label: string) => body.categories.find((c: any) => c.label === label);
    expect(of('は')).toMatchObject({ grapheme: 'U+306F', pending: 7, checked: 1, total: 8 });
    expect(of('所')).toMatchObject({ pending: 2, flagged: 1, checked: 1 });
    expect(of('𛂞')).toMatchObject({ pending: 2, hard: 1 });
    expect(of('한')).toMatchObject({ pending: 1 });
    expect(of('ハ')).toBeUndefined();
    expect(body.documents).toEqual([]);
    expect(body.items).toEqual([]);
  });

  it('deal a grapheme’s collected crops, then its corpus glyphs, leaving out the reviewer’s own skips', async () => {
    const { env, db, a, b, c, crop } = await setup();
    const at = Date.now();
    db.exec(`INSERT INTO skips(crop,actor,cut,at,submission) VALUES(${crop(a, 0)},1,0,${at},1),(${crop(b, 0)},2,0,${at},2),(${crop(a, 1)},1,0,${at - 4 * 86400000},1)`);
    const round = await every(env, '/atlas?purpose=review&grapheme=U%2B306F&state=pending&seed=11', 3, 'u1');
    const body = round.body;
    expect(body.grapheme).toEqual({ code_point: 'U+306F', char: 'は', members: ['は', 'ハ', '𛂞'] });
    // Another reviewer's skip first; then a1 (its skip has rested); then the corpus glyphs but the one
    // whose image may not be served.
    expect(round.seen[0]).toBe(publicId(crop(b, 0)));
    expect(round.seen.slice(1, 2)).toEqual([publicId(crop(a, 1))]);
    expect(round.seen.slice(2).sort()).toEqual([0, 1, 3, 4, 5].map(s => publicId(crop(c, s))).sort());
    expect(body.total).toBe(2 + 6);
    const counts = await get(env, '/atlas?purpose=review&grapheme=U%2B306F&state=pending&limit=0', 'u1');
    expect(counts.categories.find((x: any) => x.label === 'は')).toMatchObject({ pending: 6, skipped: 1 });
    // Without a reviewer, nothing is skipped.
    expect((await every(env, '/atlas?purpose=review&grapheme=U%2B306F&state=pending&seed=11', 4)).seen.length).toBe(8);
  });

  it('deal a kanji’s small seal glyphs with the rest, and a script’s round that script’s alone', async () => {
    // 所's small seal character, filed under 所, with a member that came to the table after it.
    const SEAL = '\u{3FA6A}', MEMBER = '𫠦';
    const { env } = await setup((db, page) => {
      db.exec(`UPDATE graphemes SET head=4 WHERE id=4; INSERT INTO graphemes(id,text,head) VALUES(7,'${SEAL}',4),(8,'${MEMBER}',4);`);
      const glyph = db.prepare('INSERT INTO crops(id,kind,book,char,production,category,style,state,quiz,shuffle,cut,revision,flags) VALUES(?,1,3,?,4,NULL,0,0,1,?,0,0,0)');
      glyph.run(cropId(page, 10), 7, 500); glyph.run(cropId(page, 11), 4, 600);
    });
    const whole = await get(env, '/atlas?purpose=review&grapheme=U%2B6240&state=pending&seed=11&limit=0');
    // The number keys follow the members, so the small seal character comes last whatever its id.
    expect(whole.grapheme.members).toEqual(['所', MEMBER, SEAL]);
    for (const production of ['handwritten', 'handwritten&group=all'])
      expect((await get(env, `/atlas?purpose=review&grapheme=U%2B6240&state=pending&seed=11&limit=0&production=${production}`)).total).toBe(1 + 2);
    // One collected 所 and its corpus glyph in the kanji round, the seal glyph in the small seal round.
    expect((await get(env, '/atlas?purpose=review&grapheme=U%2B6240&state=pending&seed=11&limit=0&production=handwritten&group=kanji')).total).toBe(1 + 1);
    expect((await get(env, '/atlas?purpose=review&grapheme=U%2B6240&state=pending&seed=11&limit=0&production=handwritten&group=seal')).total).toBe(1);
    expect((await get(env, '/atlas?purpose=review&grapheme=U%2B6240&state=pending&seed=11&limit=0&production=handwritten&group=kana')).total).toBe(0);
    // Asked for by its small seal character, a round is that character's.
    const asked = await get(env, `/atlas?purpose=review&grapheme=U%2B3FA6A&state=pending&seed=11&limit=0&production=handwritten`);
    expect([asked.grapheme, asked.total]).toEqual([{ code_point: 'U+3FA6A', char: SEAL, members: [SEAL] }, 1]);
    // Each character counts under the grapheme, so the interface's rounds hold both.
    const counts = await get(env, '/atlas?purpose=review&limit=0&state=pending&production=handwritten');
    expect(counts.categories.find((x: any) => x.label === SEAL)).toMatchObject({ grapheme: 'U+6240', pending: 1 });
  });

  it('deal a member’s crops alone under its key, on every page, whatever a claim files them under', async () => {
    const { env, db } = await setup();
    const key = 'U%2B1B09E', dealt = async (round: string) => every(env, `/atlas?purpose=review&grapheme=${round}&state=pending&seed=11`, 1);
    const member = await dealt(key), family = await dealt('U%2B306F');
    expect(member.body.grapheme).toEqual({ code_point: 'U+1B09E', char: '𛂞', members: ['𛂞'] });
    expect(member.seen.length).toBeGreaterThan(1);
    expect(member.seen.every((id: string) => family.seen.includes(id))).toBe(true);
    expect(family.seen.length).toBeGreaterThan(member.seen.length);
    // A round goes by what its crops are written as: one claimed for 所 stays, and 所's round does not gain it.
    const there = (await dealt('U%2B6240')).seen;
    for (const id of member.seen) db.prepare(`INSERT INTO current_claims(subject,predicate,scope,slot,crop,cut,status,object,members,supporting,claims,resolver,at)
      VALUES(?,'has_grapheme','','',?,0,'asserted','U+6240','[]','[]','[]','r','2026-11-24')`).run(id, cropOf(id));
    expect([(await dealt(key)).seen, (await dealt('U%2B6240')).seen]).toEqual([member.seen, there]);
  });

  it('never deal a crop no one has read, which browsing shows', async () => {
    const { env, a, crop } = await setup();
    const unread = publicId(crop(a, 8));
    expect((await every(env, '/atlas?seed=3', 6)).seen).toContain(unread);
    const round = await every(env, '/atlas?purpose=review&production=all&seed=3', 6);
    expect(round.seen).not.toContain(unread);
    expect(round.seen.length).toBe(14);
  });

  it('deal a round’s reference crops of one state, a member’s its own, and refuse a deep page', async () => {
    const { env, a, crop } = await setup();
    expect((await get(env, '/atlas?purpose=review&grapheme=U%2B306F&state=checked&limit=10')).items.map((i: any) => i.id)).toEqual([publicId(crop(a, 2))]);
    const member = await get(env, '/atlas?purpose=review&grapheme=U%2B30CF&state=checked&limit=10');
    expect([member.grapheme, member.items]).toEqual([{ code_point: 'U+30CF', char: 'ハ', members: ['ハ'] }, []]);
    for (const bad of ['x', '1.c.5.1', '0.a.5', '99999', '0.a.5.0'])
      await expect(get(env, `/atlas?purpose=review&grapheme=U%2B306F&cursor=${bad}`)).rejects.toThrow('Invalid cursor.');
    // A grapheme the table lacks is its own text.
    expect((await get(env, '/atlas?purpose=review&grapheme=U%2B3042&limit=10')).grapheme.members).toEqual(['あ']);
  });
});

describe('rounds by priority', () => {
  it('deal on four seeds in five each priority in turn, on the fifth all together', async () => {
    const { env, db, a, b, c, crop } = await setup();
    db.exec(`UPDATE crops SET priority=2 WHERE id=${crop(a, 0)}; UPDATE crops SET priority=0 WHERE id=${crop(a, 1)}`);
    for (const seed of [11, 12, 13, 14]) {
      const { seen } = await every(env, `/atlas?purpose=review&grapheme=U%2B306F&state=pending&seed=${seed}`, 2);
      expect(seen.slice(0, 3)).toEqual([publicId(crop(a, 1)), publicId(crop(b, 0)), publicId(crop(a, 0))]);
      expect(seen.slice(3).sort()).toEqual([0, 1, 3, 4, 5].map(s => publicId(crop(c, s))).sort());
    }
    // The fifth seed deals the collected crops in shuffle order from its point, whatever their priority.
    const rotated = (seed: number) => [crop(a, 0), crop(a, 1), crop(b, 0)]
      .map(id => [id, (db.query('SELECT shuffle FROM crops WHERE id=?').get(id) as { shuffle: number }).shuffle] as const)
      .sort((x, y) => (x[1] - seed + 2 ** 31) % 2 ** 31 - (y[1] - seed + 2 ** 31) % 2 ** 31).map(([id]) => publicId(id));
    for (const seed of [10, 2 ** 30 + 1]) expect((await every(env, `/atlas?purpose=review&grapheme=U%2B306F&state=pending&seed=${seed}`, 2)).seen.slice(0, 3))
      .toEqual(rotated(seed));
  });
});

describe('rounds of doubted crops', () => {
  // は: a1 and b0 doubted, a0 marked under a label it no longer holds, the corpus glyph c1 doubted.
  const marked = async () => {
    const found = await setup(), { db, a, b, c, crop } = found;
    const text = (id: number) => (db.query('SELECT g.text FROM crops k JOIN graphemes g ON g.id=k.char WHERE k.id=?').get(id) as { text: string }).text;
    for (const id of [crop(a, 1), crop(b, 0), crop(c, 1)]) db.query('INSERT INTO suspects VALUES(?,0.01,NULL,?,NULL)').run(id, text(id));
    db.query('INSERT INTO suspects VALUES(?,0.01,NULL,?,NULL)').run(crop(a, 0), '別');
    // A doubted crop nobody may be dealt: one flagged, one withheld.
    for (const id of [crop(a, 6), crop(a, 10)]) db.query('INSERT INTO suspects VALUES(?,0.01,NULL,?,NULL)').run(id, text(id));
    return found;
  };
  const path = '/atlas?purpose=review&grapheme=U%2B306F&state=pending';
  it('deal them before the rest of the collected crops and of the corpus glyphs, each crop once', async () => {
    const { env, a, b, c, crop } = await marked();
    for (const seed of [11, 12, 13, 14]) for (const limit of [1, 2, 20]) {
      const { seen } = await every(env, `${path}&seed=${seed}`, limit);
      expect(seen.slice(0, 2).sort()).toEqual([crop(a, 1), crop(b, 0)].map(publicId).sort());
      expect(seen.slice(2, 4)).toEqual([crop(a, 0), crop(c, 1)].map(publicId));
      expect(new Set(seen).size).toBe(seen.length);
      expect(seen.length).toBe(8);
    }
    // The fifth seed deals the collected crops together, doubted or not.
    const together = await every(env, `${path}&seed=10`, 3);
    expect(new Set(together.seen).size).toBe(8);
    // The marks change no count: one of the nine is a corpus glyph whose image may not be shown.
    expect((await every(env, `${path}&seed=11`, 20)).body.total).toBe(9);
  });
  it('deal them alone when asked, uncounted, under the marks their characters hold', async () => {
    const { env, a, b, c, crop } = await marked();
    for (const seed of [10, 11]) {
      const { seen, body } = await every(env, `${path}&seed=${seed}&suspects=only`, 1);
      expect(seen.slice(0, 2).sort()).toEqual([crop(a, 1), crop(b, 0)].map(publicId).sort());
      expect(seen.slice(2)).toEqual([publicId(crop(c, 1))]);
      expect(body.total).toBeNull();
    }
    const summary = await get(env, '/atlas?purpose=review&limit=0&state=pending&suspects=only&production=all');
    expect(summary.categories.map((x: any) => [x.label, x.pending])).toEqual([['は', 3]]);
    // A material narrows them, and other states are not narrowed.
    expect((await get(env, '/atlas?purpose=review&limit=0&state=pending&suspects=only&production=handwritten')).categories.map((x: any) => x.pending)).toEqual([1]);
    const states = async (more: string) => (await get(env, `/atlas?purpose=review&grapheme=U%2B306F&state=checked&limit=5&seed=1${more}`)).items.map((x: any) => x.id);
    expect(await states('&suspects=only')).toEqual(await states(''));
    // Browsing is not narrowed by the marks.
    expect((await get(env, '/atlas?suspects=only&limit=1')).total).toBeGreaterThan(3);
  });
});

describe('a listing ends where the index does', () => {
  it('when D1 holds more crops than the answer counts, or fewer', async () => {
    const { env, db, a, b, c, crop } = await setup();
    // A withheld crop the publication counted as withheld is shown since, and another is gone.
    db.exec(`UPDATE crops SET quiz=1 WHERE id=${crop(a, 7)}; DELETE FROM crops WHERE id=${crop(b, 5)}`);
    const { seen, body } = await every(env, '/atlas?seed=77', 4);
    expect(seen).toContain(publicId(crop(a, 7)));
    expect(seen).not.toContain(publicId(crop(b, 5)));
    expect(seen.length).toBe(15);
    expect(body.total).toBe(15);
    const round = await every(env, '/atlas?purpose=review&grapheme=U%2B6240&state=pending&production=all&seed=3', 1);
    expect(round.seen.sort()).toEqual([crop(a, 5), crop(a, 7), crop(b, 2), crop(c, 8)].map(publicId).sort());
  });
});

describe('what an isolate holds', () => {
  function counted(env: Env) {
    const sql: string[] = [];
    const atlas = (env as any).ATLAS;
    return { sql, env: { ...env, ATLAS: { prepare: (q: string) => (sql.push(q), atlas.prepare(q)), batch: atlas.batch } } as unknown as Env };
  }
  it('reads others’ skips only for a page of crops, and the tally once while the catalogue stands', async () => {
    const { env: base } = await setup();
    const { sql, env } = counted(base);
    await get(env, '/atlas?purpose=review&limit=0&state=pending', 'u1');
    expect(sql.filter(q => q.includes('k.actor NOT IN'))).toEqual([]);
    for (let i = 0; i < 2; i++) await get(env, '/atlas?purpose=review&grapheme=U%2B306F&state=pending&limit=4', 'u1');
    expect(sql.filter(q => q.includes('k.actor NOT IN')).length).toBe(1);
    expect(sql.filter(q => q.includes('FROM tally')).length).toBe(1);
  });

  it('serves a page bundle the edge holds without reading R2, and keeps each one it reads', async () => {
    const { env, objects, a } = await setup();
    const key = bundleKey(a, 1), bytes = objects.get(key)!;
    // A bundle read from R2 is put at the edge.
    expect((await bundle(env, a, 1))!.bySlot.size).toBe(12);
    expect(await (globalThis as any).caches.default.match(new Request(`https://glyphatlas.org/_bundles/${key}`))).toBeTruthy();
    // A page whose bundle only the edge holds.
    const other = a + 100000;
    await (globalThis as any).caches.default.put(new Request(`https://glyphatlas.org/_bundles/${bundleKey(other, 1)}`), new Response(bytes));
    expect((await bundle(env, other, 1))!.bySlot.size).toBe(12);
    expect(objects.has(bundleKey(other, 1))).toBe(false);
  });
});

describe('the queries', () => {
  it('read every listing range from its index, in its order', async () => {
    const { db, env } = await setup();
    void env;
    const plan = (sql: string, values: unknown[]) => (db.query(`EXPLAIN QUERY PLAN ${sql}`).all(...(values as any[])) as { detail: string }[]).map(r => r.detail).join(' | ');
    const base: Filter = { chars: null, book: null, category: null, state: null, withheld: false, productions: new Set([2, 4]), named: false };
    for (const [filter, kind, index, tier] of [[base, 0, 'crop_listing', false], [{ ...base, category: 2 }, 0, 'crop_listing', false],
      [{ ...base, state: 1, named: true }, 0, 'crop_listing', false], [{ ...base, chars: new Set([1, 2]) }, 0, 'crop_deal', false],
      [{ ...base, chars: new Set([1]), state: 0, named: true }, 1, 'crop_deal', true], [{ ...base, book: 1 }, 0, 'crop_book', false],
      [{ ...base, book: 3 }, 0, 'crop_book', false]] as [Filter, 0 | 1, string, boolean][]) {
      const texts = new Map([[1, 'は']]);
      for (const range of [...ranges(filter, kind, [2, 3, 5], tier ? [0] : [0, 1, 2], tier, filter.book === 3),
        ...(filter.chars ? [true, false].flatMap(marked => ranges(filter, kind, [2, 3, 5], [0, 1, 2], tier, false, { marked, texts })) : [])]) for (const [side, after] of [['>=', false], ['<', true], ['>=', true]] as const) {
        const detail = plan(sideQuery(range, side, after), [...range.values, 0, ...(after ? [5, 9] : []), 10]);
        expect(detail).toContain(`USING ${index === 'crop_book' ? 'INDEX' : 'COVERING INDEX'} ${index}`);
        expect(detail).not.toContain('TEMP B-TREE');
        expect(detail).not.toMatch(/SCAN crops(?! USING)/);
      }
    }
    const counting = bookGlyphsQuery({ ...base, book: 3, state: 1 });
    expect(plan(counting.sql, counting.values)).toContain('USING INDEX crop_book');
    expect(plan(counting.sql, counting.values)).not.toMatch(/SCAN crops(?! USING)/);
    // The count reads no more than the cap of the book's rows, whatever the filter keeps.
    expect(counting.sql).toContain(`LIMIT ${BOOK_ROWS}) WHERE kind=1`);
    const flagged = flaggedQuery({ ...base, withheld: true, chars: new Set([4]) });
    expect(plan(flagged.sql, flagged.values)).toContain('crop_flagged');
    expect(plan(flagged.sql, flagged.values)).toContain('event_crop');
    for (const [sql, values, wanted] of [
      [ownSkipsQuery(), ['u1', 0], 'SEARCH k USING INDEX skip_actor'], [othersSkipsQuery(), ['u1'], 'SEARCH c USING INTEGER PRIMARY KEY'],
      [tallyDeltasQuery(), [3], 'USING PRIMARY KEY (gen>?)'],
      [charsQuery(), ['[1]'], 'SEARCH g USING INTEGER PRIMARY KEY'], [bookDatesQuery(), ['[1]'], 'SEARCH d USING PRIMARY KEY (book=?)'],
      [booksQuery(), ['[1]'], 'SEARCH books USING INTEGER PRIMARY KEY'], [graphemeQuery(), ['は'], 'USING INDEX sqlite_autoindex_graphemes_1'],
      [membersQuery(), [1], 'grapheme_head'], [familyQuery(), ['は'], 'grapheme_head'], [familyQuery(), ['は'], 'USING INDEX sqlite_autoindex_graphemes_1'], [datingQuery(), ['["hk:a"]'], 'SEARCH d USING PRIMARY KEY (book=?)'],
      [cropSlotsQuery(), ['[1]', 'has_form'], 'current_claim_crop'], [corpusFormsQuery(), ['[1]'], 'SEARCH g USING INTEGER PRIMARY KEY'],
      [doubtedQuery(), [], 'SEARCH c USING INTEGER PRIMARY KEY'],
      [marksQuery(), ['[1]'], 'SEARCH s USING INTEGER PRIMARY KEY'], [reviewedQuery(), ['[1]'], 'SEARCH e USING INDEX event_crop (crop=?)'],
      [reviewedQuery(), ['[1]'], 'SEARCH r USING INTEGER PRIMARY KEY']] as [string, unknown[], string][]) {
      const detail = plan(sql, values);
      expect(detail).toContain(wanted);
      expect(detail).not.toMatch(/SCAN (c|crops|g|graphemes|d|book_dates|books)\b(?! USING)/);
    }
    // `skips` keeps 90 days of rounds' skips and is read whole for others' skips; every other table by key.
    expect(plan(othersSkipsQuery(), ['u1'])).toMatch(/SCAN k/);
    expect(REVIEWED_IN_INSPECTOR).toContain('e.crop=c.id');
  });

  it('deal several ranges as one, from the seed’s point round past the highest, each page after the last', async () => {
    const { env } = await setup();
    const filter: Filter = { chars: null, book: null, category: null, state: null, withheld: true, productions: null, named: false };
    const stages: Stage[] = [{ list: ranges(filter, 0, [2, 3, 5], [0, 1, 2]) }];
    const whole: number[] = [];
    let cursor: string | null = null;
    do { const got = await page(env, stages, 2 ** 30, cursor, 4, new Set()); whole.push(...got.ids); cursor = got.next; } while (cursor);
    expect(new Set(whole).size).toBe(18);
    expect((await page(env, stages, 2 ** 30, null, 18, new Set())).ids).toEqual(whole);
    // A page that fills at the end of the last stage still names a next one; it holds none.
    const all = await page(env, stages, 2 ** 30, null, 18, new Set());
    expect(all.next === null || (await page(env, stages, 2 ** 30, all.next, 5, new Set())).ids.length === 0).toBe(true);
    expect(cursorOf(cursorText(3, { side: 'b', shuffle: 7, id: whole[0] }))).toEqual({ stage: 3, place: { side: 'b', shuffle: 7, id: whole[0] } });
  });

});
