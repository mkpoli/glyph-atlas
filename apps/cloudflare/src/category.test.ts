import { describe, it, expect } from 'bun:test';
import { SAMPLE_RANGE, refiled } from './category';

describe('refiled', () => {
  const high = SAMPLE_RANGE + 1;
  it('refiles copies a decision moved to another script and keeps the rest', () => {
    expect(refiled([
      { id: 'a', shuffle: 1, category: 'kana', character: '仮' },
      { id: 'b', shuffle: 2, category: 'kanji', character: '仮' },
      { id: 'c', shuffle: high, category: 'kana', character: '그' },
    ])).toEqual([
      { sql: 'UPDATE corpus_gallery SET category=? WHERE id=?', values: ['kanji', 'a'] },
      { sql: 'UPDATE corpus_gallery SET category=? WHERE id=?', values: ['hangul', 'c'] },
    ]);
  });
  it('removes a copy only its old script was copied whole for', () => {
    expect(refiled([{ id: 'd', shuffle: high, category: 'hangul', character: 'あ' }]))
      .toEqual([{ sql: 'DELETE FROM corpus_gallery WHERE id=?', values: ['d'] }]);
  });
});
