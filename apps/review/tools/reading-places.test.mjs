import { afterAll, beforeEach, describe, expect, it, mock } from 'bun:test'

const request = mock()
mock.module('../src/lib/client.js', () => ({ request }))
const { bookOf, otherPlaces, placeReading, placesSaved, placesText, readingPlaces, readingsNow, runPlaces } = await import('../src/lib/readingPlaces.js')
afterAll(() => mock.restore())
beforeEach(() => request.mockReset())

const value = { run: ['諸', '器'], at: 1, reading: ['しょ', 'き'], ruby: ['𛁈よ', 'き'] }
const place = (a, b, more = {}) => ({ crops: [{ id: a, label: '諸' }, { id: b, label: '器' }], page: { image: 'p' }, vertical: true, ...more })
const now = (id, reading = null) => [id, { id, crop_version: id + '@v', text: '諸器ニ', reading, word: null }]

it('a run is searched as two to eight characters, in the book its crop\'s page is of', async () => {
  expect([placesText(value), placesText({ run: ['器'] }), placesText({ run: Array(9).fill('諸') }), placesText(null)]).toEqual(['諸器', '', '', ''])
  expect([bookOf({ page_id: 'hl:e54:27' }), bookOf({})]).toEqual(['hl:e54', null])
  request.mockResolvedValue({ items: [] })
  await runPlaces('諸器', { document: 'hl:e54', offset: 10 }); await runPlaces('一二三四五六七八')
  const asked = request.mock.calls.map(([path]) => Object.fromEntries(new URL(path, 'https://x').searchParams))
  // A page of places holds no more crops than one reading of them takes.
  expect(asked).toEqual([{ text: '諸器', sort: 'source', offset: '10', limit: '10', document: 'hl:e54' }, { text: '一二三四五六七八', sort: 'source', offset: '0', limit: '6' }])
})

it('the crops of the places are read together', async () => {
  request.mockResolvedValue({ items: [{ id: 'a', reading: null }] })
  expect([...(await readingsNow(['a', 'b'])).keys()]).toEqual(['a'])
  expect(request.mock.calls[0][0]).toBe('/atlas/readings?crops=a,b')
})

describe('the other places of a run', () => {
  const read = { status: 'disputed', values: [{ run: ['諸', '器'], at: 1, reading: ['もろ', 'き'] }] }
  it('leave out the crop\'s own, and are open while none of their crops is read', () => {
    const reads = new Map([now('a'), now('b'), now('c'), now('d', read)])
    const places = readingPlaces([place('x', 'own'), place('a', 'b'), place('c', 'd')], reads, 'own')
    expect(places.map(one => [one.id, one.open, one.read?.status ?? null])).toEqual([['a', true, null], ['c', false, 'disputed']])
  })
  it('are closed where a crop carries a ruby and no reading, which a save would take back', () => {
    const ruby = { status: null, values: [], ruby: { status: 'asserted', values: [{ run: ['諸', '器'], at: 0, ruby: ['しょ', 'き'] }] }, left: null }
    expect(readingPlaces([place('a', 'b')], new Map([now('a', ruby), now('b')]), 'own')[0]).toMatchObject({ open: false, read: ruby })
  })
  it('are closed with the one saved, where they share a crop', () => {
    const places = readingPlaces([place('a', 'b'), place('b', 'c'), place('d', 'e')], new Map(['a', 'b', 'c', 'd', 'e'].map(id => now(id))), 'own')
    expect(placesSaved(places, places[0], read).map(one => [one.id, one.open, Boolean(one.read)])).toEqual([['a', false, true], ['b', false, true], ['d', true, false]])
  })
  it('are counted without the crop\'s own', () => {
    const own = place('own', 'z'), other = place('a', 'b')
    expect([otherPlaces({ items: [own, other], total: 2 }, 'own'), otherPlaces({ items: [own], total: 1 }, 'own'), otherPlaces({ items: [other], total: 1 }, 'own'),
      otherPlaces({ items: [], total: 0 }, 'own'), otherPlaces({ items: [other], total: 30 }, 'own')]).toEqual([1, 0, 1, 0, 29])
  })
  it('are closed where their page cannot be seen, a crop is not published, or one has no version or character', () => {
    const open = (item, entries) => readingPlaces([item], new Map(entries), 'own')[0].open
    expect(open(place('a', 'b', { page: null }), [now('a'), now('b')])).toBe(false)
    expect(open(place('a', 'b'), [now('a')])).toBe(false)
    expect(open(place('a', 'b'), [now('a'), ['b', { id: 'b', crop_version: null }]])).toBe(false)
    expect(open({ ...place('a', 'b'), crops: [{ id: 'a', label: null }, { id: 'b', label: '器' }] }, [now('a'), now('b')])).toBe(false)
  })
  it('are saved in their own characters with the reading alone, the ruby of another page never with it', () => {
    const [one] = readingPlaces([{ ...place('a', 'b'), crops: [{ id: 'a', label: '諸' }, { id: 'b', label: '器', written_character: '噐' }] }], new Map([now('a'), now('b')]), 'own')
    const [crop, reading] = placeReading(one, value, { method: 'context', word: undefined })
    expect([crop.id, crop.crop_version]).toEqual(['a', 'a@v'])
    expect(reading).toEqual({ run: ['諸', '噐'], at: 0, reading: ['しょ', 'き'], method: 'context', word: undefined, crops: new Map([[1, one.crops[1]]]) })
    expect('ruby' in reading || 'left' in reading).toBe(false)
  })
})
