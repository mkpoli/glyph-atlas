// What Back returns to (`lib/keptViews.js`): a view is kept per history entry, read only on Back or
// Forward, and dropped by any write.
import { beforeEach, describe, expect, it } from 'bun:test'
import { keepView, keptView, noteNavigation, noteWrite } from '../src/lib/keptViews.js'

const at = index => { globalThis.history = { state: { 'sveltekit:history': index } } }

describe('kept views', () => {
  beforeEach(() => { noteWrite(); noteNavigation('link') })
  it('answers only a Back or Forward to the entry it was kept under', () => {
    at(1); keepView([1], { seed: 1 })
    at(2); keepView([2], { seed: 2 })
    at(1); expect(keptView()).toBeNull()
    noteNavigation('popstate')
    expect(keptView()).toEqual({ seed: 1 })
    at(2); expect(keptView()).toEqual({ seed: 2 })
    at(3); expect(keptView()).toBeNull()
  })
  it('keeps one view under each entry it names, and none under a missing one', () => {
    keepView([4, 5, null], { seed: 4 })
    noteNavigation('popstate')
    at(4); expect(keptView()).toEqual({ seed: 4 })
    at(5); expect(keptView()).toEqual({ seed: 4 })
  })
  it('forgets every view once anything is written', () => {
    at(1); keepView([1], { seed: 1 })
    noteWrite(); noteNavigation('popstate')
    expect(keptView()).toBeNull()
  })
  it('holds a handful of views, dropping the oldest', () => {
    for (let i = 0; i < 10; i++) keepView([i], { seed: i })
    noteNavigation('popstate')
    at(0); expect(keptView()).toBeNull()
    at(9); expect(keptView()).toEqual({ seed: 9 })
  })
})

describe('kept views after going on from an entry', () => {
  beforeEach(() => { noteWrite(); noteNavigation('link') })
  it('forgets the entries past the one a link reaches, whose numbers it gives again', async () => {
    const { noteArrival } = await import('../src/lib/keptViews.js')
    at(1); keepView([1], { seed: 1 })
    at(2); keepView([2], { seed: 2 })
    at(2); noteArrival('link')
    noteNavigation('popstate')
    at(2); expect(keptView()).toBeNull()
    at(1); expect(keptView()).toEqual({ seed: 1 })
  })
  it('keeps them on Back or Forward', async () => {
    const { noteArrival } = await import('../src/lib/keptViews.js')
    at(2); keepView([2], { seed: 2 })
    at(1); noteArrival('popstate')
    noteNavigation('popstate')
    at(2); expect(keptView()).toEqual({ seed: 2 })
  })
})

describe('leaving with nothing to keep', () => {
  it('drops the view kept earlier for that entry', () => {
    noteWrite(); noteNavigation('link')
    at(3); keepView([3], { seed: 3 }); keepView([3], null)
    noteNavigation('popstate')
    expect(keptView()).toBeNull()
  })
})
