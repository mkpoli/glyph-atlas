/** What an undo answered: how many of its crops it left as they are because a publication changed them
 *  since (`refused`), and how many it undid. The site answers 200 with each crop's outcome. */
export function undoOutcome(answer) {
  const results = Array.isArray(answer?.results) ? answer.results : []
  const refused = results.filter(r => r?.refused).length
  return { refused, undone: results.length - refused }
}
