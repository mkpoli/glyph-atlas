import { afterEach, beforeEach, describe, expect, it } from 'bun:test';
import { bundle, bundleKey, cropId, publicId } from '../atlas3';
import { charsQuery, tallyDeltasQuery } from '../atlas3Answers';
import { corpusFormsQuery, cropFormsQuery, datingQuery, marksQuery, reviewedQuery } from '../atlas3Listing';
import { REVIEWED_IN_INSPECTOR, bookDatesQuery, booksQuery, corpusCharacters, cursorOf, cursorText, dateStats, flaggedQuery, gallery, page, type Stage,
  graphemeQuery, health, membersQuery, othersSkipsQuery, ownSkipsQuery, publishedAt, ranges, sideQuery, siteValue, type Filter } from './browse';
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
afterEach(() => { for (const db of databases.splice(0)) db.close(); });

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

describe('corpus glyphs by character', () => {
  it('count each character in the material asked for, under its grapheme, in code point order', async () => {
    const { env } = await setup();
    const all = await corpusCharacters(env, ctx, new URL('https://glyphatlas.org/atlas/corpus/characters'));
    expect(all.items).toEqual([['は', 'U+306F', 4], ['所', 'U+6240', 1], ['한', 'U+D55C', 1], ['𛂞', 'U+306F', 3]]);
    const scoped = await corpusCharacters(env, ctx, new URL('https://glyphatlas.org/atlas/corpus/characters?production=not:printed/type'));
    expect(scoped.items.map((i: any) => i[0])).toEqual(['は', '한', '𛂞']);
    expect((await corpusCharacters(env, ctx, new URL('https://glyphatlas.org/atlas/corpus/characters?production=handwritten'))).items)
      .toEqual([['𛂞', 'U+306F', 3]]);
    await expect(corpusCharacters(env, ctx, new URL('https://glyphatlas.org/atlas/corpus/characters?production=wood'))).rejects.toThrow('Invalid production scope.');
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
    const page = await gallery(env, new URLSearchParams({ seed: String(entries[6][1] as number), limit: '5' }));
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
    expect((await gallery(env, new URLSearchParams({ seed: String(entries[7][1] as number), limit: '1' }))).items[0].state).toBe('pending');
  });
  it('deal a script group from its own glyphs, and leave out one a review moved to another script', async () => {
    const { env, db, objects, c, records, pub } = await setup();
    sampled(objects, c, records, pub);
    expect((await gallery(env, new URLSearchParams({ group: 'hangul' }))).items.map((i: any) => i.source_id)).toEqual(['codh:7']);
    expect((await gallery(env, new URLSearchParams({ group: 'gugyeol' }))).items).toEqual([]);
    expect((await gallery(env, new URLSearchParams({ group: 'kanji', limit: '3' }))).items.map((i: any) => i.source_id)).toEqual(['codh:8']);
    db.exec(`UPDATE crops SET char=1 WHERE id=${cropId(c, 8)}`);
    expect((await gallery(env, new URLSearchParams({ group: 'kanji' }))).items).toEqual([]);
    await expect(gallery(env, new URLSearchParams({ group: 'latin' }))).rejects.toThrow('Invalid group.');
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
    expect(body.documents.map((d: any) => [d.id, d.title, d.total, d.pending])).toEqual([['hk:a', 'Book A', 10, 6], ['hk:b', 'Book B', 5, 4]]);
    expect(body.withheld_count).toBe(3);
    expect(body.review_epoch).toBe(3);
    const shown = await get(env, '/atlas?limit=0&withheld=show');
    expect(shown.documents.map((d: any) => d.total)).toEqual([12, 6]);
    expect(shown.withheld_count).toBeNull();
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
    expect((await get(env, '/atlas?document=nowhere&limit=5')).items).toEqual([]);
    expect((await get(env, '/atlas?character=X&limit=5')).total).toBe(0);
  });

  it('show each crop with its form, dating, suspect mark and shape order', async () => {
    const { env, db, a, crop } = await setup();
    db.exec(`INSERT INTO suspects VALUES(${crop(a, 0)},0.9,'ほ','は','{"x":0,"y":0,"w":1,"h":1}'),(${crop(a, 1)},0.8,'ほ','あ',NULL);
      INSERT INTO shapes VALUES(${crop(a, 0)},4);
      INSERT INTO current_claims(subject,predicate,scope,slot,crop,cut,status,object,members,supporting,claims,resolver,at) VALUES
        ('x','has_form','','',${crop(a, 0)},0,'single','f:1','[{"object":"f:1","confidence":null,"assertion":"c1"}]','["c1"]',
         '[{"asserted_by":"u1","standing":true,"members":[{"assertion":"c1"}]}]','r',''),
        ('y','has_form','','',${crop(a, 1)},1,'single','f:2','[{"object":"f:2","confidence":null,"assertion":"c2"}]','["c2"]','[]','r','');`);
    const items = (await get(env, '/atlas?character=は&limit=10')).items;
    const first = items.find((i: any) => i.id === publicId(crop(a, 0))), second = items.find((i: any) => i.id === publicId(crop(a, 1)));
    expect(first.suspect).toEqual({ p: 0.9, reads_as: 'ほ' });
    expect(first.shape_order).toBe(4);
    expect(first.form).toEqual({ status: 'single', values: [{ form: 'f:1', scheme: null, text: null, confidence: null }], supporting: ['c1'], by: ['u1'] });
    expect(first.dating.witness.label).toBe('1712');
    expect(first.dating.composed.label).toBe('1600-1650');
    // A mark on another label, and a claim on an earlier cut, no longer hold.
    expect(second.suspect).toBeNull();
    expect(second.form).toBeNull();
    expect(first.document).toBe('hk:a');
    expect(first.page_id).toBe('hk:a:1');
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
  });

  it('count a review at once, before the next publication', async () => {
    const { env, db, a, crop } = await setup();
    db.exec(`UPDATE crops SET state=1, revision=1 WHERE id=${crop(a, 0)};
      INSERT INTO tally VALUES(1,0,1,1,2,0,0,1,-1),(1,0,1,1,2,0,1,1,1);
      UPDATE crops SET char=6, revision=1 WHERE id=${crop(a, 8)};
      INSERT INTO tally VALUES(1,0,-1,1,2,0,0,1,-1),(1,0,6,1,2,0,0,1,1);`);
    const body = await get(env, '/atlas?limit=0');
    expect(body.counts.checked).toBe(3);
    expect(body.categories.find((c: any) => c.label === 'は')).toMatchObject({ total: 5, pending: 3, checked: 2 });
    expect(body.categories.find((c: any) => c.label === '処').total).toBe(2);
    expect((await get(env, '/atlas?character=処&limit=10')).total).toBe(2);
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

  it('never deal a crop no one has read, which browsing shows', async () => {
    const { env, a, crop } = await setup();
    const unread = publicId(crop(a, 8));
    expect((await every(env, '/atlas?seed=3', 6)).seen).toContain(unread);
    const round = await every(env, '/atlas?purpose=review&production=all&seed=3', 6);
    expect(round.seen).not.toContain(unread);
    expect(round.seen.length).toBe(14);
  });

  it('deal a round’s reference crops of one state, and refuse a member or a deep page', async () => {
    const { env, a, crop } = await setup();
    expect((await get(env, '/atlas?purpose=review&grapheme=U%2B306F&state=checked&limit=10')).items.map((i: any) => i.id)).toEqual([publicId(crop(a, 2))]);
    await expect(get(env, '/atlas?purpose=review&grapheme=U%2B30CF&limit=10')).rejects.toThrow('ハ is filed under は.');
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
      [{ ...base, chars: new Set([1]), state: 0, named: true }, 1, 'crop_deal', true], [{ ...base, book: 1 }, 0, 'crop_book', false]] as [Filter, 0 | 1, string, boolean][]) {
      for (const range of ranges(filter, kind, [2, 3, 5], tier ? [0] : [0, 1, 2], tier)) for (const [side, after] of [['>=', false], ['<', true], ['>=', true]] as const) {
        const detail = plan(sideQuery(range, side, after), [...range.values, 0, ...(after ? [5, 9] : []), 10]);
        expect(detail).toContain(`USING ${index === 'crop_book' ? 'INDEX' : 'COVERING INDEX'} ${index}`);
        expect(detail).not.toContain('TEMP B-TREE');
        expect(detail).not.toMatch(/SCAN crops(?! USING)/);
      }
    }
    const flagged = flaggedQuery({ ...base, withheld: true, chars: new Set([4]) });
    expect(plan(flagged.sql, flagged.values)).toContain('crop_flagged');
    expect(plan(flagged.sql, flagged.values)).toContain('event_crop');
    for (const [sql, values, wanted] of [
      [ownSkipsQuery(), ['u1', 0], 'SEARCH k USING INDEX skip_actor'], [othersSkipsQuery(), ['u1'], 'SEARCH c USING INTEGER PRIMARY KEY'],
      [tallyDeltasQuery(), [3], 'USING PRIMARY KEY (gen>?)'],
      [charsQuery(), ['[1]'], 'SEARCH g USING INTEGER PRIMARY KEY'], [bookDatesQuery(), ['[1]'], 'SEARCH d USING PRIMARY KEY (book=?)'],
      [booksQuery(), ['[1]'], 'SEARCH books USING INTEGER PRIMARY KEY'], [graphemeQuery(), ['は'], 'USING INDEX sqlite_autoindex_graphemes_1'],
      [membersQuery(), [1], 'grapheme_head'], [datingQuery(), ['["hk:a"]'], 'SEARCH d USING PRIMARY KEY (book=?)'],
      [cropFormsQuery(), ['[1]'], 'current_claim_crop'], [corpusFormsQuery(), ['[1]'], 'SEARCH g USING INTEGER PRIMARY KEY'],
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
