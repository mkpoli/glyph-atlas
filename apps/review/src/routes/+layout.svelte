<script>
  import '../app.css'
  import '../layers.css'
  import '@fontsource/klee-one/400.css'
  import '../fallback-fonts.css'
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
    · <a href="https://github.com/mkpoli/glyph-atlas" rel="noopener" target="_blank">GitHub ↗</a>
    <!-- Community chats, each named as it names itself and marked with the language spoken there. Marks: Simple Icons 16.32.0 (CC0). -->
    · <span class="chat" lang="zh-Hant"><a href="https://t.me/+OH7sJST3huo1ODll" rel="noopener" target="_blank"><svg class="chat-mark" viewBox="0 0 24 24" role="img" aria-label="Telegram"><path d="M11.944 0A12 12 0 0 0 0 12a12 12 0 0 0 12 12 12 12 0 0 0 12-12A12 12 0 0 0 12 0a12 12 0 0 0-.056 0zm4.962 7.224c.1-.002.321.023.465.14a.506.506 0 0 1 .171.325c.016.093.036.306.02.472-.18 1.898-.962 6.502-1.36 8.627-.168.9-.499 1.201-.82 1.23-.696.065-1.225-.46-1.9-.902-1.056-.693-1.653-1.124-2.678-1.8-1.185-.78-.417-1.21.258-1.91.177-.184 3.247-2.977 3.307-3.23.007-.032.014-.15-.056-.212s-.174-.041-.249-.024c-.106.024-1.793 1.14-5.061 3.345-.48.33-.913.49-1.302.48-.428-.008-1.252-.241-1.865-.44-.752-.245-1.349-.374-1.297-.789.027-.216.325-.437.893-.663 3.498-1.524 5.83-2.529 6.998-3.014 3.332-1.386 4.025-1.627 4.476-1.635z"/></svg>互助認字</a>（中文）</span>
    · <span class="chat" lang="ja"><a href="https://discord.gg/PMMRZ7Dw8D" rel="noopener" target="_blank"><svg class="chat-mark" viewBox="0 0 24 24" role="img" aria-label="Discord"><path d="M20.317 4.3698a19.7913 19.7913 0 00-4.8851-1.5152.0741.0741 0 00-.0785.0371c-.211.3753-.4447.8648-.6083 1.2495-1.8447-.2762-3.68-.2762-5.4868 0-.1636-.3933-.4058-.8742-.6177-1.2495a.077.077 0 00-.0785-.037 19.7363 19.7363 0 00-4.8852 1.515.0699.0699 0 00-.0321.0277C.5334 9.0458-.319 13.5799.0992 18.0578a.0824.0824 0 00.0312.0561c2.0528 1.5076 4.0413 2.4228 5.9929 3.0294a.0777.0777 0 00.0842-.0276c.4616-.6304.8731-1.2952 1.226-1.9942a.076.076 0 00-.0416-.1057c-.6528-.2476-1.2743-.5495-1.8722-.8923a.077.077 0 01-.0076-.1277c.1258-.0943.2517-.1923.3718-.2914a.0743.0743 0 01.0776-.0105c3.9278 1.7933 8.18 1.7933 12.0614 0a.0739.0739 0 01.0785.0095c.1202.099.246.1981.3728.2924a.077.077 0 01-.0066.1276 12.2986 12.2986 0 01-1.873.8914.0766.0766 0 00-.0407.1067c.3604.698.7719 1.3628 1.225 1.9932a.076.076 0 00.0842.0286c1.961-.6067 3.9495-1.5219 6.0023-3.0294a.077.077 0 00.0313-.0552c.5004-5.177-.8382-9.6739-3.5485-13.6604a.061.061 0 00-.0312-.0286zM8.02 15.3312c-1.1825 0-2.1569-1.0857-2.1569-2.419 0-1.3332.9555-2.4189 2.157-2.4189 1.2108 0 2.1757 1.0952 2.1568 2.419 0 1.3332-.9555 2.4189-2.1569 2.4189zm7.9748 0c-1.1825 0-2.1569-1.0857-2.1569-2.419 0-1.3332.9554-2.4189 2.1569-2.4189 1.2108 0 2.1757 1.0952 2.1568 2.419 0 1.3332-.946 2.4189-2.1568 2.4189Z"/></svg>漢字泉 #集字厨ノ天下</a>（日本語）</span></p>
</footer>
{#if shown}{#if shown.origin === 'corpus'}<CorpusDialog id={shown.id} clientId={session.state.clientId} {close} {saved} {previous} {next} {position} {initial} />{:else}<CharacterDialog id={shown.id} clientId={session.state.clientId} {changed} {close} onVerdict={inspector.state.onVerdict} {saved} {previous} {next} {position} {initial} />{/if}{/if}
{#if savedNotice}<div class="save-toast" role="status">✓ {savedNotice}</div>{/if}
{#if exporting}<ExportReviews close={() => { exporting = false; menuButton?.focus() }} />{/if}
{#if session.state.progress}<CollectionProgress close={() => { session.state.progress = false; menuButton?.focus() }} />{/if}
