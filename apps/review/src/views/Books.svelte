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
  // The query the address holds. A search writes it as the reader types; a link to another query, or
  // Back, brings one in. `written` is the query this view put there, so its own writes are not read
  // back as a new search.
  const asked = $derived(page.url.searchParams.get('q')?.trim() ?? '')
  let written = null, seen = null
  $effect(() => {
    const wanted = asked
    if (seen !== null && wanted !== seen && wanted !== written) { find = wanted; load() }
    seen = wanted
  })

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
      const query = find.trim()
      written = query
      replaceState(address(query), page.state)
      load()
    }, 200)
  }

  onMount(() => {
    seen = written = asked
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
          <!-- The cover is hidden from a screen reader and out of the tab order: the title below opens
               the same book, and the photo says nothing it does not. -->
          <span class="book-cover" role="presentation">
            {#if book.cover_url}<img src={book.cover_url} alt="" loading="lazy" decoding="async" />{:else}<span class="cover-none">∅</span>{/if}
          </span>
          <div class="book-text">
            <a class="book-title" href={localize('/books/' + encodeURIComponent(book.id))} lang="ja">{book.title || book.id}</a>
            <span class="book-meta">{#if book.shelfmark}<span class="book-shelfmark">{book.shelfmark}</span>{/if}<ProductionBadge item={book} />{#if Object.keys(book.dating).length}<DateMark item={book} />{/if}
              <span class="book-counts">{t('books.pages', { count: book.page_count })} · {t('pages.boxes.count', { count: book.box_count })}</span></span>
            {#if book.holder || book.licence}<SourceCredit item={book} />{/if}
          </div>
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
  .book-card{display:grid;grid-template-columns:63px minmax(0,1fr);gap:16px;align-items:center;padding:12px 10px;border-bottom:1px solid var(--line);font-size:13px}
  /* The cover is the book's first page, small: it stands where the first letter of the title is, and
     keeps that height on every row. */
  .book-cover{display:block;height:84px;width:63px;border:1px solid var(--line);border-radius:4px;background:var(--surface-sunken);overflow:hidden}
  .book-cover img{width:100%;height:100%;object-fit:cover;object-position:top center}
  .cover-none{display:grid;place-items:center;height:100%;font-size:15px;color:var(--faint)}
  .book-text{display:grid;gap:4px;min-width:0}
  .book-title{font-size:15px;font-weight:500;justify-self:start}
  .book-shelfmark{font-size:11px;color:var(--ink-soft)}
  .book-meta{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}
  .book-counts{font-size:11px;color:var(--ink-soft);font-variant-numeric:tabular-nums}
  .book-card :global(.source-credit){margin-top:2px}
  .books-more{margin-top:14px;font-size:12px;padding:9px 16px}
</style>
