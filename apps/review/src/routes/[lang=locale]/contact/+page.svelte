<script>
  import Seo from '$components/Seo.svelte'
  import LinkMark from '$components/LinkMark.svelte'
  import { t, around, locale, formatDateTime } from '$lib/i18n.svelte.js'
  import { CHATS, IRC_CHANNELS, IRC_WEB, MATRIX_TO, OTHER_GROUPS, SPACE } from '$lib/community.js'
  import { defaultRoom, groupMessages, linkify, roomLabel, roomLang } from '$lib/preview.js'
  const EMAIL = 'contact@glyphatlas.org'
  const REPO = 'https://github.com/mkpoli/glyph-atlas'
  const email = $derived(around('contact.email.body', 'email'))
  const failed = $derived(around('contact.form.failed', 'email'))
  // The message form posts to the Worker, which hands it to the maintainers' Matrix inbox; no session
  // is started for it. `website` is a field people never see (`contact.ts`).
  let message = $state(''), reply = $state(''), website = $state('')
  let status = $state('idle')
  async function send(event) {
    event.preventDefault()
    if (status === 'sending' || !message.trim()) return
    status = 'sending'
    try {
      const response = await fetch('/api/contact', { method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ message, reply, website, locale: locale() }) })
      status = response.ok ? 'sent' : response.status === 429 ? 'busy' : 'failed'
      if (response.ok) { message = ''; reply = '' }
    } catch { status = 'failed' }
  }
  const apps = $derived(around('contact.matrix.apps', 'space'))
  // How many people each chat place has, keyed by its mark; read once the page is open (`community.ts`).
  // IRC keeps no membership and QQ publishes none, so those marks carry no count.
  let members = $state({})
  $effect(() => { fetch('/api/community').then(response => response.ok ? response.json() : {}).then(found => { members = found }).catch(() => {}) })
  // Recent messages from the public rooms, so someone who has not signed in can see what the chat is like
  // before joining (`preview.ts`). The panel stays out of the page until there is something to show.
  let preview = $state(undefined)
  $effect(() => { fetch('/api/preview').then(response => response.ok ? response.json() : []).then(found => { preview = found }).catch(() => { preview = [] }) })
  // The room open in the preview defaults to the page's language (lzh pages are Chinese), or the first room.
  const spoken = $derived(locale() === 'lzh' ? 'zh' : locale().split('-')[0])
  let chosenRoom = $state(null)
  const activeRoom = $derived(preview?.some(room => room.alias === chosenRoom) ? chosenRoom : defaultRoom(preview, spoken))
  let tablist = $state(null)
  let moreStart = $state(false), moreEnd = $state(false)
  function selectRoom(alias, { focus = false } = {}) {
    chosenRoom = alias
    const index = preview?.findIndex(room => room.alias === alias) ?? -1
    const button = index >= 0 ? tablist?.querySelector(`#preview-tab-${index}`) : null
    button?.scrollIntoView({ block: 'nearest', inline: 'nearest', container: 'nearest' })
    if (focus) button?.focus()
  }
  function stepTab(event, index) {
    if (!preview?.length) return
    const rtl = getComputedStyle(event.currentTarget).direction === 'rtl'
    const back = rtl ? 'ArrowRight' : 'ArrowLeft'
    const forward = rtl ? 'ArrowLeft' : 'ArrowRight'
    let next = null
    if (event.key === forward) next = (index + 1) % preview.length
    else if (event.key === back) next = (index - 1 + preview.length) % preview.length
    else if (event.key === 'Home') next = 0
    else if (event.key === 'End') next = preview.length - 1
    if (next === null) return
    event.preventDefault()
    selectRoom(preview[next].alias, { focus: true })
  }
  // Fallback for browsers without scroll-driven animations: mark which edges of the room strip overflow.
  $effect(() => {
    const el = tablist
    if (!el || !preview?.length || CSS.supports('animation-timeline', 'scroll()')) return
    const buttons = el.querySelectorAll('[role="tab"]')
    const first = buttons[0], last = buttons[buttons.length - 1]
    if (!first || !last) return
    const observer = new IntersectionObserver(entries => {
      for (const { target, intersectionRatio } of entries) {
        if (target === first) moreStart = intersectionRatio < 1
        if (target === last) moreEnd = intersectionRatio < 1
      }
    }, { root: el, threshold: 1, rootMargin: '1px' })
    observer.observe(first)
    observer.observe(last)
    return () => observer.disconnect()
  })
  // A message reads as a time today, and as a date and time before that, so a quiet room still looks alive.
  // The site's own formatter writes the date in the page's language, classical numerals included.
  const at = (stamp) => {
    const sameDay = new Date(stamp).toDateString() === new Date().toDateString()
    try { return formatDateTime(stamp, { date: !sameDay }) } catch { return '' }
  }
  let irc, picked = $state([])
  $effect.pre(() => { picked = IRC_CHANNELS.filter(item => item.language === spoken).map(item => item.channel) })
  // The Matrix door opens a picker: the community's Element, or matrix.to for any other app.
  let matrix
  // Only a press on the backdrop closes the dialog, not one in its padding.
  function outside(event) {
    const dialog = event.currentTarget
    const box = dialog.getBoundingClientRect()
    if (event.target === dialog && (event.clientX < box.left || event.clientX > box.right
        || event.clientY < box.top || event.clientY > box.bottom)) dialog.close()
  }
  const joined = $derived(IRC_CHANNELS.map(item => item.channel).filter(channel => picked.includes(channel)).join(','))
