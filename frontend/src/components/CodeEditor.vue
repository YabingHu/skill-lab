<template>
  <div ref="el" class="cm-host"></div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { EditorView, basicSetup } from 'codemirror'
import { EditorState } from '@codemirror/state'
import { markdown } from '@codemirror/lang-markdown'
import { languages } from '@codemirror/language-data'
import { yamlFrontmatter } from '@codemirror/lang-yaml'

const props = defineProps({
  modelValue: { type: String, default: '' },
  readonly: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue', 'save'])
const el = ref(null)
let view = null

function create() {
  const saveKey = EditorView.domEventHandlers({
    keydown(e) {
      if ((e.ctrlKey || e.metaKey) && e.key === 's') { e.preventDefault(); emit('save') }
    },
  })
  view = new EditorView({
    parent: el.value,
    state: EditorState.create({
      doc: props.modelValue,
      extensions: [
        basicSetup,
        yamlFrontmatter({ content: markdown({ codeLanguages: languages }) }),
        EditorView.lineWrapping,
        EditorState.readOnly.of(props.readonly),
        saveKey,
        EditorView.updateListener.of(u => { if (u.docChanged) emit('update:modelValue', u.state.doc.toString()) }),
        EditorView.theme({ '&': { height: '100%', fontSize: '13.5px' }, '.cm-scroller': { fontFamily: 'var(--sl-mono)' } }),
      ],
    }),
  })
}

onMounted(create)
onBeforeUnmount(() => view && view.destroy())
watch(() => props.modelValue, v => {
  if (view && v !== view.state.doc.toString()) {
    view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: v ?? '' } })
  }
})
watch(() => props.readonly, () => { view && view.destroy(); create() })
</script>

<style scoped>
.cm-host { height: 100%; border: 1px solid var(--sl-border); border-radius: 8px; overflow: hidden; background: #fff; }
.cm-host :deep(.cm-editor) { height: 100%; }
.cm-host :deep(.cm-editor.cm-focused) { outline: none; }
</style>
