<script>
  /**
   * The line view: the crop at readable size, the unit boxes, and the transcription beside it.
   *
   * Each character of the transcription is linked to its unit: hovering or selecting either one
   * marks the other. The neighbouring two lines are drawn faint for context. Long lines scroll and
   * render only the boxes in the window; a 割書 line whose units form two runs renders its
   * transcription in two columns.
   */
  import Crop from '../components/Crop.svelte'
  import { columns, pagePoint, visibleRange } from '../lib/geometry.js'

  let { session } = $props()

  let reader = $state(null)
  let crop = $state(null)
  let scrollTop = $state(0)
  let viewH = $state(640)
  let viewW = $state(0)
  let hover = $state(null)
  let drag = $state(null)
  let inputEl = $state(null)

  const line = $derived(session.line)
  const box = $derived(session.lineBox)
  const vertical = $derived(line?.vertical ?? true)
  const ordered = $derived(session.ordered)
  const windowed = $derived(ordered.length > 120)
  /** What the transcription shows: everything, or the window of a very long line. */
  const transcribable = $derived(windowed ? shown : ordered)
  const columnsOf = $derived(columns(transcribable, vertical))

  /** Css pixels per page pixel: fit the line to the reader, but never below a readable size. */
  const scale = $derived.by(() => {
    if (!box || !box.h || !box.w) return 0.5
    const byHeight = viewH / box.h
    const byWidth = viewW ? (viewW - 60) / box.w : 1.4
    return Math.max(0.12, Math.min(1.5, Math.max(byHeight, 0.55), Math.max(byWidth, 0.2)))
  })

  const offsets = $derived(ordered.map((unit) => (unit.box ? (unit.box.y - (box?.y ?? 0)) * scale : 0)))
  const range = $derived(windowed ? visibleRange(offsets, scrollTop, viewH) : { start: 0, end: ordered.length })
  const shown = $derived(windowed ? ordered.slice(range.start, range.end) : ordered)

  /**
   * The neighbours in the order they are drawn. Vertical text reads right to left, so the next line
   * sits to the left of this one and the previous to its right; horizontal text puts the previous
   * above and the next below. `session.neighbours` is `[previous, next]`, either may be null.
   */
  const before = $derived(vertical ? [session.neighbours[1]].filter(Boolean) : [session.neighbours[0]].filter(Boolean))
  const after = $derived(vertical ? [session.neighbours[0]].filter(Boolean) : [session.neighbours[1]].filter(Boolean))

  /** The cut `s` would make: a line through the pointer, across the line's direction. */
  const cut = $derived.by(() => {
    const unit = session.current
    if (!unit?.box || !session.pointer || !box) return null
    const point = session.pointer
    const inside =
      point.x >= unit.box.x - 4 &&
      point.x <= unit.box.x + unit.box.w + 4 &&
      point.y >= unit.box.y - 4 &&
      point.y <= unit.box.y + unit.box.h + 4
    if (!inside) return null
    if (vertical) {
      return { left: 0, top: (point.y - box.y) * scale, width: box.w * scale, height: 2 }
    }
    return { left: (point.x - box.x) * scale, top: 0, width: 2, height: box.h * scale }
  })

  $effect(() => {
    if (inputEl && (session.mode === 'reading' || session.mode === 'note')) inputEl.focus()
  })

  $effect(() => {
    if (line) session.loadNeighbours()
  })

  function character(unit) {
    if (unit.unicode) {
      const text = unit.unicode
        .split(/\s+/)
        .map((code) => {
          const match = /^U\+([0-9A-Fa-f]+)$/.exec(code)
          if (!match) return ''
          try {
            return String.fromCodePoint(parseInt(match[1], 16))
          } catch {
            return ''
          }
        })
        .join('')
      if (text) return text
    }
    if (unit.granularity === 'sequence') return unit.text_source || unit.reading || '〓'
    if (unit.kind === 'gap') return '∅'
    if (unit.kind === 'unreadable') return '〓'
    return unit.text_source || unit.reading || '□'
  }

  function unitClass(unit) {
    const classes = ['box']
    if (session.selection.includes(unit.id)) classes.push('selected')
    if (session.focused === unit.id) classes.push('focused')
    if (hover === unit.id) classes.push('focused')
    return classes.join(' ')
  }

  function unitStyle(unit) {
    const current = boxOf(unit)
    const events = session.mode === 'draw-unit' ? 'pointer-events:none;' : ''
    return `${events}left:${(current.x - box.x) * scale}px;top:${(current.y - box.y) * scale}px;width:${
      current.w * scale
    }px;height:${current.h * scale}px`
  }

  function pick(event, unit) {
    hover = unit.id
    session.setPointer(pagePoint(event, crop, box, scale))
  }

  function hit(event, unit) {
    if (session.mode === 'draw-unit') return
    event.stopPropagation()
    session.setPointer(pagePoint(event, crop, box, scale))
    session.select(unit.id, { extend: event.shiftKey, toggle: event.metaKey || event.ctrlKey })
    startDrag(event, unit, 'move')
  }

  function startDrag(event, unit, mode, corner = null) {
    if (event.button !== 0 || !unit.box) return
    const origin = pagePoint(event, crop, box, scale)
    drag = { unit, mode, corner, origin, current: { ...unit.box } }
    window.addEventListener('pointermove', onDragMove)
    window.addEventListener('pointerup', onDragEnd, { once: true })
  }

  function onDragMove(event) {
    if (!drag) return
    const point = pagePoint(event, crop, box, scale)
    const dx = point.x - drag.origin.x
    const dy = point.y - drag.origin.y
    const start = drag.unit.box
    const minimum = 6
    let next = { ...start }
    if (drag.mode === 'move') {
      next.x = start.x + dx
      next.y = start.y + dy
    } else {
      const corner = drag.corner
      if (corner.includes('w')) {
        next.x = Math.min(start.x + dx, start.x + start.w - minimum)
        next.w = start.x + start.w - next.x
      }
      if (corner.includes('e')) next.w = Math.max(minimum, start.w + dx)
      if (corner.includes('n')) {
        next.y = Math.min(start.y + dy, start.y + start.h - minimum)
        next.h = start.y + start.h - next.y
      }
      if (corner.includes('s')) next.h = Math.max(minimum, start.h + dy)
    }
    drag = {
      ...drag,
      current: {
        x: Math.round(next.x),
        y: Math.round(next.y),
        w: Math.round(next.w),
        h: Math.round(next.h),
      },
    }
    session.setPointer(point)
  }

  async function onDragEnd() {
    window.removeEventListener('pointermove', onDragMove)
    const finished = drag
    drag = null
    if (!finished) return
    const before = finished.unit.box
    const after = finished.current
    if (before.x === after.x && before.y === after.y && before.w === after.w && before.h === after.h) return
    await session.post(
      {
        target_type: 'unit',
        target_id: finished.unit.id,
        field: 'box',
        new: after,
        base_revision: finished.unit.revision,
      },
      { label: `${finished.mode} ${finished.unit.id.split(':').pop()}` },
    )
    await session.refreshLine()
  }

  function boxOf(unit) {
    if (drag?.unit.id === unit.id) return drag.current
    return unit.box
  }

  function submitInput(event) {
    event.preventDefault()
    if (session.mode === 'reading') session.setReading(session.draft)
    else if (session.mode === 'note') session.addNote(session.draft)
  }
