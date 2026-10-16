<template>
  <div class="tl">
    <div class="legend">
      <span v-for="t in legendTypes" :key="t" class="lg"><i :style="{ background: TYPE_COLORS[t] }"></i>{{ t === 'LLM' ? 'LLM 推理' : t }}</span>
      <span class="lg"><i class="est"></i>工具耗时为估算（两次 LLM 调用间隔）</span>
      <span class="lg"><i class="err"></i>工具报错</span>
      <span class="lg"><el-icon color="#d97706"><WarningFilled /></el-icon>输出疑似报错</span>
    </div>
    <div class="axis">
      <div class="label-col"></div>
      <div class="track">
        <span v-for="t in ticks" :key="t" class="tick" :style="{ left: pct(t) }">{{ fmtMs(t) }}</span>
      </div>
      <div class="dur-col"></div>
    </div>
    <div class="rows">
      <div v-for="ev in sorted" :key="ev.kind + ev.ref" class="tl-row" :class="{ active: activeKey === ev.kind + ev.ref }"
           @click="$emit('select', ev)" @mouseenter="hover = ev" @mouseleave="hover = null">
        <div class="label-col">
          <span class="idx">{{ ev.kind === 'llm' ? 'L' : 'T' }}{{ ev.ref }}</span>
          <span class="ellipsis name" :title="ev.label">{{ ev.kind === 'llm' ? 'LLM 推理' : ev.label }}</span>
          <el-tag v-if="ev.step" size="small" effect="plain" class="step">{{ ev.step }}</el-tag>
        </div>
        <div class="track">
          <span v-for="t in ticks" :key="t" class="grid" :style="{ left: pct(t) }"></span>
          <div class="bar" :class="{ est: ev.estimated, err: ev.error }"
               :style="{ left: pct(ev.start_ms), width: `max(3px, ${pctNum(ev.duration_ms)}%)`, background: color(ev) }"></div>
          <el-icon v-if="ev.error || ev.suspect" class="flag" :color="ev.error ? '#dc2626' : '#d97706'"
                   :style="{ left: `calc(${pct(ev.start_ms + ev.duration_ms)} + 4px)` }"><WarningFilled /></el-icon>
        </div>
        <div class="dur-col mono">{{ fmtMs(ev.duration_ms) }}</div>
      </div>
    </div>
    <div v-if="hover" class="hover-tip">
      <b>{{ hover.kind === 'llm' ? `LLM #${hover.ref}` : `工具 #${hover.ref} ${hover.label}` }}</b>
      <span>开始 +{{ fmtMs(hover.start_ms) }}</span>
      <span>耗时 {{ fmtMs(hover.duration_ms) }}{{ hover.estimated ? '（估算）' : '' }}</span>
      <span v-if="hover.tokens">tokens {{ hover.tokens }}</span>
      <span v-if="hover.type">类型 {{ hover.type }}</span>
      <span v-if="hover.step">对齐步骤 {{ hover.step }}</span>
      <span v-if="hover.error" style="color:#fca5a5">报错</span>
      <span v-else-if="hover.suspect" style="color:#fcd34d">输出疑似报错</span>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { TYPE_COLORS, fmtMs } from '../utils'

const props = defineProps({ items: { type: Array, default: () => [] }, activeKey: String })
defineEmits(['select'])
const hover = ref(null)
const sorted = computed(() => [...props.items].sort((a, b) => a.start_ms - b.start_ms || (a.kind === 'llm' ? -1 : 1)))
const total = computed(() => Math.max(1, ...props.items.map(e => e.start_ms + (e.duration_ms || 0))))
const pctNum = v => (100 * (v || 0)) / total.value
const pct = v => `${pctNum(v)}%`
const color = ev => (ev.kind === 'llm' ? TYPE_COLORS.LLM : TYPE_COLORS[ev.type] || TYPE_COLORS.TOOL)
const legendTypes = computed(() => ['LLM', ...new Set(props.items.filter(e => e.kind === 'tool').map(e => e.type))])
const ticks = computed(() => {
  const t = total.value
  const raw = t / 6
  const mag = 10 ** Math.floor(Math.log10(raw))
  const step = [1, 2, 5, 10].map(m => m * mag).find(s => s >= raw)
  const out = []
  for (let v = 0; v <= t; v += step) out.push(v)
  return out
})
</script>

<style scoped>
.tl { position: relative; }
.legend { display: flex; gap: 14px; flex-wrap: wrap; font-size: 12px; color: var(--sl-muted); margin-bottom: 8px; }
.lg { display: inline-flex; align-items: center; gap: 5px; }
.lg i { width: 12px; height: 8px; border-radius: 2px; display: inline-block; }
.lg i.est { background: repeating-linear-gradient(45deg, #94a3b8 0 2px, transparent 2px 4px); border: 1px solid #94a3b8; }
.lg i.err { border: 2px solid #dc2626; background: #fff; }
.axis, .tl-row { display: grid; grid-template-columns: 260px 1fr 70px; align-items: center; }
.axis { height: 20px; font-size: 11px; color: var(--sl-muted); }
.axis .track { position: relative; height: 100%; }
.tick { position: absolute; transform: translateX(-50%); white-space: nowrap; }
.rows { border-top: 1px solid var(--sl-border); }
.tl-row { height: 28px; cursor: pointer; border-bottom: 1px solid #f1f5f9; }
.tl-row:hover, .tl-row.active { background: #f8fafc; }
.label-col { display: flex; align-items: center; gap: 6px; padding-right: 10px; min-width: 0; font-size: 12.5px; }
.idx { font-family: var(--sl-mono); font-size: 11px; color: var(--sl-muted); width: 28px; flex: none; }
.name { flex: 1; }
.step { flex: none; }
.track { position: relative; height: 100%; }
.grid { position: absolute; top: 0; bottom: 0; width: 1px; background: #f1f5f9; }
.bar { position: absolute; top: 8px; height: 12px; border-radius: 4px; }
.bar.est { background-image: repeating-linear-gradient(45deg, rgba(255,255,255,.45) 0 3px, transparent 3px 6px) !important; }
.bar.err { outline: 2px solid #dc2626; outline-offset: 1px; }
.flag { position: absolute; top: 7px; font-size: 14px; }
.dur-col { text-align: right; font-size: 12px; color: var(--sl-muted); }
.hover-tip { position: sticky; bottom: 0; display: flex; gap: 14px; flex-wrap: wrap; font-size: 12px; background: #0f172a; color: #e2e8f0; padding: 6px 12px; border-radius: 8px; margin-top: 8px; }
</style>
