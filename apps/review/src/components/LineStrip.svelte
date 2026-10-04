<script>
  // The crop's neighbours on its line, in reading order, each with the label it carries now. When a
  // line's labels sit one box off, "Label is in another box" moves them: the reviewer names the box that
  // holds this crop's label, the strip shows the run relabelled, and one save records every crop in it
  // as a line correction. Each crop of the run can also have its box redrawn against the page, and the
  // same save carries the new boxes. Nothing is shown for a crop with no neighbours.
  import { untrack, tick } from 'svelte'
  import { line as readLine, request } from '../lib/client.js'
  import { readCrop } from '../lib/cropCache.js'
  import { fromEdit, redrawable, toSource } from '../lib/cropBox.js'
  import CropContext from './CropContext.svelte'
  import { t } from '../lib/i18n.svelte.js'
  import { moveOffset, planShift, shiftBody } from '../lib/lineShift.js'
  import ScriptText from './ScriptText.svelte'

  // `ready` holds the request back until the crop itself is on screen. `working` tells the dialog a
  // save is under way, and `saved` receives its answer.
  let { id, ready = true, disabled = false, working = null, saved = null } = $props()
  let items = $state(null), shift = $state(null), error = $state(''), notice = $state(''), busy = $state(false)
  // The records of the crops being redrawn, by offset, and the one whose box is on the page view now.
  let records = $state({}), editing = $state(null)
  let root = $state(null), generation = 0, shown = null, submission = null

  async function load(target) {
    const current = ++generation
    shift = null; editing = null; error = ''
    try {
      const result = await readLine(target)
      if (current === generation) items = result.items
    } catch { /* A line that cannot be read is left out; the next crop asks again. */ }
  }
  // Another crop starts from nothing; the same crop read again (after a save) keeps what it shows meanwhile.
  $effect(() => {
    const target = id
    untrack(() => { if (target !== shown) { shown = target; notice = ''; items = null; shift = null; editing = null; records = {}; error = '' } })
    if (ready) untrack(() => load(target)); else generation++
  })

  const plan = $derived(items && shift?.offset != null ? planShift(items, shift) : null)
  const steps = $derived(new Map(plan?.steps.map(step => [step.at, step])))
  const count = $derived(plan?.writes.length ?? 0)

  const focusBox = async offset => { await tick(); root?.querySelector(`[data-offset="${offset}"]`)?.focus() }
  function begin() {
    notice = ''; error = ''
    shift = { offset: null, skip: 0, cut: 0, blanks: new Set(), boxes: {}, tool: null }
    focusBox(moveOffset(items, 0, -1) || moveOffset(items, 0, 1))
  }
  const stop = () => { shift = null; editing = null; error = '' }
  function choose(offset) { shift = { ...shift, offset, skip: 0, cut: 0, blanks: new Set(), boxes: {} }; editing = null; submission = null }
  function pick(item) {
    if (busy || disabled || !shift) return
    if (shift.tool === 'blank') {
      const blanks = new Set(shift.blanks)
      if (!blanks.delete(item.offset)) blanks.add(item.offset)
      shift = { ...shift, blanks }; submission = null
    } else if (shift.tool === 'box') redraw(item)
    else if (item.offset !== 0) choose(item.offset)
  }
  const useTool = tool => { shift = { ...shift, tool: shift.tool === tool ? null : tool }; editing = null }
  // A crop of the run is redrawn on its own record: the page view needs its context and its box.
  async function redraw(item) {
    const step = steps.get(item.offset)
    if (!step || step.blank || step.kept) return
    error = ''
    try {
      const record = records[item.offset]?.id === item.id ? records[item.offset] : await readCrop(item.id)
      if (!redrawable(record)) { error = t('line.noBox'); return }
      records = { ...records, [item.offset]: record }
      editing = item.offset
    } catch (e) { error = e.message }
  }
  const edit = (source, mode) => { const record = records[editing]; if (record) { shift = { ...shift, boxes: { ...shift.boxes, [editing]: fromEdit(record, source, mode) } }; submission = null } }
  function resetBox() { const { [editing]: _, ...boxes } = shift.boxes; shift = { ...shift, boxes }; submission = null }
  const trim = (key, by) => { shift = { ...shift, [key]: shift[key] + by }; editing = null; submission = null }
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
    const body = shiftBody('', plan.writes, shift.boxes), signature = JSON.stringify(body)
    if (!submission || submission.signature !== signature) submission = { signature, id: crypto.randomUUID() }
    busy = true; working?.(true); error = ''
    try {
      const result = await request('/atlas/corrections', { ...body, id: submission.id })
      const skipped = result.skipped?.length ?? 0
      notice = [t('line.saved', { count: result.results.length }), skipped ? t('line.skipped', { count: skipped }) : ''].filter(Boolean).join(' ')
      shift = null; editing = null; records = {}; submission = null
      await saved?.(result)
      await load(id)
    } catch (e) { error = e.message }
    finally { busy = false; working?.(false) }
  }
</script>

