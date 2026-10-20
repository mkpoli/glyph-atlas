/**
 * Whether a list's item is the crop the inspector shows (`inspect`, its history entry). An address swap
 * (an old id, or a retired crop, opened as the crop it names now) keeps the id the list asked for (`was`)
 * and the source id of the crop on screen (`source`), so the list still finds its place: by the id it
 * holds, the old id it was written with, or its own source id.
 */
export const showing = (item, inspect) => Boolean(inspect) && (item.id === inspect.id
  || (inspect.was != null && item.id === inspect.was)
  || (inspect.source != null && (item.id === inspect.source || item.source_id === inspect.source)))
