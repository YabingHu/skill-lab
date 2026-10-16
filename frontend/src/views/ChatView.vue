<template>
  <div class="page">
    <div class="page-title">问答调试 <span class="sub">向环境提问，可注入故障、自定义 Header，自动记录 conversation-id</span></div>
    <div class="grid">
      <div class="card">
        <el-form label-position="top" size="default">
          <el-form-item label="问答接口">
            <el-select v-model="form.endpoint" style="width:100%">
              <el-option v-for="e in options.endpoints" :key="e.name" :value="e.name" :label="e.name">
                <span>{{ e.name }}</span><span class="muted mono" style="float:right">{{ e.method || 'POST' }} {{ e.url }}</span>
              </el-option>
            </el-select>
          </el-form-item>
          <el-form-item label="问题">
            <el-input v-model="form.question" type="textarea" :rows="4" placeholder="输入要问环境的问题，Ctrl+Enter 发送"
                      @keydown.ctrl.enter="send" @keydown.meta.enter="send" />
          </el-form-item>
          <el-form-item label="故障注入">
            <div class="faults">
              <div v-for="f in options.faults" :key="f.name" class="fault" :class="{ on: form.faults.includes(f.name) }" @click="toggleFault(f.name)">
                <el-checkbox :model-value="form.faults.includes(f.name)" @click.stop @change="toggleFault(f.name)" />
                <div class="grow">
                  <div class="fname">{{ f.label || f.name }} <span class="muted mono">{{ f.name }}</span></div>
                  <div class="muted fdesc">{{ f.description }}</div>
                  <div class="mono fh">
                    <span v-for="(v, k) in f.headers" :key="k">{{ k }}: {{ v }}</span>
                    <span v-if="f.pod_cmd">pod 命令: {{ f.pod_cmd }}</span>
                  </div>
                </div>
              </div>
              <div v-if="!options.faults.length" class="muted">未配置故障（config.yaml → chat.faults）</div>
            </div>
          </el-form-item>
          <el-form-item label="自定义 Header">
            <KeyValueEditor v-model="form.headers" style="width:100%" />
          </el-form-item>
          <el-collapse class="adv">
            <el-collapse-item title="高级选项">
              <el-form-item label="备注（便于在列表里区分本次实验）"><el-input v-model="form.note" /></el-form-item>
              <el-form-item label="请求体覆盖（JSON，可用 {question} 占位符；留空使用配置模板）">
                <el-input v-model="form.body_override" type="textarea" :rows="3" class="mono" placeholder='{"query": "{question}", "stream": false}' />
              </el-form-item>
              <el-form-item label="故障命令作用的 pod（包含字符串）"><el-input v-model="form.pod_filter" placeholder="仅对配置了 pod_cmd 的故障生效" /></el-form-item>
              <el-form-item label="重复提问次数（用于稳定性对比）"><el-input-number v-model="repeat" :min="1" :max="50" /></el-form-item>
            </el-collapse-item>
          </el-collapse>
          <div class="row" style="justify-content:flex-end; margin-top:12px">
            <el-button :icon="View" @click="previewReq">预览请求</el-button>
            <el-button type="primary" :icon="Promotion" :loading="sending" :disabled="!form.question" @click="send">
              发送{{ repeat > 1 ? `（×${repeat}）` : '' }}
            </el-button>
          </div>
        </el-form>
      </div>

      <div class="card result">
        <div class="card-title">结果 <span v-if="progress" class="muted">{{ progress }}</span></div>
        <el-empty v-if="!result && !sending" description="发送问题后在这里查看回答" :image-size="80" />
        <div v-if="sending && !result" class="muted" style="padding:30px;text-align:center"><el-icon class="is-loading"><Loading /></el-icon> 等待环境响应…</div>
        <template v-if="result">
          <div class="meta">
            <el-tag :type="result.error || (result.status_code >= 400) ? 'danger' : 'success'" effect="dark">{{ result.error ? '请求失败' : 'HTTP ' + result.status_code }}</el-tag>
            <span class="muted">耗时 {{ fmtMs(result.latency_ms) }}</span>
            <el-tag v-for="f in result.faults" :key="f" type="warning" size="small" effect="plain">故障：{{ f }}</el-tag>
          </div>
          <div class="conv">
            <span class="muted">conversation-id</span>
            <template v-if="result.conversation_id">
              <span class="mono cid">{{ result.conversation_id }}</span>
              <el-button link size="small" :icon="CopyDocument" @click="copy(result.conversation_id)" />
            </template>
            <span v-else class="warn">未从响应中提取到，可手动填写 →</span>
            <el-button link size="small" type="primary" @click="editConv(result)">编辑</el-button>
          </div>
          <div class="row" style="margin:6px 0 10px">
            <el-button size="small" type="primary" plain :icon="Download" :disabled="!result.conversation_id" :loading="pulling" @click="pullLogs(result)">拉取该会话日志</el-button>
            <el-button size="small" type="success" plain :icon="DataAnalysis" :disabled="!result.conversation_id" @click="goAnalysis(result)">分析执行过程</el-button>
          </div>
          <el-alert v-if="result.error" type="error" :title="result.error" :closable="false" />
          <div v-if="result.answer" class="answer" v-html="render(result.answer)"></div>
          <el-collapse>
            <el-collapse-item title="请求详情">
              <pre class="code light">{{ pretty(result.request) }}</pre>
            </el-collapse-item>
            <el-collapse-item title="响应头 / 原始响应">
              <pre class="code light">{{ pretty(result.response_headers) }}</pre>
              <pre class="code light" style="margin-top:8px">{{ pretty(result.response_text) }}</pre>
            </el-collapse-item>
          </el-collapse>
        </template>
      </div>
    </div>

    <div class="card">
      <div class="card-title">
        提问记录
        <el-input v-model="q" size="small" placeholder="搜索问题 / conversation-id / 备注" clearable style="width:280px" @change="loadHistory" />
        <span class="spacer"></span>
        <el-button size="small" text :icon="Refresh" @click="loadHistory" />
      </div>
      <el-table :data="history" size="small" @row-click="r => openRecord(r)" row-class-name="clickable">
        <el-table-column label="时间" width="120"><template #default="{ row }">{{ fmtTime(row.created_at) }}</template></el-table-column>
        <el-table-column prop="question" label="问题" min-width="220" show-overflow-tooltip />
        <el-table-column label="故障" width="150">
          <template #default="{ row }"><el-tag v-for="f in row.faults" :key="f" size="small" type="warning" effect="plain" style="margin-right:4px">{{ f }}</el-tag></template>
        </el-table-column>
        <el-table-column label="conversation-id" min-width="180">
          <template #default="{ row }"><span class="mono">{{ row.conversation_id || '-' }}</span></template>
        </el-table-column>
        <el-table-column prop="note" label="备注" width="120" show-overflow-tooltip />
        <el-table-column label="状态" width="76">
          <template #default="{ row }"><el-tag size="small" :type="row.error || row.status_code >= 400 ? 'danger' : 'success'">{{ row.error ? '失败' : row.status_code }}</el-tag></template>
        </el-table-column>
        <el-table-column label="耗时" width="80"><template #default="{ row }">{{ fmtMs(row.latency_ms) }}</template></el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button link size="small" type="primary" @click.stop="reuse(row)">再问一次</el-button>
            <el-button link size="small" type="success" :disabled="!row.conversation_id" @click.stop="goAnalysis(row)">分析</el-button>
            <el-button link size="small" @click.stop="editConv(row)">改 ID</el-button>
            <el-button link size="small" type="danger" @click.stop="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="previewVisible" title="将要发送的请求" width="680px">
      <pre class="code">{{ pretty(previewData) }}</pre>
    </el-dialog>
  </div>
