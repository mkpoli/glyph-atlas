// How a page of the site is cited: the entry it shows (a crop at one evidence version, a grapheme, or a
// form), its stable address, and the formats a citation manager reads (CSL-JSON, BibTeX, schema.org).
// The Worker serves the CSL-JSON at /atlas/cite/<kind>/<id>, and the pages build the same entry from
// the record they render. Nothing is stated that the record does not carry; attributions stay verbatim.
type Json = Record<string, any>;

export const SITE = 'Glyph Atlas';
export const KINDS = ['crop', 'grapheme', 'form'] as const;
export type Kind = typeof KINDS[number];

/** Where a crop's image comes from, as its record states it. */
export type Source = {
  title?: string; holder?: string; shelfmark?: string; page?: number; licence?: string; attribution?: string;
  rights?: string; record?: string; transcription?: string; transcriptionPage?: string;
};
export type Entry = {
  kind: Kind; id: string; label: string | null; codePoint?: string;
  /** The page's address on the site, without a language prefix: the site sends it to the reader's language. */
  path: string;
  /** A crop's evidence version (`{unit}@{pixels}@{box}`, migration 0047) and its short form in the address. */
  version?: string; token?: string;
  source?: Source;
};
/** A date as its year, month and day. */
export type Day = [number, number, number];

const text = (value: unknown): string | undefined => typeof value === 'string' && value.trim() ? value.trim() : undefined;
const web = (value: unknown): string | undefined => typeof value === 'string' && /^https?:\/\//i.test(value) ? value : undefined;
const defined = <T extends Json>(value: T): T => Object.fromEntries(Object.entries(value).filter(([, v]) => v !== undefined && v !== null)) as T;

// The first twelve characters of the image checksum tell one version of a crop from another; the box
// follows in whole pixels, so `2c9afc05d11e-785-2131-136-123` names the version an address cites.
const PIXELS_SHOWN = 12;
/** A crop version's short form for an address, or null for a version of another crop or none. */
export function versionToken(unit: string, version: string | null | undefined): string | null {
  if (!version || !version.startsWith(unit + '@')) return null;
  const rest = version.slice(unit.length + 1), at = rest.lastIndexOf('@');
  if (at < 1) return null;
  const pixels = rest.slice(0, at), box = rest.slice(at + 1);
  return [pixels.slice(0, PIXELS_SHOWN), ...(box ? box.split(',') : [])].join('-');
}
/** The version among `versions` an address's token names, or null when none does. */
export const citedVersion = (unit: string, token: string, versions: string[]): string | null =>
  versions.find(version => versionToken(unit, version) === token) ?? null;

/** A code point or a sequence as an address writes it: `U+304B U+309A` is `U+304B-U+309A`. */
export const slug = (codePoint: string) => codePoint.trim().split(/\s+/).join('-');

/** What a crop's record says of its source. A collection crop names its work as text, a corpus glyph as an object. */
export function cropSource(record: Json): Source {
  const source = record.source;
  const work = typeof source === 'string' ? source : source && typeof source === 'object' ? source : {};
  return defined({
    title: text(typeof work === 'string' ? work : work.title),
    holder: text(record.holder) ?? text((work as Json).holder),
    shelfmark: text((work as Json).shelfmark),
    page: Number.isInteger(record.page_number) && record.page_number > 0 ? record.page_number : undefined,
    // `unknown` states nothing about the terms.
    licence: record.licence === 'unknown' ? undefined : text(record.licence),
    attribution: text(record.attribution),
    rights: web(record.rights_url),
    record: web(record.record_url),
    transcription: text(record.text_attribution),
    transcriptionPage: web(record.honkoku_url),
  });
}

/** A crop at one of its evidence versions, by default the one its record has now. A version is pixels and
 *  a box, and the label is the record's now; an earlier cut is cited without it, by its id alone. */
export function cropEntry(record: Json, origin: 'collection' | 'corpus' = 'collection', version: string | null = record.crop_version ?? null): Entry {
  const id = String(record.id);
  const token = versionToken(id, version);
  const unassigned = record.identity_status === 'unassigned'
    || (record.written_character === null && record.identity_basis === 'normalized_transcription');
  const earlier = Boolean(version) && version !== (record.crop_version ?? null);
  return {
    kind: 'crop', id, label: unassigned || earlier ? null : text(record.written_character) ?? text(record.label) ?? null,
    path: (origin === 'corpus' ? '/corpus/' : '/crop/') + encodeURIComponent(id) + (token ? '?v=' + encodeURIComponent(token) : ''),
    ...(token ? { version: version!, token } : {}), source: cropSource(record),
  };
}

/** A character's page: its whole grapheme family, or the one form alone. */
export function characterEntry(kind: 'grapheme' | 'form', card: Json): Entry {
  const named = kind === 'grapheme' && card.grapheme?.code_point ? card.grapheme : card;
  const codePoint = String(named.code_point);
  return { kind, id: slug(codePoint), label: text(named.char) ?? null, codePoint,
    path: `/character/${slug(codePoint)}?scope=${kind === 'grapheme' ? 'family' : 'exact'}` };
}

/** The address that serves an entry as CSL-JSON. */
export const citeAddress = (entry: Entry) =>
  `/atlas/cite/${entry.kind}/${encodeURIComponent(entry.id)}` + (entry.token ? '?v=' + encodeURIComponent(entry.token) : '');

