<script>
  // How a crop is read at its place: the run of characters it stands in (諸器, the crop its 器), the
  // reading over each or over all of them, what that rests on, and the ruby as the page writes it. The
  // run is picked on the crop's own line, and one save reads every published crop of it. A word of the
  // dictionary that the characters write can be named with it.
  import { tick, untrack } from 'svelte'
  import { line as readLine } from '../lib/client.js'
  import { RUN_MAX, beside, clearReading, cropLine, opening, pressed, readingChips, setReading, stretch } from '../lib/cropReadings.js'
  import { wordChoices } from '../lib/cropWords.js'
  import { lettersAt, plainKana } from '../lib/hentaigana.js'
  import { t } from '../lib/i18n.svelte.js'
  import { sessionStarted, signedInUser } from '../lib/session.svelte.js'
  import Glyph from './Glyph.svelte'
  import ReadingRuby from './ReadingRuby.svelte'
  import ScriptText from './ScriptText.svelte'

  let { item, disabled = false, working = null, saved = null } = $props()
  const METHODS = ['ruby', 'gloss', 'note', 'parallel', 'context']
  // The line is shown this far either side of the crop, and past the run. It stays still as the run grows.
  const AROUND = 9
  const uid = $props.id()
  let root = $state(null), editing = $state(false), pending = $state(false), error = $state('')
  let line = $state(null), from = $state(0), to = $state(0), touched = $state(false)
  // The published crops beside this one, and the read that brings them, which a save waits for.
  let neighbours = $state([]), arriving = null
  // What is written over each place of the line, and over the run as a whole.
  let readings = $state({}), rubies = $state({}), whole = $state(false), wholeReading = $state(''), wholeRuby = $state('')
  // The 左訓 over the run, and whether its row stands open.
  let left = $state(''), leftOpen = $state(false), leftWas = []
  let letters = $state.raw(null)
  let method = $state('context'), choices = $state([])
  // The dictionary word chosen here; `undefined` leaves the reader's word as it stands.
  let chosen = $state(undefined), probable = $state(false), changed = false

  const chips = $derived(readingChips(item.reading))
  const words = $derived(item.word?.values ?? [])
  // A gallery's row has no reading yet; the slot stands empty, in its place, until the crop's record gives one.
  const known = $derived(Object.hasOwn(item, 'reading'))
  const mine = slot => Boolean(signedInUser()?.id && slot?.by?.includes(signedInUser().id))
  // Only a reader's own claim can be cleared; the others stand until their readers take them back.
  // The word the reader holds alone, which the editor shows chosen until they choose another.
  const held = $derived(mine(item.word) && item.word.status !== 'disputed' ? words[0] : null)
  const picked = $derived(chosen === undefined ? held?.word ?? null : chosen)
  const offered = $derived(held?.word && !choices.some(choice => choice.id === held.word) ? [{ id: held.word, reading: held.reading, class: held.class, spellings: [] }, ...choices] : choices)
  // The reading's slots: the reading itself and the ruby on each side.
  const slots = $derived([item.reading, item.reading?.ruby, item.reading?.left])
  const own = $derived(slots.some(mine) || mine(item.word))
  const busy = $derived(disabled || pending)

  const places = $derived(line ? Array.from({ length: to - from + 1 }, (_, i) => from + i) : [])
  // The published crops of the line the run can be saved on with this one.
  const crops = $derived(new Map(line ? [...beside(line.parts, line.at, neighbours)].filter(([, one]) => one.crop_version) : []))
  // A run ends at the punctuation either side of the crop.
  const within = $derived(line ? stretch(line.parts, line.at) : null)
  const run = $derived(places.map(place => line.parts[place]))
  const joined = $derived(whole && places.length > 1)
  const shown = $derived(line ? { from: Math.max(0, Math.min(line.at - AROUND, from - 2)), to: Math.min(line.parts.length - 1, Math.max(line.at + AROUND, to + 2)) } : null)
  const reading = $derived(joined ? [wholeReading.trim()] : places.map(place => (readings[place] ?? '').trim()))
  const ruby = $derived(joined ? [wholeRuby.trim()] : places.map(place => (rubies[place] ?? '').trim()))
  const reads = $derived(reading.join(''))
  const kana = part => /^[\p{Script=Hiragana}\p{Script=Katakana}\p{M}ー]+$/u.test(part)

  // What an earlier reading, or the kana of a run, already gives each place.
  function fill() {
    for (const place of places) if (readings[place] === undefined && kana(line.parts[place])) readings[place] = plainKana(line.parts[place])
  }
  async function open() {
    // Whose reading it opens with waits until the page knows who reads.
    await sessionStarted()
    error = ''; chosen = undefined; changed = false; probable = held?.form === 'probable'; choices = []; letters = null; touched = false
    const opened = opening(item.reading, signedInUser()?.id)
    method = opened.method ?? untrack(() => method)
    place(cropLine(item), opened)
    editing = true
    tick().then(() => root?.querySelector('.reading-over input')?.focus({ preventScroll: true }))
    neighbours = []
    arriving = item.origin === 'corpus' ? null : readLine(item.id, { reach: RUN_MAX - 1 }).then(({ items }) => {
      if (!editing) return
      neighbours = items
      if (!touched) place(cropLine(item, items), opened)
    }).catch(() => { /* The run is then saved on this crop alone. */ })
  }
  // Stand the crop on its line, with the run of an earlier reading where the line still holds it.
  function place(next, { value, reading: over, ruby: beside, left: onLeft }) {
    const start = value ? next.at - value.at : next.at, kept = value && value.run.every((part, i) => next.parts[start + i] === part)
    line = value && !kept ? { parts: value.run, at: value.at, also: [] } : next
    from = value ? line.at - value.at : line.at; to = from + (value?.run.length ?? 1) - 1
    readings = {}; rubies = {}; whole = false; wholeReading = ''; wholeRuby = ''; letters = null
    left = onLeft.join(''); leftWas = onLeft; leftOpen = Boolean(left)
    if (value) {
      whole = value.run.length > 1 && (over.length || beside.length) === 1
      if (whole) { wholeReading = over.join(''); wholeRuby = beside.join('') }
      else value.run.forEach((_, i) => { readings[from + i] = over[i] ?? ''; if (beside.length === value.run.length) rubies[from + i] = beside[i] })
    }
    fill()
  }
  function press(at) {
    touched = true; error = ''; letters = null
    // Another place of the same character, apart from the run, is where the crop stands instead.
    if (line.also.includes(at) && (at < from - 1 || at > to + 1 || at < within.from || at > within.to)) { line = { ...line, at, also: [...line.also.filter(each => each !== at), line.at] }; from = to = at }
    else ({ from, to } = pressed({ from, to }, line.at, at))
    fill()
  }
  // The arrows move along the line, and leave the list of crops where it is.
  function along(event) {
    const step = { ArrowLeft: -1, ArrowRight: 1 }[event.key]
    if (!step || event.metaKey || event.ctrlKey || event.altKey) return
    event.preventDefault()
    const buttons = [...event.currentTarget.querySelectorAll('button')]
    buttons[buttons.indexOf(document.activeElement) + step]?.focus()
  }
  // The ruby gives the reading its kana until the reader writes another, and says what the reading rests on.
  function wroteRuby(at, text) {
    touched = true; error = ''
    // The 左訓 is often a gloss in other words, and leaves the reading as it is.
    if (at === 'left') { left = text; return }
    const before = at === null ? wholeRuby : rubies[at] ?? '', now = at === null ? wholeReading : readings[at] ?? ''
    if (now === '' || now === plainKana(before)) { if (at === null) wholeReading = plainKana(text); else readings[at] = plainKana(text) }
    if (at === null) wholeRuby = text; else rubies[at] = text
    if (text.trim()) method = 'ruby'
  }
  // The letters the kana before the caret of a ruby may be written with. They stay until a reading is written or
  // the run changes, so the buttons below them never move under a press.
  function seek(at, input) {
    const found = lettersAt(input.value, input.selectionEnd)
    letters = found && { ...found, at, input }
  }
  function write({ letter }) {
    const { at, input, from: start, to: end } = letters, text = input.value.slice(0, start) + letter + input.value.slice(end)
    wroteRuby(at, text)
    tick().then(() => { input.focus(); input.setSelectionRange(start + letter.length, start + letter.length); seek(at, input) })
  }
  function wroteReading(at, text) {
    if (at === null) wholeReading = text; else readings[at] = text
    touched = true; error = ''
  }
  // Read as one, a run keeps what was written over its characters, and back.
  function toggleWhole() {
    touched = true; letters = null
    if (!whole) { wholeReading = reading.join('') || wholeReading; wholeRuby = ruby.join('') || wholeRuby }
    whole = !whole
  }
  // The 左訓 is written over the run as one; one that a save split over its characters, and nobody touched, stays split.
  function leftText() {
    const text = left.trim().normalize('NFC')
    return !text ? null : text === leftWas.join('') && (leftWas.length === 1 || leftWas.length === run.length) ? leftWas : [text]
  }
  function openLeft() { leftOpen = true; tick().then(() => root?.querySelector('.reading-left')?.focus({ preventScroll: true })) }
  function pick(choice) {
    touched = true
    chosen = picked === choice.id ? null : choice.id
    if (chosen && !reads) { whole = places.length > 1; if (whole) wholeReading = choice.reading; else readings[from] = choice.reading }
  }

  $effect(() => {
    if (!editing) return
    const crop = item, q = reads
    let active = true
    const timer = setTimeout(async () => {
      try { const found = await wordChoices(crop, q); if (active) choices = found } catch { /* No word is offered. */ }
    }, 250)
    return () => { active = false; clearTimeout(timer) }
  })

  // The word the reader chose here, or whose certainty they changed. Any other goes with the run as they hold it.
  function wordText() {
    const form = probable ? 'probable' : 'certain'
    if (chosen) return { id: chosen, form, method }
    return chosen === null ? null : held?.word && changed ? { id: held.word, form } : undefined
  }
  async function save() {
    if (busy) return
    if (!reads) { error = t('reading.needed'); root?.querySelector('.reading-over input')?.focus(); return }
    pending = true; working?.(true); error = ''
    try {
      await arriving
      const others = new Map(places.flatMap((place, i) => place !== line.at && crops.has(place) ? [[i, crops.get(place)]] : []))
      // The reading is written in ordinary kana; a hentaigana typed there is the page's, kept in the ruby.
      saved?.(await setReading(item, { run, at: line.at - from, reading: reading.map(plainKana), ruby: ruby.some(Boolean) ? ruby.map(text => text.normalize('NFC')) : null,
        left: leftText(), method, word: wordText(), crops: others }))
      leave()
    } catch (e) { error = e.message || t('client.saveFailed') }
    finally { pending = false; working?.(false) }
  }
  async function clear() {
    if (busy) return
    pending = true; working?.(true); error = ''
    try {
      saved?.(await clearReading(item, { word: mine(item.word) }))
      leave()
    } catch (e) { error = e.message || t('client.saveFailed') }
    finally { pending = false; working?.(false) }
  }
  // The focus goes back to the button that opened the editor.
  const leave = () => { editing = false; error = ''; tick().then(() => root?.querySelector('.reading-set')?.focus({ preventScroll: true })) }
  function keydown(event) {
    if (event.isComposing) return
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); leave() }
    else if (event.key === 'Enter' && event.target.matches('input[type=text]')) { event.preventDefault(); save() }
  }
