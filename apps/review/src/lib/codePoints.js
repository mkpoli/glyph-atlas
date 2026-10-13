/** The text a code point key (`U+1112 U+119E`) names, or null when it names none: six hex digits
 * can spell a value past U+10FFFF, which is no code point. */
export function keyText(key) {
  if (!/^U\+[0-9A-F]{4,6}(\s+U\+[0-9A-F]{4,6})*$/i.test(key?.trim() ?? '')) return null
  const points = key.trim().split(/\s+/).map(point => parseInt(point.slice(2), 16))
  return points.every(point => point <= 0x10FFFF) ? String.fromCodePoint(...points) : null
}
