<script>
  // The character a crop is written as, in its script's colour, and the script named beside it.
  import ReferenceGlyph from './ReferenceGlyph.svelte'
  import { scriptInfo } from '../lib/identity.js'
  import { t } from '../lib/i18n.svelte.js'
  let { char = '', script = '', size = 'lg' } = $props()
  // The script is the character's own, by its code point; the record's statement is used only for a
  // character the code point does not place. A hentaigana is coloured as hiragana and named for itself.
  const point = $derived([...char].length === 1 ? char.codePointAt(0) : 0)
  const hentaigana = $derived(point >= 0x1B002 && point <= 0x1B11F)
  const own = $derived(scriptInfo(char).key)
  const key = $derived(own === 'unknown' ? scriptInfo(char, script).key : own)
  const name = $derived(hentaigana ? t('script.hentaigana') : t(`script.${key}`))
</script>

<h2 class="crop-title"><ReferenceGlyph {char} script={key} {size} /><small class="script-name">{name}</small></h2>

<style>
  .crop-title{display:inline-flex;align-items:baseline;gap:10px}
  .crop-title :global(.reference-glyph){font-size:42px;line-height:1.3}
  .script-name{font-size:11px;font-weight:400;color:var(--muted);letter-spacing:.02em}
</style>
