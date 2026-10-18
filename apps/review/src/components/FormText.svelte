<script>
  // A form as the picker and the form bar show it: a character in its script's colour, or a shape
  // Unicode lacks, named by its ideographic description sequence and marked as one. Such a form is
  // drawn from the composer's drawing of the sequence (`drawingKey`, served under /atlas/ids/) in the
  // colour of Han, and shown as the sequence's text until the drawing loads or where none is made.
  // Hovering shows the sequence as written. A registered figure, a character with an ideographic
  // variation selector, is drawn in MJ Figures, the font that maps its sequence. `stacked` sets the
  // tag under the form, as a card's label; `lang` the language whose fonts draw it (a regional shape).
  import ScriptText from './ScriptText.svelte'
  import { drawingKey, isDescription } from '../lib/ids.js'
  import { FAMILY, drawFigure, isFigure } from '../lib/mjFigures.js'
  import { t } from '../lib/i18n.svelte.js'
  let { text = '', script = '', lang = '', stacked = false } = $props()
  const described = $derived(isDescription(text))
  const figure = $derived(isFigure(text))
  let drawing = $state(null)

  $effect(() => { if (figure) drawFigure(text).catch(() => {}) })

  $effect(() => {
    drawing = null
    if (!described) return
    let current = true
    drawingKey(text).then(key => {
      const url = `/atlas/${key}`, image = new Image()
      image.onload = () => { if (current) drawing = url }
      image.src = url
    }, () => {})
    return () => { current = false }
  })
</script>

{#if described}
  <span class="ids-form" class:stacked title={text}>{#if drawing}<span class="ids-drawing script-char" data-script="kanji" role="img" aria-label={text} style:--drawing="url({drawing})"></span>{:else}<span class="ids-text"><ScriptText {text} script="han" {lang} titled={false} /></span>{/if}<small class="ids-tag">{t('form.ids.tag')}</small></span>
{:else if figure}
  <span class="figure-form" style:font-family={`"${FAMILY}", serif`}><ScriptText {text} {script} /></span>
{:else}
  <ScriptText {text} {script} {lang} />
{/if}

<style>
  .ids-form{display:inline-flex;align-items:baseline;gap:4px}
  .ids-form.stacked{flex-direction:column;align-items:center;gap:2px}
  .stacked .ids-drawing{width:1.9rem;height:1.9rem}
  .ids-text{font-size:0.8em;letter-spacing:-0.02em}
  .ids-drawing{display:inline-block;width:1em;height:1em;align-self:center;background:currentColor;
    -webkit-mask:var(--drawing) center/contain no-repeat;mask:var(--drawing) center/contain no-repeat}
  @media (forced-colors: active){.ids-drawing{forced-color-adjust:none;background:CanvasText}}
  .ids-tag{font-family:ui-monospace,monospace;font-size:9px;letter-spacing:0.04em;color:var(--muted);border:1px solid var(--line);border-radius:3px;padding:0 3px}
</style>
