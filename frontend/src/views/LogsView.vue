<template>
  <div class="page">
    <div class="page-title">会话日志 <span class="sub">从环境 pod 拉取 llm-calls.jsonl，按 conversation-id 建立索引</span></div>
    <div class="grid">
      <div class="card">
        <div class="card-title">拉取日志</div>
        <el-form label-width="130px" size="default">
          <el-form-item label="pod 包含">
            <el-input v-model="form.pod_filter" placeholder="pod 名包含的字符串，如 agent" />
          </el-form-item>
          <el-form-item label="namespace"><el-input v-model="form.namespace" /></el-form-item>
          <el-form-item label="conversation-id">
            <el-input v-model="form.conversation_id" placeholder="可选：只拉取包含该 ID 的行（pod 内 grep，数据量小）" clearable />
          </el-form-item>
          <el-form-item label="日志目录">
            <el-select v-model="form.paths" multiple filterable allow-create default-first-option style="width:100%" placeholder="回车添加目录" />
          </el-form-item>
          <el-form-item label="文件名匹配"><el-input v-model="form.glob" class="mono" /></el-form-item>
          <div class="row" style="justify-content:flex-end">
            <el-upload :show-file-list="false" :http-request="upload" accept=".jsonl,.json,.log,.txt">
              <el-button :icon="UploadFilled">导入本地 jsonl</el-button>
            </el-upload>
            <el-button type="primary" :icon="Download" :loading="job?.status === 'running'" @click="pull">拉取</el-button>
          </div>
        </el-form>
      </div>
      <div class="card">
        <div class="kpi-grid" style="margin-bottom:12px">
          <div class="kpi"><div class="label">日志记录</div><div class="value">{{ fmtNum(stats.records || 0) }}</div></div>
          <div class="kpi"><div class="label">会话数</div><div class="value">{{ stats.conversations || 0 }}</div></div>
          <div class="kpi"><div class="label">来源 pod</div><div class="value">{{ stats.pods || 0 }}</div></div>
          <div class="kpi"><div class="label">最近拉取</div><div class="value small">{{ fmtTime(stats.last_fetch) }}</div></div>
        </div>
        <JobLog v-if="job" :job="job" height="200px" />
        <div v-else class="muted help">
          <p>· 拉取时在每个匹配的 pod 内执行 <code>find 目录 -name 文件名</code>，再通过 exec <code>cat</code>（或按 ID <code>grep</code>）读回本地。</p>
          <p>· 每行日志按内容去重，重复拉取不会产生重复记录。</p>
          <p>· conversation-id 字段位置在 config.yaml 的 <code>logs.conversation_id_paths</code> 里配置。</p>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-title">
        已拉取的原始文件
        <span class="muted small-note">与 pod 上的文件内容一致，未做任何处理</span>
        <span class="spacer"></span>
        <el-input v-model="dlConv" size="small" placeholder="输入 conversation-id 下载对应日志行" clearable style="width:280px"
                  @keyup.enter="dlByConv" />
        <el-button size="small" :icon="Download" :disabled="!dlConv.trim()" @click="dlByConv">按 ID 下载</el-button>
        <el-button size="small" type="primary" :icon="Files" :disabled="!files.length" @click="download('/logs/files/archive', 'llm-calls.zip')">全部打包下载</el-button>
      </div>
      <el-table :data="files" size="small" max-height="320" empty-text="还没有拉取过日志">
        <el-table-column prop="pod" label="pod" width="190" show-overflow-tooltip />
        <el-table-column label="pod 内路径" min-width="260"><template #default="{ row }"><span class="mono">{{ row.remote }}</span></template></el-table-column>
        <el-table-column label="范围" width="220">
          <template #default="{ row }">
            <el-tag v-if="row.conversation_id" size="small" type="info" effect="plain">仅 {{ row.conversation_id }}</el-tag>
            <el-tag v-else size="small" effect="plain">全量</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="大小" width="90"><template #default="{ row }">{{ fmtSize(row.size) }}</template></el-table-column>
        <el-table-column label="拉取时间" width="120"><template #default="{ row }">{{ fmtTime(row.mtime) }}</template></el-table-column>
        <el-table-column label="" width="80" fixed="right">
          <template #default="{ row }">
            <el-button link size="small" type="primary" :icon="Download" @click="download('/logs/files/download?rel=' + encodeURIComponent(row.rel), row.remote.split('/').pop())">下载</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div class="card">
      <div class="card-title">
        会话列表
        <el-input v-model="q" size="small" placeholder="搜索 conversation-id" clearable style="width:260px" @change="load" />
        <span class="spacer"></span>
        <el-button size="small" text :icon="Refresh" @click="load" />
      </div>
      <el-table :data="convs" size="small" @row-click="r => go(r)" row-class-name="clickable">
        <el-table-column label="conversation-id" min-width="200">
          <template #default="{ row }"><span class="mono">{{ row.conversation_id }}</span></template>
        </el-table-column>
        <el-table-column label="问题" min-width="220" show-overflow-tooltip>
          <template #default="{ row }"><span v-if="row.question">{{ row.question }}</span><span v-else class="muted">（非本工具发起）</span></template>
        </el-table-column>
        <el-table-column label="故障" width="140">
          <template #default="{ row }"><el-tag v-for="f in row.faults" :key="f" size="small" type="warning" effect="plain">{{ f }}</el-tag></template>
        </el-table-column>
        <el-table-column label="LLM 调用" width="86">
          <template #default="{ row }">
            <el-tag v-if="!row.calls" size="small" type="info">未拉取</el-tag><span v-else>{{ row.calls }}</span>
          </template>
        </el-table-column>
        <el-table-column label="pod" width="170" show-overflow-tooltip><template #default="{ row }">{{ row.pods.join(', ') }}</template></el-table-column>
        <el-table-column label="开始" width="120"><template #default="{ row }">{{ fmtTime(row.first_ts) }}</template></el-table-column>
        <el-table-column label="时长" width="80"><template #default="{ row }">{{ row.calls ? fmtMs((row.last_ts - row.first_ts) * 1000) : '-' }}</template></el-table-column>
        <el-table-column label="操作" width="220" fixed="right">
          <template #default="{ row }">
            <el-button link size="small" type="primary" :icon="Download" @click.stop="downloadConversation(row.conversation_id)">下载</el-button>
            <el-button link size="small" type="success" @click.stop="go(row)">分析</el-button>
            <el-button link size="small" type="primary" @click.stop="pullOne(row)">重新拉取</el-button>
            <el-button link size="small" type="danger" :disabled="!row.calls" @click.stop="remove(row)">清除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup>
