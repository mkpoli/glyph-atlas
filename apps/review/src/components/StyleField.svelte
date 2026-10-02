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
  let { item, editable = true, disabled = false, working = null, saved } = $props()
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
        { id: crypto.randomUUID(), revision: item.revision, style })
      saved?.(result)
    } catch (e) {
      error = e.message
      menu.value = own
    } finally { working?.(false) }
  }
</script>

{#if item?.style}
  <div class="style-field">
    <!-- A style nobody assessed says nothing, so only the menu to set one is shown. -->
    {#if item.style !== 'unassessed'}<span class="style-value">{t('style.label')}: <b>{t(`style.kind.${item.style}`)}</b>
      {#if item.style_basis !== 'none'}<span class="style-basis">{t(`style.basis.${basis}`)}</span>{/if}</span>{/if}
    {#if item.style_editable && editable}
      <label class="style-own">{item.style === 'unassessed' ? t('style.label') : t('style.own')}
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
  .style-own select{appearance:none;font:inherit;font-size:12px;color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:4px 24px 4px 9px;cursor:pointer;
    background:var(--surface) url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 6'%3E%3Cpath d='M1 1l4 4 4-4' fill='none' stroke='%238a8a93' stroke-width='1.4'/%3E%3C/svg%3E") no-repeat right 8px center/9px 6px}
  .style-own select:hover:not(:disabled){border-color:var(--line-strong)}
  .style-own select:focus-visible{outline:3px solid var(--focus-ring);outline-offset:2px}
  .style-error{color:var(--wrong)}
</style>
