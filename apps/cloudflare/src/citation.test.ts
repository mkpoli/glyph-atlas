import { describe, expect, it } from 'bun:test';
import { Database } from 'bun:sqlite';
import { readdirSync, readFileSync } from 'node:fs';
import { d1 } from './forms.test';
import { bibtex, characterEntry, citationKey, citeAddress, citedVersion, cropEntry, csl, hayagriva, linkedData, versionToken } from './citation';
import worker from './index';

const pixels = '2c9afc05d11e155460189250e6d42f06b852f1eb11646f5e423442849ddc0740';
const crop = {
  id: 'ex:1', label: '敢', source: 'Sibu Congkan0443', page_number: 100, licence: 'PD', holder: null,
  attribution: 'unstated holder, via Wikimedia Commons, https://commons.wikimedia.org/wiki/File:X.djvu',
  rights_url: 'https://commons.wikimedia.org/wiki/File:X.djvu', crop_version: `ex:1@${pixels}@785,2131,136,123`,
};

describe('a crop version in an address', () => {
  const token = versionToken('ex:1', crop.crop_version)!;
  it('is six letters and digits, the same for the same version', () => {
    expect(token).toMatch(/^[0-9a-z]{6}$/);
    expect(versionToken('ex:1', crop.crop_version)).toBe(token);
    expect(versionToken('ex:1', `ex:1@${pixels}@1,2,3,4`)).not.toBe(token);
    expect(versionToken('ex:2', crop.crop_version)).toBeNull();
    expect(versionToken('ex:1', null)).toBeNull();
  });
  it('names one of the crop’s versions, and none when two would answer to it', () => {
    const older = `ex:1@${'f'.repeat(64)}@1,2,3,4`;
    expect(citedVersion('ex:1', versionToken('ex:1', older)!, [older, crop.crop_version])).toBe(older);
    expect(citedVersion('ex:1', 'zzzzzz', [older, crop.crop_version])).toBeNull();
    // Two cuts whose names collide (found by search) are named by neither.
    const pair = ['ex:1@237819@', 'ex:1@327606@'];
    expect(versionToken('ex:1', pair[0])).toBe(versionToken('ex:1', pair[1]));
    expect(citedVersion('ex:1', versionToken('ex:1', pair[0])!, pair)).toBeNull();
  });
});

