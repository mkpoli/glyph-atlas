<script>
  import Seo from '$components/Seo.svelte'
  import Chronology from '$views/Chronology.svelte'
  import { useInspector } from '$lib/inspector.svelte.js'
  import { t } from '$lib/i18n.svelte.js'
  let { data } = $props()
  const inspector = useInspector()
</script>

<Seo title={`${data.card.char} · ${t('nav.chronology')}`} description={t('meta.chronology.description', { character: data.card.char, codePoint: data.card.code_point })} />

{#key data.card.code_point}
  <Chronology card={data.card} options={data.options} first={data.axis} inspect={inspector.inspect.bind(inspector)} />
{/key}
