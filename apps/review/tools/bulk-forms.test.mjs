import { describe, expect, it } from 'bun:test'
import { bulkActionPlan, bulkFormChoices, bulkReviewBatches, bulkReviewBody, bulkReviewOutcome, mergeBulkDone, mergeUndoTiles } from '../src/lib/bulkForms.js'
import { ROUND_MAX } from '../../cloudflare/src/rounds.ts'

const crop = (id, extra = {}) => ({ id, label: '說', written_character: '說', revision: 2, image_sha256: 'a'.repeat(64), state: 'pending', ...extra })
const corpusCrop = (id, extra = {}) => ({ id, origin: 'corpus', label: '說', written_character: '說', revision: 1, source_revision: 'b'.repeat(64), proxyable: true, image: '/c.png', state: 'pending', ...extra })

describe('bulk form choices on a character or grapheme page', () => {
  const card = {
    char: '所', code_point: 'U+6240',
    grapheme: { char: '所', code_point: 'U+6240', members: [{ char: '所', code_point: 'U+6240' }] },
    variants: { items: [{ char: '處', code_point: 'U+8655' }], forms: [{ char: '⿰戸斤', form: 'fm:1' }] },
  }
  const listed = {
    char: '所',
    members: [{ char: '所', code_point: 'U+6240' }, { char: '\u{3D307}', code_point: 'U+3D307' }],
    variants: [
      { char: '所\u{E0102}', code_point: 'U+6240 U+E0102', mj: 'MJ012120' },
      { char: '⿰戶斤', code_point: null, encoded: false, regions: 'GHTKPV', lang: 'zh-Hans' },
      { char: '處', code_point: 'U+8655' },
      { char: '⿰戸斤', code_point: null, form: 'fm:1' },
    ],
  }

  it('offers grapheme members, IVD figures, regional shapes, variants and linked forms, with small seals last', () => {
    const choices = bulkFormChoices(listed, card)
    expect(choices.map(choice => choice.char)).toEqual(['所', '所\u{E0102}', '⿰戶斤', '處', '⿰戸斤', '\u{3D307}'])
    expect(choices[1].mj).toBe('MJ012120')
    expect(choices[2].regions).toBe('GHTKPV')
    expect(choices[2].lang).toBe('zh-Hans')
  })

  it('falls back to cardTargets while listForms is loading', () => {
    expect(bulkFormChoices(null, card).map(choice => choice.char)).toEqual(['所', '處', '⿰戸斤'])
    expect(bulkFormChoices(null, null)).toEqual([])
  })

  it('prepends the graphemes a form with no grapheme of its own writes', () => {
    const own = { char: '𠮮', code_point: 'U+20BAE', variants: { own: false, writes: [{ char: '只', code_point: 'U+53EA' }] } }
    const forms = { char: '𠮮', members: [{ char: '𠮮', code_point: 'U+20BAE' }], variants: [] }
    expect(bulkFormChoices(forms, own).map(choice => choice.char)).toEqual(['只', '𠮮'])
  })
})

