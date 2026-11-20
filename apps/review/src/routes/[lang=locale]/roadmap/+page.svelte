<script>
  import Seo from '$components/Seo.svelte'
  import { t, localize } from '$lib/i18n.svelte.js'
  import { HOLDER_LANG, STAGES, tally } from '$lib/roadmap.js'
  const count = tally()
  const STATUSES = ['published', 'underway', 'planned']
  // A published copy opens its books on this site; any other opens the holder's scan.
  const address = edition => edition.book ? localize('/books') + '?' + new URLSearchParams({ q: edition.book }) : edition.href
</script>

<Seo title={t('roadmap.title')} />

<article class="roadmap">
  <header class="roadmap-head">
    <h1>{t('roadmap.title')}</h1>
    <dl class="roadmap-tally">
      {#each STATUSES as status (status)}
        <div><dt><span class="status" data-status={status}>{t(`roadmap.status.${status}`)}</span></dt><dd>{count[status]}</dd></div>
      {/each}
    </dl>
  </header>

  {#each STAGES as stage, index (stage.key)}
    <section class="stage">
      <h2><span class="stage-order">{index + 1}</span>{t(`roadmap.stage.${stage.key}`)}</h2>
      {#if stage.works.length}
        <table>
          <thead><tr><th scope="col">{t('roadmap.work')}</th><th scope="col">{t('roadmap.edition')}</th><th scope="col">{t('roadmap.holder')}</th><th scope="col">{t('roadmap.status')}</th></tr></thead>
          {#each stage.works as work (work.title + (work.by ?? ''))}
            {@const lang = work.lang ?? 'zh-Hant'}
            <tbody>
              {#each work.editions.length ? work.editions : [null] as edition, row (row)}
                <tr>
                  {#if row === 0}<th scope="rowgroup" rowspan={Math.max(1, work.editions.length)} {lang}><span class="work-title">{work.title}</span>{#if work.by}<small>{work.by}</small>{/if}</th>{/if}
                  {#if edition}
                    <td class="edition" {lang}>{#if address(edition)}<a href={address(edition)} rel={edition.book ? undefined : 'noopener'} target={edition.book ? undefined : '_blank'}>{edition.edition}{#if !edition.book}<span aria-hidden="true"> ↗</span>{/if}</a>{:else}{edition.edition}{/if}</td>
                    <td class="holder" lang={HOLDER_LANG[edition.holder] ?? 'zh-Hant'}>{edition.holder}</td>
                    <td><span class="status" data-status={edition.status}>{t(`roadmap.status.${edition.status}`)}</span></td>
                  {:else}
                    <td class="open" colspan="2">{t('roadmap.edition.open')}</td>
                    <td><span class="status" data-status="planned">{t('roadmap.status.planned')}</span></td>
                  {/if}
                </tr>
              {/each}
            </tbody>
          {/each}
        </table>
      {/if}
    </section>
  {/each}
</article>

<style>
  .roadmap { padding: 24px 24px 64px; font-size: 13px; line-height: 1.5; }
  .roadmap-head { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 12px 32px; margin-bottom: 24px; }
  .roadmap h1 { font-size: 28px; font-weight: 500; letter-spacing: -.6px; }
  .roadmap-tally { display: flex; flex-wrap: wrap; gap: 8px 20px; margin: 0; }
  .roadmap-tally div { display: flex; align-items: center; gap: 8px; }
  .roadmap-tally dd { margin: 0; font-variant-numeric: tabular-nums; font-weight: 600; }
  .stage + .stage { margin-top: 32px; }
  .stage h2 { display: flex; align-items: baseline; gap: 10px; font-size: 16px; font-weight: 600; padding-bottom: 8px; border-bottom: 1px solid var(--line); }
  .stage-order { color: var(--muted); font-variant-numeric: tabular-nums; font-weight: 400; }
  table { width: 100%; border-collapse: collapse; }
  thead th { text-align: start; font-size: 11px; font-weight: 500; color: var(--muted); padding: 10px 12px 6px 0; }
  tbody { border-bottom: 1px solid var(--line); }
  tbody th, tbody td { text-align: start; vertical-align: top; padding: 8px 12px 8px 0; font-weight: 400; }
  tbody th { width: 22%; }
  .work-title { display: block; font-family: var(--char-font); font-size: 16px; }
  tbody th small { display: block; color: var(--muted); font-size: 12px; margin-top: 2px; }
  .edition { font-family: var(--char-font); font-size: 14px; }
  .edition a { text-decoration: underline; text-decoration-color: var(--line); text-underline-offset: 3px; }
  .edition a:hover { text-decoration-color: currentColor; }
  .holder { width: 24%; color: var(--muted); }
  .open { color: var(--muted); }
  tbody td:last-child { width: 1%; white-space: nowrap; padding-right: 0; }
  .status { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; white-space: nowrap; }
  .status::before { content: ''; width: 8px; height: 8px; border-radius: 50%; border: 1.5px solid var(--muted); box-sizing: border-box; }
  .status[data-status='underway']::before { border-color: var(--accent); background: linear-gradient(90deg, var(--accent) 50%, transparent 50%); }
  .status[data-status='published']::before { border-color: var(--accent); background: var(--accent); }
  .status[data-status='planned'] { color: var(--muted); }
  @media (max-width: 700px) {
    .roadmap { padding: 16px 16px 48px; }
    thead { display: none; }
    table, tbody, tr, tbody th, tbody td { display: block; width: auto; }
    tbody { padding: 10px 0; }
    tbody th { padding: 0 0 6px; }
    tbody tr + tr { margin-top: 10px; }
    tbody td { padding: 0; }
    tbody td:last-child { width: auto; margin-top: 2px; }
    .holder { width: auto; font-size: 12px; }
  }
</style>
