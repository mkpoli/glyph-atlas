import { describe, it, expect } from 'bun:test';
import { canonical, samePixels, literal, hira, single, readingFrom, validRound, categoryOf,
  encodeCursor, decodeCursor, historyItem, historyQuery, moved } from './index';
import { ROUND_MAX } from './rounds';
import { componentTerm, rankMatches } from './components';

describe('historical character identities', () => {
  it('keeps supplementary characters intact', () => {
    expect(literal('U+2A708')).toBe('𪜈');
    expect(single('𪜈')).toBe(true);
    expect(single('トモ')).toBe(false);
  });
  it('normalizes combining kana without rewriting written forms', () => {
    expect(literal('か\u3099')).toBe('が');
    expect(hira('トモ')).toBe('とも');
    expect(literal('假')).toBe('假');
    expect(literal('仮')).toBe('仮');
  });
  it('canonicalizes idempotency payloads independently of object key order', () => {
    expect(canonical({ id:'one', answers:[{ issue:'merged', correction:'トモ' }] }))
      .toBe(canonical({ answers:[{ correction:'トモ', issue:'merged' }], id:'one' }));
    expect(canonical({ character:'ム' })).not.toBe(canonical({ character:'厶' }));
  });
});

it('a compatibility ideograph stays the character it is, and a voiced kana still composes', () => {
  expect(literal('U+FA30')).toBe('侮')
  expect(literal('侮')).toBe('侮')
  expect(literal('が')).toBe('が')
})

it('a corrected character carries the reading the character layer states', () => {
  expect(readingFrom({ char: 'り', script: 'hiragana', readings: ['り'] })).toBe('り')
  expect(readingFrom({ char: '𪜈', script: 'han', readings: [], ligature: { reading: 'トモ' } })).toBe('とも')
  expect(readingFrom({ char: '国', script: 'han', readings: [] })).toBe('国')
  expect(readingFrom({ char: '𛄝', script: 'hentaigana', readings: ['ん', 'む', 'も'] })).toBe(null)
})

describe('a round names flagged answers, seen crops, or both', () => {
  const hash = 'a'.repeat(64)
  it('accepts a round that only records seen crops', () => {
    expect(validRound({ seen: [{ id: 'one', image_sha256: hash }] }).seen).toHaveLength(1)
    expect(validRound({ seen: [{ id: 'one', image_sha256: hash }] }).answers).toHaveLength(0)
  })
  it('accepts a corpus glyph whose image is the long file address of its source', () => {
    const hng = 'https://raw.githubusercontent.com/chise/hng-basic-data/e2174a30844b8100c34af1c0dbe1e301f186883e/77_%E5%9B%9B%E5%88%86%E5%BE%8B%E5%8D%B7%E7%AC%AC%E5%8D%81%E5%85%AD%28%E6%AD%A3%E5%80%89%E9%99%A2%E4%BA%94%E6%9C%88%E4%B8%80%E6%97%A5%E7%B6%93%29/glyphs/BMP/0122.bmp'
    expect(hng.length).toBeGreaterThan(256)
    expect(validRound({ seen: [{ id: 'hng:1', source_revision: hash, image: hng }] }).seen).toHaveLength(1)
    expect(() => validRound({ seen: [{ id: 'hng:1', source_revision: hash, image: 'x'.repeat(2049) }] })).toThrow('crop image')
  })
  it('accepts a round of 144 crops', () => {
    expect(ROUND_MAX).toBe(144)
    const full = Array.from({ length: ROUND_MAX }, (_, i) => ({ id: `u${i}`, image_sha256: hash }))
    expect(validRound({ seen: full }).seen).toHaveLength(ROUND_MAX)
  })
  it('refuses an empty round, a crop named twice, or more than 144 crops', () => {
    expect(() => validRound({ answers: [], seen: [] })).toThrow('1–144')
    expect(() => validRound({ answers: [{ id: 'one' }], seen: [{ id: 'one', image_sha256: hash }] })).toThrow('1–144')
    const many = Array.from({ length: ROUND_MAX + 1 }, (_, i) => ({ id: `u${i}`, image_sha256: hash }))
    expect(() => validRound({ seen: many })).toThrow('1–144')
  })
  it('names a corpus glyph by its source revision', () => {
    expect(validRound({ skipped: [{ id: 'codh:1', source_revision: hash }] }).skipped).toHaveLength(1)
    expect(() => validRound({ seen: [{ id: 'codh:1', source_revision: 'nope' }] })).toThrow('image hash')
  })
  it('refuses a seen crop without an image hash, and seen crops outside a round', () => {
    expect(() => validRound({ seen: [{ id: 'one', image_sha256: 'nope' }] })).toThrow('image hash')
    expect(() => validRound({ seen: [{ id: 'one', image_sha256: hash }] }, 'one')).toThrow('Only a round')
  })
})

