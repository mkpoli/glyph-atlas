<script>
  import ProductionBadge from './ProductionBadge.svelte'
  import ZiLink from './ZiLink.svelte'
  // Keep the same crop and context visible while its issue and correction are chosen.
  import Glyph from './Glyph.svelte'
  import CropContext from './CropContext.svelte'
  let { items = [], index = 0, label = '', backLabel = 'Change selection',
        disabled = false, onback, onjump, onprev, onnext, children } = $props()
  // The caller clamps too; this is the last line of defence, so a stage never shows a blank crop while
  // the queue is being rebuilt under it.
  const position = $derived(Math.max(0, Math.min(index, items.length - 1)))
  const item = $derived(items[position] ?? null)
</script>

<section class="quiz-focus" aria-label={label}>
  <header class="focus-head">
    <button type="button" class="quiet-link focus-back" {disabled} onclick={onback}>← {backLabel}</button>
    <span class="focus-progress" aria-live="polite">{position + 1} / {items.length}</span>
  </header>
  {#if item}
    <div class="focus-images">
      <div class="focus-figure" data-unit={item.id}>
        {#key item.id + ':' + item.revision + ':' + item.image_sha256}<CropContext {item} {disabled} />{/key}
      </div>
      <div class="focus-image-meta"><ProductionBadge {item} /><ZiLink character={item.written_character ?? item.label} /></div>
    </div>
  {/if}
  <div class="focus-body">{@render children?.()}</div>
  <nav class="focus-nav" aria-label="Selected crops">
    <button type="button" class="focus-step" aria-label="Previous crop" disabled={disabled || position <= 0} onclick={onprev}>←</button>
    <div class="focus-strip">
      {#each items as entry, i (entry.id)}
        <button type="button" class="focus-thumb" class:current={i === position} data-index={i}
                aria-label={`Crop ${i + 1}`} aria-current={i === position} {disabled} onclick={() => onjump(i)}>
          <Glyph item={entry} />
        </button>
      {/each}
    </div>
    <button type="button" class="focus-step" aria-label="Next crop" disabled={disabled || position >= items.length - 1} onclick={onnext}>→</button>
  </nav>
</section>

<style>
  .quiz-focus{display:grid;grid-template-columns:minmax(240px,1fr) minmax(320px,1.15fr);gap:20px 32px;max-width:920px;margin:0 auto;padding:8px 0 28px;align-items:start}
  .focus-head{grid-column:1/-1;display:flex;align-items:center;justify-content:space-between;width:100%;gap:12px}
  .focus-progress{font-size:13px;color:var(--muted);font-variant-numeric:tabular-nums}
  .focus-images,.focus-figure{width:100%;min-width:0}
  .focus-image-meta{display:flex;justify-content:space-between;gap:12px;padding-top:8px}
  .focus-body{width:100%;min-width:0}
  .focus-body :global(.issue-card){min-height:102px;padding:14px;gap:5px}
  .focus-body :global(.issue-card.chosen){padding:13px}
  .focus-nav{grid-column:1/-1;display:flex;align-items:center;gap:10px;width:100%;margin-top:4px}
  .focus-step{font-size:16px;padding:10px 16px;border:1px solid var(--line);border-radius:7px;background:#fff;cursor:pointer}
  .focus-step:disabled{opacity:.4;cursor:default}
  .focus-strip{display:flex;gap:8px;overflow-x:auto;padding:3px;flex:1;justify-content:center}
  .focus-thumb{padding:6px;border:1px solid var(--line);border-radius:7px;background:#fff;cursor:pointer;line-height:0}
  .focus-thumb.current{border-color:var(--accent);box-shadow:0 0 0 2px var(--accent-light)}
  .focus-thumb :global(img){width:44px;height:44px;object-fit:contain}
  @media(max-width:760px){
    .quiz-focus{grid-template-columns:1fr;gap:16px;padding-bottom:24px}
    .focus-body :global(.issue-card){min-height:92px}
    .focus-strip{justify-content:flex-start}
  }
</style>
