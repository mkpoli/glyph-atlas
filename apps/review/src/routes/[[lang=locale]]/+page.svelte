<script>
  import { page } from '$app/state'
  import Seo from '$components/Seo.svelte'
  import { t, locale } from '$lib/i18n.svelte.js'
  import Explore from '$views/Explore.svelte'
  import { useInspector } from '$lib/inspector.svelte.js'
  import { useSession } from '$lib/session.svelte.js'
  let { data } = $props()
  const inspector = useInspector(), session = useSession()
  // A character picked here turns the view into that character's page, address and title with it.
  let character = $state(null)
</script>

<svelte:head>
  <!-- The first crops are asked for before the page around them is read. -->
  {#each (data.explore.lead?.items ?? []).filter(item => item.proxyable && item.image) as item (item.id)}<link rel="preload" as="image" href={item.image} fetchpriority="high" />{/each}
</svelte:head>

{#if character}<Seo title={`${character.char} (${character.code_point})`} description={t('meta.character.description', { character: character.char, codePoint: character.code_point })} />
{:else}<Seo data={{ '@type': 'WebSite', name: t('app.name'), url: page.url.origin + '/', inLanguage: locale() }} />{/if}

<Explore initial={data.explore} addressed bind:shown={character} inspect={inspector.inspect.bind(inspector)} ink={session.state.ink} onink={value => session.setInk(value)} onprogress={() => session.showProgress()} />
