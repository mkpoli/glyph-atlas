<script>
  import Seo from '$components/Seo.svelte'
  import NgramView from '$components/NgramView.svelte'
  import { ngramWords } from '$lib/ngrams.js'
  import { useInspector } from '$lib/inspector.svelte.js'
  let { data } = $props()
  const inspector = useInspector()
</script>

<Seo title={`${data.text} · ${ngramWords(data.kind).name()}`} index={false} />

{#key data.kind + '|' + data.text + '|' + data.work}
  <NgramView kind={data.kind} text={data.text} work={data.work} first={data.first} inspect={inspector.inspect.bind(inspector)} />
{/key}
