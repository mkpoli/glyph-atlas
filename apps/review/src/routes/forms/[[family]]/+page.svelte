<script>
  import { page } from '$app/state'
  import Seo from '$components/Seo.svelte'
  import Forms from '$views/Forms.svelte'
  import { t } from '$lib/i18n.svelte.js'
  let { data } = $props()
  const family = $derived(data.initial.family)
  // The bare /forms address shows the first family but stands for the index.
  const listed = $derived(page.params.family && family && data.initial.list.find(item => item.code_point === family.code_point))
</script>

{#if listed}<Seo title={`${listed.char} · ${t('nav.forms')}`} description={t('meta.forms.description', { character: listed.char, codePoint: listed.code_point, count: listed.count, clusters: listed.clusters })} />
{:else}<Seo title={t('nav.forms')} description={t('meta.formsIndex.description')} />{/if}

<Forms initial={data.initial} />
