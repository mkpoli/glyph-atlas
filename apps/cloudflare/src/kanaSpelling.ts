// A reading typed in historical kana (歴史的仮名遣), read in the modern spelling the readings table keys
// its kanji by: さふらふ and さうらふ are そうろう, おもふ is おもう, しやう is しょう. The steps are the
// correspondences of 現代仮名遣い (内閣告示第一号, 1986): ゐ ゑ を and ぢ づ by their sound, は-row kana
// inside a word by their sound (ハ行転呼), then a vowel followed by う by the long vowel it is read as.
// A spelling may stop at any step (あふ is あう, 会う, before あう becomes おう), so every step's
// spelling is offered and the readings table decides which are readings.

const SOUND: Record<string, string> = { 'ゐ': 'い', 'ゑ': 'え', 'を': 'お', 'ぢ': 'じ', 'づ': 'ず' };
const INNER: Record<string, string> = { 'は': 'わ', 'ひ': 'い', 'ふ': 'う', 'へ': 'え', 'ほ': 'お' };
// Each kana by its row and vowel, to turn a vowel followed by う into the long vowel.
const ROWS = ['あいうえお', 'かきくけこ', 'がぎぐげご', 'さしすせそ', 'ざじずぜぞ', 'たちつてと', 'だぢづでど',
  'なにぬねの', 'はひふへほ', 'ばびぶべぼ', 'ぱぴぷぺぽ', 'まみむめも', 'らりるれろ'];
const AT = new Map(ROWS.flatMap((row, r) => [...row].map((kana, v) => [kana, [r, v]] as const)));
const SMALL: Record<string, string> = { 'や': 'ゃ', 'ゆ': 'ゅ', 'よ': 'ょ' };

/** ゝ and ゞ written out: the kana before them again, ゞ voiced. */
function repeated(text: string): string {
  const out: string[] = [];
  for (const c of text) {
    const last = out.at(-1);
    if ((c === 'ゝ' || c === 'ゞ') && last) {
      const at = AT.get(last);
      // か さ た は rows voice to the row after them.
      out.push(c === 'ゞ' && at && [1, 3, 5, 8].includes(at[0]) ? ROWS[at[0] + 1][at[1]] : last);
    } else out.push(c);
  }
  return out.join('');
}

/** The vowel and う pairs as long vowels: かう こう, きう きゅう, けう きょう, やう よう, いう ゆう, えう よう. */
function fused(text: string): string {
  const kana = [...text], out: string[] = [];
  for (let i = 0; i < kana.length; i++) {
    const c = kana[i], at = AT.get(c);
    if (kana[i + 1] !== 'う' || (!at && c !== 'や' && c !== 'わ')) { out.push(c); continue; }
    if (c === 'や') out.push('よ');
    else if (c === 'わ') out.push('お');
    else if (at![1] === 0) out.push(ROWS[at![0]][4]);
    else if (at![1] === 1) out.push(...(at![0] === 0 ? ['ゆ'] : [c, 'ゅ']));
    else if (at![1] === 3) out.push(...(at![0] === 0 ? ['よ'] : [ROWS[at![0]][1], 'ょ']));
    else { out.push(c); continue; }
    out.push('う'); i += 1;
  }
  return out.join('');
}

/** や ゆ よ written full size after an い-row kana, as small kana: しよう しょう. */
const contracted = (text: string) => text.replace(/([きしちにひみりぎじびぴ])([やゆよ])/g, (_, a: string, b: string) => a + SMALL[b]);

/** The modern spellings a hiragana reading may stand for, besides itself, in the order of the steps. */
export function modernSpellings(reading: string): string[] {
  const sounded = [...repeated(reading).replace(/[くぐ]わ/g, m => m[0] === 'く' ? 'か' : 'が')].map(c => SOUND[c] ?? c);
  const inner = sounded.map((c, i) => i > 0 ? INNER[c] ?? c : c).join('');
  const long = fused(inner);
  return [...new Set([inner, long, contracted(long)])].filter(spelling => spelling !== reading);
}
