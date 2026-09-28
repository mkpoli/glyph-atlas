<script>
  import Seo from '$components/Seo.svelte'
  import PairView from '$components/PairView.svelte'
  import { t } from '$lib/i18n.svelte.js'
  import { useInspector } from '$lib/inspector.svelte.js'
  let { data } = $props()
  const inspector = useInspector()
</script>

<Seo title={`${data.text} · ${t('explore.pairs')}`} index={false} />

{#key data.text + '|' + data.work}
  <PairView text={data.text} work={data.work} first={data.first} inspect={inspector.inspect.bind(inspector)} />
{/key}
