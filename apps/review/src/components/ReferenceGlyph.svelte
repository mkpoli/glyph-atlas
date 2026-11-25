<script>
  import ScriptText from './ScriptText.svelte'
  // A character label drawn as text, which is a different thing from a crop of ink. The site's faces
  // draw the Han ideographs and the kana, and GenZui Sans the historic kana and the kana ligatures;
  // where none covers a code point, the label falls back to the code point itself rather than to a
  // blank box: a missing glyph shown as missing, never as a similar-looking character.
  // `document.fonts.check` is not used: it answers whether a font loaded, not whether it has the
  // glyph, so coverage is stated here from the file's own cmap.
  let { char = '', code_point = '', size = 'md', showCodePoint = false, script = '', simplified = '' } = $props()
  // Read from `fonts/GenZuiSans-Kana.woff2`.
  const bundled = [[0x1aff0, 0x1aff3], [0x1aff5, 0x1affb], [0x1affd, 0x1affe], [0x1b000, 0x1b128],
                   [0x1b132, 0x1b132], [0x1b150, 0x1b152], [0x1b155, 0x1b155], [0x1b164, 0x1b168]]
  // The blocks a reader's own fonts almost never reach, where a missing glyph is the likely outcome
  // and the code point has to stand in for it. U+F67E–U+F77C are the 구결자 of the Hanyang
  // private-use convention: only a font made for it draws them, and another private-use font would
  // draw an unrelated character there.
  const historic = [[0x1aff0, 0x1b1ff], [0xf67e, 0xf77c]]
  const points = $derived([...char + simplified].map(c => c.codePointAt(0)))
  const inRanges = (ranges, point) => ranges.some(([low, high]) => point >= low && point <= high)
  const shown = $derived(points.length > 0 && !points.some(p => inRanges(historic, p) && !inRanges(bundled, p)))
  const label = $derived(code_point || points.map(p => `U+${p.toString(16).toUpperCase().padStart(4, '0')}`).join(' '))
</script>

<span class="reference-glyph {size}" class:uncovered={!shown}
      title={code_point ? `${char} ${code_point}` : char + simplified}>
  {#if shown}<ScriptText text={char} {script} {simplified} />{:else}<span class="glyph-fallback" lang="ja">{label}</span>{/if}
</span>
{#if showCodePoint && shown && code_point}<small class="glyph-codepoint">{code_point}</small>{/if}
