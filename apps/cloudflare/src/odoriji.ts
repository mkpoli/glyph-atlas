// Bidirectional iteration-mark (踊り字) association for multi-character sequences.
//
// Sequences in `ATLAS_RUNS` store folded grapheme heads (か/カ/𛀗 → か, が/ガ → が), while each
// iteration mark keeps its own grapheme (ゝ, ヽ, ゞ, ヾ, 々, 〻, 〃, 〱, 〲, 〳, 〴, 〵, 𖿠, 𖿡, 𖿣).
// Given a sequence written either spelled out (かか, かが, 人人, つれづれ) or with any iteration mark
// (かヽ, かゝ, かゞ, 人々, 人〻, つれ〲, つれ〴〵), this module derives the equivalent spellings in
// both directions, bounded so D1 lookups stay small.

const VOICE_PAIRS: readonly [string, string][] = [
  ['か', 'が'], ['き', 'ぎ'], ['く', 'ぐ'], ['け', 'げ'], ['こ', 'ご'],
  ['さ', 'ざ'], ['し', 'じ'], ['す', 'ず'], ['せ', 'ぜ'], ['そ', 'ぞ'],
  ['た', 'だ'], ['ち', 'ぢ'], ['つ', 'づ'], ['て', 'で'], ['と', 'ど'],
  ['は', 'ば'], ['ひ', 'び'], ['ふ', 'ぶ'], ['へ', 'べ'], ['ほ', 'ぼ'],
  ['う', 'ゔ'],
  ['カ', 'ガ'], ['キ', 'ギ'], ['ク', 'グ'], ['ケ', 'ゲ'], ['コ', 'ゴ'],
  ['サ', 'ザ'], ['シ', 'ジ'], ['ス', 'ズ'], ['セ', 'ゼ'], ['ソ', 'ゾ'],
  ['タ', 'ダ'], ['チ', 'ヂ'], ['ツ', 'ヅ'], ['テ', 'デ'], ['ト', 'ド'],
  ['ハ', 'バ'], ['ヒ', 'ビ'], ['フ', 'ブ'], ['ヘ', 'ベ'], ['ホ', 'ボ'],
  ['ウ', 'ヴ'], ['ワ', 'ヷ'], ['ヰ', 'ヸ'], ['ヱ', 'ヹ'], ['ヲ', 'ヺ'],
];

const VOICE = new Map(VOICE_PAIRS);
const UNVOICE = new Map(VOICE_PAIRS.map(([plain, voiced]) => [voiced, plain]));

const KANA_UNVOICED = ['ゝ', 'ヽ'] as const;
const KANA_VOICED = ['ゞ', 'ヾ'] as const;
const HAN_MARKS = ['々', '〻', '〃', '𖿣'] as const;
const TANGUT_MARK = '𖿠';
const NUSHU_MARK = '𖿡';

const ALL_MARKS = new Set<string>([
  ...KANA_UNVOICED, ...KANA_VOICED, ...HAN_MARKS,
  '〱', '〲', '〳', '〴', '〵', TANGUT_MARK, NUSHU_MARK,
]);
const KANA_UNVOICED_SET = new Set<string>(KANA_UNVOICED);
const KANA_VOICED_SET = new Set<string>(KANA_VOICED);
const HAN_MARK_SET = new Set<string>(HAN_MARKS);

const KANA_RE = /^[\p{Script=Hiragana}\p{Script=Katakana}]$/u;
const HAN_RE = /^\p{Script=Han}$/u;
const TANGUT_RE = /^\p{Script=Tangut}$/u;
const NUSHU_RE = /^\p{Script=Nushu}$/u;

const isMark = (ch: string) => ALL_MARKS.has(ch);
const isKana = (ch: string) => !isMark(ch) && ch !== 'ー' && KANA_RE.test(ch);
const isHan = (ch: string) => !isMark(ch) && HAN_RE.test(ch);
const isTangut = (ch: string) => !isMark(ch) && TANGUT_RE.test(ch);
const isNushu = (ch: string) => !isMark(ch) && NUSHU_RE.test(ch);
const isKunojiBase = (ch: string) => isKana(ch) || isHan(ch);

export const hasOdoriji = (parts: readonly string[]) => parts.some(isMark);