</script>

<div class="reading-field" class:editing bind:this={root}>
  <div class="reading-slot">
    <span>{t('reading.label')}</span>
    {#each chips as chip}
      <span class="reading-chip">
        {#if chip.reading}<ReadingRuby value={chip.reading} />{:else}<bdi class="reading-bare">{chip.run.run.join('')}</bdi>{/if}
        {#if chip.reading?.methods?.[0] || chip.ruby.length || chip.left.length}<span class="reading-notes">
          {#if chip.reading?.methods?.[0]}<span class="reading-badge" title={t('reading.method')}>{t(`reading.methods.${chip.reading.methods[0]}`)}</span>{/if}
          {#each chip.ruby as text}<bdi class="reading-ruby" title={t('reading.ruby')}>{text}</bdi>{/each}
          {#each chip.left as text}<span class="reading-badge">{t('reading.left')}</span><bdi class="reading-ruby">{text}</bdi>{/each}
        </span>{/if}
      </span>
    {/each}
    {#each words as value}
      <span class="reading-chip"><span class="reading-badge">{t('word.label')}</span><b title={value.class ?? undefined}>{value.word ? value.reading ?? value.word : `「${value.gloss}」`}</b>
        {#if value.form === 'probable'}<span class="reading-badge">{t('word.probable')}</span>{/if}</span>
    {/each}
    {#if slots.some(slot => slot?.status === 'disputed') || item.word?.status === 'disputed'}<span class="reading-badge disputed">{t('reading.disputed')}</span>{/if}
    {#if !editing}<button type="button" class="reading-set" disabled={busy || !known} onclick={open}>{chips.length || words.length ? t('reading.edit') : t('reading.add')}</button>{/if}
  </div>
  {#if editing && line}
    <!-- svelte-ignore a11y_no_static_element_interactions, a11y_no_noninteractive_element_interactions -->
    <div class="reading-editor" onkeydown={keydown}>
      <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
      <div class="reading-line" role="group" aria-label={t('reading.run')} onkeydown={along}>
        {#if shown.from > 0}<span class="reading-more">…</span>{/if}
        {#each line.parts.slice(shown.from, shown.to + 1) as part, i}
          {@const at = shown.from + i}
          {@const inside = at >= from && at <= to}
          {@const reach = at >= within.from && at <= within.to}
          <button type="button" class:inside class:here={at === line.at} class:next={reach && (at === from - 1 || at === to + 1)} class:first={at === from} class:last={at === to}
            aria-pressed={inside} aria-current={at === line.at ? 'true' : undefined} disabled={busy || !(inside || line.also.includes(at) || (reach && places.length < RUN_MAX))}
            title={inside || !reach ? undefined : t('reading.extend', { char: part })} onclick={() => press(at)}>{part}</button>
        {/each}
        {#if shown.to < line.parts.length - 1}<span class="reading-more">…</span>{/if}
      </div>
      <div class="reading-grid" style:--places={places.length}>
        <span class="reading-row" id="{uid}-reading">{t('reading.label')}</span>
        {#if joined}<span class="reading-over whole"><input type="text" lang="ja" value={wholeReading} disabled={busy} aria-label={t('reading.of', { char: run.join('') })} oninput={e => wroteReading(null, e.currentTarget.value)} onfocus={() => letters = null} /></span>
        {:else}{#each places as place (place)}<span class="reading-over"><input type="text" lang="ja" value={readings[place] ?? ''} disabled={busy} aria-label={t('reading.of', { char: line.parts[place] })} oninput={e => wroteReading(place, e.currentTarget.value)} onfocus={() => letters = null} /></span>{/each}{/if}
        <span class="reading-row">{#if places.length > 1}<button type="button" class="reading-whole" role="switch" aria-checked={whole} disabled={busy} onclick={toggleWhole}>{t('reading.whole')}</button>{/if}</span>
        {#each places as place (place)}
          {@const crop = place === line.at ? item : crops.get(place)}
          <span class="reading-char" class:here={place === line.at}>{#if crop?.image}<Glyph item={crop} alt="" class="reading-crop" />{:else}<span class="reading-crop absent"></span>{/if}<ScriptText text={line.parts[place]} titled={false} /></span>
        {/each}
        <span class="reading-row">{t('reading.ruby')}</span>
        {#if joined}<span class="reading-over whole"><input type="text" lang="ja" value={wholeRuby} disabled={busy} aria-label={t('reading.rubyOf', { char: run.join('') })} oninput={e => { wroteRuby(null, e.currentTarget.value); seek(null, e.currentTarget) }}
          onfocus={e => seek(null, e.currentTarget)} onclick={e => seek(null, e.currentTarget)} onkeyup={e => seek(null, e.currentTarget)} /></span>
        {:else}{#each places as place (place)}<span class="reading-over"><input type="text" lang="ja" value={rubies[place] ?? ''} disabled={busy} aria-label={t('reading.rubyOf', { char: line.parts[place] })} oninput={e => { wroteRuby(place, e.currentTarget.value); seek(place, e.currentTarget) }}
          onfocus={e => seek(place, e.currentTarget)} onclick={e => seek(place, e.currentTarget)} onkeyup={e => seek(place, e.currentTarget)} /></span>{/each}{/if}
        {#if leftOpen}
          <span class="reading-row">{t('reading.left')}</span>
          <span class="reading-over whole"><input class="reading-left" type="text" lang="ja" value={left} disabled={busy} aria-label={t('reading.leftOf', { char: run.join('') })} oninput={e => { wroteRuby('left', e.currentTarget.value); seek('left', e.currentTarget) }}
            onfocus={e => seek('left', e.currentTarget)} onclick={e => seek('left', e.currentTarget)} onkeyup={e => seek('left', e.currentTarget)} /></span>
        {:else}<span class="reading-row"><button type="button" class="reading-whole" disabled={busy} onclick={openLeft}>+{t('reading.left')}</button></span>{/if}
      </div>
      {#if letters}
        <div class="reading-letters" role="group" aria-label={t('reading.letters')}>
          {#each letters.letters as one (one.letter)}
            <button type="button" aria-pressed={one.letter === letters.now} title={one.jibo ? t('reading.jibo', { char: one.jibo }) : undefined} disabled={busy}
              onpointerdown={e => e.preventDefault()} onmousedown={e => e.preventDefault()} onclick={() => write(one)}><span>{one.letter}</span>{#if one.jibo}<small>{one.jibo}</small>{/if}</button>
          {/each}
        </div>
      {/if}
      <div class="reading-methods" role="radiogroup" aria-label={t('reading.method')}>
        <span>{t('reading.method')}</span>
        {#each METHODS as value}<label class:chosen={method === value}><input type="radio" name="{uid}-method" {value} bind:group={method} disabled={busy} />{t(`reading.methods.${value}`)}</label>{/each}
      </div>
      {#if offered.length}
        <div class="reading-words">
          <span>{t('word.label')}</span>
          {#each offered as choice (choice.id)}
            <button type="button" aria-pressed={picked === choice.id} disabled={busy} onclick={() => pick(choice)}><b>{choice.reading}</b> <span>{choice.class}</span> <span>{choice.spellings.join('・')}</span></button>
          {/each}
          {#if picked}<button type="button" role="switch" aria-checked={probable} disabled={busy} onclick={() => { probable = !probable; changed = true }}>{t('word.probable')}</button>{/if}
        </div>
      {/if}
      <div class="reading-actions">
        {#if own}<button type="button" class="reading-clear" disabled={busy} onclick={clear}>{t('reading.clear')}</button>{/if}
        {#if error}<span class="reading-error" role="alert">{error}</span>{/if}
        <button type="button" disabled={pending} onclick={leave}>{t('common.cancel')}</button>
        <button type="button" class="reading-save" disabled={busy} onclick={save}>{pending ? t('common.saving') : t('reading.save')}</button>
      </div>
    </div>
  {:else if error}<span class="reading-error" role="alert">{error}</span>{/if}
</div>

<style>
  .reading-field{font-size:12px;color:var(--muted);display:grid;gap:8px;min-width:0}
  .reading-field.editing{flex:1 1 100%}
  .reading-slot,.reading-methods,.reading-words,.reading-actions{display:flex;flex-wrap:wrap;gap:6px 8px;align-items:center}
  .reading-slot{min-height:32px}
  b{color:var(--ink);font-weight:600}
  button{font:inherit;color:var(--ink);background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:4px 9px;cursor:pointer}
  .reading-slot>button{min-height:32px}
  @media(max-width:700px){.reading-slot,.reading-slot>button{min-height:36px}}
  button:hover:not(:disabled){border-color:var(--line-strong)}
  button:disabled{opacity:.5;cursor:default}
  button:focus-visible,input:focus-visible{outline:3px solid var(--focus-ring);outline-offset:1px}
  .reading-chip{display:inline-flex;flex-wrap:wrap;gap:4px 10px;align-items:center;border:1px solid var(--line);border-radius:8px;padding:5px 10px;background:var(--surface);color:var(--ink)}
  .reading-chip :global(.reading-run),.reading-bare{font-family:var(--char-font);font-size:22px;line-height:1.25}
  .reading-notes{display:inline-flex;flex-wrap:wrap;gap:4px 8px;align-items:center}
  .reading-badge{font-size:11px;color:var(--muted)}
  .reading-badge.disputed{color:var(--wrong)}
  .reading-ruby{font-family:var(--char-font);font-size:15px}
  .reading-clear{margin-inline-end:auto}
  .reading-editor{display:grid;gap:14px;border:1px solid var(--line);border-radius:8px;padding:14px;background:var(--surface);min-width:0}
  .reading-line{display:flex;flex-wrap:wrap;row-gap:4px;font-family:var(--char-font);font-size:19px;line-height:1}
  .reading-line button{border:1px solid transparent;border-radius:0;background:none;color:var(--muted);padding:6px 4px;min-width:30px;min-height:34px;line-height:1}
  .reading-line button:disabled{opacity:.6}
  .reading-line button.next:not(:disabled){border-style:dashed;border-color:var(--line-strong);border-radius:6px;color:var(--ink)}
  .reading-line button:hover:not(:disabled){background:var(--accent-light);color:var(--ink);border-radius:6px}
  .reading-line button.inside{background:var(--accent-light);color:var(--ink);border-color:var(--accent);border-inline-width:0;border-radius:0}
  .reading-line button.inside.first{border-inline-start-width:1px;border-start-start-radius:6px;border-end-start-radius:6px}
  .reading-line button.inside.last{border-inline-end-width:1px;border-start-end-radius:6px;border-end-end-radius:6px}
  .reading-line button.inside.here{color:var(--accent);font-weight:600}
  .reading-more{align-self:center;padding:0 4px}
  .reading-grid{display:grid;grid-template-columns:auto repeat(var(--places),minmax(44px,88px));gap:6px 8px;align-items:center;justify-content:start;overflow-x:auto;padding:3px}
  .reading-row{white-space:nowrap;padding-inline-end:4px}
  .reading-over.whole{grid-column:span var(--places)}
  .reading-over input{width:100%;min-width:0;font:inherit;font-family:var(--char-font);font-size:15px;text-align:center;color:var(--ink);background:var(--paper);border:1px solid var(--line);border-radius:6px;padding:6px 4px}
  .reading-over input:hover{border-color:var(--line-strong)}
  .reading-char{display:grid;justify-items:center;gap:2px;font-size:24px;line-height:1.1;border:1px solid transparent;border-radius:6px;padding:4px}
  .reading-char.here{border-color:var(--accent);background:var(--accent-light)}
  .reading-char :global(.reading-crop){width:40px;height:40px;border-radius:4px}
  .reading-crop.absent{border:1px dashed var(--line)}
  .reading-letters{display:flex;flex-wrap:wrap;gap:4px;font-family:var(--char-font)}
  .reading-letters button{display:grid;justify-items:center;gap:1px;min-width:38px;padding:4px 6px;font-size:20px;line-height:1.1}
  .reading-letters small{font-size:11px;color:var(--muted)}
  .reading-whole{padding:3px 8px}
  [role=switch][aria-checked=true],.reading-words button[aria-pressed=true],.reading-letters button[aria-pressed=true]{border-color:var(--accent);background:var(--accent-light);color:var(--accent)}
  .reading-methods label{position:relative;border:1px solid var(--line);border-radius:999px;padding:4px 11px;color:var(--ink);cursor:pointer;background:var(--surface)}
  .reading-methods label:hover{border-color:var(--line-strong)}
  .reading-methods label.chosen{border-color:var(--accent);background:var(--accent-light);color:var(--accent)}
  .reading-methods label:has(:focus-visible){outline:3px solid var(--focus-ring);outline-offset:1px}
  .reading-methods input{position:absolute;inset:0;opacity:0;margin:0;cursor:pointer}
  .reading-words button{display:flex;flex-wrap:wrap;gap:6px}
  .reading-words button span{color:var(--muted)}
  .reading-actions{justify-content:flex-end}
  .reading-save{background:var(--accent-solid);border-color:var(--accent-solid);color:#fff;padding:6px 16px}
  .reading-error{color:var(--wrong)}
</style>