defineOptions({ name: 'LogsView' })
import { onActivated, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Download, Files, Refresh, UploadFilled } from '@element-plus/icons-vue'
import { api, download, downloadConversation, pollJob, qs } from '../api'
import { fmtMs, fmtNum, fmtSize, fmtTime } from '../utils'
import JobLog from '../components/JobLog.vue'

const router = useRouter()
const form = reactive({ pod_filter: '', namespace: '', conversation_id: '', paths: [], glob: '' })
const job = ref(null)
const stats = ref({})
const convs = ref([])
const q = ref('')
const files = ref([])
const dlConv = ref('')
function dlByConv() { if (dlConv.value.trim()) downloadConversation(dlConv.value.trim()) }

async function load() {
  stats.value = await api.get('/logs/stats')
  convs.value = await api.get('/conversations' + qs({ q: q.value }))
  files.value = await api.get('/logs/files')
}
async function pull() {
  const { job_id } = await api.post('/logs/pull', { ...form })
  await pollJob(job_id, j => { job.value = j })
  load()
}
async function pullOne(row) {
  form.conversation_id = row.conversation_id
  await pull()
}
async function upload({ file }) {
  const fd = new FormData()
  fd.append('file', file)
  const r = await api.post('/logs/upload', fd)
  ElMessage.success(`导入 ${r.new_records} 条记录，${r.conversations.length} 个会话`)
  load()
}
async function remove(row) {
  try { await ElMessageBox.confirm(`清除 ${row.conversation_id} 的本地日志与分析结果？（不影响环境上的日志）`, '提示', { type: 'warning' }) } catch { return }
  await api.del('/conversations/' + encodeURIComponent(row.conversation_id))
  load()
}
function go(row) { router.push('/analysis/' + encodeURIComponent(row.conversation_id)) }

onMounted(async () => {
  const cfg = await api.get('/config')
  form.pod_filter = cfg.logs.pod_filter || cfg.deploy.default_pod_filter || ''
  form.namespace = cfg.env.namespace
  form.paths = [...cfg.logs.paths]
  form.glob = cfg.logs.glob
  load()
})
onActivated(load)
</script>

<style scoped>
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 12px; }
.grid .card + .card { margin-top: 0; }
.small-note { font-size: 12px; font-weight: 400; }
.kpi .value.small { font-size: 15px; padding-top: 6px; }
.help { font-size: 13px; line-height: 1.7; }
.help p { margin: 4px 0; }
.help code { background: #f1f5f9; padding: 0 4px; border-radius: 3px; font-size: 12px; }
:deep(.clickable) { cursor: pointer; }
@media (max-width: 1100px) { .grid { grid-template-columns: 1fr; } }
</style>
