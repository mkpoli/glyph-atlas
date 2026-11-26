<script>
  // One button per style: `pressed` is filled, `dashed` (a style taken from the page or document) has a dashed
  // border, and `held` (a choice not saved yet) a heavier one. `name` gives a button's accessible name.
  import { t } from '../lib/i18n.svelte.js'
  let { styles = [], pressed = null, dashed = null, held = null, disabled = false, keys = false, name = style => t(`style.kind.${style}`), onpress } = $props()
</script>

<div class="style-options">
  {#each styles as style, i (style)}
    <button type="button" class="style-option" class:chosen={style === pressed} class:held={style === held} class:inherited={style === dashed && style !== pressed} {disabled}
      aria-pressed={style === pressed} aria-label={name(style)} aria-keyshortcuts={keys ? `Shift+${i + 1}` : undefined} title={keys ? `${name(style)} (⇧${i + 1})` : name(style)}
      onclick={() => onpress(style)}>{t(`style.kind.${style}`)}</button>
  {/each}
</div>

<style>
  .style-options{display:flex;flex-wrap:wrap;gap:8px;min-width:0}
  .style-option{height:32px;padding:0 12px;border:1px solid var(--line);border-radius:6px;background:var(--surface);color:var(--ink);font:inherit;font-size:12px;white-space:nowrap;cursor:pointer}
  .style-option:hover:not(:disabled){border-color:var(--accent)}
  .style-option.inherited{border-style:dashed;border-color:var(--line-strong);color:var(--accent)}
  .style-option.chosen{border-color:var(--accent);background:var(--accent-light);color:var(--accent);font-weight:600}
  .style-option.held{border-width:2px;padding:0 11px}
  .style-option:disabled{opacity:.5;cursor:default}
  .style-option:focus-visible{outline:3px solid var(--focus-ring);outline-offset:2px}
  @media(max-width:700px){.style-option{height:36px}}
</style>
