<script>
  import { scriptParts } from '../lib/identity.js'
  import { DRAWN_FONT, drawnForm } from '../lib/drawnForms.js'
  import { drawn, isDescription, loadDrawing } from '../lib/ids.js'
  import { paintDrawing, paintedDrawing } from '../lib/composer/paint.js'
  import { isFigure } from '../lib/mjFigures.js'
  import { figureFamilies } from '../lib/figures.js'
  import { tokens } from '../lib/decompose.js'
  import FigureText from './FigureText.svelte'
  import { t } from '../lib/i18n.svelte.js'
  // `lang` sets the language of every part, for a glyph shape a language decides (a variant drawn the
  // Chinese way); without it Hangul and 구결 read as Korean and the rest as Japanese. Each character
  // names its script on hover; `titled={false}` leaves the hover to a title around it.
  // A part written as an ideographic description sequence is drawn from the composer's drawing of it,
  // masked in its script's colour; where the site holds none, the browser draws it. It stays the
  // sequence's text until a drawing is ready or where none can be made. The drawing keeps the sequence
  // inside it, so a selection copies the text.
  // A registered figure, a character with an ideographic variation selector (突󠄃), is set in a font
  // that maps its sequence (`lib/figures.js`): any other draws the character without its selector.
  // The font can differ with the face the reader chose, so the part carries one for each, and a
  // sequence no font maps keeps the fonts around it. A description shown as text sets each part
  // that is a figure the same way.
  // A drawn form is set in the site's own font, the only one with a glyph at its code point: its sans
  // cut or its serif one, with the face the reader chose.
  // `simplified` is a 简体字 that ends a grapheme's label (`labelParts`), drawn the Chinese way.
  let { text = '', script = '', lang = '', titled = true, simplified = '' } = $props()
  // `scriptParts` returns a fixed English `label` for use outside a Svelte context (it is also
  // called directly by a plain-Node check); the label shown here is looked up again from `key` so
  // it follows the interface language.
  const parts = $derived([...scriptParts(text, script), ...scriptParts(simplified).map(part => ({ ...part, lang: 'zh-Hans' }))].map(part => ({ ...part, label: t(`script.${part.key}`), described: isDescription(part.text), figure: isFigure(part.text), drawn: Boolean(drawnForm(part.text)),
    // A drawn form is read out and titled by what it looks like: its code point says nothing.
    said: drawnForm(part.text)?.name ?? part.text })))
  const description = $derived(parts.map(part => `${part.said} · ${part.label}`).join(', '))
  // Bumped as a drawing loads, so the parts read the cache again.
  let loaded = $state(0)
  const drawings = $derived((void loaded, parts.map(part => part.described ? drawn(part.text) || paintedDrawing(part.text) || null : null)))
  const show = text => loadDrawing(text).then(url => url ?? paintDrawing(text)).then(url => { if (url) loaded++ })
  $effect(() => {
    for (const part of parts) if (part.described && !drawn(part.text) && paintedDrawing(part.text) === undefined) show(part.text)
  })
  let figured = $state({})
  $effect(() => { for (const part of parts) if (part.figure && !figured[part.text]) figureFamilies(part.text).then(families => { figured[part.text] = families }, () => {}) })
  const face = name => name ? `"${name}"` : null
  const language = ({ key, lang: own }) => own || lang || (key === 'hangul' || key === 'gugyeol' ? 'ko' : key === 'tangut' ? 'txg' : key === 'seal' ? 'lzh' : 'ja')
</script>

<span class="script-text" role="img" aria-label={description}>{#each parts as part, index (index)}{#if drawings[index]}<span class="script-char ids-drawing" data-script={part.key} lang={language(part)} title={titled ? `${part.said} · ${part.label}` : undefined} aria-hidden="true" style:--drawing={`url("${drawings[index]}")`}><span class="ids-copy">{part.text}</span></span>{:else}<span class="script-char" class:ids-text={part.described} class:figure={figured[part.text]} style:--figure-sans={face(figured[part.text]?.sans)} style:--figure-serif={face(figured[part.text]?.serif)} style:font-family={part.drawn ? DRAWN_FONT : null} data-script={part.key} lang={language(part)} title={titled ? `${part.said} · ${part.label}` : undefined} aria-hidden="true">{#if part.described}{#each tokens(part.text) as token, at (at)}{#if isFigure(token)}<FigureText text={token} />{:else}{token}{/if}{/each}{:else}{part.text}{/if}</span>{/if}{/each}</span>

<style>
  /* A figure in the font that maps its sequence under the reader's face, then the fonts around it. */
  .figure{font-family:var(--figure-sans,var(--char-face)),var(--char-font)}
  :global(:root[data-typeface=serif]) .figure{font-family:var(--figure-serif,var(--char-face)),var(--char-font)}
  .ids-drawing{position:relative;display:inline-block;width:1em;height:1em;vertical-align:-0.12em;background:currentColor;
    -webkit-print-color-adjust:exact;print-color-adjust:exact;
    -webkit-mask:var(--drawing) center/contain no-repeat;mask:var(--drawing) center/contain no-repeat}
  .ids-copy{position:absolute;inset:0;overflow:hidden;white-space:nowrap;color:transparent;clip-path:inset(50%)}
  @media (forced-colors: active){.ids-drawing{forced-color-adjust:none;background:CanvasText}}
</style>
