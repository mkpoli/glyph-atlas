<script>
  // The crop's neighbours on its line, in reading order, each with the label it carries now. When a
  // line's labels sit one box off, "Label is in another box" moves them: the reviewer names the box that
  // holds this crop's label, the strip shows the run relabelled, and one save records every crop in it
  // as a line correction. Nothing is shown for a crop with no neighbours.
  import { untrack, tick } from 'svelte'
  import { line as readLine, request } from '../lib/client.js'
  import { t } from '../lib/i18n.svelte.js'
  import { moveOffset, planShift, shiftBody } from '../lib/lineShift.js'
  import ScriptText from './ScriptText.svelte'

  // `ready` holds the request back until the crop itself is on screen. `working` tells the dialog a
  // save is under way, and `saved` receives its answer.
  let { id, ready = true, disabled = false, working = null, saved = null } = $props()
  let items = $state(null), shift = $state(null), error = $state(''), notice = $state(''), busy = $state(false)
  let root = $state(null), generation = 0, shown = null, submission = null

  async function load(target) {
    const current = ++generation
    shift = null; error = ''
    try {
      const result = await readLine(target)
      if (current === generation) items = result.items
    } catch { /* A line that cannot be read is left out; the next crop asks again. */ }
  }
  // Another crop starts from nothing; the same crop read again (after a save) keeps what it shows meanwhile.
  $effect(() => {
    const target = id
    untrack(() => { if (target !== shown) { shown = target; notice = ''; items = null; shift = null; error = '' } })
    if (ready) untrack(() => load(target)); else generation++
  })

  const plan = $derived(items && shift?.offset != null ? planShift(items, shift) : null)
  const steps = $derived(new Map(plan?.steps.map(step => [step.at, step])))
  const count = $derived(plan?.writes.length ?? 0)

  const focusBox = async offset => { await tick(); root?.querySelector(`[data-offset="${offset}"]`)?.focus() }
  function begin() {
    notice = ''; error = ''
    shift = { offset: null, skip: 0, cut: 0, blanks: new Set(), marking: false }
    focusBox(moveOffset(items, 0, -1) || moveOffset(items, 0, 1))
  }
  const stop = () => { shift = null; error = '' }
  function choose(offset) { shift = { ...shift, offset, skip: 0, cut: 0, blanks: new Set() }; submission = null }
  function pick(item) {
    if (busy || disabled || !shift) return
    if (shift.marking) {
      if (item.offset < 0) return
      const blanks = new Set(shift.blanks)
      if (!blanks.delete(item.offset)) blanks.add(item.offset)
      shift = { ...shift, blanks }; submission = null
    } else if (item.offset !== 0) choose(item.offset)
  }
  const trim = (key, by) => { shift = { ...shift, [key]: Math.max(0, shift[key] + by) }; submission = null }
  function keydown(event) {
    if (!shift || event.metaKey || event.ctrlKey || event.altKey) return
    if (event.key === 'Escape') { event.preventDefault(); stop() }
    else if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      // The arrows are the strip's while a shift is chosen, not the list's.
      event.preventDefault()
      const offset = moveOffset(items, shift.offset ?? 0, event.key === 'ArrowLeft' ? -1 : 1)
      if (offset !== shift.offset) choose(offset)
      focusBox(offset)
    }
  }

  async function save() {
    if (busy || disabled || !count) return
    const body = shiftBody('', plan.writes), signature = JSON.stringify(body)
    if (!submission || submission.signature !== signature) submission = { signature, id: crypto.randomUUID() }
    busy = true; working?.(true); error = ''
    try {
      const result = await request('/atlas/corrections', { ...body, id: submission.id })
      const skipped = result.skipped?.length ?? 0
      notice = [t('line.saved', { count: result.results.length }), skipped ? t('line.skipped', { count: skipped }) : ''].filter(Boolean).join(' ')
      shift = null; submission = null
      await saved?.(result)
      await load(id)
    } catch (e) { error = e.message }
    finally { busy = false; working?.(false) }
  }
</script>

