<template>
  <div class="joblog">
    <div class="head">
      <el-tag :type="tagType" size="small" effect="dark">{{ statusText }}</el-tag>
      <span class="title">{{ job?.title }}</span>
      <el-icon v-if="job?.status === 'running'" class="is-loading"><Loading /></el-icon>
    </div>
    <pre ref="pre" class="code" :style="{ maxHeight: height }">{{ (job?.logs || []).join('\n') || '等待输出…' }}</pre>
  </div>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'

const props = defineProps({ job: Object, height: { type: String, default: '320px' } })
const pre = ref(null)
const tagType = computed(() => ({ running: 'primary', success: 'success', failed: 'danger' }[props.job?.status] || 'info'))
const statusText = computed(() => ({ running: '执行中', success: '成功', failed: '失败' }[props.job?.status] || '未开始'))
watch(() => props.job?.logs?.length, async () => {
  await nextTick()
  if (pre.value) pre.value.scrollTop = pre.value.scrollHeight
})
</script>

<style scoped>
.head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.title { font-weight: 500; }
</style>
