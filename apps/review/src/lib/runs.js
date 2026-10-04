// Whether a search query is a run of characters, the text `/atlas/runs` takes. It holds no interface
// strings, so tests import it bare.

const graphemes = new Intl.Segmenter('ja', { granularity: 'grapheme' })
/** The query as a run, when it is one: two to eight characters, none of them a space or ASCII (which
 *  the search box reads as a character name or a code point), else ''. */
export function runText(query) {
  const text = query.trim()
  if (!/^[^\s\x00-\x7f]+$/u.test(text)) return ''
  const length = [...graphemes.segment(text)].length
  return length >= 2 && length <= 8 ? text : ''
}