describe('planning bulk form confirmation and relabelling', () => {
  const shuoFamily = new Map([
    ['說', new Set(['説', '說', '说', '𧧘'])],
    ['説', new Set(['説', '說', '说', '𧧘'])],
    ['所', new Set(['所'])],
    ['後', new Set(['後'])],
    ['あ', new Set(['あ', 'ア', '𛀂'])],
  ])

  it('confirms unchecked crops and claims has_form when choosing the crop\'s own character', () => {
    const [pending, checked] = [crop('a'), crop('b', { state: 'checked' })]
    const plan = bulkActionPlan({ chosen: [pending, checked], target: '說', fromForm: true, families: shuoFamily })
    expect(plan.map(({ reviewCharacter, needsReview, relabel, claimForm }) => ({ reviewCharacter, needsReview, relabel, claimForm }))).toEqual([
      { reviewCharacter: '說', needsReview: true, relabel: false, claimForm: true },
      { reviewCharacter: '說', needsReview: false, relabel: false, claimForm: true },
    ])
  })

  it('relabels and claims has_form for another encoded member of the same grapheme', () => {
    const [a, b] = [crop('a', { label: '説', written_character: '説' }), crop('b', { label: '說', written_character: '說' })]
    const plan = bulkActionPlan({ chosen: [a, b], target: '說', fromForm: true, families: shuoFamily })
    expect(plan.map(({ reviewCharacter, needsReview, relabel, claimForm }) => ({ reviewCharacter, needsReview, relabel, claimForm }))).toEqual([
      { reviewCharacter: '說', needsReview: true, relabel: true, claimForm: true },
      { reviewCharacter: '說', needsReview: true, relabel: false, claimForm: true },
    ])
  })

  it('assigns and claims has_form for an unassigned crop on its grapheme page', () => {
    const picked = { char: '説', code_point: 'U+8AAC', grapheme: { code_point: 'U+8AAC', members: [{ char: '説' }, { char: '說' }] } }
    const unassigned = corpusCrop('u1', { identity_status: 'unassigned', written_character: null, label: 'Unassigned', grapheme: 'U+8AAC' })
    const [entry] = bulkActionPlan({ chosen: [unassigned], target: '說', fromForm: true, families: shuoFamily, picked, pickedFamily: new Set(['説', '說']) })
    expect(entry).toMatchObject({ reviewCharacter: '說', needsReview: true, relabel: true, claimForm: true })
  })

  it('keeps each crop\'s character, confirms unchecked ones and claims has_form for IVD figures and IDS', () => {
    const [a, b, unassigned] = [
      crop('a', { label: '所', written_character: '所' }),
      crop('b', { label: '所', written_character: '所', state: 'checked' }),
      corpusCrop('c', { identity_status: 'unassigned', written_character: null, label: 'Unassigned' }),
    ]
    for (const target of ['所\u{E0102}', '⿰戶斤', '⿰言兊']) {
      const plan = bulkActionPlan({ chosen: [a, b, unassigned], target, fromForm: false, families: shuoFamily })
      expect(plan.map(({ reviewCharacter, needsReview, relabel, claimForm }) => ({ reviewCharacter, needsReview, relabel, claimForm }))).toEqual([
        { reviewCharacter: '所', needsReview: true, relabel: false, claimForm: true },
        { reviewCharacter: '所', needsReview: false, relabel: false, claimForm: true },
        { reviewCharacter: null, needsReview: false, relabel: false, claimForm: true },
      ])
    }
  })

  it('keeps character, confirms and claims has_form for an attested variant chip outside the grapheme', () => {
    const [a, b] = [crop('a', { label: '後', written_character: '後' }), crop('b', { label: '後', written_character: '後', state: 'checked' })]
    const plan = bulkActionPlan({ chosen: [a, b], target: '后', fromForm: true, families: shuoFamily })
    expect(plan.map(({ reviewCharacter, needsReview, relabel, claimForm }) => ({ reviewCharacter, needsReview, relabel, claimForm }))).toEqual([
      { reviewCharacter: '後', needsReview: true, relabel: false, claimForm: true },
      { reviewCharacter: '後', needsReview: false, relabel: false, claimForm: true },
    ])
  })

  it('relabels without claiming a fine form when fixing a misreading via character search', () => {
    const [a, b] = [crop('a', { label: 'あ', written_character: 'あ' }), crop('b', { label: 'お', written_character: 'お', state: 'checked' })]
    const plan = bulkActionPlan({ chosen: [a, b], target: 'お', fromForm: false, families: shuoFamily })
    expect(plan.map(({ reviewCharacter, needsReview, relabel, claimForm }) => ({ reviewCharacter, needsReview, relabel, claimForm }))).toEqual([
      { reviewCharacter: 'お', needsReview: true, relabel: true, claimForm: false },
      { reviewCharacter: 'お', needsReview: false, relabel: false, claimForm: true },
    ])
  })
})

