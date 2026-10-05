<script>
  // One occurrence of a run, opened from its card: the run on its page with the page around it (the
  // whole context render it was cut from), its characters, each opening its crop in the inspector, and
  // where it comes from, with a link to the source: the dataset's own viewer at the place for a corpus
  // glyph, the transcription page on みんなで翻刻 for a crop transcribed there.
  import { onMount } from 'svelte'
  import RunImage from './RunImage.svelte'
  import ScriptText from './ScriptText.svelte'
  import SourceCredit from './SourceCredit.svelte'
  import ReferenceGlyph from './ReferenceGlyph.svelte'
  import { tileDate } from '../lib/dating.js'
  import { sourceTitle } from '../lib/seo.js'
  import { t } from '../lib/i18n.svelte.js'

  let { occurrence, text, inspect, close } = $props()
  let dialog
  const lead = $derived(occurrence.crops[0])
  const corpus = $derived(lead.origin === 'corpus')
  const web = url => /^https?:\/\//i.test(url ?? '') ? url : null
  const source = $derived(corpus ? (web(lead.record_url) && { href: lead.record_url, label: t('corpus.sourceRecord') })
    : (web(lead.honkoku_url) && { href: lead.honkoku_url, label: t('character.sourceHonkoku') }))
  const where = $derived([sourceTitle(lead), lead.page_number ? t('tile.page', { page: lead.page_number }) : null, tileDate(lead) || null].filter(Boolean).join(' · '))
  const open = crop => inspect(crop.id, crop.origin === 'corpus' ? 'corpus' : 'collection')
  onMount(() => dialog.showModal())
</script>

<dialog class="run-occurrence-dialog" bind:this={dialog} oncancel={e => { e.preventDefault(); close() }}
        onclick={e => { if (e.target === dialog) close() }} aria-labelledby="run-occurrence-title">
  <header>
    <h2 id="run-occurrence-title"><ScriptText {text} /></h2>
    <button class="icon-button" aria-label={t('run.detail.close')} onclick={close}>×</button>
  </header>
  <figure class="occurrence-page"><RunImage crops={occurrence.crops} page={occurrence.page} vertical={occurrence.vertical} context oninspect={id => open(occurrence.crops.find(c => c.id === id))} /></figure>
  <ul class="occurrence-crops">
    {#each occurrence.crops as crop (crop.id)}
      <li><button onclick={() => open(crop)} aria-label={t('explore.tile.inspect', { label: crop.label })}>
        {#if crop.image}<img src={crop.image} alt="" />{/if}<ReferenceGlyph char={crop.label} size="sm" />
      </button></li>
    {/each}
  </ul>
  {#if where}<p class="occurrence-where">{where}</p>{/if}
  <SourceCredit item={lead} {corpus} record={false} />
  {#if source}<a class="primary occurrence-source" href={source.href} target="_blank" rel="noreferrer">{source.label}</a>{/if}
</dialog>

<style>
  .run-occurrence-dialog{width:min(640px,calc(100vw - 32px));max-height:calc(100vh - 32px);border:1px solid var(--line);border-radius:12px;padding:20px 22px;background:var(--surface);color:var(--ink);box-shadow:0 20px 60px var(--shadow)}
  .run-occurrence-dialog::backdrop{background:light-dark(#16151f40, rgb(0 0 0 / 50%))}
  header{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px}
  header h2{margin:0;font-size:24px;font-weight:500}
  header .icon-button{font-size:26px;color:var(--muted)}
  .occurrence-page{margin:0 0 14px;height:min(56vh,480px);padding:10px;background:var(--surface-tile);border:1px solid var(--line);border-radius:8px}
  .occurrence-crops{display:flex;flex-wrap:wrap;gap:8px;list-style:none;margin:0 0 12px;padding:0}
  .occurrence-crops button{display:flex;align-items:center;gap:8px;border:1px solid var(--line);border-radius:8px;background:transparent;padding:4px 10px 4px 4px;color:inherit}
  .occurrence-crops button:hover,.occurrence-crops button:focus-visible{background:var(--surface-selected)}
  .occurrence-crops img{width:40px;height:40px;object-fit:contain;background:var(--surface-tile);border-radius:6px}
  .occurrence-where{margin:0 0 4px;font-size:13px}
  .occurrence-source{display:inline-block;margin-top:14px;font-size:13px;padding:10px 16px;text-decoration:none}
</style>
