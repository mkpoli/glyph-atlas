<script>
  // A form as the picker and the form bar show it: a character in its script's colour, or a shape
  // Unicode lacks, named by its ideographic description sequence and marked as one, which `ScriptText`
  // draws in the colour of Han. Hovering shows the sequence as written. A registered figure, a
  // character with an ideographic variation selector, is drawn in MJ Figures, the font that maps its
  // sequence. `stacked` sets the tag under the form, as a card's label; `lang` the language whose fonts
  // draw it (a regional shape).
  import ScriptText from './ScriptText.svelte'
  import { isDescription } from '../lib/ids.js'
  import { FAMILY, drawFigure, isFigure } from '../lib/mjFigures.js'
  import { t } from '../lib/i18n.svelte.js'
  let { text = '', script = '', lang = '', stacked = false } = $props()
  const described = $derived(isDescription(text))
  const figure = $derived(isFigure(text))

  $effect(() => { if (figure) drawFigure(text).catch(() => {}) })
</script>

{#if described}
  <span class="ids-form" class:stacked title={text}><ScriptText {text} script="han" {lang} titled={false} /><small class="ids-tag">{t('form.ids.tag')}</small></span>
{:else if figure}
  <span class="figure-form" style:font-family={`"${FAMILY}", serif`}><ScriptText {text} {script} /></span>
{:else}
  <ScriptText {text} {script} {lang} />
{/if}

<style>
  .ids-form{display:inline-flex;align-items:baseline;gap:4px}
  .ids-form.stacked{flex-direction:column;align-items:center;gap:2px}
  .stacked :global(.ids-drawing){width:1.9rem;height:1.9rem}
  .ids-form :global(.ids-text){font-size:0.8em;letter-spacing:-0.02em}
  .ids-tag{font-family:ui-monospace,monospace;font-size:9px;letter-spacing:0.04em;color:var(--muted);border:1px solid var(--line);border-radius:3px;padding:0 3px}
</style>
