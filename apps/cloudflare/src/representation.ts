// How a form is named, by the rules `glyph_atlas.representation` applies (docs/design/form-model.md).
// What a picker takes is one character, a character with a variation selector (an IVS), or an
// Ideographic Description Sequence for a shape Unicode does not encode (⿺辶𦊷 is 辶 wrapped round 𦊷).
// Each of BabelStone's operators takes its own number of descriptions; a component is an ideograph, a
// radical, a stroke, a katakana letter standing for a component of that shape (コ in ⿱コ疋, which no
// ideograph writes), a private-use character, ？ for a part no character names, or a part no code point
// names written as its identifier (`&X-00087;`, the top of 合), and may carry a variation selector. A
// description holding such a part has one spelling (`spelt`): ⿱{88}龴 and ⿱⿵𠆢一龴 are ⿱&X-00087;龴. The review app checks what a reader types by the same rules (`lib/ids.js`).
// A private-use character names a form only where the site's own mapping gives it: the drawn forms
// (data/vocab/drawn-forms.yaml, docs/decisions/0014-drawn-forms.md).
import { MOJI_JOHO } from './compatibilityData';
import { customPart, spelt, unknownParts } from './customParts';
import { DRAWN, DRAWN_NAMESPACE, DRAWN_VERSION } from './drawnFormsData';
import { arity, units, yaoSpelling } from './idsOperators';

export const FORM_LONGEST = 64;
/** The longest text a request may name a form with: every component a part's identifier, nine code
 *  units each. */
export const FORM_TEXT_LONGEST = 10 * FORM_LONGEST;
// Radicals, katakana letters, strokes, the ideograph blocks, private use and ？. Planes 2 and 3 hold
// ideographs only.
const COMPONENTS: [number, number][] = [[0x2E80, 0x2FDF], [0x30A1, 0x30FA], [0x31C0, 0x31EE], [0x3400, 0x4DBF], [0x4E00, 0x9FFF],
  [0xE000, 0xF8FF], [0xF900, 0xFAFF], [0xFF1F, 0xFF1F], [0x20000, 0x3FFFD], [0xF0000, 0x10FFFD]];
const component = (c: string) => {
  if (c.length > 2) return Boolean(yaoSpelling(c)) || customPart(c) === c.slice(1, -1);
  const n = c.codePointAt(0)!;
  return COMPONENTS.some(([low, high]) => n >= low && n <= high);
};
const selector = (c: string | undefined) => c !== undefined && /^[︀-️\u{E0100}-\u{E01EF}]$/u.test(c);
// One character: one base, and after it only combining marks and variation selectors, as Python counts it.
const single = (value: string) => /^\P{M}[\p{M}\uFE00-\uFE0F\u{E0100}-\u{E01EF}]*$/u.test(value);
/** A description starts with an operator or names a registered variant operand on its own. */
export const isSequence = (value: string) => arity([...value][0] ?? '') > 0 || Boolean(yaoSpelling(value));
/** The characters and descriptions a text names one after another: a description ends where its
 *  operators are satisfied, so 說⿰月兊説 names three. What follows a description cut short, or one
 *  longer than a form may be, is left out. */
export function written(text: string): string[] {
  const chars = units(text), found: string[] = [];
  let at = 0;
  const described = (): boolean => {
    const c = chars[at++];
    if (c === undefined) return false;
    if (!arity(c)) { if (selector(chars[at])) at++; return component(c); }
    for (let n = arity(c); n > 0; n--) if (!described()) return false;
    return true;
  };
  while (at < chars.length) {
    const from = at;
    if (arity(chars[at])) { if (!described() || at - from > FORM_LONGEST) break; }
    else for (at++; at < chars.length && /^[\p{M}\uFE00-\uFE0F\u{E0100}-\u{E01EF}]$/u.test(chars[at]);) at++;
    found.push(chars.slice(from, at).join(''));
  }
  return found;
}
/** The key a name's 構形 is kept under: a description as written, a character without its marks and
 *  variation selector. A compatibility ideograph has its unified ideograph's (說 U+F96F has 說's). */
export const structureKey = (name: string) => isSequence(name) ? name.normalize('NFC') : [...name.normalize('NFC')][0] ?? '';
/** Whether `char` is a CJK compatibility ideograph: a code point that stands for a unified ideograph
 *  (說 U+F96F and U+F9A1 for 說 U+8AAA), which NFC writes in its place. The twelve unified ideographs
 *  of the compatibility block (﨑 U+FA11) are characters of their own and stand for nothing. */
