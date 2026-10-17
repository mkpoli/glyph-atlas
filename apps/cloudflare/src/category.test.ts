import { describe, it, expect } from 'bun:test';
import { refiled } from './category';

describe('refiled', () => {
  it('refiles copies a decision moved to another script and keeps the rest', () => {
    expect(refiled([
      { id: 'a', category: 'kana', character: '仮' },
      { id: 'b', category: 'kanji', character: '仮' },
      { id: 'c', category: 'kana', character: '그' },
    ])).toEqual([
      { sql: 'UPDATE corpus_gallery SET category=? WHERE id=?', values: ['kanji', 'a'] },
      { sql: 'UPDATE corpus_gallery SET category=? WHERE id=?', values: ['hangul', 'c'] },
    ]);
  });
  it('keeps a copy only its old script was copied whole for, so an undo files it back', () => {
    expect(refiled([{ id: 'd', category: 'hangul', character: 'あ' }]))
      .toEqual([{ sql: 'UPDATE corpus_gallery SET category=? WHERE id=?', values: ['kana', 'd'] }]);
  });
});
