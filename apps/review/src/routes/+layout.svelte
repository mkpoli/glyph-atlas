<script>
  import '../app.css'
  import '../layers.css'
  import '../script-colors.css'
  import { onMount, untrack } from 'svelte'
  import { afterNavigate, goto } from '$app/navigation'
  import { page } from '$app/state'
  import CharacterDialog from '$components/CharacterDialog.svelte'
  import CorpusDialog from '$components/CorpusDialog.svelte'
  import ExportReviews from '$components/ExportReviews.svelte'
  import CollectionProgress from '$components/CollectionProgress.svelte'
  import { createInspector, provideInspector } from '$lib/inspector.svelte.js'
  import { createSession, provideSession } from '$lib/session.svelte.js'
  import { t, around, LOCALES, locale, localName, setLocale, useLocale } from '$lib/i18n.svelte.js'
  let { data, children } = $props()
  // Before anything renders, so the server and the browser draw the same words.
  useLocale(untrack(() => data.locale))
  const inspector = provideInspector(createInspector())
  const session = provideSession(createSession())
  let menuButton, menu = $state(false), exporting = $state(false), savedNotice = $state('')
  const path = $derived(page.url.pathname)
  const section = $derived(path.startsWith('/pages') ? '/pages' : path.startsWith('/forms') ? '/forms' : path)
  // A crop page is a crop opened over the collection; the inspector's own choice takes over from it.
  const routed = $derived(page.data.record ? { id: page.params.id, origin: page.route.id === '/corpus/[id]' ? 'corpus' : 'collection' } : null)
  const shown = $derived(inspector.state.selected ? { id: inspector.state.selected, origin: inspector.state.origin } : routed)
  const index = $derived(inspector.state.queue.findIndex(item => item.id === inspector.state.selected))
  const previous = $derived(index > 0 ? () => inspector.step(-1) : null)
  const next = $derived(index >= 0 && index + 1 < inspector.state.queue.length ? () => inspector.step(1) : null)
  const initial = $derived(routed && shown?.id === routed.id ? page.data.record : null)
  const position = $derived(inspector.state.queue.length ? `${index + 1} / ${inspector.state.queue.length}` : '')
  $effect(() => { document.documentElement.lang = locale() })
  // Set on the document so the single image rule in app.css reaches every view.
  $effect(() => { document.documentElement.dataset.ink = session.state.ink })
  function close() {
    if (inspector.state.selected) inspector.close()
    else if (routed) goto('/', { replaceState: true, noScroll: true })
  }
  function saved(id, result) {
    inspector.update?.(id, result)
    savedNotice = t('app.saved')
    setTimeout(() => savedNotice = '', 2000)
    if (next) next()
    else close()
  }
  function exportReviews() { menu = false; exporting = true }
  function showProgress() { menu = false; session.showProgress() }
  // A new page closes whatever the last one left open.
  afterNavigate(() => { menu = false; if (inspector.state.selected) inspector.close() })
  onMount(() => {
    session.start()
    // Until now the page was the server's markup, with no handlers; this marks it live.
    document.documentElement.dataset.hydrated = ''
  })
</script>

<svelte:head><title>{t('app.name')}</title></svelte:head>

<header class="site-header"><a href="/" class="wordmark" aria-label={t('app.home.aria')}><svg class="atlas-symbol" viewBox="0 0 32 32" fill="none" aria-hidden="true"><path d="M4 4h9v9H4zM19 4h9v9h-9zM4 19h9v9H4z" fill="currentColor"/><path d="M19 19h9v9h-9z" stroke="currentColor" stroke-width="2"/></svg>{#if localName()}<span class="local-name" lang={locale()}>{localName()}</span>{:else}<span lang="en">GLYPH <b>ATLAS</b></span>{/if}<small class="slogan" lang="ja">Let's 集字!</small></a>
  <nav aria-label={t('app.nav.aria')}><a class:active={section === '/'} href="/">{t('nav.explore')}</a>{#if data.forms}<a class:active={section === '/forms'} href="/forms">{t('nav.forms')}</a>{/if}{#if data.pages}<a class:active={section === '/pages'} href="/pages">{t('nav.pages')}</a>{/if}<a class:active={section === '/flagged'} href="/flagged">{t('nav.flagged')}</a><a class:active={section === '/history'} href="/history">{t('nav.history')}</a></nav>
  <div class="header-actions"><a class="review-link" class:current={section === '/review'} href="/review">{t('nav.quickReview')} <span>↗</span></a><div class="header-menu"><button bind:this={menuButton} class="icon-button" aria-label={t('app.reviewOptions')} aria-expanded={menu} onclick={() => menu = !menu}>···</button>{#if menu}<div class="options-menu"><button onclick={showProgress}>{t('explore.collectionProgress')}</button><button onclick={exportReviews}>{t('export.menuItem')}</button>{#if LOCALES.length > 1}<div class="language-group"><small>{t('menu.language')}</small><div class="language-options">{#each LOCALES as loc (loc.tag)}<button lang={loc.tag} class:active={locale() === loc.tag} aria-pressed={locale() === loc.tag} onclick={() => setLocale(loc.tag)}>{loc.name}</button>{/each}</div></div>{/if}<small>{session.state.clientId}</small></div>{/if}</div></div>
</header>
<main>
  {@render children()}
</main>
<!-- The dataset's own licence covers the records and annotations made here; images and texts keep the
     terms of their sources, which each crop's "Source & rights" link shows. -->
<footer class="site-footer">
  <p>{around('footer.data', 'license')[0]}<a href="https://creativecommons.org/licenses/by-sa/4.0/" rel="license noopener" target="_blank">CC BY-SA 4.0</a>{around('footer.data', 'license')[1]}
    · {t('footer.images')}
    · {around('footer.code', 'license')[0]}<a href="https://github.com/mkpoli/glyph-atlas/blob/main/LICENSE" rel="noopener" target="_blank">MIT</a>{around('footer.code', 'license')[1]}
    · <a href="https://github.com/mkpoli/glyph-atlas" rel="noopener" target="_blank">GitHub ↗</a></p>
</footer>
{#if shown}{#if shown.origin === 'corpus'}<CorpusDialog id={shown.id} clientId={session.state.clientId} {close} {saved} {previous} {next} {position} {initial} />{:else}<CharacterDialog id={shown.id} clientId={session.state.clientId} {close} onVerdict={inspector.state.onVerdict} {saved} {previous} {next} {position} {initial} />{/if}{/if}
{#if savedNotice}<div class="save-toast" role="status">✓ {savedNotice}</div>{/if}
{#if exporting}<ExportReviews close={() => { exporting = false; menuButton?.focus() }} />{/if}
{#if session.state.progress}<CollectionProgress close={() => { session.state.progress = false; menuButton?.focus() }} />{/if}
