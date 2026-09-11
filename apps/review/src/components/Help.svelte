<script>
  /** The keys of the interface, as the session declares them. */
  import Session from '../lib/session.svelte.js'

  let { session } = $props()

  const bindings = Session.bindings
</script>

<div
  class="overlay"
  role="presentation"
  onclick={(event) => {
    if (event.target === event.currentTarget) session.help = false
  }}
>
  <div class="dialog" role="dialog" aria-modal="true" aria-label="keys" tabindex="-1">
    <h2>keys</h2>
    <table class="grid">
      <tbody>
        {#each bindings as binding (binding.keys)}
          <tr>
            <th style="width:110px">
              {#each binding.keys.split(' ') as key (key)}
                <kbd>{key}</kbd>
              {/each}
            </th>
            <td>{binding.what}</td>
          </tr>
        {/each}
      </tbody>
    </table>
    <p class="small muted">
      The queue view lists the lines the server hands out; space moves to the next one. Every decision
      is a review event; <kbd>z</kbd> undoes this client's last one with a compensating review.
    </p>
    <button onclick={() => (session.help = false)}>close</button>
  </div>
</div>
