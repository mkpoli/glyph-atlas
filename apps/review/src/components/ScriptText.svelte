<script>
  import { scriptParts } from '../lib/identity.js'
  import { drawn, isDescription, loadDrawing } from '../lib/ids.js'
  import { drawingSvgUrl, inlineDrawing } from '../lib/composer/inline.js'
  import { t } from '../lib/i18n.svelte.js'
  // `lang` sets the language of every part, for a glyph shape a language decides (a variant drawn the
  // Chinese way); without it Hangul and 구결 read as Korean and the rest as Japanese. Each character
  // names its script on hover; `titled={false}` leaves the hover to a title around it.
  // A part written as an ideographic description sequence is drawn from the export's own image where
  // it has one; `composer` asks the browser composer for the form where the export has no image, so a
  // shape Unicode lacks shows its generated character. It stays the sequence's text until the drawing
  // arrives or where none is made. The drawing keeps the sequence inside it, so a selection copies it.
  let { text = '', script = '', lang = '', titled = true, composer = false } = $props()
  // `scriptParts` returns a fixed English `label` for use outside a Svelte context (it is also
  // called directly by a plain-Node check); the label shown here is looked up again from `key` so
  // it follows the interface language.
  const parts = $derived(scriptParts(text, script).map(part => ({ ...part, label: t(`script.${part.key}`), described: isDescription(part.text) })))
  const description = $derived(parts.map(part => `${part.text} · ${part.label}`).join(', '))
  // A page can hold many forms behind closed details, so the composer is asked only once the text
  // has come near the window.
  let root = $state(/** @type {HTMLElement | null} */ (null))
  let visible = $state(false)
  $effect(() => {
    const element = composer ? root : null
    if (!element || visible) return
    if (typeof IntersectionObserver === 'undefined') { visible = true; return }
    const observer = new IntersectionObserver(entries => {
      if (!entries.some(entry => entry.isIntersecting)) return
      visible = true
      observer.disconnect()
    }, { rootMargin: '240px' })
    observer.observe(element)
    return () => observer.disconnect()
  })
  // Bumped as a drawing loads, so the parts read the cache again.
  let loaded = $state(0)
  // The browser composer's drawings, by sequence, where the export holds no image of it.
  let composed = $state.raw(/** @type {Record<string, string>} */ ({}))
  const drawings = $derived((void loaded, parts.map(part => part.described ? drawn(part.text) || composed[part.text] || null : null)))
  // The export's own image is tried first, as it always was; only a miss is put to the browser
  // composer, and only where the caller wants a generated character.
  $effect(() => {
    for (const part of parts) if (part.described && drawn(part.text) === undefined) loadDrawing(part.text).then(url => { if (url || composer) loaded++ })
  })
  $effect(() => {
    if (!composer || !visible) return
    void loaded
    let live = true
    for (const part of parts) {
      if (!part.described || drawn(part.text) !== null || composed[part.text]) continue
      inlineDrawing(part.text).then(drawing => {
        if (!live || !drawing) return
        const url = drawingSvgUrl(drawing)
        if (url) composed = { ...composed, [part.text]: url }
      })
    }
    return () => { live = false }
  })
  const language = key => lang || (key === 'hangul' || key === 'gugyeol' ? 'ko' : key === 'tangut' ? 'txg' : 'ja')
</script>

<span class="script-text" bind:this={root} role="img" aria-label={description}>{#each parts as part, index (index)}{#if drawings[index]}<span class="script-char ids-drawing" data-script={part.key} lang={language(part.key)} title={titled ? `${part.text} · ${part.label}` : undefined} aria-hidden="true" style:--drawing={`url("${drawings[index]}")`}><span class="ids-copy">{part.text}</span></span>{:else}<span class="script-char" class:ids-text={part.described} data-script={part.key} lang={language(part.key)} title={titled ? `${part.text} · ${part.label}` : undefined} aria-hidden="true">{part.text}</span>{/if}{/each}</span>

<style>
  .ids-drawing{position:relative;display:inline-block;width:1em;height:1em;vertical-align:-0.12em;background:currentColor;
    -webkit-print-color-adjust:exact;print-color-adjust:exact;
    -webkit-mask:var(--drawing) center/contain no-repeat;mask:var(--drawing) center/contain no-repeat}
  .ids-copy{position:absolute;inset:0;overflow:hidden;white-space:nowrap;color:transparent;clip-path:inset(50%)}
  @media (forced-colors: active){.ids-drawing{forced-color-adjust:none;background:CanvasText}}
</style>
