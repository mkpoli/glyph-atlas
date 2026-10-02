<script>
  import ScriptText from './ScriptText.svelte'
  // A line of text, or a message with one character in it (`withText`), the character drawn in its
  // script's colour.
  let { line } = $props()
</script>

{#if typeof line === 'string'}{line}{:else}{line.before}<ScriptText text={line.text} />{line.after}{/if}
