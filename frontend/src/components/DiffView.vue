<template>
  <div class="diff-wrap">
    <div v-if="labels" class="diff-labels"><span>{{ labels[0] }}</span><span>{{ labels[1] }}</span></div>
    <div ref="el" class="diff-host" :style="{ maxHeight }"></div>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { EditorView, basicSetup } from 'codemirror'
import { EditorState } from '@codemirror/state'
import { MergeView } from '@codemirror/merge'
import { markdown } from '@codemirror/lang-markdown'
import { yamlFrontmatter } from '@codemirror/lang-yaml'

const props = defineProps({
  oldText: { type: String, default: '' },
  newText: { type: String, default: '' },
  labels: { type: Array, default: null },
  maxHeight: { type: String, default: '520px' },
})
const el = ref(null)
let mv = null

function build() {
  if (mv) mv.destroy()
  const ext = [basicSetup, yamlFrontmatter({ content: markdown() }), EditorView.lineWrapping, EditorState.readOnly.of(true), EditorView.editable.of(false),
    EditorView.theme({ '&': { fontSize: '13px' }, '.cm-scroller': { fontFamily: 'var(--sl-mono)' } })]
  mv = new MergeView({
    a: { doc: props.oldText || '', extensions: ext },
    b: { doc: props.newText || '', extensions: ext },
    parent: el.value,
    collapseUnchanged: { margin: 3, minSize: 6 },
    gutter: true,
  })
}
onMounted(build)
onBeforeUnmount(() => mv && mv.destroy())
watch(() => [props.oldText, props.newText], build)
</script>

<style scoped>
.diff-wrap { border: 1px solid var(--sl-border); border-radius: 8px; overflow: hidden; background: #fff; }
.diff-labels { display: grid; grid-template-columns: 1fr 1fr; font-size: 12px; color: var(--sl-muted); background: #f8fafc; border-bottom: 1px solid var(--sl-border); }
.diff-labels span { padding: 5px 10px; }
.diff-labels span + span { border-left: 1px solid var(--sl-border); }
.diff-host { overflow: auto; }
</style>