describe('categoryOf', () => {
  it('names a label by the script of its first character', () => {
    expect(['ア', '仮', 'ㅿ', 'ᄫ', '한', '', 'A', ''].map(categoryOf)).toEqual(['kana', 'kanji', 'hangul', 'hangul', 'hangul', 'gugyeol', 'other', 'other']);
  });
});

describe('history cursor', () => {
  it('round-trips at and id, and rejects a cursor that is not one', () => {
    const cursor = encodeCursor('2026-01-02T03:04:05.000Z', 'cf:one');
    expect(decodeCursor(cursor)).toEqual({ at: '2026-01-02T03:04:05.000Z', id: 'cf:one' });
    expect(() => decodeCursor('not-base64!!')).toThrow('Invalid cursor.');
    expect(() => decodeCursor(btoa(JSON.stringify(['only-one'])))).toThrow('Invalid cursor.');
    expect(() => decodeCursor(btoa(JSON.stringify([1, 'cf:one'])))).toThrow('Invalid cursor.');
  });
});

describe('historyQuery', () => {
  it('filters by kind and orders newest first, keyset-paged', () => {
    const { sql, values } = historyQuery(null, null, null);
    expect(sql).toContain(`kind IN ('review','undo')`);
    expect(sql).toContain('ORDER BY at DESC,id DESC LIMIT ?1');
    expect(values).toEqual([]);
  });
  it('binds the label and cursor once, then one id per arm, with the LIMIT after them', () => {
    const { sql, values } = historyQuery(['u1', 'reviewer-0a1b2c3d'], 'ア', { at: '2026-01-02T00:00:00.000Z', id: 'cf:one' });
    expect(sql).toContain('(at,id)<(?2,?3) AND actor=?4 UNION ALL SELECT');
    expect(sql).toContain('(at,id)<(?2,?3) AND actor=?5');
    expect(sql).toContain('LIMIT ?6');
    expect(values).toEqual(['ア', '2026-01-02T00:00:00.000Z', 'cf:one', 'u1', 'reviewer-0a1b2c3d']);
  });
});

describe('historyItem', () => {
  it('maps a review row, converting a suggested code point to its character', () => {
    const event = JSON.stringify({ id: 'cf:e1', target_type: 'unit', target_id: 'one', field: 'review',
      old: 'machine', new: 'reviewed', role: 'reviewer', actor: 'alice', at: '2026-01-02T00:00:00.000Z',
      evidence: JSON.stringify({ kind: 'character-review', verdict: 'wrong', issue: 'character',
        suggested_character: 'U+30D7', suggested_reading: 'ぷ', round: null }) });
    expect(historyItem({ id: 'cf:e1', at: '2026-01-02T00:00:00.000Z', actor: 'reviewer-0a1b2c3d', target: 'one',
      kind: 'review', event, label: 'ア', user: 'u1', name: 'Alice' }, 'u1')).toEqual({
      id: 'cf:e1', at: '2026-01-02T00:00:00.000Z', target: 'one', label: 'ア', kind: 'review',
      reviewer: { user: 'u1', name: 'Alice', mine: true },
      verdict: 'wrong', issue: 'character', character: 'プ', reading: 'ぷ', round: null, batch: null, undoes: null,
    });
  });
  it('maps an undo row, naming the event it reverses and leaving the review fields null', () => {
    const event = JSON.stringify({ id: 'cf:e2', target_type: 'unit', target_id: 'one', field: 'review',
      old: 'reviewed', new: 'machine', role: 'reviewer', actor: 'alice', at: '2026-01-03T00:00:00.000Z',
      evidence: 'undo of cf:e1' });
    expect(historyItem({ id: 'cf:e2', at: '2026-01-03T00:00:00.000Z', actor: 'reviewer-0a1b2c3d', target: 'one',
      kind: 'undo', event, label: null, user: null, name: null }, 'u1')).toEqual({
      id: 'cf:e2', at: '2026-01-03T00:00:00.000Z', target: 'one', label: null, kind: 'undo',
      reviewer: { user: null, name: 'reviewer-0a1b2c3d', mine: false },
      verdict: null, issue: null, character: null, reading: null, round: null, batch: null, undoes: 'cf:e1',
    });
  });
  it('carries a round id when the review came from a visual quiz round', () => {
    const event = JSON.stringify({ id: 'cf:e3', target_type: 'unit', target_id: 'two', field: 'review',
      old: 'machine', new: 'reviewed', role: 'reviewer', actor: 'bob', at: '2026-01-04T00:00:00.000Z',
      evidence: JSON.stringify({ kind: 'visual-quiz', round: 'round-1', label: 'ア', verdict: 'match', issue: null }) });
    expect(historyItem({ id: 'cf:e3', at: '2026-01-04T00:00:00.000Z', actor: 'bob', target: 'two',
      kind: 'review', event, label: 'ア', user: 'u2', name: 'Bob' }).round).toBe('round-1');
  });
});

