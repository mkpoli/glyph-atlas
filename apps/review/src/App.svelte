<script>
  import { onMount } from 'svelte'
  import Explore from './views/Explore.svelte'
  import Quiz from './views/Quiz.svelte'
  import CharacterDialog from './components/CharacterDialog.svelte'
  import { reviewer, request, download } from './lib/client.js'
  let route = $state('/'), reading = $state(''), selected = $state(null), onVerdict = $state(null)
  let clientId = $state(''), version = $state(0), menu = $state(false), exportError = $state('')
  let lastFocus
  function navigate() {
    const url = new URL(location.hash.slice(1) || '/', location.origin)
    route = ['/review', '/flagged'].includes(url.pathname) ? url.pathname : '/'
    reading = url.searchParams.get('reading') || ''
    selected = url.pathname.startsWith('/character/') ? decodeURIComponent(url.pathname.slice(11)) : null
    menu = false; onVerdict = null
  }
  function inspect(id, decision = null) { lastFocus = document.activeElement; selected = id; onVerdict = decision }
  function close() { selected = null; onVerdict = null; lastFocus?.focus() }
  async function exportReviews() {
    exportError = ''
    try { download(await request('/atlas/reviews'), 'atlas-character-reviews.json'); menu = false }
    catch (e) { exportError = e.message }
  }
  onMount(() => { clientId = reviewer(); navigate(); window.addEventListener('hashchange', navigate); return () => window.removeEventListener('hashchange', navigate) })
</script>

<header class="site-header"><a href="#/" class="wordmark" aria-label="Kuzushiji Atlas home"><svg class="atlas-symbol" viewBox="0 0 32 32" fill="none" aria-hidden="true"><path d="M4 4h9v9H4zM19 4h9v9h-9zM4 19h9v9H4z" fill="currentColor"/><path d="M19 19h9v9h-9z" stroke="currentColor" stroke-width="2"/></svg><span>KUZUSHIJI <b>ATLAS</b></span></a>
  <nav aria-label="Main navigation"><a class:active={route === '/'} href="#/">Explore</a><a class:active={route === '/flagged'} href="#/flagged">Flagged</a></nav>
  <div class="header-actions"><a class="review-link" class:current={route === '/review'} href="#/review">Quick review <span>↗</span></a><div class="header-menu"><button class="icon-button" aria-label="Review options" aria-expanded={menu} onclick={() => menu = !menu}>···</button>{#if menu}<div class="options-menu"><button onclick={exportReviews}>Export reviews ↓</button><small>{clientId}</small>{#if exportError}<p role="alert">{exportError}</p>{/if}</div>{/if}</div></div>
</header>
<main>
  {#if clientId}{#if route === '/review'}{#key reading}<Quiz {clientId} initialReading={reading} {inspect} />{/key}
  {:else}{#key `${route}:${version}`}<Explore flagged={route === '/flagged'} {inspect} />{/key}{/if}{/if}
</main>
{#if selected}{#key selected}<CharacterDialog id={selected} {clientId} {close} {onVerdict} saved={() => version++} />{/key}{/if}
