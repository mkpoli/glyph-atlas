<script>
  import { onMount } from 'svelte'
  import Explore from './views/Explore.svelte'
  import Quiz from './views/Quiz.svelte'
  import Forms from './views/Forms.svelte'
  import { formsAvailable } from './lib/forms.js'
  import CharacterDialog from './components/CharacterDialog.svelte'
  import CorpusDialog from './components/CorpusDialog.svelte'
  import ExportReviews from './components/ExportReviews.svelte'
  import CollectionProgress from './components/CollectionProgress.svelte'
  import { reviewer, stored, remember } from './lib/client.js'
  let route = $state('/'), reading = $state(''), selected = $state(null), onVerdict = $state(null)
  let clientId = $state(''), menu = $state(false), exporting = $state(false), progress = $state(false)
  let queue = $state([]), savedNotice = $state(''), updateItem = null
  // null until the service has answered whether it holds the form-assignment API.
  let forms = $state(null), formFamily = $state('')
  let selectedOrigin = $state('collection')
  // One display preference for the whole interface: a manuscript scan is a colour photograph of
  // paper and ink, so Original is the default and B&W is the reader's choice. It lives here rather
  // than in a view so the collection, the round and the reviewer cannot disagree about it.
  let ink = $state(stored('atlas.ink', 'original') === 'bw' ? 'bw' : 'original')
  function setInk(value) { ink = value; remember('atlas.ink', value) }
  // Set on the document so the single image rule in app.css reaches every view.
  $effect(() => { document.documentElement.dataset.ink = ink })
  const selectedIndex = $derived(queue.findIndex(item => item.id === selected))
  let lastFocus
  function navigate() {
    const url = new URL(location.hash.slice(1) || '/', location.origin)
    route = ['/review', '/flagged', '/hard', '/forms'].includes(url.pathname) ? url.pathname : '/'
    formFamily = url.searchParams.get('family') || ''
    reading = url.searchParams.get('reading') || ''
    selected = url.pathname.startsWith('/character/') ? decodeURIComponent(url.pathname.slice(11)) : null
    selectedOrigin = url.pathname.startsWith('/corpus/') ? 'corpus' : 'collection'
    if (selectedOrigin === 'corpus') selected = decodeURIComponent(url.pathname.slice(8))
    queue = []; updateItem = null
    menu = false; onVerdict = null
  }
  function inspect(id, decision = null, collection = [], update = null, origin = 'collection') { lastFocus = document.activeElement; selected = id; selectedOrigin = origin; onVerdict = decision; queue = [...collection]; updateItem = update }
  function step(direction) { const item = queue[selectedIndex + direction]; if (item) { selected = item.id; selectedOrigin = item.origin ?? 'collection' } }
  function saved(id, result) {
    updateItem?.(id, result)
    savedNotice = 'Saved'
    setTimeout(() => savedNotice = '', 2000)
    if (selectedIndex >= 0 && selectedIndex + 1 < queue.length) step(1)
    else close()
  }
  function close() { selected = null; onVerdict = null; lastFocus?.focus() }
  function exportReviews() { menu = false; exporting = true }
  function showProgress() { menu = false; progress = true }
  onMount(() => { clientId = reviewer(); navigate(); formsAvailable().then(value => forms = value); window.addEventListener('hashchange', navigate); return () => window.removeEventListener('hashchange', navigate) })
</script>

<header class="site-header"><a href="#/" class="wordmark" aria-label="Glyph Atlas home"><svg class="atlas-symbol" viewBox="0 0 32 32" fill="none" aria-hidden="true"><path d="M4 4h9v9H4zM19 4h9v9h-9zM4 19h9v9H4z" fill="currentColor"/><path d="M19 19h9v9h-9z" stroke="currentColor" stroke-width="2"/></svg><span>GLYPH <b>ATLAS</b></span><small class="slogan" lang="ja">Let's 集字!</small></a>
  <nav aria-label="Main navigation"><a class:active={route === '/'} href="#/">Explore</a><a class:active={route === '/flagged'} href="#/flagged">Flagged</a><a class:active={route === '/hard'} href="#/hard" title="Crops two reviewers skipped">Hard</a>{#if forms}<a class:active={route === '/forms'} href="#/forms">Forms</a>{/if}</nav>
  <div class="header-actions"><a class="review-link" class:current={route === '/review'} href="#/review">Quick review <span>↗</span></a><div class="header-menu"><button class="icon-button" aria-label="Review options" aria-expanded={menu} onclick={() => menu = !menu}>···</button>{#if menu}<div class="options-menu"><button onclick={showProgress}>Collection progress</button><button onclick={exportReviews}>Export reviews ↓</button><small>{clientId}</small></div>{/if}</div></div>
</header>
<main>
  {#if clientId}{#if route === '/review'}{#key reading}<Quiz {clientId} initialReading={reading} {inspect} />{/key}
  {:else if route === '/forms' && forms !== false}{#if forms}<Forms initialFamily={formFamily} />{/if}
  {:else}{#key route}<Explore queue={route === '/flagged' ? 'flagged' : route === '/hard' ? 'hard' : ''} {inspect} {ink} onink={setInk} onprogress={showProgress} />{/key}{/if}{/if}
</main>
{#if selected}{#if selectedOrigin === 'corpus'}<CorpusDialog id={selected} {clientId} {close} {saved} previous={selectedIndex > 0 ? () => step(-1) : null} next={selectedIndex >= 0 && selectedIndex + 1 < queue.length ? () => step(1) : null} position={queue.length ? `${selectedIndex + 1} / ${queue.length}` : ''} />{:else}<CharacterDialog id={selected} {clientId} {close} {onVerdict} {saved} previous={selectedIndex > 0 ? () => step(-1) : null} next={selectedIndex >= 0 && selectedIndex + 1 < queue.length ? () => step(1) : null} position={queue.length ? `${selectedIndex + 1} / ${queue.length}` : ''} />{/if}{/if}
{#if savedNotice}<div class="save-toast" role="status">✓ {savedNotice}</div>{/if}
{#if exporting}<ExportReviews close={() => { exporting = false; document.querySelector('[aria-label="Review options"]')?.focus() }} />{/if}
{#if progress}<CollectionProgress close={() => { progress = false; document.querySelector('[aria-label="Review options"]')?.focus() }} />{/if}