/** Expand iteration marks in `parts` into their spelled-out character sequence(s). */
export function expandOdoriji(parts: readonly string[], maxLength = 8, maxExpanded = 8): string[][] {
  let states: string[][] = [[]];
  const pushAll = (branches: (seq: string[]) => string[][]) => {
    const next: string[][] = [];
    const seen = new Set<string>();
    for (const seq of states) {
      for (const out of branches(seq)) {
        if (out.length > maxLength) continue;
        const key = out.join('');
        if (seen.has(key)) continue;
        seen.add(key);
        next.push(out);
        if (next.length >= maxExpanded) break;
      }
      if (next.length >= maxExpanded) break;
    }
    states = next;
  };

  for (let i = 0; i < parts.length && states.length; i++) {
    const ch = parts[i];
    const nextCh = parts[i + 1];

    // Split vertical kana repeat marks: 〳〵 (unvoiced) and 〴〵 (voiced).
    const splitKunoji = (ch === '〳' || ch === '〴') && nextCh === '〵';
    if (ch === '〱' || ch === '〲' || splitKunoji) {
      const voiced = ch === '〲' || ch === '〴';
      if (splitKunoji) i += 1;
      pushAll(seq => {
        const spans: number[] = [];
        if (seq.length >= 2) spans.push(2);
        if (seq.length >= 3) spans.push(3);
        if (seq.length === 1) spans.push(1);
        const results: string[][] = [];
        for (const k of spans) {
          const slice = seq.slice(-k);
          if (!slice.every(isKunojiBase)) continue;
          const head = slice[0];
          const first = voiced ? (VOICE.get(head) ?? head) : head;
          results.push([...seq, first, ...slice.slice(1)]);
        }
        return results.length ? results : [[...seq, ch, ...(splitKunoji ? ['〵'] : [])]];
      });
      continue;
    }

    // Two consecutive Han iteration marks after two distinct Han characters: 部分々々 → 部分部分.
    if (HAN_MARK_SET.has(ch) && nextCh && HAN_MARK_SET.has(nextCh)) {
      const canPair = states.some(seq => seq.length >= 2 && isHan(seq[seq.length - 2]) && isHan(seq[seq.length - 1]) && seq[seq.length - 2] !== seq[seq.length - 1]);
      if (canPair) {
        i += 1;
        pushAll(seq => {
          if (seq.length >= 2 && isHan(seq[seq.length - 2]) && isHan(seq[seq.length - 1]) && seq[seq.length - 2] !== seq[seq.length - 1]) {
            return [[...seq, seq[seq.length - 2], seq[seq.length - 1]]];
          }
          const prev = seq.at(-1);
          return prev && isHan(prev) ? [[...seq, prev, prev]] : [[...seq, ch, nextCh]];
        });
        continue;
      }
    }

    if (KANA_UNVOICED_SET.has(ch)) {
      pushAll(seq => {
        const prev = seq.at(-1);
        if (!prev || !isKana(prev)) return [[...seq, ch]];
        const plain = UNVOICE.get(prev);
        // Historically がゝ repeats the base unvoiced kana (がか), while some transcriptions use it for がが.
        return plain ? [[...seq, plain], [...seq, prev]] : [[...seq, prev]];
      });
      continue;
    }

    if (KANA_VOICED_SET.has(ch)) {
      pushAll(seq => {
        const prev = seq.at(-1);
        if (!prev || !isKana(prev)) return [[...seq, ch]];
        return [[...seq, VOICE.get(prev) ?? prev]];
      });
      continue;
    }

    if (HAN_MARK_SET.has(ch)) {
      pushAll(seq => {
        const prev = seq.at(-1);
        if (!prev) return [[...seq, ch]];
        if (isHan(prev) || (ch === '〃' && !isMark(prev))) return [[...seq, prev]];
        return [[...seq, ch]];
      });
      continue;
    }

    if (ch === TANGUT_MARK) {
      pushAll(seq => {
        const prev = seq.at(-1);
        return prev && isTangut(prev) ? [[...seq, prev]] : [[...seq, ch]];
      });
      continue;
    }

    if (ch === NUSHU_MARK) {
      pushAll(seq => {
        const prev = seq.at(-1);
        return prev && isNushu(prev) ? [[...seq, prev]] : [[...seq, ch]];
      });
      continue;
    }

    pushAll(seq => [[...seq, ch]]);
  }
  return states;
}

