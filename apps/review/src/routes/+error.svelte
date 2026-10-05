<script>
  // Every error page, in the site's header and footer: what went wrong in a sentence, the next step
  // (a search, Explore, back, or trying again), and the status code drawn as a grid of cells. Once the
  // page is in the browser, real crops fly in and settle into the code's cells, each a link to its
  // crop; without them the code stays drawn in solid cells.
  import { onMount, untrack } from 'svelte'
  import { page } from '$app/state'
  import Glyph from '$components/Glyph.svelte'
  import Seo from '$components/Seo.svelte'
  import { cropAddress } from '$lib/inspector.svelte.js'
  import { t, around, localize, delocalize } from '$lib/i18n.svelte.js'
  import { codeCells, errorCase, suggestion, sampleCrops } from '$lib/errorPage.js'
  import { runAddress } from '$lib/ngrams.js'
  import { cropTone } from '$lib/cropPaint.js'

  let online = $state(true), canGoBack = $state(false)
  const status = $derived(page.status)
  const kind = $derived(errorCase({ status, code: page.error?.code, message: page.error?.message ?? '', routeId: page.route?.id ?? '', online }))
  const path = $derived(delocalize(page.url.pathname).path)
  const offered = $derived(status === 404 ? suggestion(path, kind) : null)
  const code = $derived(codeCells(status))
  const title = $derived(t(`error.${kind}.title`))
  const retry = $derived(kind === 'server' || kind === 'offline')

  // The crops dealt into the lit cells, by slot: `{ crop, key, dx, dy, r, delay, swap }`.
  let dealt = $state([]), runs = $state([])
  let pool = [], next = 0, keys = 0
  const still = () => typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches
  // Where a crop flies in from, in cells, and how long it waits: the cells fill roughly in reading order.
  const deal = (crop, slot, swap = false) => ({ crop, key: keys++, swap, dx: (Math.random() - .5) * 10, dy: (Math.random() - .3) * 8,
    r: (Math.random() - .5) * 70, delay: swap ? 0 : Math.round(slot * 32 + Math.random() * 160) })

  // Another error page in the same visit has its own count of lit cells; the crops already read are dealt again.
  $effect(() => { const lit = code.lit; untrack(() => { if (pool.length) { dealt = Array.from({ length: lit }, (_, slot) => deal(pool[(next + slot) % pool.length], slot)); next += lit } }) })
  onMount(() => {
    online = navigator.onLine
    canGoBack = history.length > 1
    // Back online, the page is asked for again.
    const reconnect = () => { if (kind === 'offline') location.reload() }
    addEventListener('online', reconnect)
    const controller = new AbortController()
    let timer = 0
    const stop = setTimeout(() => controller.abort(), 8000)
    if (online) sampleCrops({ signal: controller.signal }).then(found => {
      if (!found.crops.length) return
      pool = found.crops; runs = found.runs
      dealt = Array.from({ length: code.lit }, (_, slot) => deal(pool[slot % pool.length], slot))
      next = code.lit
      // Once they have settled, a cell now and then takes a crop it has not shown, while the tab is in view.
      if (still() || pool.length <= code.lit) return
      timer = setInterval(() => {
        if (document.hidden) return
        const slot = Math.floor(Math.random() * code.lit)
        dealt[slot] = deal(pool[next++ % pool.length], slot, true)
      }, 2600)
    }, () => {}).finally(() => clearTimeout(stop))
    return () => { removeEventListener('online', reconnect); controller.abort(); clearTimeout(stop); clearInterval(timer) }
  })
</script>

