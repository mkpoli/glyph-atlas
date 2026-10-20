import { describe, expect, it } from 'bun:test';
import { judgeBatch, validBatch } from './index';

const hash = 'a'.repeat(64);
const data = (id: string, label: string, state = 'pending') => JSON.stringify({ id, label, state, revision: 0, image_sha256: hash, image: `/img/${id}.webp` });

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
