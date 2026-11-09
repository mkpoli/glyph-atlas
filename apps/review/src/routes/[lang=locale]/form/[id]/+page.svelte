<script>
  import Seo from '$components/Seo.svelte'
  import SiteLinks from '$components/SiteLinks.svelte'
  import FormText from '$components/FormText.svelte'
  import Glyph from '$components/Glyph.svelte'
  import { cropAddress } from '$lib/inspector.svelte.js'
  import { t } from '$lib/i18n.svelte.js'
  let { data } = $props()
  const form = $derived(data.form)
</script>

<Seo title={`${form.form} · ${t('nav.forms')}`} description={t('forms.glyphs.count', { count: form.count })} />

<section class="explore form-page">
  <div class="page-status">
    <h1 class="visually-hidden">{t('chips.forms')}</h1>
    <SiteLinks />
    <div class="collection-meta"><span class="live-dot"></span><span>{t('forms.glyphs.count', { count: form.count })}</span></div>
  </div>
  <header class="form-head">
    <span class="form-mark"><FormText text={form.form} /></span>
  </header>
  {#if form.items.length}
    <ol class="form-crops">
      {#each form.items as item (item.id)}
        <li>
          <a href={cropAddress(item.id, item.origin)} aria-label={t('chips.showCharacter', { char: item.label })}>
            <Glyph {item} alt="" />
            <span class="crop-label">{item.label}</span>
          </a>
        </li>
      {/each}
    </ol>
  {:else}
    <p class="empty">{t('explore.empty.noCharacters')}</p>
  {/if}
</section>

<style>
  .form-page{padding-bottom:48px}
  .form-head{display:flex;align-items:center;gap:14px;border-top:1px solid var(--line);padding:18px 0 22px}
  .form-mark{font-size:48px;line-height:1;color:var(--accent)}
  .form-mark :global(.ids-drawing){width:1em;height:1em}
  .form-crops{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(104px,1fr));gap:8px}
  .form-crops a{display:flex;flex-direction:column;align-items:center;gap:5px;min-width:0;padding:6px;border:1px solid var(--line);border-radius:8px;background:var(--surface);color:inherit;text-decoration:none}
  .form-crops a:hover,.form-crops a:focus-visible{border-color:var(--accent)}
  .form-crops :global(.crop-paint){width:100%;aspect-ratio:1}
  .crop-label{max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:11px;color:var(--muted)}
  .empty{color:var(--muted);padding:24px 0;font-size:14px}
</style>