describe('samePixels', () => {
  const hash = 'a'.repeat(64);
  it('names a hashed crop by its page hash', () => {
    expect(samePixels(new URLSearchParams({ image_sha256: hash }), 'local', { image_sha256: hash })).toBe(true);
    expect(samePixels(new URLSearchParams({ image_sha256: 'b'.repeat(64) }), 'local', { image_sha256: hash })).toBe(false);
  });
  it('names a crop published without a hash by its image', () => {
    const image = 'https://gallica.bnf.fr/iiif/ark:/12148/btv1b83018108/f7/1259,1470,91,98/480,/0/default.jpg';
    expect(samePixels(new URLSearchParams({ image }), 'local', { image_sha256: null, image })).toBe(true);
    expect(samePixels(new URLSearchParams({ image_sha256: 'null' }), 'local', { image_sha256: null, image })).toBe(false);
    expect(samePixels(new URLSearchParams({ image: image + '?v=2' }), 'local', { image })).toBe(false);
  });
  it('names a corpus glyph by its source revision', () => {
    expect(samePixels(new URLSearchParams({ source_revision: 'r1' }), 'corpus', { source_revision: 'r1' })).toBe(true);
  });
});

it('moves crops counted apart out of pending, and never more than pending holds', () => {
  const row = (label: string, document: string | null, state: string, n: number) => ({ label, family: null, document, title: document && 'T', state, n })
  expect(moved([row('ア', 'd', 'pending', 5), row('ア', 'd', 'checked', 1), row('イ', null, 'pending', 2)],
    [row('ア', 'd', 'seen', 2), row('イ', null, 'hard', 2), row('ウ', null, 'seen', 1), row('ア', 'd', 'skipped', 9)]))
    .toEqual([row('ア', 'd', 'checked', 1), row('ア', 'd', 'seen', 2), row('イ', null, 'hard', 2), row('ア', 'd', 'skipped', 3)])
})

describe('a search of ideographs asks for the characters built from them', () => {
  it('reads two or more ideographs, radicals or strokes as components, counted', () => {
    expect(componentTerm('水骨')).toEqual(new Map([['水', 1], ['骨', 1]]))
    expect(componentTerm('⽔ 骨')).toEqual(new Map([['水', 1], ['骨', 1]]))
    expect(componentTerm('木木木')).toEqual(new Map([['木', 3]]))
    expect(componentTerm('⺡㇆')).toEqual(new Map([['⺡', 1], ['㇆', 1]]))
  })
  it('leaves one character, kana and long terms to the other searches', () => {
    expect(componentTerm('骨')).toBe(null)
    expect(componentTerm('とも')).toBe(null)
    expect(componentTerm('水a')).toBe(null)
    expect(componentTerm('一二三四五六七八九')).toBe(null)
  })
  it('ranks top-level parts, then everyday characters, then simpler ones', () => {
    const rows = [{ code_point: 'U+80C4', tier: 0, size: 4, direct: 1 }, { code_point: 'U+2493C', tier: 2, size: 3, direct: 2 },
      { code_point: 'U+660E', tier: 0, size: 2, direct: 2 }, { code_point: 'U+3B0C', tier: 1, size: 2, direct: 2 }]
    expect(rankMatches(rows).map(r => r.code_point)).toEqual(['U+660E', 'U+3B0C', 'U+2493C', 'U+80C4'])
  })
})
