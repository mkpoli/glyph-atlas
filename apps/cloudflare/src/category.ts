import { formProblem, isSequence } from './representation';

// A label's category is its first character's script, as the migrations and publication scripts compute it.
// 구결자 have no Unicode script property, so they are tested by their Hanyang private-use range.
export const isGugyeol=(value:string)=>{const c=value.codePointAt(0)??-1;return c>=0xF67E&&c<=0xF77C};
// A well-formed ideographic description sequence describes one Han character Unicode lacks.
export const idsCharacter = (value: string) => isSequence(value) && formProblem(value) === null;
export const categoryOf=(value:string)=>{const first=[...value][0]??'';if(idsCharacter(value))return 'kanji';return /[\p{Script=Hiragana}\p{Script=Katakana}]/u.test(first)?'kana':/\p{Script=Han}/u.test(first)?'kanji':/\p{Script=Hangul}/u.test(first)?'hangul':isGugyeol(first)?'gugyeol':'other'};

/** The statements that give gallery copies whose glyph a decision has moved to `character` its new
 * category. A copy is never removed here, so undoing the decision files it back. */
export function refiled(rows: { id: string; category: string | null; character: string | null }[]) {
  return rows.flatMap(row => {
    const category = categoryOf(row.character ?? '');
    return category === row.category ? [] : [{ sql: 'UPDATE corpus_gallery SET category=? WHERE id=?', values: [category, row.id] }];
  });
}
