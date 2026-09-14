export const issues = [
  { id: 'reading', title: 'Wrong character', example: 'ア → カ', hint: 'One character, a different reading', key: '1' },
  { id: 'merged', title: 'Joined characters', example: 'アカ', hint: 'Two or more in one crop', key: '2' },
  { id: 'crop', title: 'Cut off', example: 'ア', hint: 'Part of the character is missing', key: '3' },
  { id: 'blank', title: 'Not a character', example: '', hint: 'Blank paper, a mark or noise', key: '4' },
  { id: 'unclear', title: 'Can’t tell', example: '?', hint: 'Too faint or hard to read', key: '5' },
]
export const issueTitle = id => issues.find(i => i.id === id)?.title || 'Selected'
export const decision = issue => ({ verdict: issue === 'unclear' ? 'unsure' : 'wrong', issue, correction: null })
export const isSingle = text => [...new Intl.Segmenter('ja', { granularity: 'grapheme' }).segment(text)].length === 1
