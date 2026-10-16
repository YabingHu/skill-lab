<template>
  <div class="page">
    <div class="page-title">环境 <span class="sub">pod 列表、环境信息脚本、在 pod 内执行命令、当前配置</span></div>
    <div class="grid">
      <div class="card">
        <div class="card-title">Pod
          <el-input v-model="filter" size="small" placeholder="包含字符串" style="width:200px" clearable @keyup.enter="loadPods" />
          <el-button size="small" :icon="Search" :loading="podsLoading" @click="loadPods">查询</el-button>
        </div>
        <el-alert v-if="podsErr" type="error" :title="podsErr" :closable="false" />
        <div class="muted mono small" v-if="podsCmd">$ {{ podsCmd }}</div>
        <el-table :data="pods" size="small" max-height="320" highlight-current-row @current-change="p => p && (execForm.pod = p.name)">
          <el-table-column prop="name" label="名称" />
          <el-table-column label="状态" width="110"><template #default="{ row }"><el-tag size="small" :type="row.status === 'Running' ? 'success' : 'warning'" effect="plain">{{ row.status }}</el-tag></template></el-table-column>
        </el-table>
      </div>
      <div class="card">
        <div class="card-title">在 pod 内执行命令</div>
        <div class="row" style="margin-bottom:8px">
          <el-select v-model="execForm.pod" filterable placeholder="选择 pod" style="width:220px">
            <el-option v-for="p in pods" :key="p.name" :value="p.name" :label="p.name" />
          </el-select>
          <el-input v-model="execForm.cmd" class="grow mono" placeholder="例如 ls /opt/agent/skills" @keyup.enter="exec" />
          <el-button type="primary" :loading="execLoading" :disabled="!execForm.pod || !execForm.cmd" @click="exec">执行</el-button>
        </div>
        <div class="row quick">
          <span class="muted small">快捷：</span>
          <el-button v-for="q in quick" :key="q" size="small" text type="primary" @click="execForm.cmd = q; exec()">{{ q }}</el-button>
        </div>
        <pre v-if="execOut" class="code" style="margin-top:8px">{{ execOut }}</pre>
      </div>
    </div>

    <div class="card">
      <div class="card-title">环境信息 <span class="muted small mono">{{ cfg.env?.commands?.env_info || '未配置 env.commands.env_info' }}</span>
        <span class="spacer"></span><el-button size="small" :loading="infoLoading" @click="loadInfo">运行</el-button></div>
      <pre v-if="info" class="code">{{ info.output || info.error }}</pre>
    </div>

    <div class="card">
      <div class="card-title">LLM <span class="spacer"></span><el-button size="small" :loading="pingLoading" @click="ping">测试连接</el-button></div>
      <div class="kv mono small">
        <span>LLM_BASE_URL</span><span>{{ cfg.llm?.base_url }}</span>
        <span>LLM_MODEL</span><span>{{ cfg.llm?.model }}</span>
        <span>LLM_API_KEY</span><span>{{ cfg.llm?.api_key ? '已配置' : '未配置' }}</span>
      </div>
      <el-alert v-if="pingRes" :type="pingRes.ok ? 'success' : 'error'" :closable="false" style="margin-top:8px"
                :title="pingRes.ok ? `连接成功：${pingRes.reply}` : pingRes.error" />
    </div>

    <div class="card">
      <div class="card-title">当前配置 <span class="muted small mono">{{ cfg._config_path }}</span>
        <span class="spacer"></span><el-button size="small" @click="reload">重新加载配置</el-button></div>
      <pre class="code light">{{ pretty(cfgShown) }}</pre>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, reactive } from 'vue'
import { ElMessage } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { api, qs } from '../api'
import { pretty } from '../utils'

const cfg = ref({})
const filter = ref('')
const pods = ref([])
const podsErr = ref('')
const podsCmd = ref('')
const podsLoading = ref(false)
const execForm = reactive({ pod: '', cmd: '' })
const execOut = ref('')
const execLoading = ref(false)
const info = ref(null)
const infoLoading = ref(false)
const pingRes = ref(null)
const pingLoading = ref(false)
const quick = computed(() => [
  `ls -la ${cfg.value.deploy?.remote_dir || '/opt/agent/skills'}`,
  `ls -la ${(cfg.value.logs?.paths || [])[0] || '/var/log/agent'}`,
  'df -h',
])
const cfgShown = computed(() => Object.fromEntries(Object.entries(cfg.value).filter(([k]) => !k.startsWith('_'))))

async function loadPods() {
  podsLoading.value = true
  try {
    const r = await api.get('/env/pods' + qs({ filter: filter.value }))
    pods.value = r.pods
    podsErr.value = r.ok ? '' : r.error
    podsCmd.value = r.cmd
  } finally { podsLoading.value = false }
}
async function exec() {
  execLoading.value = true
  try {
    const r = await api.post('/env/exec', execForm)
    execOut.value = `$ ${r.cmd}\n(rc=${r.rc})\n${r.stdout}${r.stderr ? '\n[stderr]\n' + r.stderr : ''}`
  } finally { execLoading.value = false }
}
async function loadInfo() {
  infoLoading.value = true
  try { info.value = await api.get('/env/info') } finally { infoLoading.value = false }
}
async function ping() {
  pingLoading.value = true
  try { pingRes.value = await api.get('/llm/ping') } finally { pingLoading.value = false }
}
async function reload() {
  cfg.value = await api.post('/config/reload')
  ElMessage.success('配置已重新加载')
}

onMounted(async () => {
  cfg.value = await api.get('/config')
  filter.value = cfg.value.deploy?.default_pod_filter || ''
  loadPods()
})
</script>

<style scoped>
.grid { display: grid; grid-template-columns: 1fr 1.3fr; gap: 12px; margin-bottom: 12px; }
.grid .card + .card { margin-top: 0; }
.small { font-size: 12px; }
.quick { gap: 2px; }
.kv { display: grid; grid-template-columns: 140px 1fr; gap: 6px; }
.kv > span:nth-child(odd) { color: var(--sl-muted); }
.card-title .spacer { flex: 1; }
@media (max-width: 1100px) { .grid { grid-template-columns: 1fr; } }
</style>
