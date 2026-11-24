<script>
  import Seo from '$components/Seo.svelte'
  import Forms from '$views/Forms.svelte'
  import { t } from '$lib/i18n.svelte.js'
  import { graphemeLabel } from '$worker/graphemeLabel.ts'
  let { data } = $props()
  // The family on show, once it is read; until then the address's character names the page. The bare
  // address becomes the first family's, so only that one is indexed.
  let family = $state(null)
  const char = $derived(family?.char ?? (data.family ? String.fromCodePoint(parseInt(data.family.slice(2), 16)) : ''))
  const name = $derived(graphemeLabel(char))
</script>

{#if family}<Seo title={`${name} · ${t('nav.forms')}`} description={t('meta.forms.description', { character: name, codePoint: family.code_point, count: family.count, clusters: family.clusters })} />
{:else if char}<Seo title={`${name} · ${t('nav.forms')}`} description={t('meta.formsIndex.description')} />
{:else}<Seo title={t('nav.forms')} description={t('meta.formsIndex.description')} index={false} />{/if}

<Forms initialFamily={data.family} onshow={shown => family = shown} />
