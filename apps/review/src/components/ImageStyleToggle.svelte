<script>
  import { t } from '../lib/i18n.svelte.js'
  // A manuscript scan is a colour photograph of paper and ink, so it is shown as it was captured by
  // default. Black and white stays available because a reader comparing two hands may want the ink
  // alone. The preference is one value for the whole interface, held by the app and remembered.
  let { ink = 'original', onchange = () => {} } = $props()
  const modes = [['original', () => t('ink.original')], ['bw', () => t('ink.bw')]]
</script>

<div class="ink-toggle" role="group" aria-label={t('ink.group.label')}>
  {#each modes as [value, text]}<button class:active={ink === value} aria-pressed={ink === value}
    onclick={() => onchange(value)}>{text()}</button>{/each}
</div>
