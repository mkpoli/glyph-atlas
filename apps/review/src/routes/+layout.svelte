<script>
  import '../app.css'
  import '../layers.css'
  import '../script-colors.css'
  import { onMount, untrack } from 'svelte'
  import { afterNavigate, goto, pushState } from '$app/navigation'
  import { page } from '$app/state'
  import CharacterDialog from '$components/CharacterDialog.svelte'
  import CorpusDialog from '$components/CorpusDialog.svelte'
  import ExportReviews from '$components/ExportReviews.svelte'
  import CollectionProgress from '$components/CollectionProgress.svelte'
  import { createInspector, provideInspector } from '$lib/inspector.svelte.js'
  import { number } from '$lib/client.js'
  import { createSession, provideSession } from '$lib/session.svelte.js'
  import { t, around, LOCALES, locale, localName, setLocale, useLocale, localize, delocalize } from '$lib/i18n.svelte.js'
  let { data, children } = $props()
  // Set from the address before anything renders, so the server and the browser draw the same words.
  // The layout also draws error pages, which have no route to carry the language.
  useLocale(untrack(() => delocalize(page.url.pathname).tag))
  // Moving to another language's address changes it for the pages that follow.
  $effect.pre(() => useLocale(delocalize(page.url.pathname).tag))
  const inspector = provideInspector(createInspector())
  const session = provideSession(createSession())
  let menuButton, menu = $state(false), exporting = $state(false), savedNotice = $state('')
  const path = $derived(delocalize(page.url.pathname).path)
  const section = $derived(path.startsWith('/pages') ? '/pages' : path.startsWith('/forms') ? '/forms' : path)
  // A crop page is a crop opened over the collection; the inspector's own choice takes over from it.
  // Closing it moves to the collection's address in place, and Back opens it again.
  const routed = $derived(page.data.record && !page.state.closed ? { id: page.params.id, origin: page.route.id?.endsWith('/corpus/[id]') ? 'corpus' : 'collection' } : null)
  const shown = $derived(inspector.state.selected ? { id: inspector.state.selected, origin: inspector.state.origin } : routed)
  const index = $derived(inspector.state.queue.findIndex(item => item.id === inspector.state.selected))
  const previous = $derived(index > 0 ? () => inspector.step(-1) : null)
  const next = $derived(index >= 0 && index + 1 < inspector.state.queue.length ? () => inspector.step(1) : null)
  // A crop page's record is what its load read. Once this session writes to that crop the record is
  // stale, and handing it to the dialog again (Back, or a link to the same crop) would reopen it at
  // the old revision and have its next save refused; the dialog then reads the crop itself.
  let written = $state({})
  const initial = $derived(routed && shown?.id === routed.id && !written[routed.id] ? page.data.record : null)
  const position = $derived(inspector.state.queue.length ? `${number(index + 1)} / ${number(inspector.state.queue.length)}` : '')
  $effect(() => { document.documentElement.lang = locale() })
  // Set on the document so the single image rule in app.css reaches every view.
  $effect(() => { document.documentElement.dataset.ink = session.state.ink })
  function close() {
    if (inspector.state.selected) inspector.close()
    else if (routed) pushState(localize('/'), { closed: true })
  }
  // A write that keeps the inspector open (a crop's style): the crop's tile and record are stale all
  // the same, so the next open reads it afresh.
  function changed(id, result) {
    inspector.update?.(id, result)
    written = { ...written, [id]: true }
  }
  function saved(id, result) {
    inspector.update?.(id, result)
    written = { ...written, [id]: true }
    savedNotice = t('app.saved')
    setTimeout(() => savedNotice = '', 2000)
    close()
  }
  // The address bar, not `page.url`: a view may have rewritten the address in place since the page loaded.
  function chooseLocale(tag) { setLocale(tag); menu = false; goto(localize(delocalize(location.pathname).path, tag) + location.search, { noScroll: true, keepFocus: true }) }
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

<header class="site-header"><a href={localize('/')} class="wordmark" aria-label={t('app.home.aria')}><svg class="atlas-symbol" viewBox="0 0 32 32" fill="none" aria-hidden="true"><path d="M4 4h9v9H4zM19 4h9v9h-9zM4 19h9v9H4z" fill="currentColor"/><path d="M19 19h9v9h-9z" stroke="currentColor" stroke-width="2"/></svg>{#if localName()}<span class="local-name" lang={locale()}>{localName()}</span>{:else}<span lang="en">GLYPH <b>ATLAS</b></span>{/if}<small class="slogan" lang="ja">Let's 集字!</small></a>
  <nav aria-label={t('app.nav.aria')}><a class:active={section === '/'} href={localize('/')}>{t('nav.explore')}</a>{#if data.forms}<a class:active={section === '/forms'} href={localize('/forms')}>{t('nav.forms')}</a>{/if}{#if data.pages}<a class:active={section === '/pages'} href={localize('/pages')}>{t('nav.pages')}</a>{/if}<a class:active={section === '/flagged'} href={localize('/flagged')}>{t('nav.flagged')}</a><a class:active={section === '/history'} href={localize('/history')}>{t('nav.history')}</a></nav>
  <div class="header-actions"><a class="review-link" class:current={section === '/review'} href={localize('/review')}>{t('nav.quickReview')} <span>↗</span></a><div class="header-menu"><button bind:this={menuButton} class="icon-button" aria-label={t('app.reviewOptions')} aria-expanded={menu} onclick={() => menu = !menu}>···</button>{#if menu}<div class="options-menu"><button onclick={showProgress}>{t('explore.collectionProgress')}</button><button onclick={exportReviews}>{t('export.menuItem')}</button>{#if LOCALES.length > 1}<div class="language-group"><small>{t('menu.language')}</small><div class="language-options">{#each LOCALES as loc (loc.tag)}<button lang={loc.tag} class:active={locale() === loc.tag} aria-pressed={locale() === loc.tag} onclick={() => chooseLocale(loc.tag)}>{loc.name}</button>{/each}</div></div>{/if}<small>{session.state.clientId}</small></div>{/if}</div></div>
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
{#if shown}{#if shown.origin === 'corpus'}<CorpusDialog id={shown.id} clientId={session.state.clientId} {close} {saved} {previous} {next} {position} {initial} />{:else}<CharacterDialog id={shown.id} clientId={session.state.clientId} {changed} {close} onVerdict={inspector.state.onVerdict} {saved} {previous} {next} {position} {initial} />{/if}{/if}
{#if savedNotice}<div class="save-toast" role="status">✓ {savedNotice}</div>{/if}
{#if exporting}<ExportReviews close={() => { exporting = false; menuButton?.focus() }} />{/if}
{#if session.state.progress}<CollectionProgress close={() => { session.state.progress = false; menuButton?.focus() }} />{/if}
