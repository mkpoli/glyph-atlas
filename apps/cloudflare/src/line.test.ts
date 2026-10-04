import { describe, expect, it } from 'bun:test';
import { Database } from 'bun:sqlite';
import { readdirSync, readFileSync } from 'node:fs';
import { judgeBatch, lineItems, lineQuery, validBatch } from './index';

const hash = 'a'.repeat(64);
function migrated() {
  const db = new Database(':memory:');
  const migrations = new URL('../migrations/', import.meta.url);
  for (const file of readdirSync(migrations).filter(f => f.endsWith('.sql')).sort()) db.exec(readFileSync(new URL(file, migrations), 'utf8'));
  return db;
}
const data = (id: string, label: string, state = 'pending') => JSON.stringify({ id, label, state, revision: 0, image_sha256: hash, image: `/img/${id}.webp` });
// A line of crops c0..c(n-1), each followed by the next as a pair, in reading order.
function line(db: Database, labels: string[], other = 'x') {
  const unit = db.prepare(`INSERT INTO units(id,origin,character,production,category,state,revision,quiz,priority,shuffle,data,snapshot,context,visual) VALUES(?,'local',?,'handwritten','han','pending',0,1,1,0,?,'{}','{}','{}')`);
  const pair = db.prepare("INSERT INTO unit_ngrams(first,size,second,text,document) VALUES(?,2,?,?,'d')");
  labels.forEach((label, i) => unit.run(`${other}${i}`, label, data(`${other}${i}`, label)));
  for (let i = 0; i + 1 < labels.length; i++) pair.run(`${other}${i}`, `${other}${i + 1}`, labels[i] + labels[i + 1]);
}

describe('a crop\'s line', () => {
  it('reads four crops either side in reading order, and stops at the line\'s ends', () => {
    const db = migrated();
    line(db, [...'あいうえおかきくけこ']);
    const read = (id: string) => lineItems(db.query(lineQuery()).all(id) as any).map(i => [i.offset, i.label]);
    expect(read('x5')).toEqual([[-4, 'い'], [-3, 'う'], [-2, 'え'], [-1, 'お'], [0, 'か'], [1, 'き'], [2, 'く'], [3, 'け'], [4, 'こ']]);
    expect(read('x1')).toEqual([[-1, 'あ'], [0, 'い'], [1, 'う'], [2, 'え'], [3, 'お'], [4, 'か']]);
    expect(read('x9').at(-1)).toEqual([0, 'こ']);
    db.close();
  });
  it('is empty for a crop with no neighbour, and for one the collection does not hold', () => {
    const db = migrated();
    line(db, ['あ']);
    line(db, [...'いう'], 'y');
    expect(lineItems(db.query(lineQuery()).all('x0') as any)).toEqual([]);
    expect(lineItems(db.query(lineQuery()).all('nope') as any)).toEqual([]);
    expect(lineItems(db.query(lineQuery()).all('y0') as any)).toHaveLength(2);
    db.close();
  });
  it('carries what the review path judges a crop by', () => {
    const db = migrated();
    line(db, [...'あい']);
    expect(lineItems(db.query(lineQuery()).all('x0') as any)[1]).toEqual({ id: 'x1', offset: 1, label: 'い', image: '/img/x1.webp',
      revision: 0, image_sha256: hash, state: 'pending', issue: null });
    db.close();
  });
  it('is served by keys: no table scan and no sort, and fails once the backward index is gone', () => {
    const db = migrated();
    const plan = () => { const q = db.query(`EXPLAIN QUERY PLAN ${lineQuery()}`); const rows = (q.all('x0') as { detail: string }[]).map(r => r.detail); q.finalize(); return rows };
    // Only the walk's own steps may be scanned: the table behind `p` or `u` never.
    const bad = (rows: string[]) => rows.filter(d => /TEMP B-TREE|AUTOMATIC/.test(d) || (/^SCAN /.test(d) && !/^SCAN (CONSTANT ROW|ahead|behind|a|b|n)$/.test(d)));
    expect(bad(plan())).toEqual([]);
    expect(plan().some(d => d.includes('unit_ngram_second'))).toBe(true);
    db.exec('DROP INDEX unit_ngram_second');
    expect(bad(plan())).not.toEqual([]);
    db.close();
  });
});

