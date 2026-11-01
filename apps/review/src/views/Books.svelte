<script>
  import { onMount } from 'svelte'
  import { replaceState } from '$app/navigation'
  import { page } from '$app/state'
  import DateMark from '../components/DateMark.svelte'
  import ProductionBadge from '../components/ProductionBadge.svelte'
  import SourceCredit from '../components/SourceCredit.svelte'
  import { bookList } from '../lib/pages.js'
  import { t, localize } from '../lib/i18n.svelte.js'
  // The books of the dataset, found by what a reader knows of one: a word of its title, holder,
  // shelfmark, genre or date. Choosing a book opens its pages.
  let { initialQuery = '' } = $props()
  let find = $state(initialQuery), books = $state([]), total = $state(0), loading = $state(true), error = $state('')
  let answered = 0, closed = false, timer
  // The query in the address, as a search writes it and as a link opens it. `find` holds what the
  // reader has typed; a query that arrives from elsewhere takes its place.
  const asked = $derived(page.url.searchParams.get('q')?.trim() ?? '')
  let shownFor = null
  $effect(() => { const wanted = asked; if (shownFor !== null && wanted !== shownFor) { find = wanted; load() } shownFor = wanted })

  const address = query => localize('/books') + (query ? '?' + new URLSearchParams({ q: query }) : '')

  async function load({ append = false } = {}) {
    const query = find.trim(), current = ++answered
    loading = true
    error = ''
    try {
      const result = await bookList(query, append ? books.length : 0)
      if (closed || current !== answered) return
      books = append ? [...books, ...result.books] : result.books
      total = result.total
    } catch (failure) { if (!closed && current === answered) error = failure.message }
    finally { if (!closed && current === answered) loading = false }
  }

  function search() {
    clearTimeout(timer)
    timer = setTimeout(() => {
      replaceState(address(find.trim()), page.state)
      load()
    }, 200)
  }

  onMount(() => {
    shownFor = asked
    load()
    return () => { closed = true; clearTimeout(timer) }
  })
</script>

<section class="explore books">
  <header class="books-head">
    <h1>{t('books.heading')}</h1>
    <input class="books-find" type="search" bind:value={find} oninput={search} placeholder={t('books.find')}
           aria-label={t('books.findAria')} autocomplete="off" />
    {#if !loading && !error && total}<p class="find-count" role="status">{t('books.found', { count: total })}</p>{/if}
  </header>
  {#if error}<div class="error-message" role="alert">{error}<button onclick={() => load()}>{t('common.retry')}</button></div>{/if}
  {#if loading && !books.length}<p class="find-count" role="status">{t('books.loading')}</p>
  {:else if !books.length && !error && find.trim()}<div class="empty"><span class="empty-mark">∅</span><h2>{t('books.none', { query: find.trim() })}</h2></div>
  {:else if !books.length && !error}<div class="empty"><span class="empty-mark">∅</span><h2>{t('books.empty')}</h2></div>
  {:else}
    <ul class="book-list">
      {#each books as book (book.id)}
        <li class="book-card">
          <a class="book-title" href={localize('/books/' + encodeURIComponent(book.id))} lang="ja">{book.title || book.id}</a>
          <span class="book-meta">{#if book.shelfmark}<span class="book-shelfmark">{book.shelfmark}</span>{/if}<ProductionBadge item={book} />{#if Object.keys(book.dating).length}<DateMark item={book} />{/if}
            <span class="book-counts">{t('books.pages', { count: book.page_count })} · {t('pages.boxes.count', { count: book.box_count })}</span></span>
          {#if book.holder || book.licence}<SourceCredit item={book} />{/if}
        </li>
      {/each}
    </ul>
    {#if books.length < total}
      <button class="books-more" onclick={() => load({ append: true })} disabled={loading}>{t('books.more')}</button>
    {/if}
  {/if}
</section>

<style>
  .books{padding-bottom:48px}
  .books-head{display:grid;gap:10px;margin-bottom:18px}
  .books-head h1{font-size:28px;font-weight:500;letter-spacing:-.6px;margin:0}
  .books-find{width:100%;font-size:14px;padding:10px 12px;border-color:var(--line-strong)}
  .book-list{list-style:none;margin:0;padding:0;border-top:1px solid var(--line)}
  .book-card{display:grid;gap:4px;padding:14px 10px;border-bottom:1px solid var(--line);font-size:13px;min-height:78px;align-content:center}
  .book-title{font-size:15px;font-weight:500;justify-self:start}
  .book-shelfmark{font-size:11px;color:var(--ink-soft)}
  .book-meta{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}
  .book-counts{font-size:11px;color:var(--ink-soft);font-variant-numeric:tabular-nums}
  .book-card :global(.source-credit){margin-top:2px}
  .books-more{margin-top:14px;font-size:12px;padding:9px 16px}
</style>
