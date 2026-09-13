<script>
  /**
   * The shell: the top bar, the view the hash route names, the client's own event log, and the keys.
   *
   * Routes cover the project overview, page catalogue, page reader and character review.
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
  import ProjectView from './views/ProjectView.svelte'
  import PagesView from './views/PagesView.svelte'

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
    if (!['page', 'line', 'queue'].includes(session.route.name)) return

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
    <a class="brand" href="#/project"><span class="seal" aria-hidden="true">字</span><span>くずし字 Atlas<small>Manuscripts · transcription · review</small></span></a>
    <nav class="primary-nav" aria-label="Main navigation">
      <a href="#/project" aria-current={session.route.name === 'project' ? 'page' : undefined}>Overview</a>
      <a href="#/pages" aria-current={['pages', 'page'].includes(session.route.name) ? 'page' : undefined}>Sources</a>
      <a href="#/queue" aria-current={['queue', 'line'].includes(session.route.name) ? 'page' : undefined}>Character review</a>
    </nav>
    <span class="spacer"></span>
    {#if session.status === 'loading'}
      <span class="badge">connecting…</span>
    {:else if session.status === 'error'}
      <span class="badge bad">no service: {session.error}</span>
    {/if}
    <label class="small muted hide-narrow">
      Reviewer
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
    <button onclick={() => session.toggleTheme()} title="Change color theme" aria-label="Change color theme">
      {session.theme === 'dark' ? 'Dark' : session.theme === 'light' ? 'Light' : 'Auto'}
    </button>
    <button class="hide-narrow" onclick={() => (session.help = true)}>Shortcuts</button>
  </header>

  <div class="main" class:reading-workspace={session.route.name !== 'line'}>
    <main class="pane">
      {#if session.status === 'error'}
        <div class="panel">
          <h2>The review service is unavailable</h2>
          <p>Reconnect to the local review service to continue.</p>
          <p class="small muted">{session.error}</p>
          <button onclick={() => location.reload()}>Try again</button>
        </div>
      {:else if session.status === 'loading'}
        <div class="empty-state" role="status">Loading the collection…</div>
      {:else if session.route.name === 'project'}
        <ProjectView />
      {:else if session.route.name === 'pages'}
        {#key session.route.id}
          <PagesView {session} documentId={session.route.id ?? ''} />
        {/key}
      {:else if session.route.name === 'page'}
        <PageView {session} />
      {:else if session.route.name === 'line'}
        <LineView {session} />
      {:else}
        <QueueView {session} />
      {/if}
    </main>
    {#if session.route.name === 'line'}<aside class="side">
      <EventLog {session} />
      <div class="panel small">
        <h2>keys</h2>
        <p class="muted" style="margin:0">
          <kbd>a</kbd> accept <kbd>x</kbd> reject <kbd>s</kbd> split <kbd>m</kbd> merge <kbd>c</kbd> new unit
          <kbd>r</kbd> reading <kbd>j</kbd> 字母 <kbd>g</kbd> group <kbd>n</kbd> note <kbd>z</kbd> undo
          <kbd>space</kbd> next line <kbd>?</kbd> all of them
        </p>
      </div>
    </aside>{/if}
  </div>

  <footer class="bottom">
    <span>{session.clientId}</span>
    <span>{session.documents.length} imported volumes</span>
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
