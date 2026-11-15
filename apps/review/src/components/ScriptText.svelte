<script>
  import { scriptParts } from '../lib/identity.js'
  import { drawn, isDescription, loadDrawing } from '../lib/ids.js'
  import { paintDrawing, paintedDrawing } from '../lib/composer/paint.js'
  import { FAMILY, drawFigure, isFigure } from '../lib/mjFigures.js'
  import { t } from '../lib/i18n.svelte.js'
  // `lang` sets the language of every part, for a glyph shape a language decides (a variant drawn the
  // Chinese way); without it Hangul and 구결 read as Korean and the rest as Japanese. Each character
  // names its script on hover; `titled={false}` leaves the hover to a title around it.
  // A part written as an ideographic description sequence is drawn from the composer's drawing of it,
  // masked in its script's colour; where the site holds none, the browser draws it. It stays the
  // sequence's text until a drawing is ready or where none can be made. The drawing keeps the sequence
  // inside it, so a selection copies the text.
  // A registered figure, a character with an ideographic variation selector (突󠄃), is drawn in MJ
  // Figures, the font that maps its sequence, once the file that holds it has loaded; any other font
  // draws the character without its selector, and a sequence the font lacks keeps the fonts around it.
  let { text = '', script = '', lang = '', titled = true } = $props()
  // `scriptParts` returns a fixed English `label` for use outside a Svelte context (it is also
  // called directly by a plain-Node check); the label shown here is looked up again from `key` so
  // it follows the interface language.
  const parts = $derived(scriptParts(text, script).map(part => ({ ...part, label: t(`script.${part.key}`), described: isDescription(part.text), figure: isFigure(part.text) })))
  const description = $derived(parts.map(part => `${part.text} · ${part.label}`).join(', '))
  // Bumped as a drawing loads, so the parts read the cache again.
  let loaded = $state(0)
  const drawings = $derived((void loaded, parts.map(part => part.described ? drawn(part.text) || paintedDrawing(part.text) || null : null)))
  const show = text => loadDrawing(text).then(url => url ?? paintDrawing(text)).then(url => { if (url) loaded++ })
  $effect(() => {
    for (const part of parts) if (part.described && !drawn(part.text) && paintedDrawing(part.text) === undefined) show(part.text)
  })
  let figured = $state({})
  $effect(() => { for (const part of parts) if (part.figure && !figured[part.text]) drawFigure(part.text).then(() => { figured[part.text] = true }, () => {}) })
  const language = key => lang || (key === 'hangul' || key === 'gugyeol' ? 'ko' : key === 'tangut' ? 'txg' : 'ja')
</script>

<span class="script-text" role="img" aria-label={description}>{#each parts as part, index (index)}{#if drawings[index]}<span class="script-char ids-drawing" data-script={part.key} lang={language(part.key)} title={titled ? `${part.text} · ${part.label}` : undefined} aria-hidden="true" style:--drawing={`url("${drawings[index]}")`}><span class="ids-copy">{part.text}</span></span>{:else}<span class="script-char" class:ids-text={part.described} style:font-family={figured[part.text] ? `"${FAMILY}", serif` : null} data-script={part.key} lang={language(part.key)} title={titled ? `${part.text} · ${part.label}` : undefined} aria-hidden="true">{part.text}</span>{/if}{/each}</span>

<style>
  .ids-drawing{position:relative;display:inline-block;width:1em;height:1em;vertical-align:-0.12em;background:currentColor;
    -webkit-print-color-adjust:exact;print-color-adjust:exact;
    -webkit-mask:var(--drawing) center/contain no-repeat;mask:var(--drawing) center/contain no-repeat}
  .ids-copy{position:absolute;inset:0;overflow:hidden;white-space:nowrap;color:transparent;clip-path:inset(50%)}
  @media (forced-colors: active){.ids-drawing{forced-color-adjust:none;background:CanvasText}}
</style>
