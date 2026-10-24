<script>
  import Seo from '$components/Seo.svelte'
  import Forms from '$views/Forms.svelte'
  import { t } from '$lib/i18n.svelte.js'
  let { data } = $props()
  // The family on show, once it is read; until then the address's character names the page.
  let family = $state(null)
  const char = $derived(family?.char ?? (/^U\+[0-9A-F]{4,6}$/.test(data.family) ? String.fromCodePoint(parseInt(data.family.slice(2), 16)) : ''))
</script>

{#if family}<Seo title={`${family.char} · ${t('nav.forms')}`} description={t('meta.forms.description', { character: family.char, codePoint: family.code_point, count: family.count, clusters: family.clusters })} />
{:else if char}<Seo title={`${char} · ${t('nav.forms')}`} description={t('meta.formsIndex.description')} />
{:else}<Seo title={t('nav.forms')} description={t('meta.formsIndex.description')} />{/if}

<Forms initialFamily={data.family} onshow={shown => family = shown} />
