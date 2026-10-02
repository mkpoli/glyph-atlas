<script>
  import Seo from '$components/Seo.svelte'
  import { t } from '$lib/i18n.svelte.js'
  import { page } from '$app/state'
  import { unslug } from '$lib/gallery.js'
  import Quiz from '$views/Quiz.svelte'
  import { useInspector } from '$lib/inspector.svelte.js'
  import { useSession } from '$lib/session.svelte.js'
  const inspector = useInspector(), session = useSession()
  // `/review/U+85CF?production=handwritten` deals that grapheme's round in that material; `/review` picks one.
  // Quick Review moves the address along as rounds change without navigating, so this is read once a visit.
  const grapheme = $derived(page.params.grapheme ? unslug(page.params.grapheme) : '')
  const production = $derived(page.url.searchParams.get('production') || '')
</script>

<Seo title={t('nav.quickReview')} index={false} />

<!-- A round is dealt for one reviewer, who is known once the page runs in a browser. -->
{#if session.state.ready}{#key grapheme + '?' + production}<Quiz initialGrapheme={grapheme} initialProduction={production} inspect={inspector.inspect.bind(inspector)} />{/key}{/if}
