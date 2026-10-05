<script>
  import Seo from '$components/Seo.svelte'
  import RunView from '$components/RunView.svelte'
  import { t } from '$lib/i18n.svelte.js'
  import { useInspector } from '$lib/inspector.svelte.js'
  let { data } = $props()
  const inspector = useInspector()
</script>

<Seo title={`${data.text} · ${t('nav.explore')}`} index={false} />

{#key [data.text, data.work, data.form, data.hand, data.sort].join('|')}
  <RunView text={data.text} work={data.work} form={data.form} hand={data.hand} sort={data.sort} first={data.first} related={data.related} inspect={inspector.inspect.bind(inspector)} />
{/key}
