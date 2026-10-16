<template>
  <div class="skills-page">
    <!-- skill 列表 -->
    <div class="list-pane">
      <div class="list-head">
        <el-input v-model="kw" placeholder="搜索 skill" size="small" clearable :prefix-icon="Search" />
        <el-button size="small" type="primary" :icon="Plus" @click="createVisible = true" />
      </div>
      <div class="list-actions">
        <el-button size="small" :icon="Download" style="width:100%" @click="openPull('all')">从环境拉取 skill</el-button>
      </div>
      <div class="list">
        <div v-for="s in filteredSkills" :key="s.name" class="skill-item" :class="{ active: s.name === name }"
             @click="selectSkill(s.name)">
          <div class="ellipsis sname">{{ s.name }}</div>
          <div class="smeta">
            <span v-if="s.latest_seq">v{{ s.latest_seq }}</span>
            <span v-else class="muted">未建版本</span>
            <el-tag v-if="s.latest_seq && s.en_synced_seq && s.en_synced_seq < s.latest_seq" size="small" type="warning"
                    effect="plain" round>英文可能落后</el-tag>
          </div>
        </div>
        <el-empty v-if="!skills.length" description="skill 目录为空" :image-size="60" />
      </div>
    </div>

    <!-- 编辑区 -->
    <div v-if="detail" class="edit-pane">
      <div class="toolbar">
        <div class="title">
          <span class="sk">{{ name }}</span>
          <el-tag size="small" effect="plain">v{{ detail.latest_seq }}</el-tag>
          <el-tag v-if="dirtyCount" size="small" type="danger" effect="light">{{ dirtyCount }} 个文件未保存</el-tag>
        </div>
        <div class="row">
          <el-radio-group v-model="mode" size="small">
            <el-radio-button value="zh">中文</el-radio-button>
            <el-radio-button value="en">英文</el-radio-button>
            <el-radio-button value="both">中英对照</el-radio-button>
          </el-radio-group>
          <el-switch v-model="preview" size="small" active-text="预览" />
          <el-button size="small" type="primary" :icon="Check" :disabled="!dirtyCount" @click="openSave">保存版本</el-button>
          <el-badge :value="pendingCount" :hidden="!pendingCount" type="warning">
            <el-button size="small" :icon="MagicStick" @click="openSync">LLM 同步英文</el-button>
          </el-badge>
          <el-button size="small" :icon="Clock" @click="openHistory">版本历史</el-button>
          <el-button size="small" :icon="Download" @click="openPull('current')">从环境拉取</el-button>
          <el-button size="small" :icon="Upload" @click="$router.push({ path: '/deploy', query: { skill: name } })">发布</el-button>
        </div>
      </div>

      <el-alert v-if="pendingCount" type="warning" :closable="false" show-icon class="sync-alert">
        <template #title>
          中文有 {{ pendingCount }} 个文件在 v{{ detail.sync_pending.base_seq ?? '-' }} 之后修改，英文版还没同步：
          <span class="mono">{{ detail.sync_pending.items.map(i => i.path).join('、') }}</span>
          <el-button size="small" type="warning" link @click="openSync">让 LLM 检查并生成英文修改 →</el-button>
          <el-button size="small" link @click="markSynced">已手动处理，标记为已对齐</el-button>
        </template>
      </el-alert>

      <div class="body">
        <!-- 文件树 -->
        <div class="files">
          <div class="files-head">
            <span>文件（{{ treeLang === 'zh' ? '中文' : '英文' }}）</span>
            <el-button size="small" text :icon="DocumentAdd" @click="newFile" />
          </div>
          <div v-for="f in currentTree" :key="f.path" class="file" :class="{ active: f.path === path }"
               @click="openFile(f.path)">
            <el-icon><Document /></el-icon>
            <span class="ellipsis grow">{{ f.path }}</span>
            <span v-if="isDirty(treeLang, f.path) || (mode === 'both' && isDirty(otherLang, f.path))" class="dot"></span>
            <el-icon v-if="f.path !== detail.main_file" class="del" @click.stop="deleteFile(f.path)"><Delete /></el-icon>
          </div>
          <div v-for="p in newPaths" :key="'n' + p" class="file active-new" @click="openFile(p)">
            <el-icon><DocumentAdd /></el-icon><span class="ellipsis grow">{{ p }}</span><span class="dot"></span>
          </div>
        </div>

        <!-- 编辑器 -->
        <div class="editors" :class="{ both: mode === 'both' }">
          <template v-for="lang in visibleLangs" :key="lang">
            <div class="editor-col">
              <div class="editor-head">
                <el-tag size="small" :type="lang === 'zh' ? 'danger' : 'primary'" effect="plain">{{ lang === 'zh' ? '中文' : '英文' }}</el-tag>
                <span class="mono">{{ path }}</span>
                <span v-if="isDirty(lang, path)" class="muted">（已修改）</span>
                <span v-if="!exists(lang, path) && !buffers[key(lang, path)]" class="muted">（该语言下不存在此文件）</span>
              </div>
              <div class="editor-box">
                <div v-if="preview" class="md-preview" v-html="render(buffers[key(lang, path)]?.content || '')"></div>
                <CodeEditor v-else-if="buffers[key(lang, path)]" v-model="buffers[key(lang, path)].content" @save="openSave" />
                <div v-else class="missing">
                  <el-button size="small" @click="createInLang(lang)">在{{ lang === 'zh' ? '中文' : '英文' }}目录创建此文件</el-button>
                </div>
              </div>
            </div>
          </template>
        </div>
      </div>
    </div>
    <div v-else class="edit-pane empty"><el-empty description="选择左侧的 skill 开始编辑" /></div>

    <!-- 新建 skill -->
    <el-dialog v-model="createVisible" title="新建 skill" width="420px">
      <el-form label-width="70px">
        <el-form-item label="名称"><el-input v-model="newSkill.name" placeholder="例如 k8s-pod-troubleshoot" /></el-form-item>
        <el-form-item label="描述"><el-input v-model="newSkill.description" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" @click="createSkill">创建</el-button>
      </template>
    </el-dialog>

    <!-- 保存 -->
    <el-dialog v-model="saveVisible" title="保存为新版本" width="520px">
      <div class="muted" style="margin-bottom:8px">将保存以下文件：</div>
      <div v-for="b in dirtyList" :key="b.lang + b.path" class="mono">· [{{ b.lang }}] {{ b.path }}</div>
      <el-input v-model="saveMsg" placeholder="版本说明（例如：调整第 3 步命令参数）" style="margin-top:12px" @keyup.enter="save" />
      <template #footer>
        <el-button @click="saveVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <!-- 版本历史 -->
    <el-drawer v-model="historyVisible" title="版本历史" size="760px">
      <el-table :data="versions" size="small" stripe>
        <el-table-column label="版本" width="64"><template #default="{ row }"><b>v{{ row.seq }}</b></template></el-table-column>
        <el-table-column label="时间" width="118"><template #default="{ row }">{{ fmtTime(row.created_at) }}</template></el-table-column>
        <el-table-column label="来源" width="90">
          <template #default="{ row }"><el-tag size="small" :type="sourceMeta[row.source]?.type" effect="plain">{{ sourceMeta[row.source]?.label || row.source }}</el-tag></template>
        </el-table-column>
        <el-table-column label="说明" min-width="160" prop="message" show-overflow-tooltip />
        <el-table-column label="变更" width="120">
          <template #default="{ row }">
            <div v-for="(c, lang) in row.changes" :key="lang" class="mono chg">
              <template v-if="c.added + c.modified + c.deleted">{{ lang }}:
                <span v-if="c.added" class="add">+{{ c.added }}</span>
                <span v-if="c.modified" class="mod">~{{ c.modified }}</span>
                <span v-if="c.deleted" class="delc">-{{ c.deleted }}</span>
              </template>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="已发布" width="64">
          <template #default="{ row }"><span v-if="row.deploy_count">{{ row.deploy_count }} 次</span></template>
        </el-table-column>
        <el-table-column label="操作" width="170" fixed="right">
          <template #default="{ row }">
            <el-button link size="small" type="primary" @click="showDiff(row.seq - 1, row.seq)" :disabled="row.seq === 1">改动</el-button>
            <el-button link size="small" type="primary" @click="showDiff(row.seq, 'working')">对比当前</el-button>
            <el-dropdown size="small" @command="l => restore(row.seq, l)">
              <el-button link size="small" type="warning">回退</el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="all">中英文都回退到 v{{ row.seq }}</el-dropdown-item>
                  <el-dropdown-item command="zh">仅回退中文</el-dropdown-item>
                  <el-dropdown-item command="en">仅回退英文</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </template>
        </el-table-column>
      </el-table>
    </el-drawer>

    <!-- 差异 -->
    <el-dialog v-model="diffVisible" :title="diffTitle" width="88%" top="4vh">
      <el-empty v-if="!diffs.length" description="没有差异" />
      <div v-for="d in diffs" :key="d.lang + d.path" class="diff-item">
        <div class="diff-file">
          <el-tag size="small" :type="d.lang === 'zh' ? 'danger' : 'primary'" effect="plain">{{ d.lang }}</el-tag>
          <span class="mono">{{ d.path }}</span>
          <el-tag size="small" effect="plain" :type="{ added: 'success', deleted: 'danger', modified: 'warning' }[d.status]">{{ { added: '新增', deleted: '删除', modified: '修改' }[d.status] }}</el-tag>
          <span class="add">+{{ d.additions }}</span><span class="delc">-{{ d.deletions }}</span>
        </div>
        <DiffView :old-text="d.old" :new-text="d.new" :labels="diffLabels" />
      </div>
    </el-dialog>

    <!-- 从环境拉取 -->
    <el-dialog v-model="pullVisible" title="从环境拉取 skill" width="88%" top="4vh" :close-on-click-modal="false">
      <div class="pull-form">
        <el-input v-model="pullForm.namespace" placeholder="namespace" style="width:170px"><template #prepend>ns</template></el-input>
        <el-input v-model="pullForm.pod_filter" placeholder="pod 名包含的字符串" style="width:240px" clearable @keyup.enter="queryPullPods">
          <template #prepend>包含</template>
        </el-input>
        <el-button :icon="Search" :loading="pullPodsLoading" @click="queryPullPods">查询 pod</el-button>
        <el-select v-model="pullForm.pod" filterable placeholder="选择从哪个 pod 拉取" style="width:260px">
          <el-option v-for="p in pullPods" :key="p.name" :value="p.name" :label="p.name">
            {{ p.name }}<span class="muted" style="float:right">{{ p.status }}</span>
          </el-option>
        </el-select>
      </div>
      <div class="pull-form">
        <el-input v-model="pullForm.remote_dir" placeholder="pod 内 skill 根目录" style="width:420px" class="mono">
          <template #prepend>pod 内目录</template>
        </el-input>
        <el-radio-group v-model="pullForm.scope">
          <el-radio-button value="current" :disabled="!name">当前 skill（{{ name || '-' }}）</el-radio-button>
          <el-radio-button value="all">目录下全部 skill</el-radio-button>
        </el-radio-group>
        <el-button type="primary" :icon="Download" :disabled="!pullForm.pod" :loading="pullJob?.status === 'running'" @click="startPull">拉取并对比</el-button>
      </div>
      <div class="muted small-tip">按「{{ layoutText }}」识别目录结构；导入前会先把本地当前内容保存成一个版本，导入后可以随时回退。</div>
      <JobLog v-if="pullJob && (pullJob.status !== 'success' || !pullResult)" :job="pullJob" height="160px" style="margin-top:10px" />

      <template v-if="pullResult">
        <div class="pull-head">
          <span>来自 <b class="mono">{{ pullResult.pod }}:{{ pullResult.remote_dir }}</b>，共 {{ pullResult.skills.length }} 个 skill</span>
          <span class="spacer"></span>
          <el-button size="small" link @click="pullSelected = pullResult.skills.filter(s => s.status !== 'same').map(s => s.name)">选中有差异的</el-button>
          <el-button size="small" link @click="pullSelected = []">全不选</el-button>
        </div>
        <el-empty v-if="!pullResult.skills.length" description="没有识别到 skill，请检查 pod 内目录与 skills.layout 配置" :image-size="60" />
        <div v-for="s in pullResult.skills" :key="s.name" class="pull-item">
          <div class="pull-item-head" @click="togglePullOpen(s.name)">
            <el-checkbox :model-value="pullSelected.includes(s.name)" :disabled="s.status === 'same'" @click.stop
                         @change="v => togglePullSel(s.name, v)" />
            <b>{{ s.name }}</b>
            <el-tag size="small" effect="light" :type="{ new: 'success', changed: 'warning', same: 'info' }[s.status]">
              {{ { new: '本地没有，新增', changed: `与本地不同（${s.files.length} 个文件）`, same: '与本地一致' }[s.status] }}
            </el-tag>
            <span class="muted small-tip">环境上的目录：{{ s.langs.join(' / ') || '-' }}</span>
            <span class="spacer"></span>
            <el-icon v-if="s.files.length"><component :is="pullOpen.includes(s.name) ? 'ArrowUp' : 'ArrowDown'" /></el-icon>
          </div>
          <div v-if="pullOpen.includes(s.name)" class="pull-files">
            <div v-for="d in s.files" :key="d.lang + d.path" class="diff-item">
              <div class="diff-file">
                <el-tag size="small" :type="d.lang === 'zh' ? 'danger' : 'primary'" effect="plain">{{ d.lang }}</el-tag>
                <span class="mono">{{ d.path }}</span>
                <el-tag size="small" effect="plain" :type="{ added: 'success', deleted: 'danger', modified: 'warning' }[d.status]">
                  {{ { added: '环境新增', deleted: '环境上没有（导入后删除）', modified: '修改' }[d.status] }}
                </el-tag>
                <span class="add">+{{ d.additions }}</span><span class="delc">-{{ d.deletions }}</span>
              </div>
              <DiffView :old-text="d.old" :new-text="d.new" :labels="['本地工作区', '环境（' + pullResult.pod + '）']" max-height="360px" />
            </div>
          </div>
        </div>
      </template>
      <template #footer>
        <div class="row" style="justify-content:flex-end">
          <el-input v-model="pullMsg" placeholder="版本说明（可选）" style="width:300px" />
          <el-button @click="pullVisible = false">关闭</el-button>
          <el-button type="primary" :disabled="!pullSelected.length" :loading="pullApplying" @click="applyPull">导入选中的 {{ pullSelected.length }} 个 skill</el-button>
        </div>
      </template>
    </el-dialog>

    <!-- LLM 同步 -->
    <el-dialog v-model="syncVisible" title="LLM 同步英文（逐块人工确认）" width="92%" top="3vh" :close-on-click-modal="false">
      <div class="row" style="margin-bottom:12px">
        <el-radio-group v-model="syncMode" size="small" :disabled="syncJob?.status === 'running'">
          <el-radio-button value="diff">增量：只处理中文有变更的文件</el-radio-button>
          <el-radio-button value="full">全量检查：逐文件核对中英一致性</el-radio-button>
        </el-radio-group>
        <el-button type="primary" size="small" :icon="MagicStick" :loading="syncJob?.status === 'running'" @click="startSync">
          {{ syncItems.length ? '重新生成' : '开始 LLM 检查' }}
        </el-button>
        <span class="muted">LLM 只给建议，勾选的修改才会写入英文文件</span>
      </div>
      <JobLog v-if="syncJob && (syncJob.status !== 'success' || !syncItems.length)" :job="syncJob" height="160px" />

      <div v-for="it in syncItems" :key="it.path" class="sync-item">
        <div class="sync-head">
          <span class="mono path">{{ it.path }}</span>
          <el-tag size="small" effect="dark" :type="actionMeta[it.action]?.type">{{ actionMeta[it.action]?.label || it.action }}</el-tag>
          <span class="muted">中文：{{ zhStatus[it.zh_status] || it.zh_status }}</span>
          <span class="spacer"></span>
          <template v-if="it.hunks.length">
            <span class="muted">已选 {{ it.selected.length }}/{{ it.hunks.length }}</span>
            <el-button size="small" link @click="it.selected = it.hunks.map(h => h.id)">全选</el-button>
            <el-button size="small" link @click="it.selected = []">全不选</el-button>
            <el-button size="small" link type="primary" @click="toggleManual(it)">{{ it.manual ? '返回逐块选择' : '手动编辑结果' }}</el-button>
          </template>
          <el-checkbox v-if="it.action === 'delete' || it.action === 'copy'" v-model="it.accept">{{ it.action === 'delete' ? '删除英文文件' : '复制到英文目录' }}</el-checkbox>
        </div>
        <div v-if="it.note" class="muted" style="margin:6px 0">{{ it.note }}</div>

        <el-collapse v-if="it.zh_diff || it.zh_new" class="zh-collapse">
          <el-collapse-item :title="it.zh_diff ? '查看中文变更' : '查看中文原文'">
            <DiffView v-if="it.zh_diff" :old-text="it.zh_old" :new-text="it.zh_new" :labels="['中文（上次对齐时）', '中文（当前）']" max-height="320px" />
            <pre v-else class="code light">{{ it.zh_new }}</pre>
          </el-collapse-item>
        </el-collapse>

        <template v-if="it.manual">
          <div class="manual-box"><CodeEditor v-model="it.content" /></div>
        </template>
        <template v-else>
          <div v-for="h in it.hunks" :key="h.id" class="hunk" :class="{ on: it.selected.includes(h.id) }" @click="toggleHunk(it, h.id)">
            <el-checkbox :model-value="it.selected.includes(h.id)" @click.stop @change="toggleHunk(it, h.id)" />
            <div class="hunk-body mono">
              <div class="hunk-meta">第 {{ h.old_start }} 行 · {{ { replace: '修改', insert: '新增', delete: '删除' }[h.tag] }}</div>
              <div v-for="(l, i) in h.context_before" :key="'b' + i" class="ctx">  {{ l }}</div>
              <div v-for="(l, i) in h.old_lines" :key="'o' + i" class="rm">- {{ l }}</div>
              <div v-for="(l, i) in h.new_lines" :key="'n' + i" class="ad">+ {{ l }}</div>
              <div v-for="(l, i) in h.context_after" :key="'a' + i" class="ctx">  {{ l }}</div>
            </div>
          </div>
        </template>
      </div>

      <template #footer>
        <div class="row" style="justify-content:flex-end">
          <el-input v-model="syncMsg" placeholder="版本说明（可选）" style="width:320px" size="default" />
          <el-checkbox v-model="markAfterApply">应用后标记中英文已对齐</el-checkbox>
          <el-button @click="syncVisible = false">关闭</el-button>
          <el-button type="primary" :disabled="!canApply" :loading="applying" @click="applySync">应用选中修改</el-button>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Check, Clock, Delete, Document, DocumentAdd, Download, MagicStick, Plus, Search, Upload } from '@element-plus/icons-vue'
