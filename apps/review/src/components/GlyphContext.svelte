<script>
  import CropContext from './CropContext.svelte'
  import { corpusCharacter } from '../lib/client.js'
  import { glyphContext } from '../lib/glyphContext.svelte.js'
  import { t } from '../lib/i18n.svelte.js'

  // Records already asked for, so passing the pointer back over a glyph shows it without a request.
  const records = new Map()
  const record = id => {
    if (!records.has(id)) records.set(id, corpusCharacter(id).catch(e => { records.delete(id); throw e }))
    return records.get(id)
  }
  const id = $derived(glyphContext.hovered ?? glyphContext.pinned)
  const pinned = $derived(id && id === glyphContext.pinned && !glyphContext.hovered)
  let data = $state(null), failed = $state(false)
  $effect(() => {
    const target = id
    data = null; failed = false
    if (!target) return
    let current = true
    record(target).then(result => { if (current) data = result }, () => { if (current) failed = true })
    return () => { current = false }
  })
</script>

{#if id}
  <aside class="glyph-context" class:pinned class:left={glyphContext.side === 'left'} class:top={glyphContext.side === 'top'} aria-label={t('forms.context.label')}>
    <header>
      {#if data}<span class="context-source"><b lang="ja">{data.source_label}</b> {data.source?.title ?? ''}</span>{:else}<span class="shimmer line"></span>{/if}
      {#if pinned}<button class="icon-button" aria-label={t('forms.context.close')} onclick={() => glyphContext.pinned = null}>×</button>{/if}
    </header>
    <div class="context-figure">
      {#if data?.image && data.proxyable}
        {#key data.id + ':' + data.revision}<CropContext item={data} detail={data} corpus disabled={!pinned} />{/key}
      {:else if data || failed}<span class="context-missing">{t('character.image.unavailable')}</span>
      {:else}<span class="shimmer"></span>{/if}
    </div>
    <code class="context-id">{id}</code>
  </aside>
{/if}

<style>
  .glyph-context{position:fixed;right:16px;bottom:16px;z-index:20;width:min(320px,calc(100vw - 32px));display:flex;flex-direction:column;gap:8px;padding:10px;background:#fff;border:1px solid var(--line);border-radius:12px;box-shadow:0 8px 28px rgb(0 0 0 / 16%);pointer-events:none}
  .glyph-context.left{right:auto;left:16px}
  .glyph-context.pinned{pointer-events:auto}
  .glyph-context:not(.pinned) :global(.crop-tools){display:none}
  header{display:flex;align-items:center;gap:8px;min-height:24px}
  .context-source{flex:1;min-width:0;font-size:11px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .context-source b{font-size:17px;color:var(--ink);font-weight:500;margin-right:4px}
  header .icon-button{width:24px;height:24px;padding:0;font-size:16px;line-height:1}
  .line{display:block;width:140px;height:12px;border-radius:4px}
  .context-figure{height:260px;display:flex}
  .context-figure>span.shimmer{flex:1;border-radius:12px}
  .context-figure :global(.crop-viewport){height:260px}
  .context-missing{margin:auto;font-size:12px;color:var(--muted)}
  .context-id{font-size:9px;color:var(--muted);overflow-wrap:anywhere}
  @media(max-width:700px){.glyph-context,.glyph-context.left{left:16px;right:16px;width:auto}.glyph-context.top{top:16px;bottom:auto}.context-figure,.context-figure :global(.crop-viewport){height:200px}}
</style>
