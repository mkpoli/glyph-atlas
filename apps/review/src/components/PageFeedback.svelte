<script>
  import { onMount } from 'svelte'
  import api from '../lib/api.js'
  import SourceUpdate from './SourceUpdate.svelte'
  let { page, session } = $props()
  let data = $state(null), error = $state(''), saving = $state(false), saved = $state('')
  let line = $state(1), original = $state(''), corrected = $state(''), note = $state('')
  let target = $state('text'), rubyBase = $state(''), gloss = $state(''), entryForm = $state('')
  let pageNote = $state(''), ready = $state(false)
  let draftHash = $state(null)
  let editingId = $state(null)
  const draftKey = $derived(`atlas.page-feedback.${encodeURIComponent(page.id)}`)
  let closed = false
  const lines = $derived(data?.lines ?? [])
  const selectedLine = $derived(lines.find(item => item.number === Number(line)))
  async function reload() {
    try { const answer = await api.corrections(page.id); if (!closed) data = answer }
    catch (e) { if (!closed) error = e.message }
  }
  onMount(() => {
    try {
      const draft = JSON.parse(localStorage.getItem(draftKey) || 'null')
      if (draft) {
        ({ line, original, corrected, note, target, rubyBase, gloss, entryForm, pageNote } = draft)
        draftHash = draft.textSha256 ?? null
        editingId = draft.editingId ?? null
      }
    } catch { /* A new draft starts empty. */ }
    ready = true; reload()
    return () => { closed = true }
  })
  $effect(() => {
    if (!ready || !data) return
    if (!draftHash && (original || corrected || note)) draftHash = data.text_sha256
    const draft = { line, original, corrected, note, target, rubyBase, gloss, entryForm, pageNote, textSha256: draftHash, editingId }
    try { localStorage.setItem(draftKey, JSON.stringify(draft)) } catch { /* Server saves remain available. */ }
  })
  async function save() {
    saving = true; error = ''; saved = ''
    try {
      const body = { target_id: page.id, id: editingId ?? `atlas-${crypto.randomUUID()}`, line: Number(line), original, corrected, note, base_revision: data.revision, source_text_sha256: draftHash ?? data.text_sha256 }
      if (['rb', 'rt', 'left'].includes(target)) { body.ruby_field = target; if (rubyBase) body.ruby_base = rubyBase }
      if (target === 'entry' || target === 'gloss') body.entry = { gloss, ...(entryForm && target === 'entry' ? { form: entryForm } : {}), field: target === 'gloss' ? 'gloss' : 'form' }
      await api.saveCorrection(body, session.clientId)
      original = ''; corrected = ''; note = ''; draftHash = null; editingId = null; await reload(); saved = 'Correction saved in this workspace.'
    } catch (e) {
      error = e.status === 409 ? 'This page changed since you opened it. Your draft is preserved. Check the updated corrections, then save again.' : e.message
      await reload()
    } finally { saving = false }
  }
  async function undo(id) {
    saving = true; error = ''; saved = ''
    try {
      await api.retractCorrection(id, { page_id: page.id, base_revision: data.revision, reason: 'Withdrawn in the page reader' }, session.clientId)
      await reload(); saved = 'Correction withdrawn. The review history retains it.'
    } catch (e) { error = e.status === 409 ? 'This page changed. Review the updated corrections before withdrawing one.' : e.message; await reload() }
    finally { saving = false }
  }
  async function saveNote() {
    saving = true; error = ''; saved = ''
    try {
      await api.reviews({ target_type: 'page', target_id: page.id, field: 'note', new: pageNote, client_id: session.clientId, base_revision: data.revision, idempotency_key: crypto.randomUUID() })
      pageNote = ''; await reload(); saved = 'Page note saved.'
    } catch (e) { error = e.message; await reload() }
    finally { saving = false }
  }
  function revise(item) {
    editingId = item.id; line = item.line; original = item.original; corrected = item.corrected; note = item.note
    target = item.rubyField ?? (item.entry ? item.entry.field === 'gloss' ? 'gloss' : 'entry' : 'text')
    rubyBase = item.rubyBase ?? ''; gloss = item.entry?.gloss ?? ''; entryForm = item.entry?.form ?? ''
    draftHash = data.text_sha256; saved = ''; error = ''
    document.querySelector('.correction-form-section')?.scrollIntoView({ block: 'center' })
  }
</script>

