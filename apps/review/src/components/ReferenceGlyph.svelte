<script>
  import ScriptText from './ScriptText.svelte'
  // A character label drawn as text, which is a different thing from a crop of ink. Two bundled
  // fonts cover the historic kana and 𪜈; where neither covers a code point, the label falls back to
  // the code point itself rather than to a blank box — a missing glyph shown as missing, never as a
  // similar-looking character. `document.fonts.check` is not used: it answers whether a font loaded,
  // not whether it has the glyph, so coverage is stated here from the two files' own cmaps.
  let { char = '', code_point = '', size = 'md', showCodePoint = false, script = '' } = $props()
  const family = '"Kureedo Kata", "Noto Serif Hentaigana", system-ui, sans-serif'
  // Read from `fonts/KureedoKata-Regular.woff2` and `fonts/NotoSerifHentaigana-Regular.ttf`.
  const bundled = [[0x3000, 0x303f], [0x3099, 0x309c], [0x30a1, 0x30fe], [0x31f0, 0x31ff],
                   [0x2a708, 0x2a708], [0x1b000, 0x1b11e], [0x1b120, 0x1b122], [0x1b127, 0x1b128]]
  // The blocks a reader's own fonts almost never reach, where a missing glyph is the likely outcome
  // and the code point has to stand in for it.
  const historic = [[0x1b000, 0x1b1ff]]
  const points = $derived([...char].map(c => c.codePointAt(0)))
  const inRanges = (ranges, point) => ranges.some(([low, high]) => point >= low && point <= high)
  const shown = $derived(points.length > 0 && !points.some(p => inRanges(historic, p) && !inRanges(bundled, p)))
  const label = $derived(code_point || points.map(p => `U+${p.toString(16).toUpperCase().padStart(4, '0')}`).join(' '))
</script>

<span class="reference-glyph {size}" class:uncovered={!shown} style="font-family:{family}"
      title={code_point ? `${char} ${code_point}` : char}>
  {#if shown}<ScriptText text={char} {script} />{:else}<span class="glyph-fallback">{label}</span>{/if}
</span>
{#if showCodePoint && shown && code_point}<small class="glyph-codepoint">{code_point}</small>{/if}
