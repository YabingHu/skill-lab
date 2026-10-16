<template>
  <el-container class="layout">
    <el-aside width="208px" class="aside">
      <div class="brand">
        <div class="logo">SL</div>
        <div>
          <div class="name">Skill Lab</div>
          <div class="desc">skill 调试工作台</div>
        </div>
      </div>
      <el-menu :default-active="active" router background-color="transparent" text-color="#cbd5e1"
               active-text-color="#fff" class="menu">
        <el-menu-item index="/skills"><el-icon><EditPen /></el-icon>Skill 编辑</el-menu-item>
        <el-menu-item index="/deploy"><el-icon><Upload /></el-icon>发布</el-menu-item>
        <el-menu-item index="/chat"><el-icon><ChatDotRound /></el-icon>问答调试</el-menu-item>
        <el-menu-item index="/logs"><el-icon><Document /></el-icon>会话日志</el-menu-item>
        <el-menu-item v-if="FEATURES.analysis" index="/analysis"><el-icon><DataAnalysis /></el-icon>执行分析</el-menu-item>
        <el-menu-item index="/env"><el-icon><Monitor /></el-icon>环境</el-menu-item>
      </el-menu>
      <div class="flow">
        <div class="flow-title">调试流程</div>
        <div v-for="(s, i) in flow" :key="s" class="flow-step"><span>{{ i + 1 }}</span>{{ s }}</div>
      </div>
      <div class="llm" @click="pingLLM">
        <span class="tag-dot" :style="{ background: llm.ok === undefined ? '#94a3b8' : llm.ok ? '#22c55e' : '#ef4444' }"></span>
        <el-tooltip :content="llm.ok ? `${llm.base_url}` : (llm.error || '点击检测')" placement="right">
          <span class="ellipsis">LLM：{{ llm.model || '检测中…' }}</span>
        </el-tooltip>
      </div>
    </el-aside>
    <el-main class="main">
      <router-view v-slot="{ Component }">
        <keep-alive include="ChatView,LogsView">
          <component :is="Component" />
        </keep-alive>
      </router-view>
    </el-main>
  </el-container>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api } from './api'
import { FEATURES } from './utils'

const route = useRoute()
const active = computed(() => '/' + (route.path.split('/')[1] || 'skills'))
const flow = ['修改中文 skill', 'LLM 同步英文', '发布到 pod', '注入故障提问',
  FEATURES.analysis ? '拉取日志' : '拉取 / 下载日志', ...(FEATURES.analysis ? ['分析遵从度'] : [])]
const llm = ref({})
async function pingLLM() {
  llm.value = { model: llm.value.model }
  llm.value = await api.get('/llm/ping', { silent: true }).catch(e => ({ ok: false, error: e.message }))
}
onMounted(pingLLM)
</script>

<style scoped>
.layout { height: 100vh; }
.aside { background: var(--sl-sidebar); display: flex; flex-direction: column; }
.brand { display: flex; gap: 10px; align-items: center; padding: 18px 16px 14px; color: #fff; }
.logo { width: 34px; height: 34px; border-radius: 9px; background: #2563eb; display: grid; place-items: center; font-weight: 700; }
.name { font-weight: 600; font-size: 15px; }
.desc { font-size: 12px; color: #94a3b8; }
.menu { border-right: none; }
.menu :deep(.el-menu-item) { height: 42px; margin: 2px 8px; border-radius: 8px; }
.menu :deep(.el-menu-item.is-active) { background: #1e293b; }
.menu :deep(.el-menu-item:hover) { background: #1e293b; }
.flow { margin: 18px 16px 0; padding-top: 14px; border-top: 1px solid #1e293b; }
.flow-title { font-size: 12px; color: #64748b; margin-bottom: 8px; }
.flow-step { font-size: 12px; color: #94a3b8; display: flex; align-items: center; gap: 8px; padding: 3px 0; }
.flow-step span { width: 16px; height: 16px; border-radius: 50%; border: 1px solid #334155; display: grid; place-items: center; font-size: 10px; }
.llm { margin-top: auto; padding: 12px 16px; font-size: 12px; color: #94a3b8; border-top: 1px solid #1e293b; cursor: pointer; display: flex; align-items: center; }
.main { padding: 0; overflow: hidden; }
</style>
