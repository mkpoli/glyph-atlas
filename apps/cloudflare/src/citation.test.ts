import { describe, expect, it } from 'bun:test';
import { Database } from 'bun:sqlite';
import { readdirSync, readFileSync } from 'node:fs';
import { d1 } from './forms.test';
import { bibtex, characterEntry, citedVersion, cropEntry, csl, linkedData, versionToken } from './citation';
import worker from './index';

const pixels = '2c9afc05d11e155460189250e6d42f06b852f1eb11646f5e423442849ddc0740';
const crop = {
  id: 'ex:1', label: '敢', source: 'Sibu Congkan0443', page_number: 100, licence: 'PD', holder: null,
  attribution: 'unstated holder, via Wikimedia Commons, https://commons.wikimedia.org/wiki/File:X.djvu',
  rights_url: 'https://commons.wikimedia.org/wiki/File:X.djvu', crop_version: `ex:1@${pixels}@785,2131,136,123`,
};

describe('a crop version in an address', () => {
  it('is the checksum’s first twelve characters and the box', () => {
    expect(versionToken('ex:1', crop.crop_version)).toBe('2c9afc05d11e-785-2131-136-123');
    expect(versionToken('ex:1', `ex:1@${pixels}@`)).toBe('2c9afc05d11e');
    expect(versionToken('ex:2', crop.crop_version)).toBeNull();
    expect(versionToken('ex:1', null)).toBeNull();
  });
  it('names one of the crop’s versions, or none', () => {
    const older = `ex:1@${'f'.repeat(64)}@1,2,3,4`;
    expect(citedVersion('ex:1', 'ffffffffffff-1-2-3-4', [older, crop.crop_version])).toBe(older);
    expect(citedVersion('ex:1', 'ffffffffffff-9-9-9-9', [older, crop.crop_version])).toBeNull();
  });
});

describe('a crop’s citation', () => {
  const entry = cropEntry(crop);
  it('addresses the crop at its version and carries its source as the record states it', () => {
    expect(entry.path).toBe('/crop/ex%3A1?v=2c9afc05d11e-785-2131-136-123');
    expect(entry.source).toEqual({ title: 'Sibu Congkan0443', page: 100, licence: 'PD', attribution: crop.attribution, rights: crop.rights_url });
  });
  it('is CSL-JSON with the version, the licence and the credit verbatim, and no field the record lacks', () => {
    const item = csl(entry, 'https://glyphatlas.org', [2026, 10, 5]);
    expect(item).toEqual({
      id: 'glyphatlas:crop:ex:1@2c9afc05d11e-785-2131-136-123', type: 'webpage', title: '敢 (ex:1)', genre: 'Crop',
      'container-title': 'Glyph Atlas', URL: 'https://glyphatlas.org/crop/ex%3A1?v=2c9afc05d11e-785-2131-136-123',
      version: crop.crop_version, license: 'PD',
      note: `Source document: Sibu Congkan0443\nSource page: 100\nLicence: PD\nImage credit: ${crop.attribution}\nRights statement: ${crop.rights_url}`,
      accessed: { 'date-parts': [[2026, 10, 5]] },
    });
    expect('archive' in item).toBe(false);
  });
  it('is a BibTeX record whose text TeX prints as written', () => {
    const record = bibtex(cropEntry({ ...crop, source: 'A_B & 50%' }), 'https://glyphatlas.org', [2026, 10, 5]);
    expect(record).toStartWith('@misc{glyphatlas:crop:ex:1@2c9afc05d11e-785-2131-136-123,\n  title = {{敢 (ex:1)}},\n  howpublished = {Glyph Atlas},\n');
    expect(record).toContain('  url = {https://glyphatlas.org/crop/ex%3A1?v=2c9afc05d11e-785-2131-136-123},\n  urldate = {2026-10-05},');
    expect(record).toContain('Source document: A\\_B \\& 50\\%');
  });
  it('names a corpus glyph’s holder, shelfmark and transcription, and leaves out an unassigned label', () => {
    const glyph = cropEntry({ id: 'codh:1', label: '〓', identity_status: 'unassigned', licence: 'unknown',
      source: { title: '源氏物語', holder: '国文学研究資料館', shelfmark: '9-1' }, text_attribution: 'CODH 日本古典籍くずし字データセット' }, 'corpus');
    expect(glyph.path).toBe('/corpus/codh%3A1');
    expect(glyph.label).toBeNull();
    const item = csl(glyph, 'https://glyphatlas.org', [2026, 1, 2]);
    expect([item.title, item.archive, item.archive_location, item.license]).toEqual(['codh:1', '国文学研究資料館', '9-1', undefined]);
    expect(item.note).toContain('Transcription: CODH 日本古典籍くずし字データセット');
  });
  it('is an image of its source in the page’s JSON-LD', () => {
    const data = linkedData(entry, 'https://glyphatlas.org', 'https://glyphatlas.org/atlas/media/a.webp');
    expect(data['@type']).toBe('ImageObject');
    expect(data.license).toBeUndefined();
    expect(data.creditText).toBe(crop.attribution);
    expect(data.isBasedOn).toEqual({ '@type': 'CreativeWork', name: 'Sibu Congkan0443' });
    expect(data.isPartOf).toEqual({ '@type': 'Dataset', name: 'Glyph Atlas', url: 'https://glyphatlas.org/' });
  });
});

