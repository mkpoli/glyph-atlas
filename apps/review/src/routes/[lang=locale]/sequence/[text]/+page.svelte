<script>
  import Seo from '$components/Seo.svelte'
  import NgramView from '$components/NgramView.svelte'
  import { t } from '$lib/i18n.svelte.js'
  import { useInspector } from '$lib/inspector.svelte.js'
  let { data } = $props()
  const inspector = useInspector()
</script>

<Seo title={`${data.text} · ${t('nav.explore')}`} index={false} />

{#key data.text + '|' + data.work}
  <NgramView text={data.text} work={data.work} first={data.first} inspect={inspector.inspect.bind(inspector)} />
{/key}