import { marked } from 'marked'
import { api, pollJob, qs } from '../api'
import { fmtTime } from '../utils'
import CodeEditor from '../components/CodeEditor.vue'
import DiffView from '../components/DiffView.vue'
import JobLog from '../components/JobLog.vue'

const route = useRoute()
const router = useRouter()
const skills = ref([])
const kw = ref('')
const name = ref('')
const detail = ref(null)
const mode = ref('zh')
const preview = ref(false)
const path = ref('')
const buffers = reactive({}) // key: lang/path -> {original, content, isNew}
const newPaths = ref([])

const sourceMeta = {
  manual: { label: '手动', type: 'primary' }, import: { label: '导入', type: 'info' },
  'llm-sync': { label: 'LLM 同步', type: 'success' }, rollback: { label: '回退', type: 'warning' },
  'env-pull': { label: '环境拉取', type: 'danger' },
}
const actionMeta = {
  modify: { label: '建议修改', type: 'warning' }, create: { label: '新建英文', type: 'success' },
  delete: { label: '建议删除', type: 'danger' }, copy: { label: '复制', type: 'info' },
  unchanged: { label: '无需修改', type: 'info' }, error: { label: '失败', type: 'danger' },
}
const zhStatus = { modified: '已修改', added: '新增', deleted: '已删除', missing_en: '英文缺失', check: '全量检查' }

