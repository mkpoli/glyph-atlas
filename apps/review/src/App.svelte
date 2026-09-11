<script>
  /**
   * The shell: the top bar, the view the hash route names, the client's own event log, and the keys.
   *
   * Routes are `#/queue`, `#/page/<page id>` and `#/line/<line id>`; they can be linked to and are
   * what the screenshot tool drives.
   */
  import { onMount } from 'svelte'
  import { Session } from './lib/session.svelte.js'
  import QueueView from './views/QueueView.svelte'
  import PageView from './views/PageView.svelte'
  import LineView from './views/LineView.svelte'
  import EventLog from './components/EventLog.svelte'
  import CandidatePicker from './components/CandidatePicker.svelte'
  import ConflictDialog from './components/ConflictDialog.svelte'
  import Help from './components/Help.svelte'

  const session = new Session()

  let clientDraft = $state('')

  onMount(() => {
    clientDraft = session.clientId
    session.start()
  })

  function onKeydown(event) {
    const tag = event.target?.tagName
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return
    if (event.metaKey || event.ctrlKey || event.altKey) return
    if (session.mode === 'candidates') return

    switch (event.key) {
      case 'a':
        session.accept()
        break
      case 'x':
        session.reject()
        break
      case 's':
        session.splitAt(session.pointer)
        break
      case 'm':
        session.mergeSelection()
        break
      case 'c':
        if (session.line) session.beginDrawUnit()
        break
      case 'l':
        if (session.page) session.beginDrawLine()
        break
      case 'r':
        session.beginReading()
        break
      case 'j':
        session.beginCandidates()
        break
      case 'g':
        session.markGroup()
        break
      case 'n':
        session.beginNote()
        break
      case 'z':
        session.undo()
        break
      case ' ':
        event.preventDefault()
        if (session.route.name === 'line') session.nextLine()
        else if (session.queue.items.length) session.openLine(session.queue.items[0].id)
        break
      case 'Enter':
        if (session.route.name === 'queue' && session.queue.items.length) {
          session.openLine(session.queue.items[0].id)
        }
        break
      case 'Escape':
        session.cancel()
        break
      case '?':
        session.help = !session.help
        break
      case 'ArrowDown':
      case 'ArrowRight':
        if (session.line) {
          event.preventDefault()
          session.step(1, { extend: event.shiftKey })
        }
        break
      case 'ArrowUp':
      case 'ArrowLeft':
        if (session.line) {
          event.preventDefault()
          session.step(-1, { extend: event.shiftKey })
        }
        break
      default:
        break
    }
  }
</script>

<svelte:window onkeydown={onKeydown} />

<div class="app">
  <header class="topbar">
    <h1>kuzushiji-atlas review</h1>
    <span class="sep">·</span>
    <nav class="crumb">
      <a href="#/queue" style="color:inherit">queue</a>
      {#if session.page}
        · <a href="#/page/{encodeURIComponent(session.page.id)}" style="color:inherit">{session.page.id}</a>
      {/if}
      {#if session.line}
        · <strong>{session.line.id}</strong>
      {/if}
    </nav>
    <span class="spacer"></span>
    {#if session.status === 'loading'}
      <span class="badge">connecting…</span>
    {:else if session.status === 'error'}
      <span class="badge bad">no service: {session.error}</span>
    {/if}
    <label class="small muted hide-narrow">
      reviewer
      <input
        type="text"
        bind:value={clientDraft}
        size="10"
        onchange={() => session.setClientId(clientDraft)}
        onkeydown={(event) => {
          if (event.key === 'Enter') session.setClientId(clientDraft)
        }}
      />
    </label>
    <button class="hide-narrow" onclick={() => session.toggleTheme()} title="light, dark, or the system's choice">
      {session.theme}
    </button>
    <button onclick={() => (session.help = true)}>keys (?)</button>
  </header>

  <div class="main">
    <main class="pane">
      {#if session.status === 'error'}
        <div class="panel">
          <h2>the review service did not answer</h2>
          <p class="small">
            Start it with <code>atlas review serve &lt;dataset&gt; --port 8770</code> and reload. In
            development the Vite proxy forwards the API paths to that port.
          </p>
          <p class="small muted">{session.error}</p>
          <button onclick={() => location.reload()}>try again</button>
        </div>
      {:else if session.route.name === 'page'}
        <PageView {session} />
      {:else if session.route.name === 'line'}
        <LineView {session} />
      {:else}
        <QueueView {session} />
      {/if}
    </main>
    <aside class="side">
      <EventLog {session} />
      <div class="panel small">
        <h2>keys</h2>
        <p class="muted" style="margin:0">
          <kbd>a</kbd> accept <kbd>x</kbd> reject <kbd>s</kbd> split <kbd>m</kbd> merge <kbd>c</kbd> new unit
          <kbd>r</kbd> reading <kbd>j</kbd> 字母 <kbd>g</kbd> group <kbd>n</kbd> note <kbd>z</kbd> undo
          <kbd>space</kbd> next line <kbd>?</kbd> all of them
        </p>
      </div>
    </aside>
  </div>

  <footer class="bottom">
    <span>{session.clientId}</span>
    <span>{session.documents.length} documents</span>
    {#if session.queue.total}<span>{session.queue.total} lines in the {session.strategy} queue</span>{/if}
    {#if session.line}<span>line {session.line.id}</span>{/if}
    <span style="flex:1"></span>
    <span>{session.busy ? 'loading…' : 'ready'}</span>
  </footer>
</div>

{#if session.mode === 'candidates'}
  <CandidatePicker {session} />
{/if}
{#if session.conflict}
  <ConflictDialog {session} />
{/if}
{#if session.help}
  <Help {session} />
{/if}

<div class="toasts">
  {#each session.toasts as toast (toast.id)}
    <div class="toast {toast.kind}" onclick={() => session.dismissToast(toast.id)} role="presentation">
      {toast.message}
    </div>
  {/each}
</div>
