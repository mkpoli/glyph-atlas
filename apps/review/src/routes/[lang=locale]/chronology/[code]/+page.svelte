<script>
  import Seo from '$components/Seo.svelte'
  import { graphemeLabel } from '$worker/graphemeLabel.ts'
  import Chronology from '$views/Chronology.svelte'
  import { useInspector } from '$lib/inspector.svelte.js'
  import { t } from '$lib/i18n.svelte.js'
  let { data } = $props()
  const inspector = useInspector()
  const name = $derived(data.options.scope === 'grapheme' ? graphemeLabel(data.card.grapheme?.char ?? data.card.char) : data.card.char)
</script>

<Seo title={`${name} · ${t('nav.chronology')}`} description={t('meta.chronology.description', { character: name, codePoint: data.card.code_point })} />

{#key data.card.code_point}
  <Chronology card={data.card} options={data.options} first={data.axis} inspect={inspector.inspect.bind(inspector)} />
{/key}