describe('a line\'s correction', () => {
  const crop = (id: string, character?: string, extra = {}) => ({ id, revision: 0, image_sha256: hash, ...(character ? { character } : {}), ...extra });
  const row = (id: string, label: string, state = 'pending') => [id, { id, origin: 'local', data: data(id, label, state) } as any] as const;
  it('names a character for each crop, or a box as no character', () => {
    const ok = validBatch({ id: 'i', line: true, crops: [crop('a', 'き'), crop('b', undefined, { issue: 'blank' })] });
    expect(ok.line).toBe(true);
    expect(ok.character).toBeNull();
    const refused = (input: object) => { try { validBatch(input as any); return null } catch (e: any) { return e.message } };
    expect(refused({ line: true, character: 'き', crops: [crop('a', 'も')] })).toContain('each crop');
    expect(refused({ line: true, crops: [crop('a')] })).toContain('character');
    expect(refused({ line: true, crops: [crop('a', 'も', { issue: 'blank' })] })).toContain('no character');
    expect(refused({ line: true, crops: [crop('a', undefined, { issue: 'other' })] })).toContain('no character');
    expect(refused({ character: 'き', crops: [crop('a', 'も')] })).toContain('Only a line');
    expect(refused({ line: 'yes', character: 'き', crops: [crop('a')] })).toContain('line correction');
    expect(validBatch({ character: 'き', crops: [crop('a')] }).line).toBe(false);
  });
  it('lets a crop carry its redrawn box beside its label, and never a blank or a one-character batch', () => {
    const box = { x: 1, y: 2, w: 30, h: 40 };
    const ok = validBatch({ line: true, crops: [crop('a', 'き', { box })] });
    expect(ok.crops[0].box).toEqual(box);
    const refused = (input: object) => { try { validBatch(input as any); return null } catch (e: any) { return e.message } };
    expect(refused({ line: true, crops: [crop('a', undefined, { issue: 'blank', box })] })).toContain('cannot redraw');
    expect(refused({ line: true, crops: [crop('a', 'き', { box: null })] })).toContain('cannot redraw');
    expect(refused({ character: 'き', crops: [crop('a', undefined, { box })] })).toContain('cannot redraw');
    const rows = new Map([row('a', 'も'), row('b', 'き')]);
    const judged = judgeBatch({ character: null, line: true }, [crop('a', 'き', { box }), crop('b', 'き', { box })], rows as any);
    expect(judged.chosen.map(c => [c.id, c.verdict, c.character, c.box])).toEqual([['a', 'wrong', 'き', box], ['b', 'match', undefined, box]]);
  });
  it('writes the crops that stand, and reports the ones changed or checked meanwhile', () => {
    const rows = new Map([row('a', 'も'), row('b', 'き'), row('c', 'も', 'checked'), row('d', 'ぬ'), row('e', 'み', 'checked')]);
    const crops = [crop('a', 'き'), crop('b', 'き'), crop('c', 'ね'), { ...crop('d', 'ね'), revision: 3 }, crop('e', 'み')];
    const judged = judgeBatch({ character: null, line: true }, crops, rows as any);
    expect(judged.chosen.map(c => [c.id, c.verdict, c.character])).toEqual([['a', 'wrong', 'き'], ['b', 'match', undefined]]);
    expect(judged.refused).toEqual([{ id: 'c', reason: 'checked' }, { id: 'd', reason: 'changed' }]);
    expect(judged.unchanged).toEqual(['e']);
  });
  it('marks a box as no character unless a person already checked it', () => {
    const rows = new Map([row('a', 'も'), row('b', 'き', 'checked')]);
    const judged = judgeBatch({ character: null, line: true }, [crop('a', undefined, { issue: 'blank' }), crop('b', undefined, { issue: 'blank' })], rows as any);
    expect(judged.chosen.map(c => [c.id, c.verdict, c.issue])).toEqual([['a', 'wrong', 'blank']]);
    expect(judged.refused).toEqual([{ id: 'b', reason: 'checked' }]);
  });
  it('still holds a one-character batch to its own crops', () => {
    const rows = new Map([row('a', 'も'), row('b', 'き', 'checked')]);
    const judged = judgeBatch({ character: 'き', line: false }, [crop('a'), crop('b')], rows as any);
    expect(judged.chosen.map(c => [c.id, c.verdict])).toEqual([['a', 'wrong']]);
    expect(judged.unchanged).toEqual(['b']);
  });
});