</template>

<script setup>
defineOptions({ name: 'ChatView' })
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { CopyDocument, DataAnalysis, Download, Promotion, Refresh, View } from '@element-plus/icons-vue'
import { marked } from 'marked'
import { api, pollJob, qs } from '../api'
import { copyText, fmtMs, fmtTime, pretty } from '../utils'
import KeyValueEditor from '../components/KeyValueEditor.vue'

const router = useRouter()
const options = ref({ endpoints: [], faults: [] })
const form = reactive({ endpoint: '', question: '', faults: [], headers: [], note: '', body_override: '', pod_filter: '' })
const repeat = ref(1)
const sending = ref(false)
const progress = ref('')
const result = ref(null)
const history = ref([])
const q = ref('')
const pulling = ref(false)
const previewVisible = ref(false)
const previewData = ref(null)

const render = md => marked.parse(md || '')
function toggleFault(n) {
  const i = form.faults.indexOf(n)
  if (i >= 0) form.faults.splice(i, 1)
  else form.faults.push(n)
}
function payload() {
  return { ...form, headers: form.headers.filter(h => h.key), body_override: form.body_override.trim() || null }
}
async function previewReq() {
  const r = await api.post('/chat/preview', payload())
  previewData.value = r.request
  previewVisible.value = true
}
async function send() {
  if (!form.question || sending.value) return
  sending.value = true
  result.value = null
  try {
    for (let i = 0; i < repeat.value; i++) {
      progress.value = repeat.value > 1 ? `第 ${i + 1}/${repeat.value} 次` : ''
      result.value = await api.post('/chat/ask', payload())
      loadHistory()
    }
  } finally {
    sending.value = false
    progress.value = ''
  }
}
async function loadHistory() { history.value = await api.get('/chats' + qs({ q: q.value })) }
async function openRecord(row) { result.value = await api.get('/chats/' + row.id) }
function reuse(row) {
  form.question = row.question
  form.faults = [...(row.faults || [])]
  form.endpoint = row.endpoint
  const fh = new Set(options.value.faults.filter(f => form.faults.includes(f.name)).flatMap(f => Object.keys(f.headers || {})))
  api.get('/chats/' + row.id).then(r => {
    const ep = options.value.endpoints.find(e => e.name === row.endpoint) || {}
    const base = new Set(Object.keys(ep.headers || {}))
    form.headers = Object.entries(r.request?.headers || {})
      .filter(([k]) => !base.has(k) && !fh.has(k) && k !== ep.send_conversation_id_header)
      .map(([key, value]) => ({ key, value }))
  })
  window.scrollTo({ top: 0 })
}
async function editConv(row) {
  try {
    const { value } = await ElMessageBox.prompt('conversation-id', '编辑 conversation-id', { inputValue: row.conversation_id || '' })
    const r = await api.patch('/chats/' + row.id, { conversation_id: value })
    if (result.value?.id === row.id) result.value = r
    loadHistory()
  } catch { /* cancel */ }
}
async function remove(row) {
  try { await ElMessageBox.confirm('删除这条提问记录？', '提示', { type: 'warning' }) } catch { return }
  await api.del('/chats/' + row.id)
  if (result.value?.id === row.id) result.value = null
  loadHistory()
}
async function pullLogs(row) {
  pulling.value = true
  try {
    const { job_id } = await api.post('/logs/pull', { conversation_id: row.conversation_id })
    const j = await pollJob(job_id)
    if (j.status === 'success') ElMessage.success(j.logs[j.logs.length - 1].replace(/^\[.*?\]\s*/, ''))
    else ElMessage.error('拉取失败，请到「会话日志」页查看详情')
  } finally { pulling.value = false }
}
function goAnalysis(row) { router.push('/analysis/' + encodeURIComponent(row.conversation_id)) }
async function copy(t) { await copyText(t); ElMessage.success('已复制') }

