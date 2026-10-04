<script>
  // A written form typed or built as an ideographic description sequence. The field takes the
  // sequence as text; the operator keys and the component search write into it at the caret, or,
  // with a part of the structure below chosen, act on that part: an operator splits it (⿰ round 失
  // gives ⿰失？) and a component found replaces it. The sequence is checked as typed, by the rules the
  // site saves it under, and is used only once it is whole.
  import CharacterSearch from './CharacterSearch.svelte'
  import { OPERATORS, parse, write, replaceAt, partAt, wrap } from '../lib/ids.js'
  import { t } from '../lib/i18n.svelte.js'
  let { start = '', disabled = false, onuse } = $props()
  let draft = $state(''), chosen = $state(null), query = $state(''), field = $state(null)
  $effect(() => { draft = start; chosen = null })
  const parsed = $derived(parse(draft))
  const tree = $derived(parsed.tree ?? null)
  // The problem shown once something is typed; an empty field asks for nothing.
  const problem = $derived(draft && parsed.problem ? t(`form.ids.problem.${parsed.problem}`) : '')
  const same = (a, b) => a && b && a.length === b.length && a.every((x, i) => x === b[i])
  const pathKey = path => path.join('.')

  // Text put at the caret, or in place of the selection, keeping the caret after it.
  function insert(text) {
    const at = field?.selectionStart ?? draft.length, to = field?.selectionEnd ?? at
    draft = draft.slice(0, at) + text + draft.slice(to)
    chosen = null
    queueMicrotask(() => { field?.focus(); field?.setSelectionRange(at + text.length, at + text.length) })
  }
  function operator(op) {
    if (tree && chosen) { draft = write(replaceAt(tree, chosen, wrap(op, partAt(tree, chosen)))); chosen = [...chosen, 1] }
    else insert(op)
  }
  function component(item) {
    query = ''
    if (tree && chosen) { draft = write(replaceAt(tree, chosen, item.char)); chosen = null }
    else insert(item.char)
  }
  function choose(path) { chosen = same(chosen, path) ? null : path }
  function use() { if (tree && !disabled) onuse(draft) }
</script>

{#snippet node(part, path)}
  {#if typeof part === 'string'}
    <button type="button" class="ids-part" class:chosen={same(chosen, path)} aria-pressed={same(chosen, path)}
      title={t('form.ids.choose', { part })} onclick={() => choose(path)}>{part}</button>
  {:else}
    <span class="ids-node" class:chosen={same(chosen, path)}>
      <button type="button" class="ids-op" aria-pressed={same(chosen, path)} title={t('form.ids.choose', { part: write(part) })}
        onclick={() => choose(path)}>{part.op}</button>
      {#each part.parts as inner, i (pathKey([...path, i]))}{@render node(inner, [...path, i])}{/each}
    </span>
  {/if}
{/snippet}

<div class="ids-composer">
  <label class="ids-field">
    <span class="ids-title">{t('form.ids.label')}</span>
    <input bind:this={field} bind:value={draft} {disabled} lang="ja" spellcheck="false" autocomplete="off"
      placeholder="⿰⿱匕失⿱コ疋" aria-invalid={Boolean(problem)} oninput={() => chosen = null}
      onkeydown={event => { if (event.key === 'Enter' && !event.isComposing) { event.preventDefault(); use() } }} />
  </label>
  <div class="ids-operators" role="group" aria-label={t('form.ids.operators')}>
    {#each OPERATORS as [op] (op)}
      <button type="button" class="ids-key" {disabled} title={t('form.ids.operator', { op })} onclick={() => operator(op)}>{op}</button>
    {/each}
  </div>
  {#if tree}
    <div class="ids-tree" role="group" aria-label={t('form.ids.structure')}>{@render node(tree, [])}</div>
  {/if}
  {#if problem}<p class="ids-problem" role="status">{problem}</p>{/if}
  <div class="ids-search">
    <span class="ids-title">{chosen && tree ? t('form.ids.replace', { part: write(partAt(tree, chosen)) }) : t('form.ids.insert')}</span>
    <CharacterSearch compact codePoints bind:value={query} label={t('form.ids.search')} placeholder={t('search.placeholder')} onselect={component} />
  </div>
  <div class="ids-actions">
    <button type="button" class="ids-use" disabled={disabled || !tree} onclick={use}>{t('form.ids.use')}</button>
  </div>
</div>

<style>
  .ids-composer{display:flex;flex-direction:column;gap:10px}
  .ids-field{display:flex;flex-direction:column;gap:4px}
  .ids-title{font-size:11px;color:var(--muted)}
  .ids-field input{font-size:20px;padding:6px 8px;font-family:"GenZui Sans","Klee One","LXGW WenKai TC",serif}
  .ids-field input[aria-invalid="true"]{border-color:var(--fault)}
  .ids-operators{display:flex;flex-wrap:wrap;gap:4px}
  .ids-key{min-width:34px;padding:3px 6px;font-size:18px;line-height:1.2}
  .ids-tree{display:flex;flex-wrap:wrap;align-items:center;font-size:20px;line-height:1.3;padding:6px;border:1px dashed var(--line);border-radius:8px}
  .ids-node{display:inline-flex;align-items:center;gap:1px;padding:1px 3px;margin:0 1px;border:1px solid var(--line);border-radius:6px}
  .ids-tree > .ids-node{border-color:transparent}
  .ids-node.chosen{background:var(--accent-light)}
  .ids-part,.ids-op{padding:1px 4px;font-size:20px;line-height:1.2;border-color:transparent;background:none;
    font-family:"GenZui Sans","Klee One","LXGW WenKai TC",serif}
  .ids-op{color:var(--muted)}
  .ids-part:hover,.ids-op:hover{border-color:var(--line-strong)}
  .ids-part.chosen{border:2px solid var(--accent);padding:0 3px;background:var(--accent-light)}
  .ids-problem{margin:0;font-size:12px;color:var(--fault)}
  .ids-search{display:flex;flex-direction:column;gap:4px}
  .ids-search :global(.character-search){width:100%;min-width:0}
  .ids-actions{display:flex;justify-content:flex-end}
  .ids-use{padding:5px 14px}
</style>