</script>

<Seo title={t('contact.title')} description={t('contact.intro')} />

<article class="contact">
  <h1>{t('contact.title')}</h1>
  <p class="contact-intro">{t('contact.intro')}</p>

  <section>
    <h2>{t('contact.chat.title')}</h2>
    <p>{t('contact.chat.body')}</p>
    <ul class="doors">
      {#each CHATS as chat (chat.name)}
        <li>
          {#if chat.mark === 'irc'}
            <a href={chat.href} rel="noopener" target="_blank" aria-haspopup="dialog" onclick={event => { event.preventDefault(); irc.showModal() }}><LinkMark name={chat.mark} />{chat.name}</a>
          {:else if chat.mark === 'matrix'}
            <a href={chat.href} rel="noopener" target="_blank" aria-haspopup="dialog" onclick={event => { event.preventDefault(); matrix.showModal() }}><LinkMark name={chat.mark} />{chat.name}{#if typeof members[chat.mark] === 'number'}<small>{t('contact.chat.members', { count: members[chat.mark] })}</small>{/if}</a>
          {:else}
            <a href={chat.href} rel="noopener" target="_blank"><LinkMark name={chat.mark} />{chat.name}{#if typeof members[chat.mark] === 'number'}<small>{t('contact.chat.members', { count: members[chat.mark] })}</small>{/if}</a>
          {/if}
        </li>
      {/each}
    </ul>
    <p>{t('contact.chat.rooms')}</p>

    {#if preview?.length}
      <!-- A look at the rooms, folded away: the page is mostly about reaching the maintainers, and a wall
           of messages would push that form off the screen. One room shows at a time in a compact frame. -->
      <details class="preview-section">
        <summary>
          <span class="preview-dot" aria-hidden="true"></span>
          <span class="preview-title">{t('contact.preview.title')}</span>
        </summary>
        <div class="preview-body">
          <div class="preview-chrome">
            <p class="preview-lead">{t('contact.preview.body')}</p>
            <div class="preview-tabs" class:more-start={moreStart} class:more-end={moreEnd} role="tablist" aria-label={t('contact.preview.roomsLabel')} bind:this={tablist}>
              {#each preview as room, index (room.alias)}
                {@const active = room.alias === activeRoom}
                <button type="button" role="tab" id="preview-tab-{index}" aria-selected={active} tabindex={active ? 0 : -1}
                  lang={roomLang(room)} onclick={() => selectRoom(room.alias)} onkeydown={event => stepTab(event, index)}>{roomLabel(room, t)}</button>
              {/each}
            </div>
          </div>
          <div class="preview-panels">
            {#each preview as room, index (room.alias)}
              {@const active = room.alias === activeRoom}
              <div class="preview-panel" role="tabpanel" aria-labelledby="preview-tab-{index}"
                hidden={active ? undefined : 'until-found'} onbeforematch={() => selectRoom(room.alias)}>
                <ol class="preview-feed" tabindex={active ? 0 : undefined}>
                  {#each groupMessages(room.messages) as group, gIndex (gIndex)}
                    <li class="preview-group">
                      <div class="preview-meta">
                        <span class="preview-mark"><LinkMark name={group.mark} /></span>
                        <span class="who">{group.name}</span>
                        <time datetime={new Date(group.at).toISOString()}>{at(group.at)}</time>
                      </div>
                      <div class="preview-lines">
                        {#each group.items as line, mIndex (mIndex)}
                          <p class="what">{#each linkify(line.body) as part, pIndex (pIndex)}{#if part.type === 'link'}<a href={part.href} rel="noopener nofollow noreferrer" target="_blank">{part.label}</a>{:else}{part.text}{/if}{/each}</p>
                        {/each}
                      </div>
                    </li>
                  {/each}
                </ol>
              </div>
            {/each}
          </div>
          <p class="preview-note">{t('contact.preview.note')}</p>
        </div>
      </details>
    {/if}
  </section>

  <section>
    <h2>{t('contact.others.title')}</h2>
    <p>{t('contact.others.body')}</p>
    <ul class="doors">
      {#each OTHER_GROUPS as group (group.href)}
        <li><a href={group.href} rel="noopener" target="_blank"><LinkMark name={group.mark} /><span class="visually-hidden">{group.network}: </span><span lang={group.lang}>{group.name}</span><small lang={group.lang}>{group.language}</small></a></li>
      {/each}
    </ul>
  </section>

  <section>
    <h2>GitHub</h2>
    <p>{t('contact.github.body')}</p>
    <ul class="apps">
      <li><a href={REPO} rel="noopener" target="_blank">{t('contact.github.repo')} ↗</a></li>
      <li><a href={`${REPO}/issues/new`} rel="noopener" target="_blank">{t('contact.github.issue')} ↗</a></li>
    </ul>
  </section>

  <section>
    <h2>{t('contact.email.title')}</h2>
    <p>{email[0]}<a href={`mailto:${EMAIL}`}>{EMAIL}</a>{email[1]}</p>
  </section>

  <section>
    <h2 id="message">{t('contact.form.title')}</h2>
    <p>{t('contact.form.body')}</p>
    <form class="message-form" onsubmit={send} aria-labelledby="message">
      <label for="contact-message">{t('contact.form.message')}</label>
      <textarea id="contact-message" rows="6" maxlength="4000" required bind:value={message} oninput={() => { if (status !== 'sending') status = 'idle' }}></textarea>
      <label for="contact-reply">{t('contact.form.reply')}</label>
      <input id="contact-reply" maxlength="200" autocomplete="email" placeholder={t('contact.form.replyHint')} bind:value={reply} />
      <input class="unseen" name="website" tabindex="-1" autocomplete="off" aria-hidden="true" bind:value={website} />
      <div class="form-foot">
        <button class="send" type="submit" disabled={status === 'sending' || !message.trim()}>{status === 'sending' ? t('contact.form.sending') : t('contact.form.send')}</button>
        <p class="form-status" role="status">{#if status === 'sent'}{t('contact.form.sent')}{:else if status === 'busy'}{t('contact.form.busy')}{:else if status === 'failed'}{failed[0]}<a href={`mailto:${EMAIL}`}>{EMAIL}</a>{failed[1]}{/if}</p>
      </div>
    </form>
  </section>
</article>

<dialog class="irc matrix" bind:this={matrix} onclick={outside} aria-labelledby="matrix-title">
  <header><h2 id="matrix-title">{t('contact.matrix.title')}</h2><button type="button" class="icon-button" aria-label={t('contact.irc.close')} onclick={() => matrix.close()}>×</button></header>
  <p>{t('contact.matrix.account')}</p>
  <div class="matrix-doors">
    <a class="send" href={CHATS[0].href} rel="noopener" target="_blank">Element ↗</a>
    <a href={MATRIX_TO} rel="noopener" target="_blank">{t('contact.matrix.choose')} ↗</a>
  </div>
  <p class="irc-note">{apps[0]}<code>{SPACE}</code>{apps[1]}</p>
</dialog>

<dialog class="irc" bind:this={irc} onclick={outside} aria-labelledby="irc-title">
  <header><h2 id="irc-title">{t('contact.irc.title')}</h2><button type="button" class="icon-button" aria-label={t('contact.irc.close')} onclick={() => irc.close()}>×</button></header>
  <p>{t('contact.irc.body')}</p>
  <fieldset class="channels">
    {#each IRC_CHANNELS as item (item.channel)}
      <label><input type="checkbox" value={item.channel} bind:group={picked} /><span lang={item.lang}>{item.name}</span><code>{item.channel}</code></label>
    {/each}
  </fieldset>
  {#if joined}
    <div class="irc-open">
      <a class="send" href={IRC_WEB + joined} rel="noopener" target="_blank">{t('contact.irc.open')} ↗</a>
      <code>/join {joined}</code>
    </div>
  {/if}
  <p class="irc-note">{t('contact.irc.history')}</p>
</dialog>

<style>
  .contact { max-width: 720px; margin: auto; padding: 40px 20px 64px; font-size: 14px; line-height: 1.7; }
  .contact h1 { font-size: 30px; font-weight: 500; letter-spacing: -.6px; }
  .contact-intro { color: var(--muted); margin: 6px 0 12px; }
  .contact h2 { font-size: 16px; font-weight: 600; margin: 28px 0 8px; }
  .contact p + p, .contact ul + p, .contact ol + p { margin-top: 10px; }
  .contact p a, .contact .apps a { text-decoration: underline; text-underline-offset: 2px; }
  .contact ol { padding-left: 1.4em; display: grid; gap: 4px; }
  .contact code { font-size: 13px; padding: 1px 5px; border-radius: 4px; background: var(--surface-sunken); }
  .doors { list-style: none; padding: 0; margin: 12px 0; display: flex; flex-wrap: wrap; gap: 8px; }
  .doors a { display: inline-flex; align-items: center; gap: 8px; padding: 8px 14px; border: 1px solid var(--line); background: var(--surface); border-radius: 8px; font-weight: 500; }
  .doors a:hover { border-color: var(--line-hover); }
  .doors :global(.link-mark) { width: 18px; height: 18px; margin: 0; vertical-align: 0; }
  .doors small { color: var(--muted); font-weight: 400; }
  .preview-section { margin-block-start: 16px; border: 1px solid var(--line); border-radius: var(--ga-radius-lg); background: var(--surface); overflow: hidden; }
  .preview-section:not([open]):hover { border-color: var(--line-hover); }
  .preview-section summary { cursor: pointer; display: flex; align-items: center; gap: 8px; padding: 11px 16px; list-style: none; font-size: 14px; font-weight: 600; color: var(--ink); user-select: none; }
  .preview-section summary:focus-visible { outline-offset: -3px; border-radius: var(--ga-radius-lg); }
  .preview-section[open] summary { border-block-end: 1px solid var(--line); }
  .preview-section[open] summary:focus-visible { border-radius: var(--ga-radius-lg) var(--ga-radius-lg) 0 0; }
  .preview-section summary::-webkit-details-marker { display: none; }
  .preview-section summary::marker { content: ''; }
  .preview-section summary::before { content: ''; inline-size: 1em; block-size: 1em; flex-shrink: 0; background-color: var(--muted); mask: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Cpath fill='none' stroke='black' stroke-width='1.75' stroke-linecap='round' stroke-linejoin='round' d='M4 6l4 4 4-4'/%3E%3C/svg%3E") no-repeat center / contain; rotate: -90deg; }
  .preview-section summary:dir(rtl)::before { rotate: 90deg; }
  .preview-section[open] > summary::before { rotate: 0deg; }
  .preview-dot { inline-size: 6px; block-size: 6px; border-radius: 50%; background: var(--good); flex-shrink: 0; }
  .preview-section::details-content { interpolate-size: allow-keywords; overflow: clip; contain: layout; }
  .preview-section:not([open])::details-content { block-size: 0; }
  @media (prefers-reduced-motion: no-preference) {
    .preview-section summary::before { transition: rotate .2s ease; }
    .preview-section::details-content { transition: block-size .25s ease-in-out, content-visibility .25s ease-in-out, display .25s ease-in-out; transition-behavior: allow-discrete; }
  }
  .preview-chrome { background: var(--surface-subtle); border-block-end: 1px solid var(--line); }
  .preview-lead { margin: 0; padding: 10px 16px 4px; font-size: 12.5px; line-height: 1.5; color: var(--muted); }
  @property --fade-start { syntax: '<length>'; inherits: false; initial-value: 0px; }
  @property --fade-end { syntax: '<length>'; inherits: false; initial-value: 0px; }
  .preview-tabs { display: flex; overflow-x: auto; scrollbar-width: none; padding-inline: 5px; scroll-snap-type: inline proximity; scroll-padding-inline: 28px; --fade-size: 28px; --fade-dir: right; --fade-start-pos: 0; --fade-end-pos: 100%; mask: linear-gradient(black, black) no-clip subtract, linear-gradient(to var(--fade-dir), black, transparent) var(--fade-start-pos) 0 / var(--fade-start) 100% no-repeat no-clip, linear-gradient(to var(--fade-dir), transparent, black) var(--fade-end-pos) 0 / var(--fade-end) 100% no-repeat no-clip; transition: --fade-start .2s, --fade-end .2s; }
  .preview-tabs:dir(rtl) { --fade-dir: left; --fade-start-pos: 100%; --fade-end-pos: 0; }
  .preview-tabs::-webkit-scrollbar { display: none; }
  .preview-tabs.more-start { --fade-start: var(--fade-size); }
  .preview-tabs.more-end { --fade-end: var(--fade-size); }
  @supports (animation-timeline: scroll()) {
    .preview-tabs { animation: reveal-fade-start linear both, hide-fade-end linear both; animation-timeline: scroll(self inline); animation-range: 0 var(--fade-size), calc(100% - var(--fade-size)) 100%; transition: none; }
    .preview-feed { --fade-size: 28px; mask: linear-gradient(black, black) no-clip subtract, linear-gradient(to bottom, black, transparent) 0 0 / 100% var(--fade-start) no-repeat no-clip, linear-gradient(to bottom, transparent, black) 0 100% / 100% var(--fade-end) no-repeat no-clip; animation: reveal-fade-start linear both, hide-fade-end linear both; animation-timeline: scroll(self block); animation-range: 0 var(--fade-size), calc(100% - var(--fade-size)) 100%; }
  }
  @keyframes reveal-fade-start { to { --fade-start: var(--fade-size); } }
  @keyframes hide-fade-end { from { --fade-end: var(--fade-size); } }
  .preview-tabs [role="tab"] { position: relative; flex: none; scroll-snap-align: start; border: 0; border-radius: 5px; background: transparent; padding: 9px 11px; font-size: 13px; font-weight: 500; line-height: 1.3; color: var(--muted); white-space: nowrap; }
  .preview-tabs [role="tab"]:focus-visible { outline-offset: -3px; }
  .preview-tabs [role="tab"]:hover { color: var(--ink); }
  .preview-tabs [role="tab"][aria-selected="true"] { color: var(--ink); }
  .preview-tabs [role="tab"][aria-selected="true"]::after { content: ''; position: absolute; inset: auto 11px 0; border-block-end: 2px solid var(--accent); }
  .preview-panels { display: grid; }
  .preview-panel { grid-area: 1 / 1; min-width: 0; }
  /* A message may hold an IDS description (⿰𠤕⿱龴疋). Plangothic draws the rare ideographs it names, but no
     bundled font draws the operators themselves (U+2FF0-2FFB), so those come from a reader's own fonts where
     it has them (`apps/review/static/fonts/README.md`). */
  @font-face { font-family: "IDS Parts"; src: url("/fonts/Plangothic-ids-parts.woff2") format("woff2"); font-display: swap; unicode-range: U+2E80-2EFF, U+31E4-31EF, U+20000-3FFFF; }
  @font-face { font-family: "IDS Strokes"; src: url("/fonts/NotoSansCJKjp-ids-strokes.woff2") format("woff2"); font-display: swap; unicode-range: U+31C0-31E3; }
  .preview-feed { list-style: none; margin: 0; padding: 14px 16px 18px; max-block-size: 420px; overflow-y: auto; display: grid; gap: 12px; }
  .preview-feed:focus-visible { outline-offset: -3px; }
  .preview-group { min-width: 0; }
  .preview-group:not(:first-child) { border-block-start: 1px solid var(--line); padding-block-start: 12px; }
  .preview-meta { display: flex; align-items: center; gap: 6px; min-width: 0; margin-block-end: 4px; }
  .preview-mark { display: inline-flex; align-items: center; color: var(--muted); flex-shrink: 0; }
  .preview-mark :global(.link-mark) { width: 14px; height: 14px; margin: 0; vertical-align: 0; }
  .preview-meta .who { font-size: 13px; font-weight: 600; color: var(--ink); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .preview-meta time { color: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; white-space: nowrap; }
  .preview-lines { display: grid; gap: 5px; padding-inline-start: 20px; min-width: 0; }
  .preview-lines .what { margin: 0; font-size: 13.5px; line-height: 1.6; white-space: pre-line; overflow-wrap: anywhere; font-family: "IDS Parts", "IDS Strokes", var(--ga-font-ui); }
  .preview-lines .what a { color: var(--accent); text-decoration: underline; text-underline-offset: 2px; }
  .preview-note { margin: 0; padding: 9px 16px; border-block-start: 1px solid var(--line); background: var(--surface-subtle); color: var(--muted); font-size: 12px; line-height: 1.5; }
  .message-form { display: grid; gap: 6px; margin-top: 12px; }
  .message-form label { font-weight: 500; margin-top: 6px; }
  .message-form textarea, .message-form input { font: inherit; color: inherit; background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; width: 100%; box-sizing: border-box; }
  .message-form textarea { resize: vertical; min-height: 120px; }
  .message-form textarea:focus-visible, .message-form input:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
  .message-form .unseen { position: absolute; left: -10000px; width: 1px; height: 1px; opacity: 0; }
  .form-foot { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin-top: 8px; }
  .send { font: inherit; font-weight: 500; padding: 8px 18px; border-radius: 8px; border: 0; background: var(--accent-solid); color: var(--on-color); cursor: pointer; }
  .send:hover:not(:disabled) { background: var(--accent-solid-hover); }
  .send:disabled { opacity: .5; cursor: default; }
  .form-status { margin: 0; color: var(--muted); }
  .irc, .matrix { width: min(440px, calc(100vw - 32px)); max-height: calc(100dvh - 32px); margin: auto; padding: 18px 20px; border: 1px solid var(--line); border-radius: 12px; color: var(--ink); background: var(--surface); box-shadow: 0 24px 80px var(--shadow); font-size: 13px; line-height: 1.6; }
  .irc::backdrop { background: var(--backdrop); backdrop-filter: blur(3px); }
  .irc header { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 6px; }
  .irc h2 { font-size: 15px; font-weight: 600; margin: 0; }
  .irc .icon-button { font-size: 24px; color: var(--muted); padding: 2px 6px; }
  .channels { border: 0; padding: 0; margin: 10px 0; display: grid; gap: 0; }
  .channels label { display: flex; align-items: center; gap: 10px; padding: 5px 4px; border-radius: 6px; cursor: pointer; min-width: 0; }
  .channels label:hover { background: var(--surface-sunken); }
  .channels input { accent-color: var(--accent-solid); margin: 0; flex-shrink: 0; }
  .channels code { margin-left: auto; color: var(--muted); font-size: 12px; overflow-wrap: anywhere; text-align: right; }
  .irc-open { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; }
  .irc-open a.send { text-decoration: none; display: inline-block; }
  .irc-open code { font-size: 12px; padding: 1px 5px; border-radius: 4px; background: var(--surface-sunken); overflow-wrap: anywhere; }
  .irc-note { margin: 10px 0 0; color: var(--muted); font-size: 12px; }
  .matrix-doors { display: flex; align-items: center; gap: 18px; margin: 12px 0 0; }
  .matrix-doors a:not(.send) { text-decoration: underline; text-underline-offset: 2px; }
  .apps { list-style: none; padding: 0; margin: 8px 0 0; display: flex; flex-wrap: wrap; gap: 4px 16px; }
</style>