describe('a character’s citation', () => {
  const card = { code_point: 'U+1B002', char: '𛀂', grapheme: { code_point: 'U+3042', char: 'あ' } };
  it('names the grapheme family or the form alone, each with a scope its page keeps', () => {
    expect(characterEntry('grapheme', card)).toEqual({ kind: 'grapheme', id: 'U+3042', label: 'あ', codePoint: 'U+3042', path: '/character/U+3042?scope=family' });
    expect(characterEntry('form', card).path).toBe('/character/U+1B002?scope=exact');
    expect(characterEntry('form', { code_point: 'U+304B U+309A', char: 'か゚' }).path).toBe('/character/U+304B-U+309A?scope=exact');
  });
});

describe('GET /atlas/cite', () => {
  function setup() {
    const db = new Database(':memory:');
    const migrations = new URL('../migrations/', import.meta.url);
    for (const file of readdirSync(migrations).filter(f => f.endsWith('.sql')).sort()) db.exec(readFileSync(new URL(file, migrations), 'utf8'));
    const data = (box: object) => JSON.stringify({ ...crop, crop_version: undefined, image_sha256: pixels, box });
    db.prepare(`INSERT INTO units(id,origin,character,production,category,state,revision,quiz,priority,shuffle,data,snapshot,context,visual)
      VALUES('ex:1','local','敢','printed','kanji','pending',0,1,1,0,?,'{}','{}','{}')`).run(data({ x: 1, y: 2, w: 3, h: 4 }));
    // A recut: the first version stays recorded.
    db.prepare(`UPDATE units SET data=? WHERE id='ex:1'`).run(data({ x: 785, y: 2131, w: 136, h: 123 }));
    db.prepare('INSERT INTO characters(code_point,character,name,data,detail) VALUES(?,?,?,?,?)').run('U+1B002', '𛀂', 'HENTAIGANA LETTER A-1', '{}',
      JSON.stringify({ code_point: 'U+1B002', char: '𛀂', grapheme: { code_point: 'U+3042', char: 'あ' } }));
    const stored = new Map<string, Response>();
    (globalThis as any).caches = { default: { match: async (key: Request) => stored.get(key.url)?.clone(), put: async (key: Request, value: Response) => { stored.set(key.url, value) } } };
    const waits: Promise<unknown>[] = [];
    const ctx = { waitUntil: (p: Promise<unknown>) => waits.push(p), passThroughOnException() {} } as unknown as ExecutionContext;
    const get = async (path: string) => { const response = await worker.fetch(new Request('https://glyphatlas.org' + path), { DB: d1(db) } as unknown as Env, ctx); await Promise.all(waits); return response };
    return { db, get, stored };
  }
  it('serves a crop’s current version as CSL-JSON, open to any site and cached', async () => {
    const { db, get, stored } = setup();
    const response = await get('/atlas/cite/crop/ex%3A1');
    expect(response.status).toBe(200);
    expect(response.headers.get('content-type')).toStartWith('application/vnd.citationstyles.csl+json');
    expect(response.headers.get('access-control-allow-origin')).toBe('*');
    expect(response.headers.get('cache-control')).toBe('no-cache');
    const [item] = await response.json() as any[];
    expect(item.version).toBe(crop.crop_version);
    expect(item.URL).toBe('https://glyphatlas.org/crop/ex%3A1?v=2c9afc05d11e-785-2131-136-123');
    expect(stored.size).toBe(1);
    const again = await get('/atlas/cite/crop/ex%3A1');
    expect([again.headers.get('cache-control'), (await again.json() as any[])[0].version]).toEqual(['no-cache', crop.crop_version]);
    db.close();
  });
  it('serves the earlier version a citation names, and refuses one the crop never had', async () => {
    const { db, get } = setup();
    const [item] = await (await get('/atlas/cite/crop/ex%3A1?v=2c9afc05d11e-1-2-3-4')).json() as any[];
    expect(item.version).toBe(`ex:1@${pixels}@1,2,3,4`);
    // The label is the crop's now, and that cut may not have shown it.
    expect(item.title).toBe('ex:1');
    expect((await get('/atlas/cite/crop/ex%3A1?v=2c9afc05d11e-9-9-9-9')).status).toBe(404);
    expect((await get('/atlas/cite/crop/ex%3A1?v=bad%20token')).status).toBe(422);
    db.close();
  });
  it('serves a grapheme and a form, and refuses another kind or a character not in the table', async () => {
    const { db, get } = setup();
    expect((await (await get('/atlas/cite/grapheme/U%2B1B002')).json() as any[])[0].URL).toBe('https://glyphatlas.org/character/U+3042?scope=family');
    expect((await (await get('/atlas/cite/form/U%2B1B002')).json() as any[])[0].title).toBe('𛀂 (U+1B002)');
    expect((await get('/atlas/cite/page/U%2B1B002')).status).toBe(404);
    expect((await get('/atlas/cite/form/U%2B1B003')).status).toBe(404);
    expect((await get('/atlas/cite/form/not-a-character')).status).toBe(404);
    db.close();
  });
});
