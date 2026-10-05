// How a page of the site is cited: the entry it shows (a crop at one evidence version, a grapheme, or a
// form), its stable address, and the formats citation managers read (CSL-JSON, BibTeX, Hayagriva, and
// schema.org for the page). The Worker serves them at /atlas/cite/<kind>/<id>, and the pages build the
// same entry from the record they render. Nothing is stated that the record does not carry;
// attributions stay verbatim.
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
  /** The form the site holds for a crop, when it is other than its label. */
  form?: string;
  /** The page's address on the site, without a language prefix: the site sends it to the reader's language. */
  path: string;
  /** A crop's evidence version (`{unit}@{pixels}@{box}`, migration 0047) and its name in the address. */
  version?: string; token?: string;
  source?: Source;
};
/** A date as its year, month and day. */
export type Day = [number, number, number];

const text = (value: unknown): string | undefined => typeof value === 'string' && value.trim() ? value.trim() : undefined;
const web = (value: unknown): string | undefined => typeof value === 'string' && /^https?:\/\//i.test(value) ? value : undefined;
const defined = <T extends Json>(value: T): T => Object.fromEntries(Object.entries(value).filter(([, v]) => v !== undefined && v !== null)) as T;

// A version is named in an address by six characters, a hash of its id: short enough to read and type,
// and checked against the crop's own versions, so two cuts of one crop never answer to the same name.
const TOKEN_SPACE = 36 ** 6;
/** A crop version's name in an address, or null for a version of another crop or none. */
export function versionToken(unit: string, version: string | null | undefined): string | null {
  if (!version || !version.startsWith(unit + '@')) return null;
  // FNV-1a over the version id, the same in the browser and the Worker.
  let hash = 0x811c9dc5;
  for (let i = 0; i < version.length; i++) hash = Math.imul(hash ^ version.charCodeAt(i), 0x01000193) >>> 0;
  return (hash % TOKEN_SPACE).toString(36).padStart(6, '0');
}
/** The version among `versions` an address's token names; null when none does, or when two would. */
export function citedVersion(unit: string, token: string, versions: string[]): string | null {
  const named = [...new Set(versions)].filter(version => versionToken(unit, version) === token);
  return named.length === 1 ? named[0] : null;
}
/** A crop id as an address writes it: escaped where it must be, its colons and at signs left readable. */
export const idPath = (id: string) => encodeURIComponent(id).replace(/%3A/gi, ':').replace(/%40/gi, '@');

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
 *  a box, and the label is the record's now; an earlier cut is cited without it, by its source alone. */
