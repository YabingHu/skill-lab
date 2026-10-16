<template>
  <div class="kv">
    <div v-for="(row, i) in modelValue" :key="i" class="kv-row">
      <el-input v-model="row.key" placeholder="Header 名" size="small" class="k" />
      <el-input v-model="row.value" placeholder="值" size="small" class="v" />
      <el-button size="small" text type="danger" :icon="Delete" @click="remove(i)" />
    </div>
    <el-button size="small" text type="primary" :icon="Plus" @click="add">添加 Header</el-button>
  </div>
</template>

<script setup>
import { Delete, Plus } from '@element-plus/icons-vue'

const props = defineProps({ modelValue: { type: Array, default: () => [] } })
const emit = defineEmits(['update:modelValue'])
function add() { emit('update:modelValue', [...props.modelValue, { key: '', value: '' }]) }
function remove(i) { const a = [...props.modelValue]; a.splice(i, 1); emit('update:modelValue', a) }
</script>

<style scoped>
.kv-row { display: flex; gap: 6px; margin-bottom: 6px; align-items: center; }
.k { width: 38%; }
.v { flex: 1; }
</style>
