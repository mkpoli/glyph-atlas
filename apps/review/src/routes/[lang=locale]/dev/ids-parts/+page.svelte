<script>
  import IdsParts from '$components/IdsParts.svelte'
  let picked = $state('')
</script>

<main class="dev-ids-parts">
  <IdsParts onpick={part => picked += part} />
  <output>{picked}</output>
</main>

<style>
  .dev-ids-parts{max-width:420px;margin:24px auto;padding:0 16px;display:flex;flex-direction:column;gap:12px}
  output{font-size:24px;min-height:1.4em}
</style>