export function cropEntry(record: Json, origin: 'collection' | 'corpus' = 'collection', version: string | null = record.crop_version ?? null): Entry {
  const id = String(record.id);
  const token = versionToken(id, version);
  const unassigned = record.identity_status === 'unassigned'
    || (record.written_character === null && record.identity_basis === 'normalized_transcription');
  const earlier = Boolean(version) && version !== (record.crop_version ?? null);
  const label = unassigned || earlier ? null : text(record.written_character) ?? text(record.label) ?? null;
  // The form a crop is filed as, when people agree on one and it is not the label.
  const forms = record.form?.status === 'disputed' ? [] : (record.form?.values ?? []).map((value: Json) => text(value?.text)).filter(Boolean);
  const form = label && forms.length && forms.join(' / ') !== label ? forms.join(' / ') : undefined;
  return {
    kind: 'crop', id, label, ...(form ? { form } : {}),
    path: (origin === 'corpus' ? '/corpus/' : '/crop/') + idPath(id) + (token ? '?v=' + token : ''),
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

export const FORMATS = ['csl', 'bibtex', 'hayagriva'] as const;
export type Format = typeof FORMATS[number];
/** The address that serves an entry in a format. */
export const citeAddress = (entry: Entry, format: Format = 'csl') =>
  `/atlas/cite/${entry.kind}/${idPath(entry.id)}` + (entry.token ? '?v=' + entry.token : '')
    + (format === 'csl' ? '' : (entry.token ? '&' : '?') + 'format=' + format);

/** An entry's title in the formats a manager reads: the character it shows, else its id. */
export const title = (entry: Entry): string => entry.label ?? (entry.kind === 'crop' ? entry.id : entry.codePoint!);

/** A short key a bibliography cites the entry by: `glyphatlas-敢-k3x9a2`, `glyphatlas-あ-grapheme`. A
 *  label that is not letters (a description sequence) is named by its code points. */
export function citationKey(entry: Entry): string {
  const label = entry.label && /^[\p{L}\p{M}\p{N}]{1,4}$/u.test(entry.label) ? entry.label
    : entry.label ? [...entry.label].map(c => 'U' + c.codePointAt(0)!.toString(16).toUpperCase()).join('') : entry.kind;
  const tail = entry.kind !== 'crop' ? entry.kind : entry.token ?? versionToken(entry.id, entry.id + '@') ?? 'crop';
  return `glyphatlas-${label}-${tail}`;
}

const GENRE: Record<Kind, string> = { crop: 'Crop', grapheme: 'Grapheme', form: 'Form' };

/** What a crop's source says beside its title and page, line by line, in words no citation manager
 *  reads as one of its own fields. */
function sourceNotes(source: Source | undefined): string[] {
  if (!source) return [];
  return [
    source.attribution && `Image: ${source.attribution}`, source.licence && `Licence: ${source.licence}`,
    source.rights && `Rights statement: ${source.rights}`, source.record && `Source record: ${source.record}`,
    source.transcription && `Transcription: ${source.transcription}`, source.transcriptionPage && `Transcription page: ${source.transcriptionPage}`,
  ].filter((line): line is string => Boolean(line));
}
/** The source document as one phrase: its title, page, holder and shelfmark. */
function sourcePhrase(source: Source | undefined): string | undefined {
  if (!source) return undefined;
  return [source.title, source.page && `p. ${source.page}`, source.holder, source.shelfmark].filter(Boolean).join(', ') || undefined;
}

/** The entry as one CSL-JSON item, the format Zotero and other citation managers import. */
export function csl(entry: Entry, origin: string, accessed: Day): Json {
  const where = sourcePhrase(entry.source);
  const lines = [where && `Source: ${where}`, ...sourceNotes(entry.source)].filter(Boolean);
  return defined({
    id: citationKey(entry),
    type: 'webpage',
    title: title(entry),
    genre: GENRE[entry.kind],
    'container-title': SITE,
    URL: origin + entry.path,
    version: entry.token,
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

/** The entry as a BibTeX `@misc` record, with biblatex's `url` and `urldate`. */
export function bibtex(entry: Entry, origin: string, accessed: Day): string {
  const where = sourcePhrase(entry.source), notes = sourceNotes(entry.source);
  const fields: [string, string | undefined][] = [
    ['title', `{${tex(title(entry))}}`], ['howpublished', tex(SITE)], ['url', origin + entry.path], ['urldate', iso(accessed)],
    ['note', [where && `Source: ${where}`, ...notes].filter((part): part is string => Boolean(part)).map(tex).join('. ') || undefined],
  ];
  return `@misc{${citationKey(entry)},\n` + fields.filter(([, value]) => value).map(([name, value]) => `  ${name} = {${value}}`).join(',\n') + '\n}';
}

/** A YAML scalar: every string double-quoted, which YAML reads with JSON's escapes. */
const yaml = (value: string | number | boolean) => typeof value === 'string' ? JSON.stringify(value) : String(value);
type Fields = [string, unknown][];
/** A mapping in block style; a field whose value is `{ map }` nests one, `{ list }` lists them. */
function yamlBlock(fields: Fields, indent: string): string[] {
  const out: string[] = [];
  for (const [name, value] of fields) {
    if (value === undefined || value === null || value === '') continue;
    const nested = value as { map?: Fields; list?: Fields[] };
    if (nested.list) {
      out.push(`${indent}${name}:`);
      for (const item of nested.list) {
        const lines = yamlBlock(item, indent + '    ');
        out.push(`${indent}  - ${lines[0].trimStart()}`, ...lines.slice(1));
      }
    } else if (nested.map) out.push(`${indent}${name}:`, ...yamlBlock(nested.map, indent + '  '));
    else out.push(`${indent}${name}: ${yaml(value as string | number | boolean)}`);
  }
  return out;
}

// Names and titles are printed as the record writes them, never recased by a style.
const verbatim = (value: string | undefined) => value ? { map: [['value', value], ['verbatim', true]] as Fields } : undefined;

/**
 * The entry in Hayagriva, Typst's bibliography format (typst/hayagriva docs/file-format.md): an
 * `entry` of the site as a work of reference, its URL with the access date; a crop also has the
 * document it was cut from as its `original`, with the page, the holder (`archive`) and the shelfmark
 * (`call-number`).
 */
export function hayagriva(entry: Entry, origin: string, accessed: Day): string {
  const source = entry.source;
  const original: Fields | null = source && (source.title || source.holder) ? [
    ['type', 'original'], ['title', verbatim(source.title)], ['page-range', source.page], ['archive', verbatim(source.holder)], ['call-number', source.shelfmark],
  ] : null;
  const notes = sourceNotes(source);
  const fields: Fields = [
    ['type', 'entry'], ['title', verbatim(title(entry))], ['genre', GENRE[entry.kind]],
    ['serial-number', entry.kind === 'crop' ? entry.id : entry.codePoint],
    ['url', { map: [['value', origin + entry.path], ['date', iso(accessed)]] }],
    ['parent', { list: [[['type', 'reference'], ['title', verbatim(SITE)], ['url', origin + '/']], ...(original ? [original] : [])] }],
    ['note', notes.length ? notes.join('. ') : undefined],
  ];
  return `${citationKey(entry)}:\n` + yamlBlock(fields, '  ').join('\n') + '\n';
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
    '@type': image ? 'ImageObject' : 'CreativeWork', name: title(entry), identifier: entry.id, version: entry.token,
    url: origin + entry.path, contentUrl: image ?? undefined,
    caption: [entry.label, source.title].filter(Boolean).join(' · ') || undefined,
    license: licence ?? undefined, acquireLicensePage: source.rights, creditText: credit,
    isBasedOn: work, isPartOf: site,
  });
}