onMounted(async () => {
  options.value = await api.get('/chat/options')
  form.endpoint = options.value.endpoints[0]?.name || ''
  loadHistory()
})
</script>

<style scoped>
.grid { display: grid; grid-template-columns: minmax(380px, 1fr) 1.2fr; gap: 12px; margin-bottom: 12px; align-items: start; }
.grid .card + .card { margin-top: 0; }
.faults { display: flex; flex-direction: column; gap: 6px; width: 100%; }
.fault { display: flex; gap: 10px; border: 1px solid var(--sl-border); border-radius: 8px; padding: 6px 10px; cursor: pointer; }
.fault.on { border-color: #fdba74; background: #fff7ed; }
.fname { font-weight: 500; line-height: 1.5; }
.fdesc { font-size: 12px; line-height: 1.5; }
.fh { font-size: 11.5px; color: #9a3412; display: flex; gap: 10px; flex-wrap: wrap; }
.adv { border-top: none; }
.result { min-height: 300px; }
.meta { display: flex; gap: 10px; align-items: center; margin-bottom: 8px; }
.conv { display: flex; gap: 8px; align-items: center; background: #f8fafc; border: 1px solid var(--sl-border); border-radius: 8px; padding: 6px 10px; }
.cid { font-weight: 600; }
.warn { color: #d97706; font-size: 13px; }
.answer { border: 1px solid var(--sl-border); border-radius: 8px; padding: 2px 14px; margin-bottom: 10px; max-height: 420px; overflow: auto; }
:deep(.clickable) { cursor: pointer; }
@media (max-width: 1100px) { .grid { grid-template-columns: 1fr; } }
</style>