const filteredSkills = computed(() => skills.value.filter(s => s.name.includes(kw.value)))
const key = (lang, p) => `${lang}/${p}`
const treeLang = computed(() => (mode.value === 'en' ? 'en' : 'zh'))
const otherLang = computed(() => (treeLang.value === 'zh' ? 'en' : 'zh'))
const visibleLangs = computed(() => (mode.value === 'both' ? ['zh', 'en'] : [mode.value]))
const currentTree = computed(() => {
  if (!detail.value) return []
  const t = detail.value.tree
  if (mode.value === 'both') {
    const paths = new Set([...(t.zh || []), ...(t.en || [])].map(f => f.path))
    return [...paths].sort().map(p => ({ path: p }))
  }
  return t[treeLang.value] || []
})
const exists = (lang, p) => (detail.value?.tree[lang] || []).some(f => f.path === p)
const isDirty = (lang, p) => {
  const b = buffers[key(lang, p)]
  return b && (b.isNew || b.content !== b.original)
}
const dirtyList = computed(() => Object.entries(buffers)
  .filter(([, b]) => b.isNew || b.content !== b.original)
  .map(([k, b]) => ({ lang: k.split('/')[0], path: k.slice(k.indexOf('/') + 1), content: b.content })))
const dirtyCount = computed(() => dirtyList.value.length)
const pendingCount = computed(() => detail.value?.sync_pending?.items?.length || 0)

