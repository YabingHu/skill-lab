<template>
  <div class="page">
    <div class="page-title">发布 skill 到环境 <span class="sub">打包指定版本，复制到名称包含关键字的 pod 并执行安装命令</span></div>
    <div class="grid">
      <div class="card">
        <div class="card-title">1. 选择版本</div>
        <el-form label-width="84px" size="default">
          <el-form-item label="Skill">
            <el-select v-model="form.skill" filterable placeholder="选择 skill" style="width:100%" @change="loadVersions">
              <el-option v-for="s in skills" :key="s.name" :label="s.name" :value="s.name" />
            </el-select>
          </el-form-item>
          <el-form-item label="版本">
            <el-select v-model="form.version" style="width:100%">
              <el-option label="当前工作区（自动保存为新版本）" :value="0" />
              <el-option v-for="v in versions" :key="v.seq" :value="v.seq" :label="`v${v.seq} · ${v.message || ''}`">
                <span>v{{ v.seq }}</span>
                <span class="muted" style="margin-left:8px">{{ v.message }}</span>
                <span class="muted" style="float:right">{{ fmtTime(v.created_at) }}<template v-if="v.deploy_count"> · 已发布 {{ v.deploy_count }} 次</template></span>
              </el-option>
            </el-select>
          </el-form-item>
          <el-form-item label="语言">
            <el-checkbox-group v-model="form.langs">
              <el-checkbox value="zh">中文目录</el-checkbox>
              <el-checkbox value="en">英文目录</el-checkbox>
            </el-checkbox-group>
          </el-form-item>
        </el-form>
        <div class="card-title" style="margin-top:6px">发布命令 <span class="muted" style="font-weight:400">（config.yaml → deploy）</span></div>
        <div class="cmds mono">
          <div><span class="k">打包</span>{{ cfg.deploy?.package_cmd || '内置 tar.gz（未配置 package_cmd）' }}</div>
          <div><span class="k">复制</span>{{ cfg.env?.commands?.cp_to }}</div>
          <div><span class="k">安装</span>{{ cfg.deploy?.install_cmd }}</div>
          <div v-if="cfg.deploy?.post_cmd"><span class="k">安装后</span>{{ cfg.deploy?.post_cmd }}</div>
        </div>
      </div>

      <div class="card">
        <div class="card-title">2. 选择 pod</div>
        <div class="row" style="margin-bottom:10px">
          <el-input v-model="form.namespace" placeholder="namespace" style="width:160px">
            <template #prepend>ns</template>
          </el-input>
          <el-input v-model="form.pod_filter" placeholder="pod 名包含的字符串，如 agent" class="grow" clearable @keyup.enter="queryPods">
            <template #prepend>包含</template>
          </el-input>
          <el-button :icon="Search" :loading="podsLoading" @click="queryPods">查询</el-button>
        </div>
        <div class="muted tip">多个关键字：逗号分隔表示「或」，空格分隔表示「且」，<code>!xxx</code> 表示排除</div>
        <el-alert v-if="podsError" type="error" :title="podsError" :closable="false" style="margin:8px 0" />
        <el-table ref="podTable" :data="pods" size="small" max-height="300" @selection-change="s => selected = s.map(p => p.name)">
          <el-table-column type="selection" width="40" />
          <el-table-column prop="name" label="Pod" />
          <el-table-column label="状态" width="110">
            <template #default="{ row }"><el-tag size="small" :type="row.status === 'Running' ? 'success' : 'warning'" effect="plain">{{ row.status || '-' }}</el-tag></template>
          </el-table-column>
        </el-table>
        <div class="row" style="margin-top:12px; justify-content:flex-end">
          <span class="muted">已选 {{ selected.length }} 个 pod</span>
          <el-button type="primary" :icon="Upload" :disabled="!canDeploy" :loading="job?.status === 'running'" @click="deploy">
            发布到选中的 pod
          </el-button>
        </div>
      </div>
    </div>

    <div v-if="job" class="card"><JobLog :job="job" /></div>

    <div class="card">
      <div class="card-title">发布记录 <span class="spacer"></span><el-button size="small" text :icon="Refresh" @click="loadHistory" /></div>
      <el-table :data="history" size="small">
        <el-table-column type="expand">
          <template #default="{ row }">
            <div style="padding:0 20px">
              <div v-for="p in row.pods" :key="p.pod" class="mono">
                <el-icon :color="p.ok ? '#16a34a' : '#dc2626'"><component :is="p.ok ? 'CircleCheck' : 'CircleClose'" /></el-icon>
                {{ p.pod }}：{{ p.message || '-' }}
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="时间" width="130"><template #default="{ row }">{{ fmtTime(row.created_at) }}</template></el-table-column>
        <el-table-column prop="skill" label="Skill" />
        <el-table-column label="版本" width="70"><template #default="{ row }">v{{ row.version_seq }}</template></el-table-column>
        <el-table-column prop="langs" label="语言" width="80" />
        <el-table-column prop="pod_filter" label="pod 过滤" width="120" />
        <el-table-column label="结果" width="100">
          <template #default="{ row }">{{ row.pods.filter(p => p.ok).length }}/{{ row.pods.length }}</template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }"><el-tag size="small" :type="statusType[row.status]">{{ statusText[row.status] || row.status }}</el-tag></template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, Search, Upload } from '@element-plus/icons-vue'