/** Contract a spelled-out character sequence into iteration-mark spellings. */
function contractOne(parts: readonly string[], maxVariants: number, maxLength: number): string[][] {
  const results: string[][] = [];
  const seen = new Set<string>([parts.join('')]);
  const add = (seq: string[]) => {
    if (seq.length < 2 || seq.length > maxLength) return;
    const key = seq.join('');
    if (seen.has(key)) return;
    seen.add(key);
    results.push(seq);
  };

  // Multi-character span contractions first (ABAB / AB voice(A) B → AB〱, AB〳〵, AB〲, AB〴〵, AB々々, AB〻〻).
  for (const k of [2, 3]) {
    for (let i = k; i + k <= parts.length && results.length < maxVariants; i++) {
      const left = parts.slice(i - k, i), right = parts.slice(i, i + k);
      if (!left.every(isKunojiBase) || !right.every(isKunojiBase)) continue;
      // Avoid generating multi-char marks for a single repeated character (e.g. ああああ).
      if (new Set(left).size < 2) continue;
      const tailMatch = left.slice(1).every((c, j) => c === right[j + 1]);
      if (!tailMatch) continue;
      const exact = left[0] === right[0];
      const voicedHead = !exact && VOICE.get(left[0]) === right[0];
      if (!exact && !voicedHead) continue;
      const before = parts.slice(0, i), after = parts.slice(i + k);
      if (voicedHead) {
        add([...before, '〲', ...after]);
        add([...before, '〴', '〵', ...after]);
      } else {
        if (left.some(isKana)) {
          add([...before, '〱', ...after]);
          add([...before, '〳', '〵', ...after]);
        }
        if (k === 2 && left.every(isHan)) {
          add([...before, '々', '々', ...after]);
          add([...before, '〻', '〻', ...after]);
        }
      }
    }
  }

  // Single-character repeat contractions at each repeated position.
  type Option = { index: number; marks: string[] };
  const options: Option[] = [];
  for (let i = 1; i < parts.length; i++) {
    const prev = parts[i - 1], cur = parts[i];
    if (isKana(prev) && isKana(cur)) {
      if (prev === cur) {
        if (UNVOICE.has(prev)) {
          options.push({ index: i, marks: [...KANA_VOICED, ...KANA_UNVOICED] });
        } else {
          // Attested 2-char kana runs also use 〱 (e.g. い〱 in ATLAS_RUNS).
          const marks: string[] = [...KANA_UNVOICED];
          if (parts.length === 2) marks.push('〱');
          options.push({ index: i, marks });
        }
      } else if (VOICE.get(prev) === cur) {
        const marks: string[] = [...KANA_VOICED];
        if (parts.length === 2) marks.push('〲');
        options.push({ index: i, marks });
      } else if (UNVOICE.get(prev) === cur) {
        options.push({ index: i, marks: [...KANA_UNVOICED] });
      }
    } else if (prev === cur && isHan(prev)) {
      options.push({ index: i, marks: [...HAN_MARKS] });
    } else if (prev === cur && isTangut(prev)) {
      options.push({ index: i, marks: [TANGUT_MARK] });
    } else if (prev === cur && isNushu(prev)) {
      options.push({ index: i, marks: [NUSHU_MARK] });
    }
  }

  // Single-position substitutions first so primary forms (e.g. かゝ, かヽ, 人々, 人〻) are never crowded out.
  for (const opt of options) {
    for (const mark of opt.marks) {
      if (results.length >= maxVariants) break;
      const copy = [...parts];
      copy[opt.index] = mark;
      add(copy);
    }
  }

  // Multi-position substitution when a query repeats at several places (e.g. ははすず → はゝすゞ / はヽすヾ,
  // ああああ → あゝゝゝ / あヽヽヽ, 人人はは → 人々はゝ / 人〻はゝ).
  if (options.length >= 2 && results.length < maxVariants) {
    const families = [
      new Set(['ゝ', 'ゞ', '々', TANGUT_MARK, NUSHU_MARK]),
      new Set(['ヽ', 'ヾ', '〻', TANGUT_MARK, NUSHU_MARK]),
      new Set(['ゝ', 'ゞ', '〻', TANGUT_MARK, NUSHU_MARK]),
      new Set(['ヽ', 'ヾ', '々', TANGUT_MARK, NUSHU_MARK]),
    ];
    for (const family of families) {
      if (results.length >= maxVariants) break;
      const chosen = options.map(opt => opt.marks.find(m => family.has(m)) ?? opt.marks[0]);
      const copy = [...parts];
      options.forEach((opt, j) => { copy[opt.index] = chosen[j]; });
      add(copy);
    }
  }

  return results;
}

/**
 * All iteration-mark equivalents of `parts` in both directions (excluding `parts` itself):
 * - Spelled-out → iteration marks (`かか` → `かゝ`, `かヽ`, `か〱`; `かが` → `かゞ`, `かヾ`, `か〲`;
 *   `人人` → `人々`, `人〻`, `人〃`, `人𖿣`; `つれづれ` → `つれ〲`, `つれ〴〵`)
 * - Iteration marks → spelled-out and sibling marks (`かヽ` → `かか`, `かゝ`, `か〱`;
 *   `人々` → `人人`, `人〻`, `人〃`, `人𖿣`; `つれ〲` → `つれづれ`, `つれ〴〵`)
 */
export function odorijiVariants(parts: readonly string[], { maxVariants = 24, maxLength = 8 } = {}): string[] {
  if (parts.length < 2 || parts.length > maxLength) return [];
  const self = parts.join('');
  const out: string[] = [];
  const seen = new Set<string>([self]);
  const push = (seq: string[]) => {
    if (seq.length < 2 || seq.length > maxLength) return;
    const text = seq.join('');
    if (seen.has(text)) return;
    seen.add(text);
    out.push(text);
  };

  const expanded = hasOdoriji(parts) ? expandOdoriji(parts, maxLength) : [[...parts]];
  // Put spelled-out form(s) first when the query contains an iteration mark.
  for (const seq of expanded) {
    if (out.length >= maxVariants) break;
    push(seq);
  }
  for (const seq of expanded) {
    if (out.length >= maxVariants) break;
    for (const contracted of contractOne(seq, maxVariants - out.length, maxLength)) {
      if (out.length >= maxVariants) break;
      push(contracted);
    }
  }
  return out;
}
