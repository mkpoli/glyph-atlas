<script>
  import { page } from '$app/state'
  import Seo from '$components/Seo.svelte'
  import Explore from '$views/Explore.svelte'
  import { useInspector } from '$lib/inspector.svelte.js'
  import { useSession } from '$lib/session.svelte.js'
  import { t } from '$lib/i18n.svelte.js'
  let { data } = $props()
  const inspector = useInspector(), session = useSession()
  const image = $derived([...data.gallery.local, ...data.gallery.corpus].find(item => item.image && item.proxyable !== false)?.image)
  // The view can move to another character or to a search in place; the title follows what it shows.
  // Until the view reports one, it shows the character the page was rendered for.
  let character = $state(undefined)
  const card = $derived(character === undefined ? data.gallery.picked : character)
</script>

{#if card}<Seo title={`${card.char} (${card.code_point})`} description={t('meta.character.description', { character: card.char, codePoint: card.code_point })}
     image={image && !character ? new URL(image, page.url.origin).href : null} />{:else}<Seo />{/if}

<!-- Each navigation brings a gallery of its own, so the view starts again from it. -->
{#key data.gallery}
  <Explore gallery={data.gallery} addressed bind:shown={character} inspect={inspector.inspect.bind(inspector)} ink={session.state.ink} onink={value => session.setInk(value)} onprogress={() => session.showProgress()} />
{/key}
