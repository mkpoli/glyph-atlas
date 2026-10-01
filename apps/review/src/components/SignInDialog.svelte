<script>
  import { onMount } from 'svelte'
  import SignIn from './SignIn.svelte'
  import SpecimenWall from './SpecimenWall.svelte'
  let { close } = $props()
  let dialog
  onMount(() => { dialog.showModal() })
</script>

<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions -->
<dialog class="sign-in-dialog" bind:this={dialog} onclose={close} onclick={event => { if (event.target === dialog) dialog.close() }}>
  <div class="sign-in-dialog-strip"><SpecimenWall count={8} columns={8} /></div>
  <div class="sign-in-dialog-body"><SignIn heading="h2" done={() => dialog.close()} skip={() => dialog.close()} /></div>
</dialog>

<style>
  .sign-in-dialog { width: min(420px, calc(100vw - 32px)); max-height: calc(100dvh - 32px); padding: 0; border: 1px solid var(--line); border-radius: 16px;
    background: var(--surface); color: var(--ink); box-shadow: 0 30px 90px var(--shadow); overflow: auto; }
  .sign-in-dialog[open] { animation: arrive .32s cubic-bezier(.2, .8, .2, 1); }
  .sign-in-dialog::backdrop { background: var(--backdrop); backdrop-filter: blur(6px); animation: fade .25s ease; }
  .sign-in-dialog-strip { height: 66px; overflow: hidden; }
  .sign-in-dialog-strip :global(.specimen-wall) { border: 0; }
  .sign-in-dialog-body { padding: 0 30px 26px; margin-top: -22px; position: relative; }
  @keyframes arrive { from { opacity: 0; transform: translateY(14px) scale(.98) } }
  @keyframes fade { from { opacity: 0 } }
  @media (prefers-reduced-motion: reduce) { .sign-in-dialog[open], .sign-in-dialog::backdrop { animation: none } }
</style>
