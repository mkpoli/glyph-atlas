<script>
  import '../app.css'
  import '../layers.css'
  import '@fontsource/klee-one/400.css'
  import '../fallback-fonts.css'
  import '../script-colors.css'
  import ChatLinks from '../components/ChatLinks.svelte'
  import { onMount, untrack } from 'svelte'
  import { afterNavigate, goto, pushState } from '$app/navigation'
  import { page } from '$app/state'
  import CharacterDialog from '$components/CharacterDialog.svelte'
  import CorpusDialog from '$components/CorpusDialog.svelte'
  import ExportReviews from '$components/ExportReviews.svelte'
  import CollectionProgress from '$components/CollectionProgress.svelte'
  import DatabaseStatus from '$components/DatabaseStatus.svelte'
  import SignInDialog from '$components/SignInDialog.svelte'
  import { createInspector, provideInspector } from '$lib/inspector.svelte.js'
  import { prefetchCrop, forgetCrop } from '$lib/cropCache.js'
  import { number } from '$lib/client.js'
  import { createSession, provideSession } from '$lib/session.svelte.js'
  import { loadFavourites } from '$lib/favourites.svelte.js'
  import { THEMES, setTheme, showThemeColor } from '$lib/theme.js'
  import { t, around, LOCALES, locale, localName, setLocale, useLocale, localize, delocalize } from '$lib/i18n.svelte.js'
  let { data, children } = $props()
  // Set from the address before anything renders, so the server and the browser draw the same words.
  // The layout also draws error pages, which have no route to carry the language.
  useLocale(untrack(() => delocalize(page.url.pathname).tag))
  // Moving to another language's address changes it for the pages that follow.
  $effect.pre(() => useLocale(delocalize(page.url.pathname).tag))
  const inspector = provideInspector(createInspector())
  const session = provideSession(createSession(untrack(() => data.account)))
  let menuButton, menuRoot, menu = $state(false), exporting = $state(false), savedNotice = $state('')
  const path = $derived(delocalize(page.url.pathname).path)
  // A run's page is part of Explore; a round's address is part of Quick Review.
  const section = $derived(path.startsWith('/pages') ? '/pages' : path.startsWith('/forms') ? '/forms' : path.startsWith('/review/') ? '/review' : path.startsWith('/sequence/') ? '/' : path)
  // A crop page is a crop opened over the collection; a crop the inspector opened takes over from it.
  // Closing it moves to the collection's address in place, and Back opens it again.
  // A shallow Back to the list keeps the crop route mounted, so the address must still be the crop's.
  const routed = $derived(page.data.record && !page.state.closed && !page.state.inspect && /^\/(crop|corpus)\//.test(path) ? { id: page.params.id, origin: page.route.id?.endsWith('/corpus/[id]') ? 'corpus' : 'collection' } : null)
  const shown = $derived(inspector.shown ?? routed)
  const index = $derived(inspector.index)
  const previous = $derived(index > 0 ? () => inspector.step(-1) : null)
  const next = $derived(index >= 0 && index + 1 < inspector.queue.length ? () => inspector.step(1) : null)
  // A crop page's record is what its load read. Once this session writes to that crop the record is
  // stale, and handing it to the dialog again (Back, or a link to the same crop) would reopen it at
  // the old revision and have its next save refused; the dialog then reads the crop itself.
  let written = $state({})
  const initial = $derived(routed && shown?.id === routed.id && !written[routed.id] ? page.data.record : null)
  // The cut a crop page's address cites, for the crop that page shows.
  const cited = $derived(routed && shown?.id === routed.id ? page.data.cited ?? null : null)
  // The list's own row for the crop on show, drawn while its record loads.
  const preview = $derived(index >= 0 && inspector.queue[index]?.image ? inspector.queue[index] : null)
  // The crops either side are read ahead once this one is on show, so stepping finds them ready.
  $effect(() => {
    const queue = inspector.queue, near = [queue[index + 1], queue[index - 1]].filter(Boolean)
    // A moment later, so the crop on show is read first.
    const timer = setTimeout(() => { for (const item of near) prefetchCrop(item.id, item.origin ?? 'collection') }, 400)
    return () => clearTimeout(timer)
  })
  const position = $derived(index >= 0 ? `${number(index + 1)} / ${number(inspector.queue.length)}` : '')
  $effect(() => { document.documentElement.lang = locale() })
  // The inspector's star reads the user's favourites, once the page knows who is signed in.
  $effect(() => { if (session.state.ready) loadFavourites(session.state.user) })
  // Set on the document so the single image rule in app.css reaches every view.
  $effect(() => { document.documentElement.dataset.ink = session.state.ink })
  function close() {
    if (inspector.shown) inspector.close()
    else if (routed) pushState(localize('/'), { closed: true })
  }
  // A write that keeps the inspector open (a crop's style): the crop's tile and record are stale all
  // the same, so the next open reads it afresh.
  function changed(id, result) {
    forgetCrop(id)
    inspector.update?.(id, result)
    written = { ...written, [id]: true }
  }
  function saved(id, result) {
    forgetCrop(id)
    inspector.update?.(id, result)
    written = { ...written, [id]: true }
    savedNotice = t('app.saved')
    setTimeout(() => savedNotice = '', 2000)
    if (session.state.advance && next) next()
    else close()
  }
  // The address bar, not `page.url`: a view may have rewritten the address in place since the page loaded.
  // The scheme the server rendered with, read back once the page is in the browser.
  let theme = $state('system')
  function chooseTheme(value) { setTheme(value); theme = value }
  function chooseLocale(tag) { setLocale(tag); menu = false; goto(localize(delocalize(location.pathname).path, tag) + location.search, { noScroll: true, keepFocus: true }) }
  function exportReviews() { menu = false; exporting = true }
  function showProgress() { menu = false; session.showProgress() }
  // A new page closes whatever the last one left open.
  afterNavigate(() => { menu = false; inspector.forget() })
  // Once the dialog has gone, by its close button or by Back, the focus returns to what opened it.
  let wasShown = false
  $effect(() => { const now = Boolean(shown); void page.state.inspect; inspector.settle(); if (wasShown && !now) inspector.restoreFocus(); wasShown = now })
  onMount(() => {
    theme = document.documentElement.dataset.theme || 'system'; showThemeColor(theme)
    session.start()
    // Until now the page was the server's markup, with no handlers; this marks it live.
    document.documentElement.dataset.hydrated = ''
  })
</script>

{#snippet destinations()}<a class:active={section === '/'} aria-current={section === '/' ? 'page' : undefined} href={localize('/')}>{t('nav.explore')}</a>{#if data.forms}<a class:active={section === '/forms'} aria-current={section === '/forms' ? 'page' : undefined} href={localize('/forms')}>{t('nav.forms')}</a>{/if}{#if data.pages}<a class:active={section === '/pages'} aria-current={section === '/pages' ? 'page' : undefined} href={localize('/pages')}>{t('nav.pages')}</a>{/if}<a class:active={section === '/flagged'} aria-current={section === '/flagged' ? 'page' : undefined} href={localize('/flagged')}>{t('nav.flagged')}</a><a class:active={section === '/history'} aria-current={section === '/history' ? 'page' : undefined} href={localize('/history')}>{t('nav.history')}</a>{/snippet}
<!-- The menu closes on Escape, handing focus back to its button, and on a tap or click outside it. -->
<svelte:window onkeydown={event => { if (menu && event.key === 'Escape') { menu = false; menuButton.focus() } }} onpointerdown={event => { if (menu && !menuRoot.contains(event.target)) menu = false }} />
<header class="site-header"><a href={localize('/')} class="wordmark" aria-label={t('app.home.aria')}><svg class="atlas-symbol" viewBox="0 0 32 32" fill="none" aria-hidden="true"><path class="cell" d="M4 4h9v9H4z" fill="currentColor"/><path class="cell" d="M19 4h9v9h-9z" fill="currentColor"/><path class="cell" d="M4 19h9v9H4z" fill="currentColor"/><path class="open-cell" d="M19 19h9v9h-9z" stroke="currentColor" stroke-width="2" pathLength="1"/></svg>{#if localName()}<span class="local-name" lang={locale()}>{localName()}</span>{:else}<span lang="en">GLYPH <b>ATLAS</b></span>{/if}<small class="slogan" lang="ja">Let's 集字!</small></a>
  <nav class="site-nav" aria-label={t('app.nav.aria')}>{@render destinations()}</nav>
  <div class="header-actions"><a class="review-link" class:current={section === '/review'} href={localize('/review')}>{t('nav.quickReview')} <span>↗</span></a>{#if session.state.user && !session.state.user.anonymous}<a class="account-link" class:current={section === '/account'} href={localize('/account')} aria-label={t('account.link', { name: session.state.user.name })}><span class="avatar">{#if session.state.user.image}<img src={session.state.user.image} alt="" />{:else}{session.state.user.name.slice(0, 1).toUpperCase()}{/if}</span></a>{:else}<button class="sign-in-link" onclick={() => session.signIn()} aria-label={t('signIn.open')}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="8" r="4"/><path d="M4 21c0-4.4 3.6-8 8-8s8 3.6 8 8"/></svg><span>{t('signIn.open')}</span></button>{/if}<div class="header-menu" bind:this={menuRoot} onfocusout={event => { if (event.relatedTarget && !menuRoot.contains(event.relatedTarget)) menu = false }}><button bind:this={menuButton} class="icon-button menu-button" aria-label={t('app.menu')} aria-expanded={menu} aria-controls="site-menu" onclick={() => menu = !menu}><span class="menu-dots" aria-hidden="true">···</span><svg class="menu-bars" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16"/></svg></button><div id="site-menu" class="options-menu" hidden={!menu}><nav class="menu-nav" aria-label={t('app.nav.aria')}>{@render destinations()}</nav>{#if session.state.user && !session.state.user.anonymous}<a class="menu-account" href={localize('/account')} aria-label={t('account.link', { name: session.state.user.name })}><span class="avatar small">{#if session.state.user.image}<img src={session.state.user.image} alt="" />{:else}{session.state.user.name.slice(0, 1).toUpperCase()}{/if}</span><span class="menu-account-name">{session.state.user.name}</span></a>{:else}<button class="menu-account" onclick={() => { menu = false; session.signIn() }}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="8" r="4"/><path d="M4 21c0-4.4 3.6-8 8-8s8 3.6 8 8"/></svg><span>{t('signIn.open')}</span></button>{/if}{#if session.state.user?.admin}<a class="menu-admin" href={localize('/admin')}>{t('admin.title')}</a>{/if}<a class="menu-admin" href={localize('/favourites')}>{t('favourites.title')}</a><a class="menu-admin" href={localize('/ranking')}>{t('ranking.title')}</a><button onclick={showProgress}>{t('explore.collectionProgress')}</button><button onclick={exportReviews}>{t('export.menuItem')}</button>{#if LOCALES.length > 1}<div class="language-group"><small>{t('menu.language')}</small><div class="language-options">{#each LOCALES as loc (loc.tag)}<button lang={loc.tag} class:active={locale() === loc.tag} aria-pressed={locale() === loc.tag} onclick={() => chooseLocale(loc.tag)}>{loc.name}</button>{/each}</div></div>{/if}<div class="language-group"><small>{t('menu.theme')}</small><div class="language-options">{#each THEMES as value (value)}<button aria-pressed={theme === value} onclick={() => chooseTheme(value)}>{t(`menu.theme.${value}`)}</button>{/each}</div></div></div></div></div>
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
    · <a href="https://github.com/mkpoli/glyph-atlas" rel="noopener" target="_blank">GitHub ↗</a>
    · <a href={localize('/privacy')}>{t('footer.privacy')}</a>
    · <ChatLinks /></p>
</footer>
{#if shown}{#if shown.origin === 'corpus'}<CorpusDialog id={shown.id} {preview} {changed} {close} {saved} {previous} {next} {position} {initial} {cited} />{:else}<CharacterDialog id={shown.id} {preview} {changed} {close} onVerdict={inspector.onVerdict} {saved} {previous} {next} {position} {initial} {cited} />{/if}{/if}
{#if savedNotice}<div class="save-toast" role="status">✓ {savedNotice}</div>{/if}
<DatabaseStatus />
{#if exporting}<ExportReviews close={() => { exporting = false; menuButton?.focus() }} />{/if}
{#if session.state.signingIn}<SignInDialog close={() => session.state.signingIn = false} />{/if}
{#if session.state.progress}<CollectionProgress close={() => { session.state.progress = false; menuButton?.focus() }} />{/if}
