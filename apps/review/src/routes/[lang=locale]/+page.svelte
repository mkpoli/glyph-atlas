<script>
  import { page } from '$app/state'
  import Seo from '$components/Seo.svelte'
  import { indexCollection } from '$lib/seo.js'
  import { galleryName, galleryTitle } from '$lib/citation.js'
  import { t, locale } from '$lib/i18n.svelte.js'
  import Explore from '$views/Explore.svelte'
  import { useInspector } from '$lib/inspector.svelte.js'
  import { useSession } from '$lib/session.svelte.js'
  let { data } = $props()
  const inspector = useInspector(), session = useSession()
  // A character or grapheme picked here turns the view into its page, address and title with it.
  let shown = $state(null)
</script>

{#if shown}<Seo title={galleryTitle(shown)} description={t('meta.character.description', { character: galleryName(shown), codePoint: shown.code_point })} />
{:else}<Seo index={indexCollection(page.url.searchParams)} data={{ '@type': 'WebSite', name: t('app.name'), url: page.url.origin + '/', inLanguage: locale() }} />{/if}

<Explore initial={data.explore} addressed bind:shown inspect={inspector.inspect.bind(inspector)} ink={session.state.ink} onink={value => session.setInk(value)} />