export const compatibility = (char: string) => /^\p{Script=Han}$/u.test(char) && char.normalize('NFC') !== char;
let figures: Map<string, string> | null = null;
/** What a compatibility ideograph is written as: the Moji_Joho sequence of the figure the
 *  MJ文字情報一覧表 implements as it (突 U+FA55 is 突 U+7A81 U+E0103), or with none its unified ideograph
 *  (說 U+F96F is 說 U+8AAA). Inside a longer text, the base of a sequence or a part of a description, it
 *  is its unified ideograph. */
export function standsFor(char: string): string {
  if (!compatibility(char)) return [...char].map(c => compatibility(c) ? c.normalize('NFC') : c).join('');
  figures ??= new Map(MOJI_JOHO.split(' ').map(pair => {
    const [code, selector] = pair.split(':').map(hex => String.fromCodePoint(parseInt(hex, 16)));
    return [code, code.normalize('NFC') + selector];
  }));
  return figures.get(char) ?? char.normalize('NFC');
}

/** What is wrong with a written form: `character` (not one character nor a description), `component`
 *  (a part no description may name), `missing` (an operator short of descriptions) or `extra` (more
 *  than its operators take); null when it is one. */
export type FormProblem = 'character' | 'component' | 'missing' | 'extra';
export function formProblem(value: string): FormProblem | null {
  const chars = units(value);
  if (!chars.length || chars.length > FORM_LONGEST || value !== value.trim() || /[\p{Cc}\p{Cf}\p{Cs}\p{Z}]/u.test(value)) return 'character';
  if (!isSequence(value)) return /^\p{M}/u.test(value) || !single(value) ? 'character' : null;
  let at = 0;
  const described = (): FormProblem | null => {
    const c = chars[at++];
    if (c === undefined) return 'missing';
    const n = arity(c);
    if (!n) {
      if (!component(c)) return 'component';
      if (selector(chars[at])) { if (c.length > 2) return 'component'; at++ }
      return null;
    }
    for (let i = 0; i < n; i++) { const problem = described(); if (problem) return problem }
    return null;
  };
  return described() ?? (at < chars.length ? 'extra' : null);
}

/** The kinds of private-use and selector code point the schemes tell apart. */
const PRIVATE: [number, number][] = [[0xE000, 0xF8FF], [0xF0000, 0xFFFFD], [0x100000, 0x10FFFD]];
const isPrivate = (c: string) => { const n = c.codePointAt(0)!; return PRIVATE.some(([low, high]) => n >= low && n <= high) };
export type Representation = { scheme: 'unicode' | 'ivs' | 'ids' | 'mj' | 'glyphwiki' | 'pua'; value: string; namespace: string | null; version: string | null };
/** The drawn form a character names: the head of the grapheme it writes, its script and what it looks
 *  like; undefined for any other character. */
export const drawnForm = (char: string): { for: string; script: string; name: string } | undefined => Object.hasOwn(DRAWN, char) ? DRAWN[char] : undefined;
/** Every drawn form, in code point order. */
export const drawnForms = () => Object.keys(DRAWN).sort((a, b) => a.codePointAt(0)! - b.codePointAt(0)!);
/** The drawn forms of a grapheme, named by its head, in code point order. */
export const drawnFormsOf = (head: string) => drawnForms().filter(char => DRAWN[char].for === head);
/** What a reviewer typed or picked, as a representation, or why it is none. A private-use character
 *  is a drawn form where the site's mapping gives it; any other names nothing without its mapping,
 *  which a picker cannot give, so it is refused. A compatibility ideograph is what it stands for
 *  (`standsFor`). */
export function typed(written: string): Representation | FormProblem | 'private' {
  if (isSequence(written)) {
    if (unknownParts(written).length) return 'component';
    written = spelt(standsFor(written));
  }
  const problem = formProblem(written);
  if (problem) return problem;
  const value = standsFor(written), chars = [...value];
  if (isSequence(value)) return { scheme: 'ids', value, namespace: null, version: null };
  if (drawnForm(value)) return { scheme: 'pua', value, namespace: DRAWN_NAMESPACE, version: DRAWN_VERSION };
  if (isPrivate(chars[0])) return 'private';
  if (selector(chars.at(-1))) return chars.length === 2 ? { scheme: 'ivs', value, namespace: null, version: null } : 'character';
  return { scheme: 'unicode', value, namespace: null, version: null };
}
export async function digest(text: string) {
  const bytes = new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text)));
  return [...bytes].map(b => b.toString(16).padStart(2, '0')).join('').slice(0, 32);
}
/** A representation's id, as `Representation.id` hashes it. */
export const representationId = async (r: Representation) => 'rp:' + await digest(JSON.stringify([r.scheme, r.namespace, r.version, r.value]));
/** The id of the broad form a representation is the encoded name of, as `anchored_form` derives it. */
export const anchoredForm = async (representation: string) => 'fm:' + await digest('anchor\n' + representation);