/** An entry's title, the same in every language: what it shows and its id. */
export function title(entry: Entry): string {
  if (entry.kind === 'crop') return entry.label ? `${entry.label} (${entry.id})` : entry.id;
  return entry.label ? `${entry.label} (${entry.codePoint})` : entry.codePoint!;
}

const GENRE: Record<Kind, string> = { crop: 'Crop', grapheme: 'Grapheme', form: 'Form' };

/** A crop's source, line by line, with words no citation manager reads as one of its own fields. */
function sourceLines(source: Source | undefined): string[] {
  if (!source) return [];
  return [
    source.title && `Source document: ${source.title}`, source.page && `Source page: ${source.page}`,
    source.holder && `Holder: ${source.holder}`, source.shelfmark && `Shelfmark: ${source.shelfmark}`,
    source.licence && `Licence: ${source.licence}`, source.attribution && `Image credit: ${source.attribution}`,
    source.rights && `Rights statement: ${source.rights}`, source.record && `Source record: ${source.record}`,
    source.transcription && `Transcription: ${source.transcription}`, source.transcriptionPage && `Transcription page: ${source.transcriptionPage}`,
  ].filter((line): line is string => Boolean(line));
}

/** The entry as one CSL-JSON item, the format Zotero and other citation managers import. */
export function csl(entry: Entry, origin: string, accessed: Day): Json {
  const lines = sourceLines(entry.source);
  return defined({
    id: `glyphatlas:${entry.kind}:${entry.id}${entry.token ? '@' + entry.token : ''}`,
    type: 'webpage',
    title: title(entry),
    genre: GENRE[entry.kind],
    'container-title': SITE,
    URL: origin + entry.path,
    version: entry.version,
    archive: entry.source?.holder,
    archive_location: entry.source?.shelfmark,
    license: entry.source?.licence,
    note: lines.length ? lines.join('\n') : undefined,
    accessed: { 'date-parts': [accessed] },
  });
}

const iso = ([y, m, d]: Day) => `${String(y).padStart(4, '0')}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
// TeX's special characters, written so they print as themselves.
const tex = (value: string) => value.replace(/[\\{}$&#^_%~]/g, c =>
  c === '\\' ? '\\textbackslash{}' : c === '~' ? '\\textasciitilde{}' : c === '^' ? '\\textasciicircum{}' : '\\' + c);

/** The entry as a BibTeX `@misc` record, with biblatex's `url`, `urldate` and `version`. */
export function bibtex(entry: Entry, origin: string, accessed: Day): string {
  // Two cuts of one crop are two records, so the version is part of the key.
  const key = `glyphatlas:${entry.kind}:${entry.id}${entry.token ? '@' + entry.token : ''}`.replace(/[^\w:.@-]+/g, '-');
  const lines = sourceLines(entry.source);
  const fields: [string, string | undefined][] = [
    ['title', `{${tex(title(entry))}}`], ['howpublished', tex(SITE)], ['url', origin + entry.path], ['urldate', iso(accessed)],
    ['version', entry.version && tex(entry.version)], ['note', lines.length ? lines.map(tex).join('; ') : undefined],
  ];
  return `@misc{${key},\n` + fields.filter(([, value]) => value).map(([name, value]) => `  ${name} = {${value}}`).join(',\n') + '\n}';
}

const CREATIVE_COMMONS = /^CC-(BY(?:-NC)?(?:-SA|-ND)?)-(\d\.\d)$/;
/** The deed of a Creative Commons or public-domain licence id, or null for any other licence. */
export function licenceUrl(licence: string | undefined): string | null {
  if (licence === 'PDM-1.0') return 'https://creativecommons.org/publicdomain/mark/1.0/';
  if (licence === 'CC0-1.0') return 'https://creativecommons.org/publicdomain/zero/1.0/';
  const match = CREATIVE_COMMONS.exec(licence ?? '');
  return match ? `https://creativecommons.org/licenses/${match[1].toLowerCase()}/${match[2]}/` : null;
}

/** The entry as schema.org describes it, for the page's JSON-LD: a crop as an image of its source, a
 *  grapheme or form as a term of the site's set. `image` is the crop's absolute image address, if shown. */
export function linkedData(entry: Entry, origin: string, image: string | null = null): Json {
  const site = { '@type': 'Dataset', name: SITE, url: origin + '/' };
  if (entry.kind !== 'crop') return { '@type': 'DefinedTerm', name: entry.label ?? entry.codePoint, termCode: entry.codePoint,
    url: origin + entry.path, inDefinedTermSet: site };
  const source = entry.source ?? {};
  const licence = licenceUrl(source.licence);
  const credit = source.attribution ?? source.holder;
  const work = source.title || source.holder ? defined({
    '@type': source.holder ? 'ArchiveComponent' : 'CreativeWork', name: source.title,
    holdingArchive: source.holder ? { '@type': 'ArchiveOrganization', name: source.holder } : undefined,
    identifier: source.shelfmark, url: source.record,
  }) : undefined;
  return defined({
    '@type': image ? 'ImageObject' : 'CreativeWork', name: title(entry), identifier: entry.id, version: entry.version,
    url: origin + entry.path, contentUrl: image ?? undefined,
    caption: [entry.label, source.title].filter(Boolean).join(' · ') || undefined,
    license: licence ?? undefined, acquireLicensePage: source.rights, creditText: credit,
    isBasedOn: work, isPartOf: site,
  });
}
