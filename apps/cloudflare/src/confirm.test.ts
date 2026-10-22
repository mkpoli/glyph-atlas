// "Looks right" in the collection view sends the correction batches the Worker already takes: a line
// correction naming each crop's own character. These read the client's bodies through the route's checks.
import { describe, expect, it } from 'bun:test';
import { judgeBatch, validBatch } from './routes/review';
import { ROUND_MAX } from './rounds';
// @ts-ignore: the review site's module is plain JavaScript.
import { confirmationBatches, confirmationBody } from '../../review/src/lib/corrections.js';

const hash = 'a'.repeat(64), source = 'b'.repeat(64);
const local = (id: string, label: string, extra = {}) => ({ id, label, revision: 0, image_sha256: hash, state: 'pending', ...extra });
const glyph = (id: string, written: string, extra = {}) => ({ id, origin: 'corpus', label: written, written_character: written, revision: 0, source_revision: source, proxyable: true, image: '/g.png', state: 'pending', ...extra });
// `settled` is the revision a reader must have seen: one moved by a review since, not only by marks.
const row = (id: string, kind: number, data: object, revision = 0) => [id, { id, kind, revision, settled: revision, data } as any] as const;

describe('confirming a selection as it is labelled', () => {
  it('passes the route\'s checks, and confirms crops of different characters in one batch', () => {
    const [batch] = confirmationBatches([local('a', 'き'), local('b', 'も', { written_character: 'も' }), glyph('g', 'ぬ')], ROUND_MAX);
    const input = validBatch(structuredClone(confirmationBody(batch)));
    expect(input.line).toBe(true);
    const rows = new Map([row('a', 0, { label: 'き', state: 'pending', image_sha256: hash }), row('b', 0, { label: 'も', written_character: 'も', state: 'flagged', image_sha256: hash }),
      row('g', 1, { written_character: 'ぬ', state: 'pending', source_revision: source, proxyable: true })]);
    const judged = judgeBatch(input, input.crops, rows as any);
    expect(judged.chosen.map(c => [c.id, c.verdict])).toEqual([['a', 'match'], ['b', 'match'], ['g', 'match']]);
    expect(judged.refused).toEqual([]);
  });
  it('reports a crop changed since it was shown or already confirmed, and saves the rest', () => {
    const [batch] = confirmationBatches([local('a', 'き'), local('b', 'も'), glyph('g', 'ぬ'), local('c', 'ね')], ROUND_MAX);
    const input = validBatch(structuredClone(confirmationBody(batch)));
    const rows = new Map([row('a', 0, { label: 'き', state: 'pending', image_sha256: hash }, 2), row('b', 0, { label: 'も', state: 'checked', image_sha256: hash }),
      row('g', 1, { written_character: 'ぬ', state: 'pending', source_revision: 'c'.repeat(64), proxyable: true }), row('c', 0, { label: 'ね', state: 'pending', image_sha256: hash })]);
    const judged = judgeBatch(input, input.crops, rows as any);
    expect(judged.chosen.map(c => c.id)).toEqual(['c']);
    expect(judged.refused).toEqual([{ id: 'a', reason: 'changed' }, { id: 'g', reason: 'changed' }]);
    expect(judged.unchanged).toEqual(['b']);
  });
  it('never sends more crops than one correction takes', () => {
    const batches = confirmationBatches(Array.from({ length: ROUND_MAX + 1 }, (_, i) => local('n' + i, 'き')), ROUND_MAX);
    for (const batch of batches) expect(() => validBatch(structuredClone(confirmationBody(batch)))).not.toThrow();
    expect(batches.map((batch: { crops: unknown[] }) => batch.crops.length)).toEqual([ROUND_MAX, 1]);
  });
});
