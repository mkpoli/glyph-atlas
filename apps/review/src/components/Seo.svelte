<script>
  import { page } from '$app/state'
  import { t, HREFLANG, localize, delocalize } from '$lib/i18n.svelte.js'
  // `title` is the page's own name; the site's name follows it. A page kept out of search results
  // (a reviewer's working view) passes `index={false}`. `data` is the page's JSON-LD, if any.
  let { title = '', description = t('meta.description'), image = null, index = true, type = 'website', data = null } = $props()
  const site = $derived(t('app.name'))
  const full = $derived(title ? `${title} · ${site}` : site)
  const canonical = $derived(page.url.origin + page.url.pathname)
  // The same page in every interface language; English, unprefixed, is the default.
  const path = $derived(delocalize(page.url.pathname).path)
  const alternates = $derived(HREFLANG.map(locale => ({ tag: locale.tag, href: page.url.origin + localize(path, locale.tag) })))
  const picture = $derived(image ? new URL(image, page.url.origin).href : null)
  // A JSON-LD block is script text: `<` is escaped so no value can close the element.
  const ld = $derived(data ? JSON.stringify({ '@context': 'https://schema.org', ...data }).replaceAll('<', '\\u003c') : null)
</script>

<svelte:head>
  <title>{full}</title>
  <meta name="description" content={description} />
  {#if index}<link rel="canonical" href={canonical} />
    {#each alternates as alternate (alternate.tag)}<link rel="alternate" hreflang={alternate.tag} href={alternate.href} />{/each}
    <link rel="alternate" hreflang="x-default" href={page.url.origin + path} />
  {:else}<meta name="robots" content="noindex, follow" />{/if}
  <meta property="og:site_name" content={site} />
  <meta property="og:title" content={title || site} />
  <meta property="og:description" content={description} />
  <meta property="og:type" content={type} />
  <meta property="og:url" content={canonical} />
  {#if picture}<meta property="og:image" content={picture} /><meta name="twitter:card" content="summary_large_image" />{:else}<meta name="twitter:card" content="summary" />{/if}
  {#if ld}{@html `<script type="application/ld+json">${ld}</script>`}{/if}
</svelte:head>
