<script>
  import LinkMark from './LinkMark.svelte'
  import { CHATS } from '$lib/community.js'
  import { t, localize } from '$lib/i18n.svelte.js'
</script>

{#each CHATS as chat (chat.name)}<span class="chat"><a href={chat.href} rel="noopener" target="_blank"><LinkMark name={chat.mark} />{chat.name}</a></span>{' · '}{/each}<span class="chat"><a href={localize('/contact')}>{t('contact.title')}</a></span>
