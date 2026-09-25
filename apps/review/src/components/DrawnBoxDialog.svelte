<script>
  import { onMount } from 'svelte'
  import CharacterSearch from './CharacterSearch.svelte'
  import ReferenceGlyph from './ReferenceGlyph.svelte'
  import { keepBox, nameCharacter, retireBox } from '../lib/pages.js'
  import { t } from '../lib/i18n.svelte.js'
  // One box on a page photo: name the character it holds, leave it unidentified, or retire a box
  // drawn by mistake or proposed where there is no mark. A proposal can also be kept unnamed, which
  // records that a person saw a mark there; closing leaves it proposed. `changed` is called after a write so the
  // page reloads its boxes.
  let { unit, page, clientId, close, changed } = $props()
  let dialog, query = $state(''), chosen = $state(null), busy = $state(false), error = $state('')
  let submission = null
  const crop = $derived(`/atlas/characters/${encodeURIComponent(unit.id)}/image?revision=${unit.revision}&image_sha256=${page.image_sha256}`)

  function select(item) { chosen = item; error = '' }

  async function save() {
    if (busy || !chosen) return
    busy = true; error = ''
    const payload = { client_id: clientId, revision: unit.revision, image_sha256: page.image_sha256,
                      character: chosen.code_point, verdict: 'wrong', issue: 'character' }
    // The same answer retried keeps its submission id, so a lost response is not saved twice.
    const signature = JSON.stringify(payload)
    if (!submission || submission.signature !== signature) submission = { signature, id: crypto.randomUUID() }
    try {
      await nameCharacter(unit.id, { id: submission.id, ...payload })
      changed(t('pages.box.named'))
      close()
    } catch (e) { error = e.message }
    finally { busy = false }
  }

  async function keep() {
    if (busy) return
    busy = true; error = ''
    try {
      await keepBox(unit, clientId)
      changed(t('pages.box.kept'))
      close()
    } catch (e) { error = e.message }
    finally { busy = false }
  }

  async function retire() {
    if (busy) return
    busy = true; error = ''
    try {
      await retireBox(unit, clientId)
      changed(t('pages.box.removed'))
      close()
    } catch (e) { error = e.message }
    finally { busy = false }
  }

  // `showModal` focuses the first control, the close button; the search is where the work starts.
  onMount(() => { dialog.showModal(); dialog.querySelector('.character-search input')?.focus() })
</script>

<dialog class="drawn-box-dialog" bind:this={dialog} oncancel={e => { e.preventDefault(); close() }}
        onclick={e => { if (e.target === dialog) close() }} aria-labelledby="drawn-box-title">
  <header>
    <span class="overline" id="drawn-box-title">{unit.proposed ? t('pages.box.proposedTitle') : unit.manual ? t('pages.box.drawnTitle') : t('pages.box.title')}</span>
    <button class="icon-button" aria-label={t('pages.box.close')} onclick={close}>×</button>
  </header>
  <div class="box-summary">
    <figure class="box-crop"><img src={crop} alt={t('pages.box.cropAlt')} /></figure>
    <div>
      {#if unit.character}
        <span class="box-current"><ReferenceGlyph char={unit.character} code_point={unit.code_point ?? ''} size="lg" showCodePoint /></span>
      {:else}
        <span class="box-unidentified">{t('pages.box.unidentified')}</span>
      {/if}
      <p class="record-id"><code>{unit.id}</code></p>
    </div>
  </div>
  {#if unit.proposed}<p class="box-note">{t('pages.box.proposedNote')}</p>{/if}
  <p class="box-prompt">{t('pages.box.prompt')}</p>
  <CharacterSearch bind:value={query} label={t('pages.box.searchLabel')} onselect={select} />
  {#if chosen}
    <p class="box-chosen" role="status">{t('pages.box.chosen')} <ReferenceGlyph char={chosen.char} code_point={chosen.code_point} script={chosen.script} size="sm" /> <code>{chosen.code_point}</code></p>
  {/if}
  {#if error}<div class="error-message" role="alert">{error}</div>{/if}
  <footer>
    {#if unit.manual || unit.detected}<button class="negative" disabled={busy} onclick={retire}>{t('pages.box.remove')}</button>{/if}
    <span class="spacer"></span>
    {#if unit.proposed}
      <button disabled={busy} onclick={close}>{t('pages.box.close')}</button>
      <button disabled={busy} onclick={keep}>{t('pages.box.keepMark')}</button>
    {:else}
      <button disabled={busy} onclick={close}>{unit.character ? t('pages.box.keep') : t('pages.box.leaveUnidentified')}</button>
    {/if}
    <button class="primary" disabled={busy || !chosen} onclick={save}>{busy ? t('common.saving') : t('pages.box.save')}</button>
  </footer>
</dialog>

<style>
  .drawn-box-dialog{width:min(520px,calc(100vw - 32px));border:1px solid var(--line);border-radius:12px;padding:22px 24px;background:#fff;color:var(--ink);box-shadow:0 20px 60px #0002;overflow:visible}
  .drawn-box-dialog::backdrop{background:#16151f40}
  header{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px}
  header .icon-button{font-size:26px;color:var(--muted)}
  .box-summary{display:flex;gap:18px;align-items:center;margin-bottom:18px}
  .box-crop{margin:0;width:96px;height:96px;flex-shrink:0;display:flex;align-items:center;justify-content:center;background:#f4f4f5;border:1px solid var(--line);border-radius:8px;overflow:hidden}
  .box-crop img{width:100%;height:100%;object-fit:contain}
  .box-current{font-size:34px}
  .box-unidentified{font-size:15px;color:var(--muted)}
  .record-id{margin-top:6px;font-size:11px;color:var(--muted);word-break:break-all}
  .box-note{font-size:12px;color:var(--muted);margin-bottom:12px}
  .box-prompt{font-size:13px;margin-bottom:10px}
  .box-chosen{display:flex;align-items:center;gap:8px;margin-top:14px;font-size:13px}
  .box-chosen code{font-size:11px;color:var(--muted)}
  .error-message{margin:14px 0 0}
  footer{display:flex;align-items:center;gap:10px;margin-top:22px;flex-wrap:wrap}
  footer .spacer{flex:1}
  footer button{font-size:13px;padding:11px 16px}
</style>
