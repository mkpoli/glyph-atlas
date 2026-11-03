<script>
  import { onMount } from 'svelte'
  import CopyValue from '../components/CopyValue.svelte'
  import DateMark from '../components/DateMark.svelte'
  import ProductionBadge from '../components/ProductionBadge.svelte'
  import SourceCredit from '../components/SourceCredit.svelte'
  import { number } from '../lib/client.js'
  import { bookRecord } from '../lib/pages.js'
  import { t, localize } from '../lib/i18n.svelte.js'
  // One book and its pages: the source credit, then a page number per page with its box count, each
  // opening the page on its photo.
  let { bookId = '', initial = null } = $props()
  let book = $state(initial), error = $state(''), closed = false

  async function load() {
    error = ''
    try { const result = await bookRecord(bookId); if (!closed) book = result }
    catch (failure) { if (!closed) error = failure.message }
  }

  onMount(() => { if (!book) load(); return () => { closed = true } })
</script>

<section class="explore book">
  <a class="quiet-link" href={localize('/books')}>← {t('books.back')}</a>
  {#if error}<div class="error-message" role="alert">{error}<button onclick={load}>{t('common.retry')}</button></div>{/if}
  {#if book}
    <header class="book-head">
      <h1 lang="ja">{book.title || book.id}</h1>
      <p class="book-facts">
        {#if book.shelfmark}<span>{book.shelfmark}</span>{/if}
        <ProductionBadge item={book} />
        {#if Object.keys(book.dating).length}<DateMark item={book} />{/if}
        <span>{t('books.pages', { count: book.page_count })} · {t('pages.boxes.count', { count: book.box_count })}</span>
        <span class="copyable"><code>{book.id}</code><CopyValue value={book.id} /></span>
      </p>
      <SourceCredit item={book} />
    </header>
    {#if book.pages.length}
      <h2 class="book-pages-head">{t('books.pages.heading')}</h2>
      <!-- Every tile carries its box count, so the row keeps one rhythm; a page without boxes greys it. -->
      <ol class="book-pages">
        {#each book.pages as page (page.id)}
          <li><a class:shapeless={!page.width || !page.height} href={localize('/pages/' + encodeURIComponent(page.id))}>
            <span class="page-photo" style={page.width && page.height ? `aspect-ratio:${page.width}/${page.height}` : ''}>{#if page.thumb_url}<img src={page.thumb_url} alt="" loading="lazy" decoding="async" />{:else}<span class="photo-none">∅</span>{/if}</span>
            <span class="page-label"><span>{t('pages.pageNumber', { number: page.seq + 1 })}</span>
              <small class:zero={!page.units}>{page.units ? number(page.units) : 0}</small></span>
          </a></li>
        {/each}
      </ol>
    {:else}
      <div class="empty"><span class="empty-mark">∅</span><h2>{t('books.pages.empty')}</h2></div>
    {/if}
  {:else if !error}<p class="find-count" role="status">{t('books.loading')}</p>{/if}
</section>

<style>
  .book{padding-bottom:48px}
  .book-head{display:grid;gap:6px;margin:14px 0 20px}
  .book-head h1{font-size:26px;font-weight:500;letter-spacing:-.4px;margin:0}
  .book-facts{display:flex;flex-wrap:wrap;gap:12px;align-items:baseline;margin:0;font-size:12px;color:var(--muted)}
  .book-facts .copyable{display:inline-flex;align-items:center;gap:4px;color:var(--faint)}
  .book-facts code{font-size:11px}
  .book-pages-head{font-size:13px;font-weight:500;margin:0 0 8px}
  .book-pages{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(124px,1fr));gap:12px}
  /* A page is shown by its photo, in the shape the page has, with its number and its box count below;
     a record without a size gives the photo the shape of most pages (3:4). */
  .book-pages a{display:grid;gap:6px;border:1px solid var(--line);border-radius:6px;padding:6px 6px 8px;font-size:12px;background:var(--surface)}
  .page-photo{display:block;aspect-ratio:3/4;border-radius:3px;background:var(--surface-sunken);overflow:hidden}
  .shapeless .page-photo{aspect-ratio:3/4}
  .page-photo img{width:100%;height:100%;object-fit:cover;object-position:top center}
  .photo-none{display:grid;place-items:center;height:100%;font-size:15px;color:var(--faint)}
  .page-label{display:flex;align-items:baseline;justify-content:space-between;gap:6px;white-space:nowrap;padding:0 2px}
  .book-pages a:hover{border-color:var(--line-strong)}
  .book-pages small.zero{background:var(--surface-selected);color:var(--muted)}
  .book-pages small{min-width:26px;text-align:center;background:var(--accent-light);color:var(--accent);border-radius:10px;padding:1px 7px;font-size:10px;font-variant-numeric:tabular-nums}
</style>
