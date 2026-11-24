<script>
  // A form as the picker and the form bar show it: a character in its script's colour, or a shape
  // Unicode lacks, named by its ideographic description sequence and marked as one, which `ScriptText`
  // draws in the colour of Han. Pointing at it opens its card (`IdsCard`); the empty title keeps
  // the title of what it stands in from showing over that. `stacked` sets the tag under the form, as a
  // card's label; `lang` the language whose fonts draw it (a regional shape); `inline` a form that is one
  // part of a larger control, which opens no card for it.
  import ScriptText from './ScriptText.svelte'
  import { isDescription } from '../lib/ids.js'
  import { idsCard } from '../lib/idsCard.js'
  import { t } from '../lib/i18n.svelte.js'
  let { text = '', script = '', lang = '', stacked = false, inline = false } = $props()
  const described = $derived(isDescription(text))
</script>

{#if described}
  <span class="ids-form" class:stacked title="" {@attach idsCard(text, { lang, inline })}><ScriptText {text} script="han" {lang} titled={false} /><small class="ids-tag">{t('form.ids.tag')}</small></span>
{:else}
  <ScriptText {text} {script} {lang} />
{/if}

<style>
  .ids-form{display:inline-flex;align-items:baseline;gap:4px}
  .ids-form:global([role=button]){cursor:help;border-radius:3px}
  .ids-form:focus-visible{outline:2px solid var(--focus-ring);outline-offset:2px}
  .ids-form.stacked{flex-direction:column;align-items:center;gap:2px}
  .stacked :global(.ids-drawing){width:1.9rem;height:1.9rem}
  .ids-form :global(.ids-text){font-size:0.8em;letter-spacing:-0.02em}
  .ids-tag{font-family:ui-monospace,monospace;font-size:9px;letter-spacing:0.04em;color:var(--muted);border:1px solid var(--line);border-radius:3px;padding:0 3px}
</style>
