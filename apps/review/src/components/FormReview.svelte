<script>
  // Cluster by cluster, every glyph at once: mark the ones that are not the family's character,
  // then save and move on. A form key also names the rest of the cluster before moving on, and M
  // marks a cluster that holds more than one form.
  import { onMount } from 'svelte'
  import { settle } from '../lib/settle.js'
  import ReferenceGlyph from './ReferenceGlyph.svelte'
  import { members as loadMembers, decide } from '../lib/forms.js'
  import { number, reviewer } from '../lib/client.js'
  import { t } from '../lib/i18n.svelte.js'

  let { family, start = 0, isOpen, onsaved, onexit } = $props()
  let index = $state(0), glyphs = $state([]), total = $state(0), loading = $state(false)
  let marked = $state(new Set()), anchor = null, issue = $state('character'), character = $state('')
  let busy = $state(false), error = $state('')
  const cluster = $derived(family.items[index] ?? null)
  const reviewed = $derived(family.items.filter(c => !isOpen(c)).length)

  async function load(at) {
    // The last cluster's tiles go at once, so nothing marked or clicked can reach the next one.
    index = at; marked = new Set(); anchor = null; error = ''; glyphs = []; total = 0
    if (!family.items[at]) return
    loading = true
    try {
      // Least typical first: the glyphs that do not belong sit furthest from the cluster centre.
      const page = await loadMembers(family.items[at].id, 0, 500, 'unusual')
      if (index === at) { glyphs = page.items; total = page.total }
    } catch (e) { error = e.message } finally { loading = false }
  }
  async function more() {
    const id = cluster.id, page = await loadMembers(id, glyphs.length, 500, 'unusual')
    if (cluster?.id === id) glyphs = [...glyphs, ...page.items]
  }
  function toggle(i, event) {
    if (loading || busy) return
    const next = new Set(marked)
    if (event?.shiftKey && anchor != null) {
      const [from, to] = [Math.min(anchor, i), Math.max(anchor, i)]
      for (let j = from; j <= to; j++) next.add(glyphs[j].id)
    } else next.has(glyphs[i].id) ? next.delete(glyphs[i].id) : next.add(glyphs[i].id)
    anchor = i; marked = next
  }
  function markAll() { marked = marked.size === glyphs.length ? new Set() : new Set(glyphs.map(g => g.id)) }
  // The next cluster still to review after the one at `from`, in the order the list had then; the
  // list may reorder once a cluster is done, so the order is taken before saving. The cluster itself
  // comes last: moving on without naming or reporting anything leaves it open, and only a family with
  // no open cluster left is done.
  function following(from, order = family.items.map(c => c.id)) {
    const open = new Set(family.items.filter(isOpen).map(c => c.id))
    const next = [...order.slice(from + 1), ...order.slice(0, from + 1)].find(id => open.has(id))
    return next ? family.items.findIndex(c => c.id === next) : family.items.length
  }
  async function save(form = null, mixed = false) {
    if (busy || !cluster) return
    busy = true; error = ''
    try {
      const units = [...marked], wrong = issue === 'character' && character.trim() ? { character: character.trim() } : {}
      // Every glyph of the cluster marked, none with a decision of its own: the cluster is reported
      // as a whole, in one decision that clearing the cluster takes back.
      const whole = !form && !mixed && units.length === total && glyphs.length === total && glyphs.every(g => g.basis !== 'form_glyph')
      if (whole) await decide({ kind: 'cluster', cluster: cluster.id, issue, client_id: reviewer(), ...wrong })
      // A decision covers at most 1,000 glyphs; a larger mark goes in parts.
      else for (let i = 0; i < units.length; i += 1000)
        await decide({ kind: 'glyph', units: units.slice(i, i + 1000), issue, client_id: reviewer(), ...wrong })
      // Marked glyphs are reported first, so a cluster marked mixed keeps them.
      if (mixed) await decide({ kind: 'cluster', cluster: cluster.id, issue: 'mixed', client_id: reviewer() })
      if (form) await decide({ kind: 'cluster', cluster: cluster.id, form, client_id: reviewer() })
      const from = index, order = family.items.map(c => c.id)
      await onsaved({ reported: units.length, issue, mixed, form, count: cluster.count - units.length })
      character = ''
      await load(following(from, order))
    } catch (e) { error = e.message } finally { busy = false }
  }
  function keydown(event) {
    if (busy || loading) return
    if (event.target.closest?.('input, textarea') || event.metaKey || event.ctrlKey || event.altKey) {
      if (event.key === 'Enter' && event.target.closest?.('input')) { event.preventDefault(); save() }
      return
    }
    const keys = '1234567890'
    if (keys.includes(event.key) && family.forms[keys.indexOf(event.key)]) { event.preventDefault(); save(family.forms[keys.indexOf(event.key)].char) }
    else if (event.key === 'Enter') { event.preventDefault(); save() }
    else if (event.key === 'a' || event.key === 'A') { event.preventDefault(); markAll() }
    else if (event.key === 's' || event.key === 'S') { event.preventDefault(); load(following(index)) }
    else if (event.key === 'm' || event.key === 'M') { event.preventDefault(); save(null, true) }
    else if (event.key === 'b' || event.key === 'B') { event.preventDefault(); issue = issue === 'crop' ? 'character' : 'crop' }
    else if (event.key === 'Escape') { event.preventDefault(); marked.size ? marked = new Set() : onexit(index) }
  }
  onMount(() => load(start))
