<script>
  // The logo as a select of the input sites, with a line on to the gallery of writing-system projects.
  import { t, locale, localize } from '$lib/i18n.svelte.js'

  let { children } = $props()

  const SITES = [
    { name: 'Tangut Input', href: 'https://tangut.mkpo.li/', logo: '/sites/tangut-input.svg' },
    { name: 'Jurchen Input', href: 'https://jurchen.mkpo.li/', logo: '/sites/jurchen-input.svg' },
    { name: 'IDS Workshop', href: 'https://ids.mkpo.li/', logo: '/sites/ids-workshop.svg' },
  ]
  // The gallery has a page for each of these languages; any other reads the English one.
  const more = $derived(`https://scripts.mkpo.li/${['ja', 'zh-Hans', 'zh-Hant', 'ko'].includes(locale()) ? `${locale()}/` : ''}`)

  let open = $state(false)
  let root = $state()
  let trigger = $state()
  const items = () => [...root.querySelectorAll('[role="menuitem"]')]

  function show() {
    open = true
    // Once the menu has rendered, as a select opens on its current option.
    requestAnimationFrame(() => root?.querySelector('[aria-current]')?.focus())
  }
  function hide(refocus = false) {
    open = false
    if (refocus) trigger?.focus()
  }

  function onTriggerKey(event) {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      show()
    }
  }

  // The menu's keys stay in it, so that page shortcuts do not act behind it.
  function onMenuKey(event) {
    event.stopPropagation()
    const list = items()
    const at = list.indexOf(document.activeElement)
    if (event.key === 'Escape') {
      event.preventDefault()
      hide(true)
    } else if (event.key === 'Tab') {
      hide()
    } else if (event.key === ' ') {
      event.preventDefault()
      list[at]?.click()
    } else {
      const to = { ArrowDown: at + 1, ArrowUp: at - 1, Home: 0, End: list.length - 1 }[event.key]
      if (to === undefined) return
      event.preventDefault()
      list[(to + list.length) % list.length].focus()
    }
  }

  $effect(() => {
    if (!open) return
    const away = event => { if (!event.composedPath().includes(root)) hide() }
    document.addEventListener('pointerdown', away, true)
    return () => document.removeEventListener('pointerdown', away, true)
  })
</script>

{#snippet symbol()}<svg class="atlas-symbol" viewBox="0 0 32 32" fill="none" aria-hidden="true"><path class="cell" d="M4 4h9v9H4z" fill="currentColor"/><path class="cell" d="M19 4h9v9h-9z" fill="currentColor"/><path class="cell" d="M4 19h9v9H4z" fill="currentColor"/><path class="open-cell" d="M19 19h9v9h-9z" stroke="currentColor" stroke-width="2" pathLength="1"/></svg>{/snippet}

<div class="site-select" bind:this={root}>
  <button bind:this={trigger} type="button" class="wordmark" aria-haspopup="menu" aria-expanded={open} aria-label={t('app.sites')} onclick={() => (open ? hide() : show())} onkeydown={onTriggerKey}>
    {@render symbol()}{@render children()}<svg class="site-chevron" class:up={open} viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M1.5 3.5 5 7l3.5-3.5"/></svg>
  </button>
  {#if open}
    <div class="site-menu" role="menu" tabindex="-1" aria-label={t('app.sites')} onkeydown={onMenuKey}>
      <a role="menuitem" tabindex="-1" href={localize('/')} aria-current="page" aria-label={t('app.home.aria')} onclick={() => hide()}>
        {@render symbol()}<span>{t('app.name')}</span>
        <svg class="site-tick" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 8.5 6.5 12 13 4.5"/></svg>
      </a>
      {#each SITES as site (site.href)}
        <a role="menuitem" tabindex="-1" href={site.href} onclick={() => hide()}><img src={site.logo} alt="" width="28" height="28" /><span>{site.name}</span></a>
      {/each}
      <a class="site-more" role="menuitem" tabindex="-1" href={more} onclick={() => hide()}><span>{t('app.sites.more')}</span><span class="site-out" aria-hidden="true">↗</span></a>
    </div>
  {/if}
</div>
