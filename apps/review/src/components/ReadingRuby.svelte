<script>
  // A run of characters under the ruby the page writes beside it (諸器 𛁈よ・き), or bare, the crop's own
  // character marked where others stand beside it. The ruby is one text for each character, or one for them all.
  let { value } = $props()
  const over = $derived(value.ruby ?? [])
  const marked = $derived(value.run.length > 1)
</script>

<ruby class="reading-run">{#if over.length && over.length === value.run.length}{#each value.run as part, i}<span class:here={marked && i === value.at}>{part}</span><rt>{over[i]}</rt>{/each}{:else}{#each value.run as part, i}<span class:here={marked && i === value.at}>{part}</span>{/each}{#if over.length}<rt>{over[0]}</rt>{/if}{/if}</ruby>

<style>
  .reading-run{ruby-position:over}
  /* A selection copies the run without its ruby. */
  rt{font-size:.55em;line-height:1;color:var(--muted);user-select:none}
  .here{color:var(--accent)}
</style>
