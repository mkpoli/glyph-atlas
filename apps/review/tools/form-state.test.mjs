import { afterAll, describe, expect, mock, test } from 'bun:test'

// The helpers are pure once the form lists are mocked: they only read the `form` slot of a record.
mock.module('../src/lib/client.js', () => ({ request: async () => ({}), character: async () => ({}), corpusCharacter: async () => ({}) }))
mock.module('../src/lib/layers.js', () => ({ character: async () => null, ids: async () => ({}), graphemeForms: async () => ({ members: [], variants: [], related: [], derived: [], forms: [] }) }))
mock.module('../src/lib/mjFigures.js', () => ({ FAMILY: 'MJ Figures', isFigure: () => false, drawFigure: async () => {}, figuresOf: async () => [] }))
mock.module('../src/lib/i18n.svelte.js', () => ({ t: key => key === 'written.unknownVariant' ? 'Unknown variant' : key,
  withText: (key, name, params) => ({ before: '', text: params[name], after: '' }) }))
mock.module('../src/lib/dating.js', () => ({ datingLine: () => null }))
mock.module('../src/components/ProductionBadge.svelte', () => ({ productionLabel: () => null }))
const { formOf, formState, formStates } = await import('../src/lib/cropForms.js')
const { cropDetails } = await import('../src/lib/cropDetails.js')
afterAll(() => mock.restore())

const form = (values, status = 'asserted') => ({ status, values })
const named = text => ({ form: `fm:${text}`, scheme: 'unicode', text, confidence: null })

describe('the form slot as the reader sees it', () => {
  test('a sole unknown state is the slot state, and is not shown as a form', () => {
    const slot = form([{ state: 'unresolved', confidence: null }])
    expect(formStates(slot)).toEqual(['unresolved'])
    expect(formState(slot)).toBe('unresolved')
    expect(formOf({ label: '取', form: slot })).toBe('')
  })
  test('a disputed state does not choose a side', () => {
    const slot = form([{ state: 'unresolved' }, named('取')], 'disputed')
    expect(formStates(slot)).toEqual(['unresolved'])
    expect(formState(slot)).toBeNull()
    expect(formOf({ label: '取', form: slot })).toBe('')
  })
  test('an alternative set holds no single state, so the reader can choose', () => {
    const slot = form([named('取'), { state: 'unresolved' }])
    expect(formStates(slot)).toEqual(['unresolved'])
    expect(formState(slot)).toBeNull()
    expect(formOf({ label: '取', form: slot })).toBe('')
  })
  test('a named form remains the form shown, including the crop\'s own label', () => {
    const slot = form([named('取')])
    expect(formStates(slot)).toEqual([])
    expect(formState(slot)).toBeNull()
    expect(formOf({ label: '取', form: slot })).toBe('取')
  })
  test('details keep a state beside a competing form, and name a sole state', () => {
    const detail = slot => cropDetails({ label: '取', form: slot }).find(line => line && typeof line === 'object')?.text
    expect(detail(form([{ state: 'unresolved' }]))).toBe('Unknown variant')
    expect(detail(form([named('取'), { state: 'unresolved' }], 'disputed'))).toBe('取 / Unknown variant')
    expect(detail(form([named('取')]))).toBeUndefined()
  })
})