describe('batching and reading bulk review outcomes', () => {
  it('builds line: true request bodies with each crop\'s resolved character and pixels', () => {
    const plan = bulkActionPlan({
      chosen: [crop('a', { label: '説', written_character: '説' }), corpusCrop('g', { label: '說', written_character: '說' })],
      target: '說',
      fromForm: true,
      families: new Map([['説', new Set(['説', '說'])], ['說', new Set(['説', '說'])]]),
    })
    const [batch] = bulkReviewBatches(plan, '說', true, ROUND_MAX)
    expect(bulkReviewBody(batch)).toEqual({
      id: batch.id,
      line: true,
      crops: [
        { id: 'a', revision: 2, character: '說', image_sha256: 'a'.repeat(64) },
        { id: 'g', revision: 1, character: '說', source_revision: 'b'.repeat(64) },
      ],
    })
  })

  it('separates saved, unchanged, skipped and flagged crops and records only batches that wrote', () => {
    const batch1 = { id: 'b1', crops: [{ id: 'a' }, { id: 'b' }, { id: 'c' }, { id: 'd' }] }
    const batch2 = { id: 'b2', crops: [{ id: 'e' }] }
    const outcome = bulkReviewOutcome([
      {
        item: batch1,
        result: {
          id: 'b1',
          unchanged: ['b'],
          skipped: [{ id: 'c', reason: 'checked' }],
          results: [
            { target_id: 'a', revision: 3, state: 'checked' },
            { target_id: 'd', revision: 4, state: 'flagged' },
          ],
        },
      },
      { item: batch2, result: { id: 'b2', unchanged: ['e'], skipped: [], results: [] } },
    ])
    expect(Object.fromEntries(outcome.saved)).toEqual({ a: { revision: 3, state: 'checked' }, d: { revision: 4, state: 'flagged' } })
    expect([...outcome.unchanged]).toEqual(['b', 'e'])
    expect(Object.fromEntries(outcome.skipped)).toEqual({ c: 'checked' })
    expect(outcome.flagged).toEqual([{ id: 'd', reason: 'flagged' }])
    expect(outcome.batches).toEqual(['b1'])
  })

  it('matches results by target_id when present so missing skipped entries cannot shift saved crops', () => {
    const batch = { id: 'b1', crops: [{ id: 'a' }, { id: 'b' }] }
    const outcome = bulkReviewOutcome([
      { item: batch, result: { id: 'b1', unchanged: [], skipped: [], results: [{ target_id: 'b', revision: 5, state: 'checked' }] } },
    ])
    expect(Object.fromEntries(outcome.saved)).toEqual({ b: { revision: 5, state: 'checked' } })
    expect(Object.fromEntries(outcome.skipped)).toEqual({ a: 'changed' })
  })
})

describe('failure-recovery helpers for bulk apply and undo', () => {
  it('preserves earlier review batches when a failed form save is retried on the same crops', () => {
    const origA = crop('a', { state: 'pending', form: null })
    const checkedA = crop('a', { state: 'checked', revision: 3, form: null })
    const first = {
      kind: 'correct', batches: ['b1'], described: [], count: 1, char: '所\u{E0103}', lang: '',
      before: { a: origA }, reviewBefore: { a: origA }, kept: 0,
    }
    const retry = {
      kind: 'correct', batches: [], described: [{ crop: { id: 'a', form: { text: '所\u{E0103}' } }, replaced: null }],
      count: 1, char: '所\u{E0103}', lang: '',
      before: { a: checkedA }, reviewBefore: {}, kept: 0,
    }
    const merged = mergeBulkDone(first, retry, ['a'])
    expect(merged.batches).toEqual(['b1'])
    expect(merged.described.map(d => d.crop.id)).toEqual(['a'])
    expect(merged.before.a).toBe(origA)
    expect(merged.reviewBefore.a).toBe(origA)
    expect(merged.count).toBe(1)

    // A fresh selection of unrelated crops does not inherit the earlier undo record.
    const fresh = mergeBulkDone(first, { ...retry, before: { z: crop('z') } }, ['z'])
    expect(fresh.batches).toEqual([])
  })

  it('keeps the live saved form on crops whose form undo failed after review undo succeeded', () => {
    const savedForm = { text: '所\u{E0103}' }
    const oldForm = { text: '所' }
    const restored = {
      a: crop('a', { state: 'pending', revision: 4, form: oldForm }),
      b: crop('b', { state: 'pending', revision: 4, form: oldForm }),
    }
    const live = [
      crop('a', { state: 'checked', revision: 3, form: savedForm }),
      crop('b', { state: 'checked', revision: 3, form: savedForm }),
    ]
    const merged = mergeUndoTiles({
      restored,
      unmarked: [{ crop: { id: 'a', form: oldForm } }],
      formTiles: { a: crop('a', { form: oldForm }) },
      stillClaimed: new Set(['b']),
      live,
    })
    expect(merged.a.form).toEqual(oldForm)
    expect(merged.b.form).toEqual(savedForm)
  })
})