{#snippet face(item)}
  {#if shift?.boxes[item.offset] && records[item.offset]}
    {@const record = records[item.offset]}
    {@const c = record.context_box}
    {@const b = toSource(record, shift.boxes[item.offset])}
    {@const r = b.w / b.h}
    <span class="line-thumb"><span class="line-clip" style:width="{r >= 1 ? 100 : 100 * r}%" style:aspect-ratio={r}><img src={record.context_image} alt=""
      style:width="{100 * c.w / b.w}%" style:left="{-100 * (b.x - c.x) / b.w}%" style:top="{-100 * (b.y - c.y) / b.h}%" /></span></span>
  {:else if item.image}<img src={item.image} alt="" loading="lazy" decoding="async" />{:else}<span class="line-missing">—</span>{/if}
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
        <li class:here={item.offset === 0} class:source={shift?.offset === item.offset} class:changing={step && !step.kept} class:blank={step?.blank} class:marked={shift?.blanks.has(item.offset)} class:redrawn={shift?.boxes[item.offset]} class:editing={editing === item.offset}>
          {#if shift}
            <button type="button" data-offset={item.offset} disabled={busy || disabled} aria-pressed={shift.tool === 'blank' ? shift.blanks.has(item.offset) : shift.tool === 'box' ? editing === item.offset : shift.offset === item.offset}
              aria-label={item.offset === 0 ? t('line.here', { label: item.label ?? '' }) : t('line.box', { label: item.label ?? '' })} onclick={() => pick(item)}>{@render face(item)}</button>
          {:else}<div aria-current={item.offset === 0 ? 'true' : undefined}>{@render face(item)}</div>{/if}
        </li>
      {/each}
    </ul>
    {#if shift}
      <p class="line-hint" role="status">{#if shift.offset == null}{t('line.pick')}{:else if !count}{t('line.same')}{:else}{t('line.preview', { count })}{#if plan.steps.some(step => step.kept)} {t('line.kept')}{/if}{/if}</p>
      <div class="line-tools">
        <span class="line-trim"><span>{t('line.start')}</span>
          <button type="button" aria-label={t('line.startEarlier')} disabled={busy || !plan?.more.start} onclick={() => trim('skip', -1)}>‹</button>
          <button type="button" aria-label={t('line.startLater')} disabled={busy || !plan || plan.steps.length < 2} onclick={() => trim('skip', 1)}>›</button></span>
        <span class="line-trim"><span>{t('line.end')}</span>
          <button type="button" aria-label={t('line.endEarlier')} disabled={busy || !plan || plan.steps.length < 2} onclick={() => trim('cut', 1)}>‹</button>
          <button type="button" aria-label={t('line.endLater')} disabled={busy || !plan?.more.end} onclick={() => trim('cut', -1)}>›</button></span>
        <button type="button" class="line-mark" aria-pressed={shift.tool === 'box'} disabled={busy || !plan} onclick={() => useTool('box')}>{t('line.redraw')}</button>
        <button type="button" class="line-mark blank" aria-pressed={shift.tool === 'blank'} disabled={busy || !plan} onclick={() => useTool('blank')}>{t('issue.blank.title')}</button>
      </div>
      {#if editing != null && records[editing]}
        <div class="line-editor">
          {#key records[editing].id}<CropContext item={records[editing]} detail={records[editing]} cropBox={shift.boxes[editing] ? toSource(records[editing], shift.boxes[editing]) : null}
            disabled={busy} editing onedit={edit} onexit={() => editing = null} describedby="line-keys" />{/key}
          <p class="line-keys" id="line-keys">{t('character.crop.keys')}
            <button type="button" disabled={busy || !shift.boxes[editing]} onclick={resetBox}>{t('common.reset')}</button>
            <button type="button" onclick={() => editing = null}>{t('character.crop.doneAdjusting')}</button></p>
        </div>
      {:else if shift.tool === 'box'}<p class="line-hint">{t('line.redraw.pick')}</p>{/if}
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
  .line-boxes{list-style:none;margin:8px 0 0;padding:0;display:grid;grid-template-columns:repeat(var(--boxes),minmax(0,72px));justify-content:start;gap:4px}
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
  .line-thumb{width:100%;aspect-ratio:1;display:grid;place-items:center}
  .line-clip{position:relative;display:block;overflow:hidden;background:#fff;border-radius:3px}
  .line-boxes .line-clip img{position:absolute;max-width:none;aspect-ratio:auto;object-fit:fill;background:none;border-radius:0}
  li.redrawn>*{border-color:var(--accent);border-style:dashed}
  li.editing>*{outline:2px solid var(--accent);outline-offset:1px}
  .line-editor{margin-top:12px}.line-editor :global(.crop-viewport){height:280px}
  .line-keys{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:8px 0 0;font-size:10px;color:var(--muted)}
  .line-keys button{font-size:10px;padding:4px 8px}
  .line-hint{margin:10px 0 0;font-size:12px;color:var(--muted)}
  .line-tools{display:flex;flex-wrap:wrap;align-items:center;gap:6px 14px;margin-top:10px;font-size:11px;color:var(--muted)}
  .line-trim{display:inline-flex;align-items:center;gap:4px}.line-trim span{margin-right:2px}
  .line-trim button{font-size:14px;line-height:1;padding:5px 11px;border-radius:5px;background:transparent}
  .line-mark{font-size:11px;padding:7px 10px;border-radius:5px;background:var(--surface-disabled);border-color:transparent}.line-trim+.line-trim+.line-mark{margin-left:auto}
  .line-mark[aria-pressed="true"]{color:var(--accent);background:var(--accent-light)}.line-mark.blank[aria-pressed="true"]{color:var(--wrong);background:var(--wrong-light)}
  .line-actions{display:flex;align-items:center;justify-content:flex-end;gap:16px;margin-top:12px}
  .line-actions .primary{padding:11px 18px;font-size:12px;gap:12px}
  .line-error{margin:10px 0 0;font-size:12px;color:var(--wrong)}
  .line-note{margin:10px 0 0;font-size:12px;color:var(--muted)}
</style>