function render(md) { return marked.parse(md.replace(/^---\n[\s\S]*?\n---\n/, '')) }

async function loadSkills() { skills.value = await api.get('/skills') }

async function selectSkill(n) {
  if (n === name.value) return
  if (dirtyCount.value) {
    try { await ElMessageBox.confirm('当前有未保存的修改，切换后会丢失，确定切换？', '提示', { type: 'warning' }) } catch { return }
  }
  router.push('/skills/' + encodeURIComponent(n))
}

async function loadDetail(keepPath = true) {
  if (!name.value) return
  detail.value = await api.get('/skills/' + encodeURIComponent(name.value))
  const main = detail.value.main_file
  const p = keepPath && path.value ? path.value : main
  await openFile(p, true)
}

async function loadBuffer(lang, p, force = false) {
  const k = key(lang, p)
  if (buffers[k] && !force) return
  if (!exists(lang, p)) { if (force) delete buffers[k]; return }
  const { content } = await api.get(`/skills/${encodeURIComponent(name.value)}/file${qs({ lang, path: p })}`)
  buffers[k] = { original: content, content }
}

async function openFile(p, force = false) {
  path.value = p
  await Promise.all(['zh', 'en'].map(l => loadBuffer(l, p, force)))
}

function newFile() {
  ElMessageBox.prompt('相对路径，例如 references/commands.md', '新建文件', { inputPattern: /^[^/].+/, inputErrorMessage: '请输入相对路径' })
    .then(({ value }) => {
      const langs = mode.value === 'both' ? ['zh', 'en'] : [treeLang.value]
      langs.forEach(l => { buffers[key(l, value)] = { original: '', content: '', isNew: true } })
      if (!newPaths.value.includes(value)) newPaths.value.push(value)
      path.value = value
    }).catch(() => {})
}

