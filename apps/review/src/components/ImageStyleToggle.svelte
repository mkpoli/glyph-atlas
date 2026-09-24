<script>
  // A manuscript scan is a colour photograph of paper and ink, so it is shown as it was captured by
  // default. Black and white stays available because a reader comparing two hands may want the ink
  // alone. The preference is one value for the whole interface, held by the app and remembered.
  let { ink = 'original', onchange = () => {} } = $props()
  const modes = [['original', 'Original'], ['bw', 'B&W']]
</script>

<div class="ink-toggle" role="group" aria-label="Image colour">
  {#each modes as [value, text]}<button class:active={ink === value} aria-pressed={ink === value}
    onclick={() => onchange(value)}>{text}</button>{/each}
</div>
