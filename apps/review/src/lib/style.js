// The style groups a gallery is filtered and ordered by, as the site's `style_order` numbers them
// (migration 0035): running and cursive script, then what nobody has judged, then the formal scripts
// and the print faces.
export const STYLE_GROUPS = ['cursive', 'unassessed', 'formal']
/** The values of `data/vocab/style.yaml` in each group; `mixed` passes nothing down, so it is unjudged. */
export const STYLES_IN = {
  cursive: ['running', 'cursive'],
  unassessed: ['unassessed'],
  formal: ['seal', 'clerical', 'regular', 'ming', 'gothic'],
}
const GROUP_OF = Object.fromEntries(Object.entries(STYLES_IN).flatMap(([group, kinds]) => kinds.map(kind => [kind, group])))
/** A crop's place in the order: its style group's index. A crop that states no style is unjudged. */
export const styleRank = item => STYLE_GROUPS.indexOf(GROUP_OF[item?.style] ?? 'unassessed')
/** A style group an address may name, or '' for all of them. */
export const styleParam = value => STYLE_GROUPS.includes(value) ? value : ''
