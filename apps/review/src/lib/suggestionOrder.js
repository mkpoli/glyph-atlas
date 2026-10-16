// The order of a crop's suggestion buttons. It holds no interface strings, so tests import it bare.

/** The buttons to show and the arrival order to keep for the next answer. Buttons keep the order
 *  their texts first arrived in, so a later answer adds to the end and never moves one under the
 *  pointer. Of the recognisers' texts the first six that arrived are shown (a fast context answer may
 *  fill them before image OCR finishes), with every printed mark and look-alike besides. */
export function arrange(previous, texts, symbols, lookAlikes) {
  const all = new Set([...texts, ...lookAlikes])
  const order = [...previous.filter(text => all.has(text)), ...[...all].filter(text => !previous.includes(text))]
  const recognised = new Set(texts)
  const kept = new Set([...order.filter(text => recognised.has(text)).slice(0, 6), ...symbols, ...lookAlikes])
  return { order, shown: order.filter(text => kept.has(text)) }
}
