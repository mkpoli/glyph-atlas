<script>
  // The site's licence, code and chats in one line, for a page whose footer an endless grid keeps out
  // of reach.
  import { around } from '../lib/i18n.svelte.js'
  import ChatLinks from './ChatLinks.svelte'
  import LinkMark from './LinkMark.svelte'
  const licence = $derived(around('links.licence', 'license'))
</script>

<p class="site-links">{licence[0]}<a href="https://creativecommons.org/licenses/by-sa/4.0/" rel="license noopener" target="_blank"><LinkMark name="cc" />CC BY-SA 4.0</a>{licence[1]}
  · <a href="https://github.com/mkpoli/glyph-atlas" rel="noopener" target="_blank"><LinkMark name="github" />GitHub</a>
  · <ChatLinks /></p>
