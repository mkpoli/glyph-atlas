<script>
  import ScriptText from './ScriptText.svelte'
  // A modern kana's 字源: each kanji in its script's colour, marked "?" where the article 片仮名
  // contests it.
  let { origin = [], titled = true } = $props()
</script>

{#each origin ?? [] as o, i (i)}{#if i}{' '}{/if}<ScriptText text={o.char} {titled} />{o.uncertain ? '?' : ''}{/each}
