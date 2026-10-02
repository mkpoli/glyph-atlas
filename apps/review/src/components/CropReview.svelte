<script>
  // How one crop is judged, the same in Quick Review and in the inspector: the problem cards with
  // Skip, and for a wrong or joined character what it holds instead. `forms`, when given, is drawn
  // above the cards: the inspector's bar of the crop's forms.
  import IssuePicker from './IssuePicker.svelte'
  import ReadingSuggestions from './ReadingSuggestions.svelte'
  import { issueForKey, suggestsReading, SKIP_KEY, NONE_KEY } from '../lib/issues.js'
  let { issue = null, onissue, suggested = null, disabled = false, onskip, skipped = false,
    targetId = '', result = null, loading = false, contextResult = null, contextLoading = false,
    label = '', value = null, noneSelected = false, onchoose, element = $bindable(null), forms = null } = $props()
  let root = $state(null)

  // A letter chooses a card, S skips and N says none of the suggestions fits. Keys typed into a field
  // are the field's, and a review under an open dialog leaves them to the dialog.
  function keydown(event) {
    if (disabled || event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return
    if (event.target.closest?.('input, textarea, select, [contenteditable="true"]')) return
    const dialog = document.querySelector('dialog[open]')
    if (dialog && !dialog.contains(root)) return
    const key = event.key.toLowerCase(), chosen = issueForKey(key)
    if (chosen) { event.preventDefault(); onissue(chosen) }
    else if (key === SKIP_KEY && onskip) { event.preventDefault(); onskip() }
    else if (key === NONE_KEY && suggestsReading(issue) && !event.target.closest?.('button, a, summary')) { event.preventDefault(); onchoose(null, true) }
  }
</script>

<svelte:window onkeydown={keydown} />

<div class="crop-review" bind:this={root}>
  {@render forms?.()}
  <IssuePicker value={issue} choose={onissue} {suggested} {disabled} skip={onskip} {skipped} />
  <ReadingSuggestions {targetId} bind:element {result} {loading} {contextResult} {contextLoading} {issue}
    reading={label} {value} {noneSelected} {disabled} choose={onchoose} />
</div>
