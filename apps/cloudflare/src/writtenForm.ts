// What a crop's letterforms are written as, by the rule `glyph_atlas.written_form` applies: one
// character, or an Ideographic Description Sequence for a shape Unicode does not encode (⿺辶𦊷 is 辶
// wrapped round 𦊷). Each of BabelStone's operators takes its own number of descriptions; a component
// is an ideograph, a radical, a stroke, a private-use character, or ？ for a part no character names,
// and may carry a variation selector. The review app checks what a reader types with the same code.
export const FORM_LONGEST = 64;
const BINARY = new Set([...'⿰⿱⿴⿵⿶⿷⿸⿹⿺⿻⿼⿽㇯']), TERNARY = new Set([...'⿲⿳']), UNARY = new Set([...'⿾⿿〾']);
const arity = (c: string) => BINARY.has(c) ? 2 : TERNARY.has(c) ? 3 : UNARY.has(c) ? 1 : 0;
// Radicals, strokes, the ideograph blocks, private use and ？. Planes 2 and 3 hold ideographs only.
const COMPONENTS: [number, number][] = [[0x2E80, 0x2FDF], [0x31C0, 0x31EE], [0x3400, 0x4DBF], [0x4E00, 0x9FFF],
  [0xE000, 0xF8FF], [0xF900, 0xFAFF], [0xFF1F, 0xFF1F], [0x20000, 0x3FFFD], [0xF0000, 0x10FFFD]];
const component = (c: string) => { const n = c.codePointAt(0)!; return COMPONENTS.some(([low, high]) => n >= low && n <= high) };
const selector = (c: string | undefined) => c !== undefined && /^[︀-️\u{E0100}-\u{E01EF}]$/u.test(c);
// One character: one base, and after it only combining marks and variation selectors, as Python counts it.
const single = (value: string) => /^\P{M}[\p{M}\uFE00-\uFE0F\u{E0100}-\u{E01EF}]*$/u.test(value);
/** Whether a value is written as a description, which starts with an operator. */
export const isSequence = (value: string) => arity([...value][0] ?? '') > 0;
/** What is wrong with a written form: `character` (not one character nor a description), `component`
 *  (a part no description may name), `missing` (an operator short of descriptions) or `extra` (more
 *  than its operators take); null when it is one. */
export type FormProblem = 'character' | 'component' | 'missing' | 'extra';
export function formProblem(value: string): FormProblem | null {
  const chars = [...value];
  if (!chars.length || chars.length > FORM_LONGEST || value !== value.trim() || /[\p{Cc}\p{Cf}\p{Cs}\p{Z}]/u.test(value)) return 'character';
  if (!isSequence(value)) return /^\p{M}/u.test(value) || !single(value) ? 'character' : null;
  let at = 0;
  const described = (): FormProblem | null => {
    const c = chars[at++];
    if (c === undefined) return 'missing';
    const n = arity(c);
    if (!n) {
      if (!component(c)) return 'component';
      if (selector(chars[at])) at++;
      return null;
    }
    for (let i = 0; i < n; i++) { const problem = described(); if (problem) return problem }
    return null;
  };
  return described() ?? (at < chars.length ? 'extra' : null);
}