function createInLang(lang) { buffers[key(lang, path.value)] = { original: '', content: '', isNew: true } }

async function deleteFile(p) {
  const langs = mode.value === 'both' ? ['zh', 'en'] : [treeLang.value]
  try { await ElMessageBox.confirm(`删除 ${langs.join('/')} 下的 ${p}？会生成新版本，可回退。`, '删除文件', { type: 'warning' }) } catch { return }
  const changes = {}
  langs.forEach(l => { changes[l] = { [p]: null } })
  await api.post(`/skills/${encodeURIComponent(name.value)}/save`, { changes, message: `删除 ${p}` })
  langs.forEach(l => delete buffers[key(l, p)])
  ElMessage.success('已删除')
  path.value = ''
  await loadDetail(false)
  loadSkills()
}

// ---------------------------------------------------------------- 保存
const saveVisible = ref(false)
const saveMsg = ref('')
const saving = ref(false)
function openSave() { if (dirtyCount.value) { saveMsg.value = ''; saveVisible.value = true } }
async function save() {
  saving.value = true
  try {
    const changes = {}
    dirtyList.value.forEach(b => { (changes[b.lang] ||= {})[b.path] = b.content })
    const r = await api.post(`/skills/${encodeURIComponent(name.value)}/save`, { changes, message: saveMsg.value || '网页编辑' })
    Object.keys(buffers).forEach(k => delete buffers[k])
    newPaths.value = []
    saveVisible.value = false
    ElMessage.success(r.version ? `已保存为 v${r.version}` : '内容没有变化')
    await loadDetail()
    loadSkills()
    if (r.sync_pending?.items?.length && dirtyLangsIncludeZh(changes)) {
      ElMessageBox.confirm(`中文有 ${r.sync_pending.items.length} 个文件需要同步到英文，现在让 LLM 检查吗？`, '同步英文', {
        confirmButtonText: 'LLM 检查', cancelButtonText: '稍后', type: 'info',
      }).then(() => { openSync(); startSync() }).catch(() => {})
    }
  } finally { saving.value = false }
}
const dirtyLangsIncludeZh = changes => Object.keys(changes).includes('zh')