</script>

<svelte:window onkeydown={keydown} />

<div class="form-review">
  <div class="review-bar">
    <div class="review-heading">
      <button class="quiet-link" onclick={() => onexit(index)}>{t('forms.allClusters')}</button>
      {#if cluster}
        <h3>{cluster.label} <small>{t('forms.glyphs.count', { count: cluster.count })}</small></h3>
        <span class="review-progress">{t('forms.review.progress', { reviewed: number(reviewed), clusters: number(family.items.length) })}</span>
      {/if}
    </div>
    {#if cluster}
      <p class="review-hint">{t('forms.review.hint', { char: family.char })}</p>
      <div class="review-actions">
        <div class="filter-tabs" role="group" aria-label={t('forms.review.issue.label')}>
          <button class:active={issue === 'character'} aria-pressed={issue === 'character'} onclick={() => issue = 'character'}>{t('forms.review.issue.character', { char: family.char })}</button>
          <button class:active={issue === 'crop'} aria-pressed={issue === 'crop'} onclick={() => issue = 'crop'}>{t('issue.crop.title')} <kbd>B</kbd></button>
        </div>
        {#if issue === 'character'}<input class="review-actual" bind:value={character} maxlength="4" placeholder={t('forms.actual.placeholder')} aria-label={t('forms.actual.aria')} />{/if}
        <button onclick={markAll}>{marked.size === glyphs.length && glyphs.length ? t('forms.review.markNone') : t('forms.review.markAll')} <kbd>A</kbd></button>
        <button onclick={() => save(null, true)} disabled={busy || loading}>{t('forms.mixed')} <kbd>M</kbd></button>
        <button onclick={() => load(following(index))} disabled={busy}>{t('forms.review.skip')} <kbd>S</kbd></button>
        <button class="primary" onclick={() => save()} disabled={busy || loading}>
          {marked.size ? t('forms.review.report', { count: marked.size }) : t('forms.review.next')} <kbd>↵</kbd>
        </button>
      </div>
      <div class="review-forms" aria-label={t('forms.review.nameRest')}>
        <small>{t('forms.review.nameRest')}</small>
        {#each family.forms as form, i (form.char)}
          <button class="review-form" disabled={busy || loading} onclick={() => save(form.char)} title={form.name ?? form.code_point}>
            <ReferenceGlyph char={form.char} code_point={form.code_point} script={form.script} />
            {#if i < 10}<kbd>{'1234567890'[i]}</kbd>{/if}
          </button>
        {/each}
      </div>
    {/if}
  </div>
  {#if error}<p class="error-message">{error}</p>{/if}
  {#if !cluster}
    <p class="review-done">{t('forms.review.done', { char: family.char })}</p>
  {:else}
    <div class="review-grid" aria-busy={loading}>
      {#if loading && !glyphs.length}{#each Array(Math.min(cluster.count, 36)) as _, i (i)}<span class="review-glyph shimmer" aria-hidden="true"></span>{/each}{/if}
      {#each glyphs as glyph, i (glyph.id)}
        <button class="review-glyph" class:marked={marked.has(glyph.id)} class:reported={glyph.reported}
                aria-pressed={marked.has(glyph.id)} onclick={event => toggle(i, event)} title={glyph.id}>
          {#if glyph.image}<img class="glyph-image" src={glyph.image} alt="" loading="lazy" use:settle />{/if}
          {#if glyph.reported}<span class="review-flag">{glyph.character ?? '⚠'}</span>
          {:else if glyph.form}<span class="review-form-mark">{glyph.form}</span>{/if}
        </button>
      {/each}
    </div>
    {#if glyphs.length < total}<div class="load-more"><button onclick={more}>{t('forms.showMore', { count: total - glyphs.length })}</button></div>{/if}
  {/if}
</div>

<style>
  .form-review{margin-top:16px}
  .review-bar{position:sticky;top:0;z-index:3;background:#fafafaf2;backdrop-filter:blur(12px);padding:12px 0;border-bottom:1px solid var(--line);display:flex;flex-direction:column;gap:10px}
  .review-heading{display:flex;align-items:baseline;gap:16px;flex-wrap:wrap}
  .review-heading h3{font-size:16px;font-weight:500}.review-heading small{font-size:11px;color:var(--muted);font-weight:400;margin-left:6px}
  .review-progress{margin-left:auto;font-size:11px;color:var(--muted);font-variant-numeric:tabular-nums}
  .review-hint{font-size:12px;color:var(--muted);margin:0}
  .review-actions{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
  .review-actions button{font-size:12px;padding:8px 12px}
  .review-actions kbd,.review-form kbd{font-size:9px;color:var(--muted);font-family:ui-monospace,monospace}
  .review-actions .primary{margin-left:auto}.review-actions .primary kbd{color:inherit;opacity:.75;font-size:11px}
  .review-actual{width:72px;padding:7px 9px;font-size:14px}
  .review-forms{display:flex;flex-wrap:wrap;gap:4px;align-items:center}
  .review-forms small{font-size:11px;color:var(--muted);margin-right:4px}
  .review-form{position:relative;display:flex;align-items:center;gap:4px;padding:4px 8px;background:#fff}
  .review-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(104px,1fr));gap:5px;margin-top:14px}
  .review-glyph{position:relative;aspect-ratio:1;padding:7px;border:2px solid transparent;border-radius:6px;background:#f1f1f3}
  span.review-glyph{display:block}.review-glyph.marked{border-color:var(--wrong);background:#fdeceb}
  .review-glyph.marked::after{content:"✕";position:absolute;top:2px;left:6px;font-size:13px;color:var(--wrong)}
  .review-glyph.reported{opacity:.55}
  .review-flag{position:absolute;top:3px;right:6px;font-size:14px;color:var(--wrong);font-family:"Noto Sans CJK JP","Yu Gothic",sans-serif}
  .review-form-mark{position:absolute;top:3px;right:6px;font-size:14px;color:var(--accent);font-family:"Kureedo Kata","GenZui Sans",system-ui,sans-serif}
  .review-done{padding:48px 0;font-size:14px;color:var(--muted);text-align:center}
  @media(max-width:700px){.review-grid{grid-template-columns:repeat(auto-fill,minmax(76px,1fr))}.review-actions .primary{margin-left:0}}
</style>
