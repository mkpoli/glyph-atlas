import { describe, expect, it } from 'bun:test'
import { runCharacters } from '../src/lib/runs.js'
import {
  approveCropRequest,
  approvedCropPatch,
  canApproveCrop,
  mergeCropPatch,
  occurrenceState,
  occurrenceWritten,
  resolveInspectedCrop,
} from '../src/lib/sequenceReview.js'

const localCrop = (id, extra = {}) => ({
  id,
  origin: 'local',
  label: 'に',
  written_character: '𛂌',
  revision: 2,
  image_sha256: 'a'.repeat(64),
  state: 'pending',
  crop_box: { x: 10, y: 20, w: 30, h: 40 },
  dating: { witness: { start: 1790, end: 1790 } },
  ...extra,
})

const corpusCrop = (id, extra = {}) => ({
  id,
  origin: 'corpus',
  label: 'て',
  written_character: 'て',
  revision: 4,
  source_revision: 'b'.repeat(64),
  proxyable: true,
  image: '/atlas/media/c.webp',
  state: 'pending',
  crop_box: { x: 12, y: 70, w: 28, h: 35 },
  ...extra,
})

describe('sequence review helpers', () => {
  it('splits grapheme clusters accurately including combining marks and variation selectors', () => {
    expect(runCharacters('にて')).toEqual(['に', 'て'])
    expect(runCharacters('か\u3099な')).toEqual(['か\u3099', 'な'])
    expect(runCharacters('葛\u{E0100}城')).toEqual(['葛\u{E0100}', '城'])
  })

  it('checks single-crop approval eligibility for local and corpus crops', () => {
    expect(canApproveCrop(localCrop('c1'))).toBe(true)
    expect(canApproveCrop(corpusCrop('g1'))).toBe(true)
    expect(canApproveCrop(corpusCrop('g2', { identity_status: 'unassigned', written_character: null }))).toBe(false)
    expect(canApproveCrop(corpusCrop('g3', { proxyable: false }))).toBe(false)
    expect(canApproveCrop(corpusCrop('g4', { image: null }))).toBe(false)
    expect(canApproveCrop(corpusCrop('g5', { needs_segmentation: true }))).toBe(false)
    expect(canApproveCrop(localCrop('c2', { label: 'あい', written_character: null }))).toBe(false)
  })

  it('computes aggregate occurrence state and written forms', () => {
    const a = localCrop('a', { state: 'checked' })
    const b = corpusCrop('b', { state: 'checked' })
    const c = localCrop('c', { state: 'pending' })
    const d = localCrop('d', { state: 'flagged' })
    expect(occurrenceState([a, b])).toBe('checked')
    expect(occurrenceState([a, c])).toBe('pending')
    expect(occurrenceState([a, d, c])).toBe('flagged')
    expect(occurrenceWritten([a, b])).toEqual({
      forms: [
        { text: '𛂌', transcribed: false },
        { text: 'て', transcribed: false },
      ],
      written: '𛂌て',
    })
    expect(occurrenceWritten([corpusCrop('u', { identity_status: 'unassigned', written_character: null, label: 'て' })])).toEqual({
      forms: [{ text: 'て', transcribed: true }],
      written: 'て',
    })
  })

  it('builds approval requests and parses responses for local and corpus crops', () => {
    const local = localCrop('loc:1')
    expect(approveCropRequest(local, 'sub-1')).toEqual({
      route: '/atlas/characters/loc%3A1',
      body: { id: 'sub-1', revision: 2, image_sha256: 'a'.repeat(64), verdict: 'match', issue: null },
    })
    expect(approvedCropPatch(local, { id: 'sub-1', results: [{ target_id: 'loc:1', revision: 3, state: 'checked' }] })).toEqual({
      state: 'checked',
      revision: 3,
      issue: null,
    })

    const corpus = corpusCrop('cor:1')
    expect(approveCropRequest(corpus, 'sub-2')).toEqual({
      route: '/atlas/characters/cor%3A1',
      body: { id: 'sub-2', revision: 4, source_revision: 'b'.repeat(64), verdict: 'match', shown: 'て', issue: null },
    })
    expect(approvedCropPatch(corpus, { id: 'sub-2', results: [{ target_id: 'cor:1', revision: 5, state: 'checked' }] })).toEqual({
      state: 'checked',
      revision: 5,
      issue: null,
    })
  })

  it('merges crop patches without losing run geometry or dating', () => {
    const crop = localCrop('loc:1')
    const patched = mergeCropPatch(crop, { state: 'checked', revision: 3, crop_box: undefined })
    expect(patched.crop_box).toEqual({ x: 10, y: 20, w: 30, h: 40 })
    expect(patched.dating).toEqual({ witness: { start: 1790, end: 1790 } })
    expect(patched.state).toBe('checked')
  })

  it('resolves inspected crops for changed fields and saves of either origin', async () => {
    const local = localCrop('loc:1')
    const corpus = corpusCrop('cor:1')
    expect(await resolveInspectedCrop(local, 'loc:1', { style: 'cursive' }, 'changed', async () => {
      throw new Error('should not fetch')
    })).toEqual({ style: 'cursive' })
    expect(await resolveInspectedCrop(corpus, 'cor:1', { results: [{ revision: 5, state: 'checked' }] }, 'saved', async id => ({
      id, origin: 'corpus', label: 'で', revision: 5, state: 'checked',
    }))).toEqual({ id: 'cor:1', origin: 'corpus', label: 'で', revision: 5, state: 'checked' })
    expect(await resolveInspectedCrop(local, 'loc:1', { results: [{ revision: 3, state: 'checked' }] }, 'saved', async id => ({
      id, label: 'に', written_character: 'に', revision: 3, state: 'checked',
    }))).toEqual({ id: 'loc:1', label: 'に', written_character: 'に', revision: 3, state: 'checked' })
  })
})