<div class="feedback-pane">
  {#if error}<p class="notice error" role="alert">{error}</p>{/if}
  {#if saved}<p class="notice success" role="status">{saved}</p>{/if}
  {#if data}
    <section class="transcription-section"><div class="section-heading compact"><h2>Transcription</h2><span class="small muted">{lines.length} lines</span></div>
      {#if data.base}<div class="transcription-lines">{#each lines as item (item.number)}<button class:chosen={Number(line) === item.number} onclick={() => { line = item.number; saved = '' }} aria-label={`Select transcription line ${item.number}`}><span>{item.number}</span><span>{item.raw}</span></button>{/each}</div><p class="small muted">Select a line to propose a correction. Line numbers follow the source transcription.</p>
      {:else}<div class="empty-transcription"><h3>This page needs transcription</h3><p>Read the scan and leave a note about the missing text or anything that needs attention.</p></div>{/if}
    </section>
    {#if data.base}<section class="correction-form-section"><h2>{editingId ? 'Review this correction' : 'Propose a correction'}</h2><form onsubmit={(e) => { e.preventDefault(); save() }}>
      {#if draftHash && draftHash !== data.text_sha256}<div class="notice">The source text changed since this draft began. Review the current transcription before saving.<button type="button" onclick={() => draftHash = data.text_sha256}>Use current transcription</button></div>{/if}
      <div class="form-pair"><label>Transcription line<select bind:value={line}>{#each lines as item}<option value={item.number}>{item.number} · {item.raw.slice(0, 42)}</option>{/each}</select></label><label>Target<select bind:value={target}><option value="text">Text</option><option value="rb">Ruby base</option><option value="rt">Ruby reading</option><option value="left">Left ruby</option><option value="entry">Wordlist form</option><option value="gloss">Wordlist heading</option></select></label></div>
      {#if selectedLine}<blockquote class="selected-transcription">{selectedLine.raw}</blockquote>{/if}
      <div class="form-pair"><label>As transcribed<input aria-label="As transcribed" required bind:value={original} placeholder="Exact text in this line" /></label><label>Proposed reading<input aria-label="Proposed reading" bind:value={corrected} placeholder="Reading from the scan" /></label></div>
      {#if ['rb', 'rt', 'left'].includes(target)}<label>Complete ruby base<input bind:value={rubyBase} placeholder="Unchanged base in the source" /></label>{/if}
      {#if target === 'entry' || target === 'gloss'}<div class="form-pair"><label>Source headword<input required bind:value={gloss} /></label>{#if target === 'entry'}<label>Complete source form<input bind:value={entryForm} /></label>{/if}</div>{/if}
      <label>Reason / evidence<textarea required rows="3" bind:value={note} placeholder="Describe the reading and where it is visible on the scan."></textarea></label>
      <div class="row"><button class="primary" disabled={saving || !original || original === corrected || !note.trim() || (draftHash && draftHash !== data.text_sha256)}>{saving ? 'Saving…' : 'Save correction'}</button><span class="small muted">Draft kept in this browser</span></div>
    </form></section>{/if}
    <section class="saved-corrections"><div class="section-heading compact"><h2>Saved corrections</h2><span class="small muted">{data.items.length}</span></div>
      {#if !data.items.length}<p class="small muted">No corrections saved for this page yet.</p>{/if}
      {#each data.items as item (item.id)}<article class="correction-record"><div class="row"><strong>Line {item.line}</strong><span class="badge" class:bad={item.status === 'conflicted'}>{item.status === 'conflicted' ? 'Needs attention' : 'Saved proposal'}</span><button class="revise" disabled={saving} onclick={() => revise(item)}>Review again</button><button class="withdraw" disabled={saving} onclick={() => undo(item.id)}>Undo correction</button></div><p class="reading-change"><del>{item.original}</del><span aria-hidden="true"> → </span><ins>{item.corrected || '(remove)'}</ins></p><p>{item.note}</p>{#if item.reason}<p class="notice error">{item.reason}</p>{/if}<small class="muted">{item.actor || 'Reviewer not recorded'}</small></article>{/each}
    </section>
    {#key data.revision}<SourceUpdate pageId={page.id} enabled={data.items.length > 0} />{/key}
    <section class="page-notes"><h2>Page notes</h2><form onsubmit={(e) => { e.preventDefault(); saveNote() }}><label>Feedback<textarea rows="3" bind:value={pageNote} placeholder="Missing text, uncertain reading, image quality, or a question for another reviewer."></textarea></label><button disabled={saving || !pageNote.trim()}>Save page note</button></form>
      {#each data.notes ?? [] as entry (entry.id)}<article class="page-note"><p>{entry.text}</p><small class="muted">{entry.actor || 'Reviewer not recorded'}{entry.at ? ` · ${new Date(entry.at).toLocaleDateString()}` : ''}</small></article>{/each}
    </section>
  {:else if !error}<p class="empty-state" role="status">Loading transcription and feedback…</p>{/if}
</div>