{#snippet face(item)}
  {#if item.image}<img src={item.image} alt="" loading="lazy" decoding="async" />{:else}<span class="line-missing">—</span>{/if}
  <span class="line-label" class:changing={steps.has(item.offset) && !steps.get(item.offset).kept}>{#if item.label}<ScriptText text={item.label} titled={false} />{:else}·{/if}</span>
  {#if shift}
    {@const step = steps.get(item.offset)}
    <span class="line-next" class:kept={step?.kept}>{#if step?.blank}×{:else if step?.kept}✓{:else if step}<ScriptText text={step.label} titled={false} />{/if}</span>
  {/if}
{/snippet}

{#if items?.length}
  <section class="line-strip" bind:this={root} aria-label={t('line.title')}>
    <div class="line-heading">
      <h3>{t('line.title')}</h3>
      {#if !shift}<button type="button" class="quiet-link" disabled={busy || disabled} onclick={begin}>{t('line.shift')}</button>{/if}
    </div>
    <!-- Reading order runs along the strip whichever way the line is written on the page. -->
    <ul class="line-boxes" style:--boxes={items.length} onkeydown={keydown}>
      {#each items as item (item.id)}
        {@const step = steps.get(item.offset)}
        <li class:here={item.offset === 0} class:source={shift?.offset === item.offset} class:changing={step && !step.kept} class:blank={step?.blank} class:marked={shift?.blanks.has(item.offset)}>
          {#if shift}
            <button type="button" data-offset={item.offset} disabled={busy || disabled} aria-pressed={shift.marking ? shift.blanks.has(item.offset) : shift.offset === item.offset}
              aria-label={item.offset === 0 ? t('line.here', { label: item.label ?? '' }) : t('line.box', { label: item.label ?? '' })} onclick={() => pick(item)}>{@render face(item)}</button>
          {:else}<div aria-current={item.offset === 0 ? 'true' : undefined}>{@render face(item)}</div>{/if}
        </li>
      {/each}
    </ul>
    {#if shift}
      <p class="line-hint" role="status">{#if shift.offset == null}{t('line.pick')}{:else if !count}{t('line.same')}{:else}{t('line.preview', { count })}{#if plan.steps.some(step => step.kept)} {t('line.kept')}{/if}{/if}</p>
      <div class="line-tools">
        <span class="line-trim"><span>{t('line.start')}</span>
          <button type="button" aria-label={t('line.startEarlier')} disabled={busy || !plan || !shift.skip} onclick={() => trim('skip', -1)}>‹</button>
          <button type="button" aria-label={t('line.startLater')} disabled={busy || !plan || plan.steps.length < 2} onclick={() => trim('skip', 1)}>›</button></span>
        <span class="line-trim"><span>{t('line.end')}</span>
          <button type="button" aria-label={t('line.endEarlier')} disabled={busy || !plan || plan.steps.length < 2} onclick={() => trim('cut', 1)}>‹</button>
          <button type="button" aria-label={t('line.endLater')} disabled={busy || !plan || !shift.cut} onclick={() => trim('cut', -1)}>›</button></span>
        <button type="button" class="line-mark" aria-pressed={shift.marking} disabled={busy || !plan} onclick={() => shift = { ...shift, marking: !shift.marking }}>{t('issue.blank.title')}</button>
      </div>
      <div class="line-actions">
        <button type="button" class="quiet-link" disabled={busy} onclick={stop}>{t('common.cancel')}</button>
        <button type="button" class="primary" disabled={busy || disabled || !count} onclick={save}>{busy ? t('common.saving') : t('line.save', { count })}</button>
      </div>
    {/if}
    {#if error}<p class="line-error" role="alert">{error}</p>{/if}
    {#if notice}<p class="line-note" role="status">{notice}</p>{/if}
  </section>
{/if}

<style>
  .line-strip{margin:18px 0 0}
  .line-heading{display:flex;align-items:baseline;justify-content:space-between;gap:12px;min-height:22px}
  .line-heading h3{margin:0;font-size:12px;font-weight:500;color:var(--muted)}
  .line-boxes{list-style:none;margin:8px 0 0;padding:0;display:grid;grid-template-columns:repeat(var(--boxes),minmax(0,1fr));gap:4px}
  .line-boxes li{min-width:0}
  .line-boxes li>*{display:flex;flex-direction:column;align-items:center;gap:2px;width:100%;padding:3px 2px;border:1px solid var(--line);border-radius:6px;background:var(--surface);color:inherit;font:inherit}
  .line-boxes button:not(:disabled):hover{border-color:var(--line-strong)}
  .line-boxes img,.line-missing{width:100%;aspect-ratio:1;object-fit:contain;display:grid;place-items:center;color:var(--muted)}
  .line-boxes img{background:#fff;border-radius:3px}
  .line-label,.line-next{display:block;font-size:15px;line-height:22px;height:22px;max-width:100%;overflow:hidden;white-space:nowrap}
  .line-label.changing{color:var(--muted);text-decoration:line-through;text-decoration-thickness:1px}
  .line-next{color:var(--accent);font-weight:600}.line-next.kept{color:var(--muted);font-weight:400;font-size:12px}
  li.here>*{border-color:var(--accent);background:var(--accent-light)}
  li.source>*{outline:2px dashed var(--accent);outline-offset:1px}
  li.changing>*{border-color:var(--accent)}
  li.blank>*{border-color:var(--wrong)}li.blank .line-next{color:var(--wrong)}
  li.marked>*{background:var(--wrong-light)}
  .line-hint{margin:10px 0 0;font-size:12px;color:var(--muted)}
  .line-tools{display:flex;flex-wrap:wrap;align-items:center;gap:6px 14px;margin-top:10px;font-size:11px;color:var(--muted)}
  .line-trim{display:inline-flex;align-items:center;gap:4px}.line-trim span{margin-right:2px}
  .line-trim button{font-size:14px;line-height:1;padding:5px 11px;border-radius:5px;background:transparent}
  .line-mark{font-size:11px;padding:7px 10px;border-radius:5px;background:var(--surface-disabled);border-color:transparent;margin-left:auto}
  .line-mark[aria-pressed="true"]{color:var(--wrong);background:var(--wrong-light)}
  .line-actions{display:flex;align-items:center;justify-content:flex-end;gap:16px;margin-top:12px}
  .line-actions .primary{padding:11px 18px;font-size:12px;gap:12px}
  .line-error{margin:10px 0 0;font-size:12px;color:var(--wrong)}
  .line-note{margin:10px 0 0;font-size:12px;color:var(--muted)}
</style>
