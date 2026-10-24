<script>
  // The community's chat, one space reached three ways: Matrix holds it, and Discord and Telegram are
  // bridged to it, so a message sent on any of them appears on all three. The Matrix link opens the
  // space in Element on the web, where someone new can sign up.
  import LinkMark from './LinkMark.svelte'
</script>

<span class="chat"><a href="https://app.element.io/#/room/#glyphatlas:glyphatlas.org" rel="noopener" target="_blank"><LinkMark name="matrix" />Matrix</a></span>
· <span class="chat"><a href="https://discord.gg/DR25YeAYSg" rel="noopener" target="_blank"><LinkMark name="discord" />Discord</a></span>
· <span class="chat"><a href="https://t.me/+Q4wiIzMijRwzOWE1" rel="noopener" target="_blank"><LinkMark name="telegram" />Telegram</a></span>
