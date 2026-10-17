import { formProblem, isSequence } from './representation';

// A label's category is its first character's script, as the migrations and publication scripts compute it.
// 구결자 have no Unicode script property, so they are tested by their Hanyang private-use range.
export const isGugyeol=(value:string)=>{const c=value.codePointAt(0)??-1;return c>=0xF67E&&c<=0xF77C};
// A well-formed ideographic description sequence describes one Han character Unicode lacks.
export const idsCharacter = (value: string) => isSequence(value) && formProblem(value) === null;
export const categoryOf=(value:string)=>{const first=[...value][0]??'';if(idsCharacter(value))return 'kanji';return /[\p{Script=Hiragana}\p{Script=Katakana}]/u.test(first)?'kana':/\p{Script=Han}/u.test(first)?'kanji':/\p{Script=Hangul}/u.test(first)?'hangul':isGugyeol(first)?'gugyeol':'other'};

// The homepage gallery copies the corpus glyphs whose shuffle falls below SAMPLE_RANGE, and every glyph
// of the scripts the corpus holds few of (0079); `scripts/fill_corpus_gallery.py` copies them.
export const SAMPLE_RANGE = 4194304;
export const GALLERY_GROUPS = ['kana', 'kanji', 'hangul', 'gugyeol', 'other'], WHOLE_GROUPS = ['hangul', 'gugyeol'];
/** The statements that refile gallery copies whose glyph a decision has moved to `character`: each takes
 * its new category, or leaves the gallery when only its old script was copied whole. */
export function refiled(rows: { id: string; shuffle: number; category: string | null; character: string | null }[]) {
  return rows.flatMap(row => {
    const category = categoryOf(row.character ?? '');
    if (category === row.category) return [];
    return [row.shuffle >= SAMPLE_RANGE && !WHOLE_GROUPS.includes(category)
      ? { sql: 'DELETE FROM corpus_gallery WHERE id=?', values: [row.id] }
      : { sql: 'UPDATE corpus_gallery SET category=? WHERE id=?', values: [category, row.id] }];
  });
}
