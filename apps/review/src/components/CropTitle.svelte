<script>
  import { tick } from 'svelte'
  // The character a crop is written as, in its script's colour, and the script named beside it. Read-only,
  // the character leads to its own page. Given `reading` ({ item, disabled, working, saved }), it names the
  // grapheme (字種) the crop is read as instead: a holding claim for another grapheme shows that one beside
  // the character, and a press opens the grapheme picker. The space beside the character is held from the
  // first render, so the record's claim fills it without moving anything.
  import ReferenceGlyph from './ReferenceGlyph.svelte'
  import CopyValue from './CopyValue.svelte'
  import CropGrapheme from './CropGrapheme.svelte'
  import ScriptText from './ScriptText.svelte'
  import { claimedGrapheme, scriptInfo } from '../lib/identity.js'
  import { graphemeChoices } from '../lib/cropGraphemes.js'
  import { hasCharacterPage, isSingle, pointOf } from '../lib/issues.js'
  import { characterAddress } from '../lib/gallery.js'
  import { t, localize } from '../lib/i18n.svelte.js'
  let { char = '', script = '', size = 'lg', reading = null } = $props()
  // The script is the character's own, by its code point; the record's statement is used only for a
  // character the code point does not place. A hentaigana is coloured as hiragana and named for itself.
  const point = $derived(isSingle(char) ? [...char][0]?.codePointAt(0) ?? 0 : 0)
  const hentaigana = $derived(point >= 0x1B002 && point <= 0x1B11E)
  const own = $derived(scriptInfo(char).key)
  const KNOWN = ['hiragana', 'katakana', 'kanji', 'hangul', 'gugyeol', 'tangut', 'symbol', 'mixed', 'unknown']
  const key = $derived(own !== 'unknown' ? own : KNOWN.includes(scriptInfo(char, script).key) ? scriptInfo(char, script).key : 'unknown')
  const name = $derived(hentaigana ? t('script.hentaigana') : t(`script.${key}`))
  const href = $derived(hasCharacterPage(char) ? localize(characterAddress(pointOf(char))) : null)

  const claim = $derived(reading?.item?.grapheme_claim ?? null)
  const disputed = $derived(claim?.status === 'disputed')
  // The other grapheme a holding claim reads the crop as; null when none holds or it is the character's own.
  const other = $derived.by(() => {
    const filed = claimedGrapheme(reading?.item)
    const value = filed ? claim.values.find(v => v.grapheme === filed) : null
    return value && value.char !== char ? value : null
  })
  const label = $derived([other ? t('grapheme.readAs', { char, grapheme: other.char }) : null, disputed ? t('grapheme.disputed') : null,
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
  <h2 class="crop-title"><span class="title-glyph copyable">{#if reading}<button type="button" class="glyph-button" bind:this={button} aria-haspopup="dialog" aria-expanded={open}
      aria-label={label} title={label} disabled={reading.disabled} onpointerenter={prefetch} onfocus={prefetch} onclick={() => open = !open}><ReferenceGlyph {char} script={key} {size} /><span class="read-as" class:claimed={other} class:disputed aria-hidden="true">{#if other}<ScriptText text={other.char} titled={false} />{:else}<svg viewBox="0 0 12 12"><path d="M3 4.5l3 3 3-3" /></svg>{/if}</span></button>{:else if href}<a class="character-link" {href} aria-label={t('chips.showCharacter', { char })}><ReferenceGlyph {char} script={key} {size} /></a>{:else}<ReferenceGlyph {char} script={key} {size} />{/if}<CopyValue value={char} /></span><small class="script-name">{name}</small></h2>
  {#if reading && open}<CropGrapheme item={reading.item} {char} {href} disabled={reading.disabled} working={reading.working} saved={reading.saved} close={shut} />{/if}
</div>

<style>
  .crop-title-group{position:relative}
  .crop-title{display:inline-flex;align-items:baseline;gap:10px}
  .title-glyph{position:relative}
  .title-glyph :global(.copy-value){position:absolute;top:2px;right:-26px}
  .crop-title :global(.reference-glyph){font-size:42px;line-height:1.3}
  .character-link{color:inherit;text-decoration:none;border-radius:6px}
  .character-link:hover,.character-link:focus-visible{background:color-mix(in srgb, var(--accent) 12%, transparent)}
  /* The character and the grapheme it is read as, one button; the space after the character is the same
     whether it shows a claimed grapheme or the picker's caret. */
  .glyph-button{display:inline-flex;align-items:baseline;gap:2px;padding:0 4px 0 2px;border:0;border-radius:6px;background:transparent;font:inherit;line-height:inherit;color:inherit}
  .glyph-button:not(:disabled):hover,.glyph-button[aria-expanded="true"]{background:color-mix(in srgb, var(--accent) 12%, transparent)}
  .glyph-button:disabled{opacity:1;cursor:default}
  .read-as{display:inline-flex;align-items:center;justify-content:center;width:30px;height:30px;font-size:24px;line-height:1;font-weight:400;color:var(--muted);
    font-family:"Kureedo Kata","Klee One","LXGW WenKai TC","LXGW WenKai","GenZui Sans","Noto Serif Tangut",serif}
  .read-as svg{width:14px;height:14px;fill:none;stroke:currentColor;stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round}
  .read-as.claimed{color:var(--accent);border-bottom:2px solid var(--accent)}
  .read-as.disputed{color:var(--wrong)}
  .glyph-button:disabled .read-as svg{opacity:.4}
  .script-name{font-size:11px;font-weight:400;color:var(--muted);letter-spacing:.02em}
</style>
