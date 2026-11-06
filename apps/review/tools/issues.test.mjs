import { describe, expect, it, mock } from 'bun:test'

mock.module('../src/lib/i18n.svelte.js', () => ({ t: key => key }))
const { addsIssue, issueFields, toggleIssue } = await import('../src/lib/issues.js')

describe('the problem cards', () => {
  it('adds a bad crop beside a wrong or joined character, in either order', () => {
    expect(toggleIssue(['character'], 'crop')).toEqual(['character', 'crop'])
    expect(toggleIssue(['crop'], 'character')).toEqual(['character', 'crop'])
    expect(toggleIssue(['crop'], 'merged')).toEqual(['merged', 'crop'])
  })
  it('replaces one answer about what the crop holds with another, and keeps the bad crop', () => {
    expect(toggleIssue(['character'], 'merged')).toEqual(['merged'])
    expect(toggleIssue(['character', 'crop'], 'merged')).toEqual(['merged', 'crop'])
  })
  it('lets a blank stand alone', () => {
    expect(toggleIssue(['character', 'crop'], 'blank')).toEqual(['blank'])
    expect(toggleIssue(['blank'], 'crop')).toEqual(['crop'])
  })
  it('takes back a card pressed again', () => {
    expect(toggleIssue(['character', 'crop'], 'crop')).toEqual(['character'])
    expect(toggleIssue(['character', 'crop'], 'character')).toEqual(['crop'])
    expect(toggleIssue(['blank'], 'blank')).toEqual([])
  })
  it('marks only the cards that would add', () => {
    expect(['character', 'merged', 'crop', 'blank'].filter(id => addsIssue([], id))).toEqual([])
    expect(['character', 'merged', 'crop', 'blank'].filter(id => addsIssue(['character'], id))).toEqual(['crop'])
    expect(['character', 'merged', 'crop', 'blank'].filter(id => addsIssue(['crop'], id))).toEqual(['character', 'merged'])
    expect(['character', 'merged', 'crop', 'blank'].filter(id => addsIssue(['blank'], id))).toEqual([])
    expect(['character', 'merged', 'crop', 'blank'].filter(id => addsIssue(['character', 'crop'], id))).toEqual([])
  })
  it('says a bad crop only beside another problem', () => {
    expect(issueFields(['character', 'crop'])).toEqual({ issue: 'character', bad_crop: true })
    expect(issueFields(['crop'])).toEqual({ issue: 'crop' })
    expect(issueFields([])).toEqual({ issue: null })
  })
})
