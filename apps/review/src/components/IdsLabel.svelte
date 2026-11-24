<script>
  // A 構形's label: its description, which opens its card (`IdsCard`), or a character with none. The
  // card of a `caption` leaves the forms under it clear.
  import { idsCard } from '../lib/idsCard.js'
  let { text = '', caption = false } = $props()
</script>

<span class="ids-label" lang="zh" {@attach idsCard(text, { side: caption })}>{text}</span>

<style>
  .ids-label[role=button]{cursor:help;border-radius:3px}
  .ids-label[role=button]:is(:hover,[aria-expanded=true]){color:var(--accent)}
  .ids-label:focus-visible{outline:2px solid var(--focus-ring);outline-offset:2px}
</style>
