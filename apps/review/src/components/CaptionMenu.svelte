<script>
  // A group's caption that opens a menu of what can be done with the group: `items` are
  // `{ key, label, run }`. The menu is a popover, so it shows above an open inspector, closes on a
  // click elsewhere or Escape, and gives the focus back to its caption.
  let { label, lang = 'zh', items = [], disabled = false } = $props()
  const id = $props.id()
  let trigger = $state(null), menu = $state(null), open = $state(false)
  const entries = () => [...menu.querySelectorAll('[role="menuitem"]')]
  function place() {
    const anchor = trigger.getBoundingClientRect(), box = menu.getBoundingClientRect()
    const below = anchor.bottom + 4, fits = below + box.height <= window.innerHeight - 8
    menu.style.left = `${Math.max(8, Math.min(anchor.left, window.innerWidth - box.width - 8))}px`
    menu.style.top = `${fits ? below : Math.max(8, anchor.top - box.height - 4)}px`
  }
  function toggled(event) {
    open = event.newState === 'open'
    if (open) { place(); entries()[0]?.focus() }
  }
  const close = () => { if (menu?.matches(':popover-open')) menu.hidePopover() }
  function keydown(event) {
    const all = entries(), at = all.indexOf(document.activeElement)
    const to = { ArrowDown: at + 1, ArrowUp: at - 1, Home: 0, End: all.length - 1 }[event.key]
    if (to === undefined) return
    event.preventDefault()
    all[(to + all.length) % all.length]?.focus()
  }
  async function run(item) {
    close()
    trigger.focus()
    await item.run()
  }
</script>

<svelte:window onresize={close} />

<button type="button" class="caption-menu-trigger" bind:this={trigger} popovertarget={id} aria-haspopup="menu" aria-expanded={open} {disabled} {lang}>{label}</button>
<div {id} popover class="caption-menu" role="menu" aria-label={label} bind:this={menu} ontoggle={toggled} onkeydown={keydown}>
  {#each items as item (item.key)}
    <button type="button" role="menuitem" onclick={() => run(item)}>{item.label}</button>
  {/each}
</div>

<style>
  .caption-menu-trigger{display:inline-flex;align-items:center;gap:4px;align-self:flex-start;border:0;border-radius:4px;background:transparent;padding:0;font:inherit;color:inherit;text-align:left}
  .caption-menu-trigger::after{content:"";width:5px;height:5px;margin-top:-3px;border:solid currentColor;border-width:0 1px 1px 0;transform:rotate(45deg);opacity:.7}
  .caption-menu-trigger:not(:disabled):hover,.caption-menu-trigger[aria-expanded="true"]{color:var(--accent)}
  .caption-menu:popover-open{position:fixed;inset:auto;margin:0;display:flex;flex-direction:column;gap:2px;min-width:168px;max-width:calc(100vw - 16px);max-height:min(320px,calc(100vh - 16px));overflow:auto;padding:4px;
    border:1px solid var(--line);border-radius:10px;background:var(--surface);color:var(--ink);box-shadow:0 6px 24px var(--shadow)}
  .caption-menu [role="menuitem"]{flex:none;border:0;border-radius:6px;background:transparent;padding:8px 10px;font-size:13px;line-height:1.3;text-align:left;color:var(--ink)}
  .caption-menu [role="menuitem"]:hover,.caption-menu [role="menuitem"]:focus-visible{background:var(--accent-light)}
</style>
