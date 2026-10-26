// What a crop's page tells search engines: its source and holder, read from the record the page
// renders. Nothing is stated that the record does not carry. The page's JSON-LD is its citation's
// (`linkedData` in the Worker's citation module).

/** A collection record names its source as text, a corpus record as an object with a title. */
export const sourceTitle = record => typeof record.source === 'string' ? record.source : record.source?.title ?? null
export const holderOf = record => record.holder ?? (typeof record.source === 'object' ? record.source?.holder : null) ?? null

/** Search results and filtered collections have no stable landing page content of their own. */
export function indexCollection(params) {
  return !['q', 'grapheme', 'work'].some(key => params.get(key)?.trim())
    && (!params.get('group') || params.get('group') === 'all')
}
