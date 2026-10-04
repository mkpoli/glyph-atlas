// The みんなで翻刻 page a crop's transcription comes from. Honkoku-Lines (`hl:`) and the Ainu records
// (`hk:`, whose crops are `ar:` or `hk:`) key their documents by the platform's entry id, and the platform
// numbers a book's pages from 1 while a page's `seq` counts from 0.
type Json = Record<string, any>;

const ENTRY = /^(?:hl|hk):([0-9A-Fa-f]{32})$/;
const PAGE = /^(?:hl|hk):[0-9A-Fa-f]{32}:(\d+)$/;
// Corpora whose page ids end in the 0-based `seq`; any other `hk:` corpus numbers its ids from 1.
const ZERO_BASED = new Set(['honkoku-lines', 'ainu-records']);

export const honkokuUrl = (entry: string, page: number) => `https://app.honkoku.org/transcription/${entry}/${page}`;

/** The platform page of a crop record, or null for a crop that does not come from みんなで翻刻. */
export function honkokuPage(item: Json, document: string | null): string | null {
  const entry = ENTRY.exec(document ?? '')?.[1];
  if (!entry) return null;
  let page: number | null = Number.isInteger(item.page_number) && item.page_number > 0 ? item.page_number : null;
  if (page === null && ZERO_BASED.has(item.source?.corpus)) {
    const seq = PAGE.exec(item.source?.page_id ?? '')?.[1];
    if (seq !== undefined) page = Number(seq) + 1;
  }
  return page === null ? null : honkokuUrl(entry, page);
}
