// What a character's crops are attested writing (`senses` of a character card; issue #679), and each
// witness as a Wiktionary quotation line.

// A document as the gallery credits it, `題（所蔵）`: the title, then the holder in brackets.
export const titleOf = source => (source ?? '').replace(/（[^（）]*）$/, '').trim()

// The year a witness is dated to: when its copy was made, else when its text was composed.
export const yearOf = witness => witness.dating?.witness?.label ?? witness.dating?.composed?.label ?? null

// The span of years a sense's witnesses are dated to, `1739–1799`, or the one year.
export const yearsOf = sense => {
  const years = sense.witnesses.map(w => Number.parseInt(yearOf(w), 10)).filter(Number.isFinite)
  if (!years.length) return null
  const first = Math.min(...years), last = Math.max(...years)
  return first === last ? String(first) : `${first}–${last}`
}

// A witness as a line under a Wiktionary sense. The passage is the crop's character alone: the line
// text the site holds is a transcription, which may give another character (鯡 for 𩹉), so it is not
// quoted. `url` is the crop's page on the site.
export function wiktionaryLine(language, char, witness, url) {
  const title = titleOf(witness.source), year = yearOf(witness)
  if (language === 'ja') {
    const date = year ? `〔${/^\d+$/.test(year) ? year + '年' : year}〕` : ''
    return `#*'''${char}'''${title ? `（『${title}』）` : ''}${date}[${url}]`
  }
  const fields = [year && `year=${year}`, title && `title=${title}`, `url=${url}`, `passage='''${char}'''`].filter(Boolean)
  return `#* {{quote-book|ja|${fields.join('|')}}}`
}