import { api, pollJob, qs } from '../api'
import { fmtTime } from '../utils'
import JobLog from '../components/JobLog.vue'

const route = useRoute()
const cfg = ref({})
const skills = ref([])
const versions = ref([])
const pods = ref([])
const podsError = ref('')
const podsLoading = ref(false)
const selected = ref([])
const podTable = ref(null)
const job = ref(null)
const history = ref([])
const form = reactive({ skill: '', version: 0, langs: ['zh', 'en'], namespace: '', pod_filter: '' })
const statusType = { success: 'success', partial: 'warning', failed: 'danger', running: 'primary' }
const statusText = { success: '成功', partial: '部分成功', failed: '失败', running: '执行中' }
const canDeploy = computed(() => form.skill && selected.value.length && form.langs.length && job.value?.status !== 'running')

async function loadVersions() {
  if (!form.skill) return
  versions.value = await api.get(`/skills/${encodeURIComponent(form.skill)}/versions`)
  form.version = versions.value[0]?.seq || 0
}

async function queryPods() {
  podsLoading.value = true
  podsError.value = ''
  try {
    const r = await api.get('/env/pods' + qs({ filter: form.pod_filter, namespace: form.namespace }))
    if (!r.ok) { podsError.value = r.error; pods.value = []; return }
    pods.value = r.pods
    await nextTick()
    pods.value.forEach(p => podTable.value?.toggleRowSelection(p, p.status === 'Running' || !p.status))
  } finally { podsLoading.value = false }
}

async function deploy() {
  try {
    await ElMessageBox.confirm(`将 ${form.skill} ${form.version ? 'v' + form.version : '当前工作区'}（${form.langs.join('/')}）发布到 ${selected.value.length} 个 pod：\n${selected.value.join('\n')}`,
      '确认发布', { type: 'warning' })
  } catch { return }
  const r = await api.post('/deploy', {
    skill: form.skill, version: form.version || null, langs: form.langs, pods: selected.value,
    namespace: form.namespace, pod_filter: form.pod_filter,
  })
  const final = await pollJob(r.job_id, j => { job.value = j })
  ElMessage[final.status === 'success' ? 'success' : 'error'](final.status === 'success' ? '发布完成' : '发布存在失败，请查看日志')
  loadHistory()
  loadVersions()
}

async function loadHistory() { history.value = await api.get('/deployments') }

onMounted(async () => {
  cfg.value = await api.get('/config')
  form.namespace = cfg.value.env.namespace
  form.pod_filter = cfg.value.deploy.default_pod_filter || ''
  skills.value = await api.get('/skills')
  form.skill = route.query.skill || skills.value[0]?.name || ''
  await loadVersions()
  loadHistory()
  if (form.pod_filter) queryPods()
})
</script>

<style scoped>
.grid { display: grid; grid-template-columns: 1fr 1.2fr; gap: 12px; margin-bottom: 12px; }
.grid .card + .card { margin-top: 0; }
.cmds { background: #f8fafc; border: 1px solid var(--sl-border); border-radius: 8px; padding: 8px 10px; line-height: 1.8; word-break: break-all; }
.cmds .k { display: inline-block; width: 52px; color: var(--sl-muted); font-family: inherit; }
.tip { font-size: 12px; margin-bottom: 6px; }
.tip code { background: #f1f5f9; padding: 0 4px; border-radius: 3px; }
@media (max-width: 1100px) { .grid { grid-template-columns: 1fr; } }
</style>