</script>

{#snippet neighbourCrop(neighbour)}
  {#if neighbour.line.box}
    <Crop
      class="neighbour"
      src={session.page?.image_url}
      box={neighbour.line.box}
      page={session.page}
      {scale}
      title={`${neighbour.line.id} · ${neighbour.line.text_raw || neighbour.line.text || ''}`}
    >
      {#each neighbour.units as unit (unit.id)}
        {#if unit.box}
          <div
            class="box ghost"
            style="left:{(unit.box.x - neighbour.line.box.x) * scale}px;top:{(unit.box.y -
              neighbour.line.box.y) *
              scale}px;width:{unit.box.w * scale}px;height:{unit.box.h * scale}px"
          ></div>
        {/if}
      {/each}
    </Crop>
  {/if}
{/snippet}

{#if line && box}
  <div class="line-toolbar">
    <button onclick={() => session.openPage(line.page_id)}>← page</button>
    <strong>{line.id}</strong>
    <span class="muted small">
      seq {line.seq} · {vertical ? 'vertical' : 'horizontal'} · {line.role} · rev {line.revision}
    </span>
    <span style="flex:1"></span>
    <button onclick={() => session.accept()} disabled={!session.targets.length}>accept (a)</button>
    <button onclick={() => session.reject()} disabled={!session.targets.length}>reject (x)</button>
    <button onclick={() => session.splitAt(session.pointer)} disabled={!session.current}>split (s)</button>
    <button onclick={() => session.mergeSelection()} disabled={session.targets.length < 2}>merge (m)</button>
    <button onclick={() => session.beginDrawUnit()}>create unit (c)</button>
    <button onclick={() => session.beginReading()} disabled={!session.current}>reading (r)</button>
    <button onclick={() => session.beginCandidates()} disabled={!session.current}>字母 (j)</button>
    <button onclick={() => session.markGroup()} disabled={session.targets.length < 2}>group (g)</button>
    <button onclick={() => session.beginNote()}>note (n)</button>
    <button onclick={() => session.undo()} disabled={!session.undoStack.length}>undo (z)</button>
    <button class="primary" onclick={() => session.nextLine()}>next line (space)</button>
  </div>

  {#if session.mode === 'reading' || session.mode === 'note'}
    <form class="panel row" onsubmit={submitInput} style="margin-bottom:12px">
      <span class="small muted">{session.mode === 'reading' ? 'reading' : 'note'}</span>
      <input
        bind:this={inputEl}
        type="text"
        bind:value={session.draft}
        placeholder={session.mode === 'reading' ? 'かな…' : 'a note on this line'}
        style="flex:1;min-width:160px"
        onkeydown={(event) => {
          if (event.key === 'Escape') {
            event.stopPropagation()
            session.cancel()
          }
        }}
      />
      <button class="primary" type="submit">save</button>
      <button type="button" onclick={() => session.cancel()}>cancel</button>
      <span class="small muted">
        {session.mode === 'reading' ? 'the reading of the focused unit' : 'recorded as a note event'}
      </span>
    </form>
  {/if}

  {#if session.mode === 'draw-unit'}
    <p class="small" style="margin:0 0 12px">
      drag a box over the character on the line crop; <kbd>escape</kbd> cancels. The unit is created
      with <code>POST /units</code> and takes the next <code>:m</code> id of the line.
    </p>
  {/if}

  <div class="line-grid">
    <div
      class="reader"
      bind:this={reader}
      bind:clientWidth={viewW}
      bind:clientHeight={viewH}
      onscroll={(event) => (scrollTop = event.currentTarget.scrollTop)}
      style="max-height:calc(100vh - 220px);overflow:auto;padding:8px;background:var(--surface-inset);border:1px solid var(--border);border-radius:var(--panel-radius)"
    >
      <!-- The transcriptions of a very long line are windowed with its boxes, and the panel sticks
           to the top of the reader, so that the characters of the visible boxes stay in view. -->
      <div class="reader-inner" style="display:flex;gap:20px;justify-content:center">
        <div class="strip" style:flex-direction={vertical ? 'row' : 'column'}>
          {#each before as neighbour (neighbour.line.id)}
            {@render neighbourCrop(neighbour)}
          {/each}

          <Crop
            bind:element={crop}
            src={session.page?.image_url}
            {box}
            page={session.page}
            {scale}
            draw={session.mode === 'draw-unit'}
            minimum={6}
            oncreate={(drawn) => session.createUnit(drawn)}
            ontrack={(point) => session.setPointer(point)}
            onfail={() => session.markImageFailed()}
            title={`${line.id} · ${line.text_raw || line.text || ''}`}
          >
            {#each shown as unit (unit.id)}
              {#if unit.box}
                <div
                  class={unitClass(unit)}
                  data-unit-id={unit.id}
                  style={unitStyle(unit)}
                  role="button"
                  tabindex="0"
                  title={`${unit.id} · seq ${unit.seq} · ${boxOf(unit).w}×${boxOf(unit).h} · rev ${unit.revision}${
                    unit.reading ? ` · ${unit.reading}` : ''
                  }`}
                  onpointerdown={(event) => hit(event, unit)}
                  onpointerenter={(event) => pick(event, unit)}
                  onpointermove={(event) => pick(event, unit)}
                  onpointerleave={() => (hover = null)}
                  onkeydown={(event) => {
                    if (event.key === 'Enter') session.select(unit.id)
                  }}
                >
                  {#if session.selection.includes(unit.id) && session.mode !== 'draw-unit'}
                    {#each ['nw', 'ne', 'sw', 'se'] as corner (corner)}
                      <button
                        type="button"
                        class="handle"
                        aria-label={`resize ${corner}`}
                        style:left={corner.includes('w') ? '-5px' : 'calc(100% - 4px)'}
                        style:top={corner.includes('n') ? '-5px' : 'calc(100% - 4px)'}
                        style:cursor={corner === 'nw' || corner === 'se' ? 'nwse-resize' : 'nesw-resize'}
                        onpointerdown={(event) => startDrag(event, unit, 'resize', corner)}
                      ></button>
                    {/each}
                  {/if}
                </div>
              {/if}
            {/each}
            {#if cut}
              <div
                class="cut"
                style:left="{cut.left}px"
                style:top="{cut.top}px"
                style:width="{cut.width}px"
                style:height="{cut.height}px"
              ></div>
            {/if}
          </Crop>

          {#each after as neighbour (neighbour.line.id)}
            {@render neighbourCrop(neighbour)}
          {/each}
        </div>

        <div class="transcription" aria-label="transcription">
          {#each columnsOf as column, index (index)}
            <div class="column">
              {#each column as unit (unit.id)}
                <button
                  class="char {session.selection.includes(unit.id) ? 'selected' : ''} {session.focused ===
                  unit.id
                    ? 'focused'
                    : ''} {unit.kind === 'unreadable' ? 'unreadable' : ''} {unit.granularity === 'sequence'
                    ? 'gap'
                    : ''}"
                  title={`${unit.id} · ${unit.reading ?? ''} ${unit.unicode ?? ''} ${unit.jibo ?? ''}`.trim()}
                  onclick={(event) =>
                    session.select(unit.id, { extend: event.shiftKey, toggle: event.metaKey || event.ctrlKey })}
                  onpointerenter={() => (hover = unit.id)}
                  onpointerleave={() => (hover = null)}
                >
                  {character(unit)}
                </button>
              {/each}
            </div>
          {/each}
          {#if !ordered.length}
            <p class="muted small" style="writing-mode:horizontal-tb">no active unit on this line</p>
          {/if}
        </div>
      </div>

      {#if windowed}
        <p class="small muted" style="text-align:center;margin:8px 0 0">
          very long line: boxes {range.start + 1}–{range.end} of {ordered.length} and their characters
          are drawn (virtualised); scroll to see the rest.
        </p>
      {/if}
    </div>

    <div class="inspector stack" style="overflow:auto;max-height:calc(100vh - 220px)">
      <div class="panel">
        <h2>line</h2>
        <p class="small" style="margin:0 0 6px">
          <span class="tagline">{line.text_raw || '—'}</span>
        </p>
        <table class="grid">
          <tbody>
            <tr><th>page</th><td><button class="item" onclick={() => session.openPage(line.page_id)}>{line.page_id}</button></td></tr>
            <tr><th>box</th><td>{line.box ? `${line.box.x}, ${line.box.y}, ${line.box.w}×${line.box.h}` : '—'}</td></tr>
            <tr><th>match</th><td>{line.match_method ?? '—'} {line.match_confidence ?? ''}</td></tr>
            <tr><th>units</th><td>{ordered.length} active · revision {line.revision}</td></tr>
          </tbody>
        </table>
      </div>

      <div class="panel">
        <h2>units ({ordered.length})</h2>
        <table class="grid unit-table">
          <thead>
            <tr><th>#</th><th>reading</th><th>code</th><th>字母</th><th>review</th><th>rev</th></tr>
          </thead>
          <tbody>
            {#each ordered as unit (unit.id)}
              <tr
                class:current={session.focused === unit.id}
                data-unit-id={unit.id}
                onclick={() => session.select(unit.id)}
                onpointerenter={() => (hover = unit.id)}
                onpointerleave={() => (hover = null)}
              >
                <td>{unit.seq}</td>
                <td class="tagline">{unit.reading || unit.text_source || '—'}</td>
                <td class="id">{unit.unicode || '—'}</td>
                <td class="tagline">{unit.jibo || '—'}</td>
                <td>
                  <span class="badge {unit.review === 'reviewed' ? 'ok' : unit.review === 'rejected' ? 'bad' : ''}">
                    {unit.review}
                  </span>
                </td>
                <td>{unit.revision}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>

      <div class="panel small">
        <h2>selection</h2>
        {#if session.targets.length}
          <p style="margin:0 0 6px">
            {session.targets.length} unit{session.targets.length === 1 ? '' : 's'}:
            <code>{session.targets.map((unit) => unit.id.split(':').pop()).join(', ')}</code>
          </p>
          <p class="muted" style="margin:0">
            shift-click or shift-arrow extends the range; <kbd>escape</kbd> clears.
          </p>
        {:else}
          <p class="muted" style="margin:0">click a unit or a character to select it.</p>
        {/if}
        {#if session.pointer}
          <p class="muted" style="margin:6px 0 0">
            pointer: {Math.round(session.pointer.x)}, {Math.round(session.pointer.y)} page px
          </p>
        {/if}
      </div>

      {#if session.warnings.length}
        <div class="panel small">
          <h2>warnings</h2>
          {#each session.warnings as warning, index (index)}
            <p style="margin:0 0 4px">{warning}</p>
          {/each}
          <button onclick={() => session.clearWarnings()}>dismiss</button>
        </div>
      {/if}
    </div>
  </div>
{:else}
  <p class="muted">no line open.</p>
{/if}