<Seo {title} description={t(`error.${kind}.body`)} index={false} />
<section class="error-page" aria-labelledby="error-title">
  <div class="copy">
    <p class="visually-hidden">{t('error.status', { status })}</p>
    <h1 id="error-title">{title}</h1>
    <p class="body">{t(`error.${kind}.body`)}</p>
    {#if offered}
      <a class="suggestion" href={localize(offered.path)}><span class="suggested" class:id={offered.kind === 'crop'}>{offered.text}</span>
        <span class="suggestion-label">{t(`error.suggest.${offered.kind}`)}</span><span class="arrow" aria-hidden="true">→</span></a>
    {/if}
    {#if kind !== 'offline'}
      <form class="search" action={localize('/')} method="get" role="search">
        <label class="visually-hidden" for="error-search">{t('error.search.label')}</label>
        <input id="error-search" name="q" type="search" autocomplete="off" placeholder={t('search.placeholder')} />
        <button type="submit">{t('error.search.submit')}</button>
      </form>
    {/if}
    <div class="actions">
      {#if retry}<button class="lead" type="button" onclick={() => location.reload()}>{t('common.tryAgain')}</button>{/if}
      <a class:lead={!retry} href={localize('/')}>{t('nav.explore')}</a>
      {#if kind === 'forms'}<a href={localize('/forms')}>{t('nav.forms')}</a>{/if}
      {#if canGoBack}<button type="button" onclick={() => history.back()}>{t('error.back')}</button>{/if}
    </div>
  </div>
  <div class="code">
    <!-- The code is drawn for the eye; the sentence above says what it means, and the crops are reached from their sequences below. -->
    <div class="cells" class:dealt={dealt.length > 0} style:--columns={code.columns} aria-hidden="true">
      {#each code.cells as cell (cell.row * 100 + cell.column)}
        <div class="cell" class:lit={cell.lit} style:grid-row={cell.row} style:grid-column={cell.column}>
          {#if cell.lit && dealt[cell.slot]}
            {@const d = dealt[cell.slot]}
            {#key d.key}
              <a class="crop" class:swap={d.swap} href={cropAddress(d.crop.id, d.crop.origin === 'corpus' ? 'corpus' : 'collection')} tabindex="-1"
                style:--dx={d.dx} style:--dy={d.dy} style:--r="{d.r}deg" style:--delay="{d.delay}ms" style={cropTone(d.crop)}><Glyph item={d.crop} alt="" class="cell-glyph" eager /></a>
            {/key}
          {/if}
        </div>
      {/each}
    </div>
    <p class="caption">{#if runs.length}{around('error.crops.caption', 'runs')[0]}{#each runs as run (run)}<a href={localize(runAddress(run))}>{run}</a>{/each}{around('error.crops.caption', 'runs')[1]}{/if}</p>
  </div>
</section>

<style>
  .error-page{max-width:1120px;margin:0 auto;padding:clamp(40px,8vw,96px) 4.4vw clamp(56px,9vw,112px);display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.05fr);
    gap:clamp(32px,6vw,88px);align-items:center;align-content:center;min-height:min(680px,calc(100svh - 200px));overflow-x:clip}
  .code{order:-1;min-width:0}
  .cells{--gap:clamp(3px,.7vw,6px);display:grid;grid-template-columns:repeat(var(--columns),minmax(0,1fr));gap:var(--gap);width:min(100%,540px)}
  .cell{aspect-ratio:1;border-radius:22%;border:1px solid var(--line);position:relative}
  /* A lit cell is solid until a crop takes it, so the code reads before any crop arrives, and without them. */
  .cell.lit{border-color:transparent;background:var(--surface-tile)}
  .cell.lit::before{content:'';position:absolute;inset:0;border-radius:inherit;background:var(--ink);transition:opacity .6s ease-out}
  .dealt .cell.lit::before{opacity:0;transition-delay:.35s}
  .crop{position:absolute;inset:0;display:block;border-radius:inherit;background:var(--tone,var(--crop-paper));padding:7%;
    animation:settle .95s cubic-bezier(.18,.9,.22,1.08) var(--delay) both}
  .crop.swap{animation:arrive .6s cubic-bezier(.2,.8,.3,1.2) both}
  .crop:hover{z-index:1;box-shadow:0 6px 18px var(--shadow)}
  .crop :global(.cell-glyph){width:100%;height:100%;transition:transform .18s ease-out}
  .crop:hover :global(.cell-glyph){transform:scale(1.14)}
  @keyframes settle{from{transform:translate(calc(var(--dx) * 100%),calc(var(--dy) * 100%)) rotate(var(--r)) scale(.55);opacity:0}
    35%{opacity:1}to{transform:none;opacity:1}}
  @keyframes arrive{from{transform:scale(.4) rotate(calc(var(--r) / 3));opacity:0}to{transform:none;opacity:1}}
  .caption{min-height:1.7em;margin-top:14px;font-size:12px;line-height:1.7;color:var(--muted)}
  .caption a{display:inline-block;margin:0 3px;padding:0 7px;border:1px solid var(--line);border-radius:6px;background:var(--surface);color:var(--ink);
    font-family:"Klee One","LXGW WenKai TC","LXGW WenKai","GenZui Sans",serif;font-size:14px;line-height:1.6}
  .caption a:hover{border-color:var(--line-strong)}

  h1{font-size:clamp(26px,3.2vw,36px);line-height:1.25;font-weight:500;letter-spacing:-.02em;text-wrap:balance;word-break:auto-phrase}
  .body{margin-top:14px;font-size:15px;line-height:1.7;color:var(--muted);max-width:44ch;text-wrap:pretty;word-break:auto-phrase}
  .suggestion{margin-top:24px;display:flex;align-items:center;gap:16px;max-width:420px;padding:12px 18px 12px 14px;border:1px solid var(--line);border-radius:10px;background:var(--surface)}
  .suggestion:hover{border-color:var(--line-strong)}
  .suggested{font-family:"Klee One","LXGW WenKai TC","LXGW WenKai","GenZui Sans",serif;font-size:28px;line-height:1.2;min-width:0;overflow-wrap:anywhere}
  .suggestion .suggested.id{font-family:inherit;font-size:13px}
  .suggestion-label{font-size:13px;color:var(--muted);margin-left:auto;white-space:nowrap}
  .arrow{color:var(--muted)}
  .search{margin-top:24px;display:flex;gap:8px;max-width:420px}
  .search input{flex:1;padding:11px 12px;font-size:14px}
  .search button{padding:10px 16px;font-size:13px}
  .actions{margin-top:16px;display:flex;flex-wrap:wrap;gap:8px}
  .actions a,.actions button{display:inline-flex;align-items:center;padding:10px 16px;font-size:13px;border:1px solid var(--line);border-radius:7px;background:var(--surface);color:var(--ink)}
  .actions a:hover{border-color:var(--line-strong)}
  .actions .lead{background:var(--action-surface);border-color:var(--action-surface);color:var(--on-color)}
  .actions .lead:hover{border-color:var(--action-surface);opacity:.92}

  @media (max-width:760px){
    .error-page{grid-template-columns:minmax(0,1fr);gap:28px;min-height:0;padding:32px 16px 56px}
    .cells{margin:0 auto}
    .caption{text-align:center}
  }
  @media (prefers-reduced-motion:reduce){
    .crop,.crop.swap{animation:none}
    .cell.lit::before,.crop :global(.cell-glyph){transition:none}
  }
</style>
