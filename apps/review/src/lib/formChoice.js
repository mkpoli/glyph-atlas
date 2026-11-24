// What a form chosen on a crop's bar saves: the claim alone, or the review that names another character
// and claims the form with it. `base` is the character the form is a form of (`baseOf`), `character` the
// crop's own ('' for one nobody has assigned).

/** The character a chosen form names as the crop's, or null where it names none or only the crop's own. A
 *  wrong character names its own, and the form beside it is not written. */
export const characterNamed = ({ form, base, character, issue }) =>
  form != null && base != null && base !== character && issue !== 'character' ? base : null

/** The review that names `character` and claims `form` with it, on the pixels and the version the reader saw.
 *  A bad crop stands beside the character, redrawn (`box`) or reported. */
export function namingReview(crop, { character, form, issues = [], correction = null, box = null }) {
  const bad = issues.includes('crop'), issue = issues.find(each => each !== 'crop') ?? 'character'
  return { revision: crop.revision, verdict: 'wrong', issue, character, form, crop_version: crop.crop_version,
    ...(correction ? { correction } : {}), ...(bad && box ? { box } : bad ? { bad_crop: true } : {}) }
}