// ---------------------------------------------------------------- 历史
const historyVisible = ref(false)
const versions = ref([])
async function openHistory() {
  versions.value = await api.get(`/skills/${encodeURIComponent(name.value)}/versions`)
  historyVisible.value = true
}
const diffVisible = ref(false)
const diffs = ref([])
const diffTitle = ref('')
const diffLabels = ref(null)
async function showDiff(a, b) {
  diffs.value = await api.get(`/skills/${encodeURIComponent(name.value)}/diff${qs({ a, b })}`)
  diffLabels.value = [`v${a}`, b === 'working' ? '当前工作区' : `v${b}`]
  diffTitle.value = `差异：${diffLabels.value[0]} → ${diffLabels.value[1]}`
  diffVisible.value = true
}
async function restore(seq, l) {
  const langs = l === 'all' ? null : [l]
  try {
    await ElMessageBox.confirm(`把${l === 'all' ? '中英文' : l === 'zh' ? '中文' : '英文'}恢复到 v${seq}？当前内容会保留在历史版本中。`, '回退', { type: 'warning' })
  } catch { return }
  const r = await api.post(`/skills/${encodeURIComponent(name.value)}/restore`, { seq, langs })
  ElMessage.success(r.version ? `已回退，生成 v${r.version}` : '内容与当前一致，无需回退')
  Object.keys(buffers).forEach(k => delete buffers[k])
  await loadDetail()
  await openHistory()
  loadSkills()
}

// ---------------------------------------------------------------- LLM 同步
const syncVisible = ref(false)
const syncMode = ref('diff')
const syncJob = ref(null)
const syncItems = ref([])
const syncMsg = ref('')
const markAfterApply = ref(true)
const applying = ref(false)

async function openSync() {
  if (dirtyCount.value) {
    ElMessage.warning('请先保存当前修改')
    return
  }
  syncVisible.value = true
  if (!pendingCount.value) syncMode.value = 'full'
}

async function startSync() {
  syncItems.value = []
  const { job_id } = await api.post(`/skills/${encodeURIComponent(name.value)}/sync/propose`, { mode: syncMode.value })
  const job = await pollJob(job_id, j => { syncJob.value = j })
  if (job.status === 'success') {
    syncItems.value = job.result.items.map(it => ({
      ...it, selected: it.hunks.map(h => h.id), accept: it.action === 'delete' || it.action === 'copy',
      manual: false, content: null,
    }))
    if (!syncItems.value.length) ElMessage.success('没有需要同步的文件')
  }
}

function toggleHunk(it, id) {
  const i = it.selected.indexOf(id)
  if (i >= 0) it.selected.splice(i, 1)
  else it.selected.push(id)
}

function mergeLocal(it) {
  // 与后端 apply_hunks 一致：按选中的块把 LLM 建议合入当前英文
  const old = it.en_old.split('\n')
  const out = []
  let pos = 0
  for (const h of [...it.hunks].sort((a, b) => a.old_start - b.old_start)) {
    const start = h.old_start - 1
    out.push(...old.slice(pos, start))
    out.push(...(it.selected.includes(h.id) ? h.new_lines : h.old_lines))
    pos = start + h.old_lines.length
  }
  out.push(...old.slice(pos))
  return out.join('\n')
}

function toggleManual(it) {
  if (!it.manual) it.content = mergeLocal(it)
  else it.content = null
  it.manual = !it.manual
}

const canApply = computed(() => syncItems.value.some(it => (it.manual && it.content !== null) || it.selected.length || it.accept)
  || (syncItems.value.length && markAfterApply.value))

async function applySync() {
  applying.value = true
  try {
    const items = syncItems.value.map(it => ({
      path: it.path, action: it.action, en_old: it.en_old, en_new: it.en_new, en_old_digest: it.en_old_digest,
      selected: it.manual ? [] : it.selected, accept: it.accept, content: it.manual ? it.content : null,
    }))
    const r = await api.post(`/skills/${encodeURIComponent(name.value)}/sync/apply`, {
      items, message: syncMsg.value, mark_synced: markAfterApply.value,
    })
    if (!r.ok) { ElMessage.error(r.message); return }
    ElMessage.success(`已应用 ${r.changed} 个文件${r.version ? `，当前 v${r.version}` : ''}`)
    syncVisible.value = false
    syncItems.value = []
    syncJob.value = null
    Object.keys(buffers).forEach(k => delete buffers[k])
    await loadDetail()
    loadSkills()
  } finally { applying.value = false }
}

