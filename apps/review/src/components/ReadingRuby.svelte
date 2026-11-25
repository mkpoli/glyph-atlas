<script>
  // A run of characters under its reading (諸器 しょ・き), the crop's own character marked where others
  // stand beside it. `field` is the text over the run: one for each character, or one for them all.
  let { value, field = 'reading' } = $props()
  const over = $derived(value[field] ?? [])
  const marked = $derived(value.run.length > 1)
</script>

<ruby class="reading-run">{#if over.length === value.run.length}{#each value.run as part, i}<span class:here={marked && i === value.at}>{part}</span><rt>{over[i]}</rt>{/each}{:else}{#each value.run as part, i}<span class:here={marked && i === value.at}>{part}</span>{/each}<rt>{over[0]}</rt>{/if}</ruby>

<style>
  .reading-run{ruby-position:over}
  /* The reading is not part of the text a reader copies. */
  rt{font-size:.55em;line-height:1;color:var(--muted);user-select:none}
  .here{color:var(--accent)}
</style>
