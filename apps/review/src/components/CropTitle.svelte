<script>
  // The character a crop is written as, in its script's colour, and the script named beside it. The
  // character leads to its own page, with every crop of it.
  import ReferenceGlyph from './ReferenceGlyph.svelte'
  import { scriptInfo } from '../lib/identity.js'
  import { characterAddress } from '../lib/gallery.js'
  import { t, localize } from '../lib/i18n.svelte.js'
  let { char = '', script = '', size = 'lg' } = $props()
  // The script is the character's own, by its code point; the record's statement is used only for a
  // character the code point does not place. A hentaigana is coloured as hiragana and named for itself.
  const point = $derived([...char].length === 1 ? char.codePointAt(0) : 0)
  const hentaigana = $derived(point >= 0x1B002 && point <= 0x1B11E)
  const own = $derived(scriptInfo(char).key)
  const KNOWN = ['hiragana', 'katakana', 'kanji', 'hangul', 'gugyeol', 'symbol', 'mixed', 'unknown']
  const key = $derived(own !== 'unknown' ? own : KNOWN.includes(scriptInfo(char, script).key) ? scriptInfo(char, script).key : 'unknown')
  const name = $derived(hentaigana ? t('script.hentaigana') : t(`script.${key}`))
  const codes = $derived([...char].map(c => 'U+' + c.codePointAt(0).toString(16).toUpperCase().padStart(4, '0')).join(' '))
  const href = $derived(char ? localize(characterAddress(codes)) : null)
</script>

<h2 class="crop-title">{#if href}<a class="character-link" {href} aria-label={t('cropTitle.characterLink', { char })}><ReferenceGlyph {char} script={key} {size} /></a>{:else}<ReferenceGlyph {char} script={key} {size} />{/if}<small class="script-name">{name}</small></h2>

<style>
  .crop-title{display:inline-flex;align-items:baseline;gap:10px}
  .crop-title :global(.reference-glyph){font-size:42px;line-height:1.3}
  .character-link{color:inherit;text-decoration:none;border-radius:6px}
  .character-link:hover,.character-link:focus-visible{background:color-mix(in srgb, var(--accent) 12%, transparent)}
  .script-name{font-size:11px;font-weight:400;color:var(--muted);letter-spacing:.02em}
</style>
