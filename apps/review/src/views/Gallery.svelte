<script>
  // The page of a character (字体) or a grapheme (字種): its gallery in the collection view, and what it
  // tells search engines.
  import { page } from '$app/state'
  import Seo from '$components/Seo.svelte'
  import Explore from '$views/Explore.svelte'
  import { useInspector } from '$lib/inspector.svelte.js'
  import { useSession } from '$lib/session.svelte.js'
  import { canonicalAddress, graphemeOf } from '$lib/addresses.js'
  import { galleryName, galleryTitle } from '$lib/citation.js'
  import { t } from '$lib/i18n.svelte.js'
  import { characterEntry, linkedData } from '$worker/citation.ts'
  let { gallery } = $props()
  const inspector = useInspector(), session = useSession()
  const image = $derived([...gallery.local, ...gallery.corpus].find(item => item.image && item.proxyable !== false)?.image)
  // The view can move to another character or to a search in place; the title follows what it shows.
  // Until the view reports one, it shows what the page was rendered for.
  let moved = $state(undefined)
  const own = $derived({ char: gallery.picked.char, code_point: gallery.picked.code_point, of: gallery.expand === 'grapheme' ? 'grapheme' : 'character' })
  const shown = $derived(moved === undefined ? own : moved)
  const here = $derived(shown?.of === own.of && shown.code_point === own.code_point)
  // A grapheme is described by its forms that hold crops (仮/假), a character by its code point.
  const used = $derived((graphemeOf(gallery.picked).members ?? []).filter(v => (v.count ?? 0) + (v.corpus_count ?? 0)).map(v => v.char))
  const named = $derived(here && own.of === 'grapheme' && used.length > 1 ? used.join('/') : shown?.code_point)
  // The page's JSON-LD names what its address shows: the grapheme, or the one form. A widening to
  // variants names neither.
  const entry = $derived(gallery.expand === 'variants' ? null : characterEntry(own.of === 'grapheme' ? 'grapheme' : 'form', gallery.picked))
</script>

{#if shown}<Seo title={galleryTitle(shown)} description={t('meta.character.description', { character: galleryName(shown), codePoint: named })}
     path={here ? canonicalAddress(gallery.picked, gallery.expand) : undefined}
     image={image && here ? new URL(image, page.url.origin).href : null} data={here && entry ? linkedData(entry, page.url.origin) : null} />{:else}<Seo />{/if}

<!-- Each navigation brings a gallery of its own, so the view starts again from it. -->
{#key gallery}
  <Explore {gallery} addressed bind:shown={moved} inspect={inspector.inspect.bind(inspector)} ink={session.state.ink} onink={value => session.setInk(value)} />
{/key}
