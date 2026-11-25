<script>
  import { tick } from 'svelte'
  // The character a crop is written as, in its script's colour, and the script named beside it; the
  // character leads to the page of the grapheme it is a form of. Given `reading` ({ item, disabled, working,
  // saved }), a button beside the character opens the grapheme (字種) picker, and a crop a holding claim reads as
  // another grapheme is titled by that one, alone. The button's space is held from the first render.
  import ReferenceGlyph from './ReferenceGlyph.svelte'
  import CopyValue from './CopyValue.svelte'
  import CropGrapheme from './CropGrapheme.svelte'
  import { readAs, scriptInfo } from '../lib/identity.js'
  import { labelParts } from '$worker/graphemeLabel.ts'
  import { graphemeChoices } from '../lib/cropGraphemes.js'
  import { hasCharacterPage, isSingle, pointOf } from '../lib/issues.js'
  import { graphemeAddress } from '../lib/addresses.js'
  import { keyText } from '../lib/codePoints.js'
  import { t, localize } from '../lib/i18n.svelte.js'
  let { char: written = '', script = '', size = 'lg', reading = null } = $props()
  // The other grapheme a holding claim reads the crop as, which then titles it.
  const other = $derived(readAs(reading?.item))
  const char = $derived(other?.char ?? written)
  const shown = $derived(other ? labelParts(other.char) : { text: char, simplified: '' })
  // The script is the character's own, by its code point; the record's statement is used only for a
  // character the code point does not place. A hentaigana is coloured as hiragana and named for itself.
  const point = $derived(isSingle(char) ? [...char][0]?.codePointAt(0) ?? 0 : 0)
  const hentaigana = $derived(point >= 0x1B002 && point <= 0x1B11E)
  const own = $derived(scriptInfo(char).key)
  const KNOWN = ['hiragana', 'katakana', 'kanji', 'hangul', 'gugyeol', 'tangut', 'seal', 'symbol', 'mixed', 'unknown']
  const key = $derived(own !== 'unknown' ? own : KNOWN.includes(scriptInfo(char, script).key) ? scriptInfo(char, script).key : 'unknown')
  const name = $derived(hentaigana ? t('script.hentaigana') : t(`script.${key}`))
  // The record's own grapheme while the title shows the record's character, so the link needs no redirect.
  const head = $derived.by(() => {
    if (other) return other.grapheme
    const item = reading?.item, filed = item?.grapheme?.code_point ?? item?.grapheme
    return typeof filed === 'string' && char === (item.written_character ?? item.label) && hasCharacterPage(keyText(filed) ?? '') ? filed : pointOf(char)
  })
  const href = $derived(hasCharacterPage(char) ? localize(graphemeAddress(head, keyText(head) ?? char)) : null)

  const claim = $derived(reading?.item?.grapheme_claim ?? null)
  const disputed = $derived(claim?.status === 'disputed')
  const label = $derived([other ? t('grapheme.readAs', { char: written, grapheme: other.char }) : char, disputed ? t('grapheme.disputed') : null,
    t('grapheme.change')].filter(Boolean).join('\n'))
  let open = $state(false), button = $state(null)
  // Another crop starts with the picker shut.
  const shownId = $derived(reading ? reading.item?.id ?? null : null)
  $effect(() => { void shownId; open = false })
  // The picker's graphemes are asked for as the pointer or the focus reaches the character, so they are
  // usually in when it opens.
  const prefetch = () => { const item = reading?.item; if (item) graphemeChoices(item.written_character ?? item.label ?? '').catch(() => {}) }
  // The focus returns to the character once a save has enabled it again.
  function shut(refocus = true) { open = false; if (refocus) tick().then(() => button?.focus({ preventScroll: true })) }
</script>

<div class="crop-title-group">
  <h2 class="crop-title"><span class="title-glyph copyable">{#if href}<a class="character-link" {href} aria-label={t('chips.showCharacter', { char })}><ReferenceGlyph char={shown.text} simplified={shown.simplified} script={key} {size} /></a>{:else}<ReferenceGlyph char={shown.text} simplified={shown.simplified} script={key} {size} />{/if}{#if reading}<button type="button" class="glyph-button" bind:this={button} aria-haspopup="dialog" aria-expanded={open}
      aria-label={label} title={label} disabled={reading.disabled} onpointerenter={prefetch} onfocus={prefetch} onclick={() => open = !open}><span class="read-as" class:claimed={other} class:disputed aria-hidden="true"><svg viewBox="0 0 12 12"><path d="M3 4.5l3 3 3-3" /></svg></span></button>{/if}<CopyValue value={char} /></span><small class="script-name">{name}</small></h2>
  {#if reading && open}<CropGrapheme item={reading.item} disabled={reading.disabled} working={reading.working} saved={reading.saved} close={shut} />{/if}
</div>

<style>
  .crop-title-group{position:relative}
  .crop-title{display:inline-flex;align-items:center;gap:8px}
  .title-glyph{display:inline-flex;align-items:center;gap:4px}
  .crop-title :global(.reference-glyph){font-size:42px;line-height:1.3}
  .character-link{color:inherit;text-decoration:none;border-radius:6px}
  .character-link:hover,.character-link:focus-visible{background:color-mix(in srgb, var(--accent) 12%, transparent)}
  .glyph-button{display:inline-flex;padding:2px;border:0;border-radius:6px;background:transparent;font:inherit;line-height:1;color:inherit}
  .glyph-button:not(:disabled):hover,.glyph-button[aria-expanded="true"]{background:color-mix(in srgb, var(--accent) 12%, transparent)}
  .glyph-button:disabled{opacity:1;cursor:default}
  .read-as{display:inline-flex;align-items:center;justify-content:center;width:30px;height:30px;font-size:24px;line-height:1;font-weight:400;color:var(--muted);
    font-family:var(--char-font)}
  .read-as svg{width:14px;height:14px;fill:none;stroke:currentColor;stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round}
  .read-as.claimed{color:var(--accent)}
  .read-as.disputed{color:var(--wrong)}
  .glyph-button:disabled .read-as svg{opacity:.4}
  .script-name{font-size:11px;font-weight:400;color:var(--muted);letter-spacing:.02em}
</style>
