<script>
  import Chronology from '$views/Chronology.svelte'
  import { useInspector } from '$lib/inspector.svelte.js'
  let { data } = $props()
  const inspector = useInspector()
</script>

{#key data.card.code_point}
  <Chronology card={data.card} options={data.options} first={data.axis} inspect={inspector.inspect.bind(inspector)} />
{/key}