describe('a crop’s citation', () => {
  const entry = cropEntry(crop), token = versionToken('ex:1', crop.crop_version);
  const url = `https://glyphatlas.org/crop/ex:1?v=${token}`;
  it('addresses the crop at its version, readably, and carries its source as the record states it', () => {
    expect(entry.path).toBe(`/crop/ex:1?v=${token}`);
    expect(cropEntry({ ...crop, id: 'a/b c' }).path).toStartWith('/crop/a%2Fb%20c');
    expect(entry.source).toEqual({ title: 'Sibu Congkan0443', page: 100, licence: 'PD', attribution: crop.attribution, rights: crop.rights_url });
  });
  it('is CSL-JSON keyed short, with the licence and the credit verbatim and no field the record lacks', () => {
    const item = csl(entry, 'https://glyphatlas.org', [2026, 10, 5]);
    expect(item).toEqual({
      id: `glyphatlas-敢-${token}`, type: 'webpage', title: '敢', genre: 'Crop', 'container-title': 'Glyph Atlas', URL: url,
      version: token, license: 'PD',
      note: `Source: Sibu Congkan0443, p. 100\nImage: ${crop.attribution}\nLicence: PD\nRights statement: ${crop.rights_url}`,
      accessed: { 'date-parts': [[2026, 10, 5]] },
    });
    expect('archive' in item).toBe(false);
  });
  it('is a BibTeX record whose text TeX prints as written', () => {
    const record = bibtex(cropEntry({ ...crop, source: 'A_B & 50%' }), 'https://glyphatlas.org', [2026, 10, 5]);
    expect(record).toStartWith(`@misc{glyphatlas-敢-${token},\n  title = {{敢}},\n  howpublished = {Glyph Atlas},\n  url = {${url}},\n  urldate = {2026-10-05},`);
    expect(record).toContain('Source: A\\_B \\& 50\\%, p. 100');
  });
  it('is a Hayagriva entry of the site, with the document it was cut from as its original', () => {
    const glyph = cropEntry({ ...crop, source: { title: '源氏物語 "桐壺"', holder: '国文学研究資料館', shelfmark: '9-1' } }, 'corpus');
    expect(hayagriva(glyph, 'https://glyphatlas.org', [2026, 10, 5])).toBe(`glyphatlas-敢-${token}:
  type: "entry"
  title:
    value: "敢"
    verbatim: true
  genre: "Crop"
  serial-number: "ex:1"
  url:
    value: "https://glyphatlas.org/corpus/ex:1?v=${token}"
    date: "2026-10-05"
  parent:
    - type: "reference"
      title:
        value: "Glyph Atlas"
        verbatim: true
      url: "https://glyphatlas.org/"
    - type: "original"
      title:
        value: "源氏物語 \\"桐壺\\""
        verbatim: true
      page-range: 100
      archive:
        value: "国文学研究資料館"
        verbatim: true
      call-number: "9-1"
  note: "Image: ${crop.attribution}. Licence: PD. Rights statement: ${crop.rights_url}"
`);
  });
  it('cites an earlier cut without the label the crop has now', () => {
    const older = cropEntry(crop, 'collection', `ex:1@${pixels}@1,2,3,4`);
    expect([older.label, older.token, citationKey(older)]).toEqual([null, versionToken('ex:1', `ex:1@${pixels}@1,2,3,4`), `glyphatlas-crop-${older.token}`]);
  });
  it('names a corpus glyph’s holder, shelfmark and transcription, and leaves out an unassigned label', () => {
    const glyph = cropEntry({ id: 'codh:1', label: '〓', identity_status: 'unassigned', licence: 'unknown',
      source: { title: '源氏物語', holder: '国文学研究資料館', shelfmark: '9-1' }, text_attribution: 'CODH 日本古典籍くずし字データセット' }, 'corpus');
    expect(glyph.path).toBe('/corpus/codh:1');
    expect(glyph.label).toBeNull();
    const item = csl(glyph, 'https://glyphatlas.org', [2026, 1, 2]);
    expect([item.title, item.archive, item.archive_location, item.license]).toEqual(['codh:1', '国文学研究資料館', '9-1', undefined]);
    expect(item.note).toContain('Transcription: CODH 日本古典籍くずし字データセット');
  });
  it('keeps the form a crop is filed as when it is not the label', () => {
    expect(cropEntry({ ...crop, form: { values: [{ text: '𛀂' }] } }).form).toBe('𛀂');
    expect(cropEntry({ ...crop, form: { status: 'disputed', values: [{ text: '𛀂' }] } }).form).toBeUndefined();
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
  const current = versionToken('ex:1', crop.crop_version)!, first = versionToken('ex:1', `ex:1@${pixels}@1,2,3,4`)!;
  it('serves a crop’s current version as CSL-JSON, open to any site and cached', async () => {
    const { db, get, stored } = setup();
    const response = await get('/atlas/cite/crop/ex:1');
    expect(response.status).toBe(200);
    expect(response.headers.get('content-type')).toStartWith('application/vnd.citationstyles.csl+json');
    expect(response.headers.get('access-control-allow-origin')).toBe('*');
    expect(response.headers.get('cache-control')).toBe('no-cache');
    const [item] = await response.json() as any[];
    expect([item.version, item.URL]).toEqual([current, `https://glyphatlas.org/crop/ex:1?v=${current}`]);
    expect(stored.size).toBe(1);
    const again = await get('/atlas/cite/crop/ex%3A1');
    expect([again.headers.get('cache-control'), (await again.json() as any[])[0].version]).toEqual(['no-cache', current]);
    db.close();
  });
  it('serves the earlier version a citation names, and refuses one the crop never had', async () => {
    const { db, get } = setup();
    const [item] = await (await get(`/atlas/cite/crop/ex:1?v=${first}`)).json() as any[];
    // The label is the crop's now, and that cut may not have shown it.
    expect([item.version, item.title]).toEqual([first, 'ex:1']);
    expect((await get('/atlas/cite/crop/ex:1?v=zzzzzz')).status).toBe(404);
    expect((await get('/atlas/cite/crop/ex:1?v=2c9afc05d11e-1-2-3-4')).status).toBe(422);
    db.close();
  });
  it('serves BibTeX and Hayagriva on request, and refuses another format', async () => {
    const { db, get } = setup();
    const bib = await get(citeAddress(cropEntry(crop), 'bibtex'));
    expect([bib.status, bib.headers.get('content-type')]).toEqual([200, 'application/x-bibtex; charset=utf-8']);
    expect(await bib.text()).toStartWith(`@misc{glyphatlas-敢-${current},`);
    const yml = await get(`/atlas/cite/form/U+1B002?format=hayagriva`);
    expect(yml.headers.get('content-type')).toBe('application/yaml; charset=utf-8');
    expect(await yml.text()).toStartWith('glyphatlas-𛀂-form:\n  type: "entry"');
    expect((await get('/atlas/cite/form/U+1B002?format=ris')).status).toBe(422);
    db.close();
  });
  it('serves a grapheme and a form, and refuses another kind or a character not in the table', async () => {
    const { db, get } = setup();
    expect((await (await get('/atlas/cite/grapheme/U%2B1B002')).json() as any[])[0].URL).toBe('https://glyphatlas.org/character/U+3042?scope=family');
    expect((await (await get('/atlas/cite/form/U+1B002')).json() as any[])[0].title).toBe('𛀂');
    expect((await get('/atlas/cite/page/U%2B1B002')).status).toBe(404);
    expect((await get('/atlas/cite/form/U%2B1B003')).status).toBe(404);
    expect((await get('/atlas/cite/form/not-a-character')).status).toBe(404);
    db.close();
  });
});
