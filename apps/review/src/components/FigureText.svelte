<script>
  // A registered figure, a character with an ideographic variation selector, set in a font that maps
  // its sequence under the reader's face (`lib/figures.js`); any other text is left to the fonts around it.
  import { isFigure } from '../lib/mjFigures.js'
  import { figureFamilies } from '../lib/figures.js'
  let { text = '' } = $props()
  let families = $state({})
  $effect(() => { const of = text; if (isFigure(of) && !families[of]) figureFamilies(of).then(found => { families[of] = found }, () => {}) })
  const face = name => name ? `"${name}"` : null
</script>

<span class:figure={families[text]} style:--figure-sans={face(families[text]?.sans)} style:--figure-serif={face(families[text]?.serif)}>{text}</span>

<style>
  .figure{font-family:var(--figure-sans,var(--char-face)),var(--char-font)}
  :global(:root[data-typeface=serif]) .figure{font-family:var(--figure-serif,var(--char-face)),var(--char-font)}
</style>