async function markSynced() {
  await api.post(`/skills/${encodeURIComponent(name.value)}/sync/mark`)
  ElMessage.success('已标记为中英文对齐')
  await loadDetail()
  loadSkills()
}

// ---------------------------------------------------------------- 从环境拉取
const pullVisible = ref(false)
const pullForm = reactive({ namespace: '', pod_filter: '', pod: '', remote_dir: '', scope: 'current' })
const pullPods = ref([])
const pullPodsLoading = ref(false)
const pullJob = ref(null)
const pullResult = ref(null)
const pullSelected = ref([])
const pullOpen = ref([])
const pullMsg = ref('')
const pullApplying = ref(false)
const layoutText = ref('')
let pullCfgLoaded = false

async function openPull(scope) {
  if (dirtyCount.value) { ElMessage.warning('请先保存当前修改'); return }
  if (!pullCfgLoaded) {
    const cfg = await api.get('/config')
    pullForm.namespace = cfg.env.namespace
    pullForm.pod_filter = cfg.deploy.default_pod_filter || ''
    pullForm.remote_dir = cfg.deploy.remote_dir
    layoutText.value = cfg.skills.layout
    pullCfgLoaded = true
    queryPullPods()
  }
  pullForm.scope = scope === 'current' && name.value ? 'current' : 'all'
  pullResult.value = null
  pullJob.value = null
  pullVisible.value = true
}
async function queryPullPods() {
  pullPodsLoading.value = true
  try {
    const r = await api.get('/env/pods' + qs({ filter: pullForm.pod_filter, namespace: pullForm.namespace }))
    if (!r.ok) { ElMessage.error(r.error); pullPods.value = []; return }
    pullPods.value = r.pods
    if (!pullPods.value.some(p => p.name === pullForm.pod)) {
      pullForm.pod = (pullPods.value.find(p => p.status === 'Running') || pullPods.value[0])?.name || ''
    }
  } finally { pullPodsLoading.value = false }
}
async function startPull() {
  pullResult.value = null
  pullSelected.value = []
  pullOpen.value = []
  const { job_id } = await api.post('/skills/env-pull', {
    pod: pullForm.pod, namespace: pullForm.namespace, remote_dir: pullForm.remote_dir,
    skills: pullForm.scope === 'current' ? [name.value] : null,
  })
  const j = await pollJob(job_id, x => { pullJob.value = x })
  if (j.status === 'success' && j.result?.pull_id) {
    pullResult.value = j.result
    pullSelected.value = j.result.skills.filter(s => s.status !== 'same').map(s => s.name)
    pullOpen.value = j.result.skills.filter(s => s.status === 'changed').map(s => s.name).slice(0, 3)
  }
}
function togglePullSel(n, v) {
  pullSelected.value = v ? [...new Set([...pullSelected.value, n])] : pullSelected.value.filter(x => x !== n)
}
function togglePullOpen(n) {
  pullOpen.value = pullOpen.value.includes(n) ? pullOpen.value.filter(x => x !== n) : [...pullOpen.value, n]
}
async function applyPull() {
  try {
    await ElMessageBox.confirm(`用环境上的内容替换本地这 ${pullSelected.value.length} 个 skill：\n${pullSelected.value.join('\n')}\n\n本地当前内容会先保存为版本，可回退。`, '确认导入', { type: 'warning' })
  } catch { return }
  pullApplying.value = true
  try {
    const r = await api.post('/skills/env-pull/apply', { pull_id: pullResult.value.pull_id, skills: pullSelected.value, message: pullMsg.value })
    ElMessage.success('已导入：' + r.imported.map(x => `${x.skill}${x.version ? ' v' + x.version : '（无变化）'}`).join('，'))
    pullVisible.value = false
    Object.keys(buffers).forEach(k => delete buffers[k])
    await loadSkills()
    if (name.value) await loadDetail()
    else if (r.imported.length) router.push('/skills/' + encodeURIComponent(r.imported[0].skill))
  } finally { pullApplying.value = false }
}

// ---------------------------------------------------------------- 新建 skill
const createVisible = ref(false)
const newSkill = reactive({ name: '', description: '' })
async function createSkill() {
  await api.post('/skills', newSkill)
  createVisible.value = false
  await loadSkills()
  router.push('/skills/' + encodeURIComponent(newSkill.name))
}

watch(() => route.params.name, async n => {
  name.value = n || ''
  Object.keys(buffers).forEach(k => delete buffers[k])
  newPaths.value = []
  path.value = ''
  detail.value = null
  if (n) await loadDetail(false)
}, { immediate: true })

onMounted(async () => {
  await loadSkills()
  if (!name.value && skills.value.length) router.replace('/skills/' + encodeURIComponent(skills.value[0].name))
})
function onBeforeUnload(e) { if (dirtyCount.value) { e.preventDefault(); e.returnValue = '' } }
window.addEventListener('beforeunload', onBeforeUnload)
onBeforeUnmount(() => window.removeEventListener('beforeunload', onBeforeUnload))
</script>

