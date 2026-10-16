import { describe, expect, it } from 'bun:test'
import { lookAlikeLabels } from '../src/lib/lookAlikes.js'

const crop = (id, label, score, extra = {}) => ({ id, label, score, ...extra })

describe("a crop's look-alikes", () => {
  it('rank characters by the scores their crops share', () => {
    const result = {
      similar: [crop('a', '鮓', 0.96), crop('b', '鯛', 0.95), crop('c', '鯛', 0.94), crop('d', 'ノ', 0.92)],
      filed_differently: [crop('b', '鯛', 0.95), crop('e', '鯡', 0.92), crop('f', '鯡', 0.92)],
    }
    expect(lookAlikeLabels(result, '鋤')).toEqual(['鯛', '鯡', '鮓', 'ノ'])
  })
  it("leave out the crop's own character, unassigned crops and joined labels", () => {
    const result = { similar: [crop('a', '鋤', 0.99), crop('b', '申候', 0.98),
      crop('c', 'Unassigned', 0.97, { identity_status: 'unassigned' }), crop('d', 'が', 0.9)] }
    expect(lookAlikeLabels(result, '鋤')).toEqual(['が'])
  })
  it('are none for a crop without lists', () => {
    expect(lookAlikeLabels(null, '鋤')).toEqual([])
    expect(lookAlikeLabels({ revision: null, similar: [], filed_differently: [] }, '鋤')).toEqual([])
  })
})
