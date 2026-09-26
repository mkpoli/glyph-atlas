<script>
  import { request } from '../lib/client.js'
  import { t } from '../lib/i18n.svelte.js'
  // The values of `data/vocab/style.yaml`, in the order a reviewer reaches for them.
  const STYLES = ['regular', 'running', 'cursive', 'clerical', 'seal', 'ming', 'gothic', 'mixed']
  // `item` is the crop as the server reports it; a server that reports no style (the hosted site)
  // gets nothing here. `saved` receives the crop as it is after an edit. `working` tells the dialog
  // a save is under way, so its own save waits: both carry the crop's revision. In a review round
  // (`editable` false) the style is only shown, since a change would move the revision the round
  // was dealt at and the round's save would be refused.
  let { item, clientId, editable = true, disabled = false, working = null, saved } = $props()
  let error = $state('')
  const own = $derived(item?.style_basis === 'unit' ? item.style : 'unassessed')
  // The server names the basis `document-confirmed`; a catalogue key has no hyphen.
  const basis = $derived((item?.style_basis ?? 'none').replaceAll('-', '_'))

  async function choose(event) {
    // The event's target is gone once the request is awaited, so the menu is kept here.
    const menu = event.currentTarget, style = menu.value
    working?.(true); error = ''
    try {
      const result = await request(`/atlas/characters/${encodeURIComponent(item.id)}/style`,
        { id: crypto.randomUUID(), client_id: clientId, revision: item.revision, style })
      saved?.(result)
    } catch (e) {
      error = e.message
      menu.value = own
    } finally { working?.(false) }
  }
</script>

{#if item?.style}
  <div class="style-field">
    <span class="style-value">{t('style.label')}: <b>{t(`style.kind.${item.style}`)}</b>
      {#if item.style_basis !== 'none'}<span class="style-basis">{t(`style.basis.${basis}`)}</span>{/if}</span>
    {#if item.style_editable && editable}
      <label class="style-own">{t('style.own')}
        <select value={own} {disabled} onchange={choose}>
          <option value="unassessed">{t('style.inherit')}</option>
          {#each STYLES as value}<option {value}>{t(`style.kind.${value}`)}</option>{/each}
        </select>
      </label>
    {/if}
    {#if error}<span class="style-error" role="alert">{error}</span>{/if}
  </div>
{/if}

<style>
  .style-field{display:flex;flex-wrap:wrap;gap:4px 12px;align-items:center;font-size:12px;color:var(--muted)}
  .style-value b{color:var(--ink);font-weight:600}
  .style-basis{margin-left:6px}
  .style-own{display:inline-flex;gap:6px;align-items:center}
  .style-own select{font:inherit}
  .style-error{color:var(--wrong)}
</style>