<style scoped>
.skills-page { display: flex; height: 100vh; }
.list-pane { width: 230px; border-right: 1px solid var(--sl-border); background: #fff; display: flex; flex-direction: column; }
.list-head { display: flex; gap: 6px; padding: 12px; border-bottom: 1px solid var(--sl-border); }
.list { overflow: auto; flex: 1; padding: 6px; }
.list-actions { padding: 8px 12px 0; }
.pull-form { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 10px; }
.small-tip { font-size: 12px; }
.pull-head { display: flex; align-items: center; gap: 8px; margin: 14px 0 6px; }
.pull-head .spacer, .pull-item-head .spacer { flex: 1; }
.pull-item { border: 1px solid var(--sl-border); border-radius: 10px; margin-top: 8px; }
.pull-item-head { display: flex; align-items: center; gap: 10px; padding: 10px 12px; cursor: pointer; }
.pull-files { padding: 0 12px 4px; border-top: 1px solid var(--sl-border); padding-top: 10px; }
.skill-item { padding: 8px 10px; border-radius: 8px; cursor: pointer; }
.skill-item:hover { background: #f1f5f9; }
.skill-item.active { background: #eff6ff; }
.sname { font-weight: 500; }
.smeta { font-size: 12px; color: var(--sl-muted); display: flex; gap: 6px; align-items: center; margin-top: 2px; }
.edit-pane { flex: 1; display: flex; flex-direction: column; min-width: 0; padding: 12px 16px; gap: 10px; }
.edit-pane.empty { justify-content: center; }
.toolbar { display: flex; justify-content: space-between; align-items: center; gap: 10px; flex-wrap: wrap; }
.toolbar .title { display: flex; gap: 8px; align-items: center; }
.sk { font-size: 17px; font-weight: 600; }
.sync-alert :deep(.el-alert__title) { font-size: 13px; }
.body { flex: 1; display: flex; gap: 10px; min-height: 0; }
.files { width: 210px; background: #fff; border: 1px solid var(--sl-border); border-radius: 8px; overflow: auto; padding: 6px; }
.files-head { display: flex; justify-content: space-between; align-items: center; font-size: 12px; color: var(--sl-muted); padding: 2px 4px 6px; }
.file { display: flex; align-items: center; gap: 6px; padding: 6px 8px; border-radius: 6px; cursor: pointer; font-size: 13px; }
.file:hover { background: #f1f5f9; }
.file.active { background: #eff6ff; color: #1d4ed8; }
.file .del { opacity: 0; color: #dc2626; }
.file:hover .del { opacity: 1; }
.dot { width: 7px; height: 7px; border-radius: 50%; background: #f97316; flex: none; }
.editors { flex: 1; display: grid; grid-template-columns: 1fr; gap: 10px; min-width: 0; }
.editors.both { grid-template-columns: 1fr 1fr; }
.editor-col { display: flex; flex-direction: column; min-height: 0; min-width: 0; }
.editor-head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; font-size: 12px; }
.editor-box { flex: 1; min-height: 0; }
.md-preview { height: 100%; overflow: auto; background: #fff; border: 1px solid var(--sl-border); border-radius: 8px; padding: 4px 20px; }
.md-preview :deep(code) { background: #f1f5f9; padding: 1px 4px; border-radius: 4px; font-size: 12.5px; }
.md-preview :deep(pre code) { display: block; padding: 10px; }
.missing { height: 100%; display: grid; place-items: center; border: 1px dashed var(--sl-border); border-radius: 8px; background: #fafafa; }
.chg { font-size: 11.5px; }
.add { color: #16a34a; margin: 0 3px; }
.mod { color: #d97706; margin: 0 3px; }
.delc { color: #dc2626; margin: 0 3px; }
.diff-item { margin-bottom: 18px; }
.diff-file { display: flex; gap: 8px; align-items: center; margin-bottom: 6px; }
.sync-item { border: 1px solid var(--sl-border); border-radius: 10px; padding: 12px; margin-top: 12px; }
.sync-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.sync-head .path { font-weight: 600; font-size: 13.5px; }
.sync-head .spacer { flex: 1; }
.zh-collapse { margin: 8px 0; }
.hunk { display: flex; gap: 10px; align-items: flex-start; border: 1px solid var(--sl-border); border-radius: 8px; padding: 8px 10px; margin-top: 8px; cursor: pointer; opacity: .6; }
.hunk.on { opacity: 1; border-color: #93c5fd; background: #f8fbff; }
.hunk-body { flex: 1; min-width: 0; white-space: pre-wrap; word-break: break-word; line-height: 1.6; }
.hunk-meta { color: var(--sl-muted); font-size: 11.5px; margin-bottom: 2px; }
.ctx { color: #94a3b8; }
.rm { background: #fef2f2; color: #b91c1c; }
.ad { background: #f0fdf4; color: #15803d; }
.manual-box { height: 360px; margin-top: 8px; }
</style>
