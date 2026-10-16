<template>
  <div class="page">
    <!-- 选择区 -->
    <div class="topbar card">
      <el-select v-model="conv" filterable placeholder="选择会话（conversation-id）" class="conv-select" @change="go">
        <el-option v-for="c in convs" :key="c.conversation_id" :value="c.conversation_id" :label="c.conversation_id">
          <span class="mono">{{ c.conversation_id }}</span>
          <span class="muted opt-q">{{ c.question || '' }}</span>
          <span class="muted" style="float:right">{{ c.calls }} 次调用</span>
        </el-option>
      </el-select>
      <template v-if="data">
        <el-select v-model="opts.skill" placeholder="skill" style="width:190px" clearable @change="opts.version = null; load()">
          <el-option v-for="s in skills" :key="s.name" :value="s.name" :label="s.name">
            {{ s.name }}<el-tag v-if="s.name === data.detected_skill" size="small" type="success" effect="plain" style="margin-left:6px">自动识别</el-tag>
          </el-option>
        </el-select>
        <el-select v-model="opts.version" placeholder="版本" style="width:100px" @change="load">
          <el-option v-for="v in versions" :key="v.seq" :value="v.seq" :label="`v${v.seq}`">v{{ v.seq }} <span class="muted">{{ v.message }}</span></el-option>
        </el-select>
        <el-tooltip :content="versionHint" placement="bottom"><el-tag size="small" effect="plain">{{ versionSourceText }}</el-tag></el-tooltip>
        <el-radio-group v-model="opts.lang" size="small" @change="load">
          <el-radio-button value="zh">按中文步骤</el-radio-button>
          <el-radio-button value="en">按英文步骤</el-radio-button>
        </el-radio-group>
      </template>
      <span class="spacer"></span>
      <el-button :icon="Refresh" :loading="pulling" :disabled="!conv" @click="repull">重新拉取日志</el-button>
      <el-button :icon="Download" :disabled="!conv" @click="downloadConversation(conv)">下载 jsonl</el-button>
      <el-button type="primary" :icon="MagicStick" :loading="reviewing" :disabled="!data || !data.alignment.steps.length" @click="runReview">LLM 评审</el-button>
    </div>

    <el-empty v-if="!conv" description="从上方选择一个会话，或在「问答调试 / 会话日志」页点击「分析」" />
    <div v-else-if="loading && !data" class="muted" style="padding:40px;text-align:center"><el-icon class="is-loading"><Loading /></el-icon> 分析中…</div>
    <template v-else-if="data">
      <el-alert v-if="!data.record_count" type="warning" :closable="false" show-icon style="margin-bottom:12px"
                title="本地还没有这个会话的日志，点击右上角「重新拉取日志」从环境拉取。" />
      <el-alert v-if="data.skill_error" type="error" :closable="false" :title="data.skill_error" style="margin-bottom:12px" />
      <el-alert v-if="data.record_count && !data.skill" type="info" :closable="false" show-icon style="margin-bottom:12px"
                title="没有自动识别出使用的 skill，请在上方手动选择以进行步骤对齐。" />

      <!-- 问题 / 回答 -->
      <div v-if="question || data.final_answer" class="card qa">
        <div v-if="question" class="q"><span class="qa-label">问</span><span>{{ question }}</span>
          <el-tag v-for="f in data.chat?.faults || []" :key="f" size="small" type="warning" effect="plain">故障：{{ f }}</el-tag>
        </div>
        <div v-if="data.final_answer" class="a"><span class="qa-label a-label">答</span>
          <div class="answer-text" :class="{ clamp: !showAnswer }" @click="showAnswer = !showAnswer">{{ data.final_answer }}</div>
        </div>
      </div>

      <!-- KPI -->
      <div class="kpi-grid kpis">
        <div class="kpi score">
          <div class="label">skill 遵从度 <el-tooltip content="0.7×步骤覆盖率 + 0.3×顺序正确率 − 偏离调用惩罚"><el-icon><QuestionFilled /></el-icon></el-tooltip></div>
          <div class="value" :style="{ color: scoreColor(curAlign.scores.compliance) }">{{ curAlign.scores.compliance ?? '-' }}<small v-if="curAlign.scores.compliance != null">/100</small></div>
          <div class="hint">{{ alignMode === 'llm' ? 'LLM 对齐' : '启发式对齐' }}<template v-if="data.llm_review"> · LLM 评分 {{ data.llm_review.score }}</template></div>
        </div>
        <div class="kpi">
          <div class="label">步骤执行</div>
          <div class="value">{{ curAlign.scores.steps_done + curAlign.scores.steps_out_of_order }}<small>/{{ curAlign.scores.steps_total }}</small></div>
          <div class="hint">
            <span v-if="curAlign.scores.steps_missing" class="bad">缺 {{ curAlign.scores.steps_missing }}</span>
            <span v-if="curAlign.scores.steps_out_of_order" class="warn"> 乱序 {{ curAlign.scores.steps_out_of_order }}</span>
            <span v-if="curAlign.scores.extra_calls" class="warn"> 偏离 {{ curAlign.scores.extra_calls }}</span>
            <span v-if="!curAlign.scores.steps_missing && !curAlign.scores.steps_out_of_order && curAlign.scores.steps_total">全部按序执行</span>
          </div>
        </div>
        <div class="kpi">
          <div class="label">总耗时</div>
          <div class="value">{{ fmtMs(m.total_ms) }}</div>
          <div class="share" v-if="m.total_ms">
            <i :style="{ width: share(m.llm_ms) + '%', background: TYPE_COLORS.LLM }"></i>
            <i :style="{ width: share(m.tool_ms) + '%', background: TYPE_COLORS.CLI }"></i>
          </div>
          <div class="hint">LLM {{ fmtMs(m.llm_ms) }} · 工具 {{ fmtMs(m.tool_ms) }}</div>
        </div>
        <div class="kpi">
          <div class="label">LLM 调用</div>
          <div class="value">{{ m.llm_calls }}</div>
          <div class="hint ellipsis">{{ m.models.join(', ') || '-' }}</div>
        </div>
        <div class="kpi">
          <div class="label">工具调用</div>
          <div class="value">{{ m.tool_calls }}</div>
          <div class="hint">
            <span v-for="(v, t) in m.by_type" :key="t" class="type-chip"><i :style="{ background: TYPE_COLORS[t] || TYPE_COLORS.TOOL }"></i>{{ t }} {{ v.count }}</span>
            <span v-if="m.tool_errors" class="bad">报错 {{ m.tool_errors }}</span>
            <span v-if="m.tool_suspect_errors" class="warn">疑似报错 {{ m.tool_suspect_errors }}</span>
          </div>
        </div>
        <div class="kpi">
          <div class="label">Tokens</div>
          <div class="value">{{ fmtNum(m.usage.total) }}</div>
          <div class="hint">入 {{ fmtNum(m.usage.prompt) }} · 出 {{ fmtNum(m.usage.completion) }}<template v-if="m.usage.cached"> · 缓存命中 {{ Math.round(100 * m.usage.cached / Math.max(1, m.usage.prompt)) }}%</template></div>
        </div>
      </div>

      <el-tabs v-model="tab" class="card tabs">
        <!-- 步骤对齐 -->
        <el-tab-pane label="步骤对齐" name="align">
          <div class="row" style="margin-bottom:10px">
            <el-radio-group v-if="data.llm_review" v-model="alignMode" size="small">
              <el-radio-button value="heuristic">启发式对齐</el-radio-button>
              <el-radio-button value="llm">LLM 对齐</el-radio-button>
            </el-radio-group>
            <span class="muted small">
              步骤来源：{{ data.skill || '-' }} v{{ data.version ?? '-' }} / {{ data.lang === 'zh' ? '中文' : '英文' }} {{ cfgMain }}（解析方式：{{ strategyText[data.step_strategy] || data.step_strategy || '-' }}）
            </span>
            <span class="spacer"></span>
            <el-button v-if="data.skill" size="small" link type="primary" :icon="EditPen" @click="$router.push('/skills/' + data.skill)">去修改 skill</el-button>
          </div>
          <el-empty v-if="!curAlign.steps.length" description="没有解析到 skill 步骤" :image-size="60" />
          <div class="align-grid">
            <div class="steps">
              <div v-for="s in curAlign.steps" :key="s.id" class="step" :class="s.status">
                <div class="step-rail"><span class="step-dot"><el-icon><component :is="statusIcon[s.status]" /></el-icon></span></div>
                <div class="step-card">
                  <div class="step-head">
                    <span class="sid">{{ s.id }}</span>
                    <span class="stitle">{{ s.title }}</span>
                    <el-tag size="small" :type="STATUS_META[s.status]?.type" effect="light">{{ STATUS_META[s.status]?.label }}</el-tag>
                    <el-tag v-if="s.retries" size="small" type="warning" effect="plain">重试 {{ s.retries }}</el-tag>
                    <el-tag v-if="s.errors" size="small" type="danger" effect="plain">报错 {{ s.errors }}</el-tag>
                    <el-tag v-if="s.suspect_errors" size="small" type="warning" effect="plain">疑似报错 {{ s.suspect_errors }}</el-tag>
                    <span v-if="s.first_order" class="muted small">第 {{ s.first_order }} 个执行</span>
                    <span v-if="s.duration_ms" class="muted small">{{ fmtMs(s.duration_ms) }}</span>
                  </div>
                  <div v-if="s.commands?.length" class="cmds">
                    <code v-for="c in s.commands.slice(0, 3)" :key="c">{{ c }}</code>
                  </div>
                  <div v-if="llmStep(s.id)" class="llm-comment"><el-icon><MagicStick /></el-icon>{{ llmStep(s.id).comment }}</div>
                  <div class="calls">
                    <template v-for="c in s.calls" :key="c">
                      <span v-if="c === 'answer'" class="call-chip answer">最终回答</span>
                      <span v-else class="call-chip" :class="{ err: toolMap[c]?.is_error, suspect: toolMap[c]?.suspect_error }" @click="openTool(c)">
                        <i :style="{ background: TYPE_COLORS[toolMap[c]?.type] || TYPE_COLORS.TOOL }"></i>
                        T{{ c }} {{ toolMap[c]?.name }}
                        <span class="ellipsis chip-in">{{ toolMap[c]?.input_summary }}</span>
                        <span class="muted">{{ fmtMs(toolMap[c]?.duration_ms) }}</span>
                        <el-icon v-if="toolMap[c]?.is_error" color="#dc2626"><WarningFilled /></el-icon>
                        <el-tooltip v-else-if="toolMap[c]?.suspect_error" content="输出里疑似包含错误信息"><el-icon color="#d97706"><WarningFilled /></el-icon></el-tooltip>
                      </span>
                    </template>
                    <span v-if="!s.calls.length" class="muted small">没有找到对应的工具调用</span>
                  </div>
                </div>
              </div>
            </div>
            <div class="side">
              <div class="side-block">
                <div class="side-title">实际调用顺序</div>
                <div class="seq">
                  <span v-for="a in curAlign.assignments.filter(x => x.call !== 'answer')" :key="a.call" class="seq-item"
                        :class="a.kind" @click="openTool(a.call)">
                    <b>T{{ a.call }}</b>
                    <span>{{ a.kind === 'skill_load' ? '读 skill' : a.step || '偏离' }}</span>
                  </span>
                </div>
              </div>
              <div v-if="unmatched.length" class="side-block">
                <div class="side-title warn">未对齐的调用（不在 skill 步骤里）</div>
                <div v-for="a in unmatched" :key="a.call" class="um" @click="openTool(a.call)">
                  <span class="mono">T{{ a.call }} {{ toolMap[a.call]?.name }}</span>
                  <div class="ellipsis mono small">{{ toolMap[a.call]?.input_summary }}</div>
                  <div class="muted small">{{ a.reason }}</div>
                </div>
              </div>
              <div v-if="data.llm_review" class="side-block review">
                <div class="side-title"><el-icon><MagicStick /></el-icon> LLM 评审 <span class="muted small">{{ fmtTime(data.llm_review.created_at) }}</span></div>
                <p>{{ data.llm_review.summary }}</p>
                <template v-if="data.llm_review.issues?.length">
                  <div class="sub-title">发现的问题</div>
                  <ul><li v-for="(x, i) in data.llm_review.issues" :key="i">{{ x }}</li></ul>
                </template>
                <template v-if="data.llm_review.suggestions?.length">
                  <div class="sub-title">skill 优化建议</div>
                  <ul><li v-for="(x, i) in data.llm_review.suggestions" :key="i">{{ x }}</li></ul>
                </template>
              </div>
              <div v-else class="side-block muted small">
                启发式对齐基于步骤中的命令与关键词匹配。点击右上角「LLM 评审」可以让本地 LLM 做语义对齐，并给出 skill 优化建议。
              </div>
            </div>
          </div>
        </el-tab-pane>

        <!-- 时间线 -->
        <el-tab-pane label="执行时间线" name="timeline">
          <Timeline :items="data.timeline" :active-key="activeKey" @select="selectTimeline" />
        </el-tab-pane>

        <!-- 调用明细 -->
        <el-tab-pane :label="`调用明细（${events.length}）`" name="calls">
          <div class="row" style="margin-bottom:8px">
            <el-checkbox-group v-model="eventFilter" size="small">
              <el-checkbox-button value="llm">LLM</el-checkbox-button>
              <el-checkbox-button value="tool">工具</el-checkbox-button>
            </el-checkbox-group>
            <el-checkbox v-model="onlyErrors" size="small">只看报错</el-checkbox>
          </div>
          <el-table :data="filteredEvents" size="small" row-key="key" @row-click="r => r.kind === 'llm' ? openLLM(r.ref) : openTool(r.ref)" row-class-name="clickable">
            <el-table-column label="#" width="60"><template #default="{ row }"><span class="mono">{{ row.kind === 'llm' ? 'L' : 'T' }}{{ row.ref }}</span></template></el-table-column>
            <el-table-column label="开始" width="80"><template #default="{ row }"><span class="mono">+{{ fmtMs(row.start_ms) }}</span></template></el-table-column>
            <el-table-column label="类型" width="76">
              <template #default="{ row }"><span class="type-chip"><i :style="{ background: TYPE_COLORS[row.type] || TYPE_COLORS.TOOL }"></i>{{ row.type }}</span></template>
            </el-table-column>
            <el-table-column label="名称" width="130" show-overflow-tooltip><template #default="{ row }">{{ row.name }}</template></el-table-column>
            <el-table-column label="输入" min-width="260"><template #default="{ row }"><div class="ellipsis mono">{{ row.input }}</div></template></el-table-column>
            <el-table-column label="输出" min-width="240"><template #default="{ row }"><div class="ellipsis mono" :class="{ bad: row.error }">{{ row.output }}</div></template></el-table-column>
            <el-table-column label="耗时" width="80"><template #default="{ row }">{{ fmtMs(row.duration_ms) }}<span v-if="row.estimated" class="muted">*</span></template></el-table-column>
            <el-table-column label="Tokens" width="90"><template #default="{ row }">{{ row.tokens ? fmtNum(row.tokens) : '' }}</template></el-table-column>
            <el-table-column label="步骤" width="70"><template #default="{ row }"><el-tag v-if="row.step" size="small" effect="plain">{{ row.step }}</el-tag></template></el-table-column>
          </el-table>
          <div class="muted small" style="margin-top:6px">* 工具耗时为估算值：发出调用的 LLM 结束 → 下一次 LLM 请求开始</div>
        </el-tab-pane>

        <!-- Token / 耗时 -->
        <el-tab-pane label="Token 与耗时" name="cost">
          <div class="cost-grid">
            <div>
              <div class="side-title">每次 LLM 调用的 token</div>
              <div class="legend-row">
                <span><i style="background:#2a78d6"></i>输入（未缓存）</span>
                <span><i style="background:#9ec5f4"></i>输入（缓存命中）</span>
                <span><i style="background:#eb6834"></i>输出</span>
              </div>
              <div v-for="c in data.llm_calls" :key="c.index" class="tok-row" @click="openLLM(c.index)">
                <span class="mono idx">L{{ c.index }}</span>
                <div class="tok-bar">
                  <i :style="{ width: tokPct(c.usage.prompt - c.usage.cached), background: '#2a78d6' }"></i>
                  <i :style="{ width: tokPct(c.usage.cached), background: '#9ec5f4' }"></i>
                  <i :style="{ width: tokPct(c.usage.completion), background: '#eb6834' }"></i>
                </div>
                <span class="mono val">{{ fmtNum(c.usage.prompt) }} / {{ fmtNum(c.usage.completion) }}</span>
                <span class="mono val muted">{{ fmtMs(c.duration_ms) }}</span>
              </div>
            </div>
            <div>
              <div class="side-title">按工具汇总</div>
              <el-table :data="m.by_tool" size="small">
                <el-table-column prop="name" label="工具" />
                <el-table-column label="类型" width="70"><template #default="{ row }"><span class="type-chip"><i :style="{ background: TYPE_COLORS[row.type] || TYPE_COLORS.TOOL }"></i>{{ row.type }}</span></template></el-table-column>
                <el-table-column prop="count" label="次数" width="60" />
                <el-table-column label="报错" width="60"><template #default="{ row }"><span :class="{ bad: row.errors }">{{ row.errors }}</span></template></el-table-column>
                <el-table-column label="总耗时" width="80"><template #default="{ row }">{{ fmtMs(row.duration_ms) }}</template></el-table-column>
                <el-table-column label="平均" width="80"><template #default="{ row }">{{ fmtMs(row.duration_ms / row.count) }}</template></el-table-column>
              </el-table>
            </div>
          </div>
        </el-tab-pane>
      </el-tabs>
    </template>

    <!-- 详情抽屉 -->
    <el-drawer v-model="drawer" size="52%" :title="drawerTitle">
      <template v-if="detailTool">
        <div class="kv-grid">
          <span>类型</span><span><span class="type-chip"><i :style="{ background: TYPE_COLORS[detailTool.type] || TYPE_COLORS.TOOL }"></i>{{ detailTool.type }}</span></span>
          <span>工具</span><span class="mono">{{ detailTool.name }}</span>
          <span>耗时</span><span>{{ fmtMs(detailTool.duration_ms) }}{{ detailTool.duration_estimated ? '（估算）' : '' }}</span>
          <span>由</span><span><el-button link type="primary" @click="openLLM(detailTool.issued_by)">LLM #{{ detailTool.issued_by }}</el-button> 发起
            <template v-if="detailTool.consumed_by">，结果交给 <el-button link type="primary" @click="openLLM(detailTool.consumed_by)">LLM #{{ detailTool.consumed_by }}</el-button></template></span>
          <span>对齐步骤</span><span>
            <template v-if="toolAssign">
              <el-tag v-if="toolAssign.step" size="small">{{ toolAssign.step }} {{ stepTitle(toolAssign.step) }}</el-tag>
              <el-tag v-else size="small" :type="toolAssign.kind === 'skill_load' ? 'info' : 'warning'">{{ toolAssign.kind === 'skill_load' ? '加载 skill' : '未对齐' }}</el-tag>
              <span class="muted small" style="margin-left:6px">{{ toolAssign.reason }}</span>
            </template>
          </span>
          <span>状态</span><span>
            <el-tag v-if="detailTool.is_error" type="danger" size="small">报错</el-tag>
            <el-tag v-else-if="detailTool.suspect_error" type="warning" size="small">输出中疑似包含错误</el-tag>
            <el-tag v-else-if="detailTool.output == null" type="info" size="small">未找到返回结果</el-tag>
            <el-tag v-else type="success" size="small">正常</el-tag>
          </span>
        </div>
        <div class="block-title">输入 <el-button link size="small" :icon="CopyDocument" @click="copy(pretty(detailTool.arguments))" /></div>
        <pre class="code">{{ pretty(detailTool.arguments) }}</pre>
        <div class="block-title">输出 <el-button link size="small" :icon="CopyDocument" @click="copy(detailTool.output || '')" /></div>
        <pre class="code" :class="{ errcode: detailTool.is_error }">{{ pretty(detailTool.output) || '（无）' }}</pre>
      </template>

      <template v-if="detailLLM">
        <div class="kv-grid">
          <span>模型</span><span class="mono">{{ detailLLM.model || '-' }}</span>
          <span>耗时</span><span>{{ fmtMs(detailLLM.duration_ms) }}（开始 +{{ fmtMs(detailLLM.start_ms) }}）</span>
          <span>Tokens</span><span>输入 {{ detailLLM.usage.prompt }}（缓存 {{ detailLLM.usage.cached }}）· 输出 {{ detailLLM.usage.completion }}<template v-if="detailLLM.usage.reasoning"> · 思考 {{ detailLLM.usage.reasoning }}</template></span>
          <span>消息数</span><span>{{ detailLLM.message_count }}</span>
          <span>结束原因</span><span class="mono">{{ detailLLM.finish_reason || '-' }}</span>
          <span>来源</span><span class="mono small">{{ detailLLM.pod }} · {{ detailLLM.file }}:{{ detailLLM.line_no }}</span>
        </div>
        <div v-if="detailLLM.tool_calls.length" class="block-title">发起的工具调用</div>
        <div class="calls">
          <span v-for="tc in detailLLM.tool_calls" :key="tc.id" class="call-chip" @click="openToolById(tc.id)">{{ tc.name }}</span>
        </div>
        <div class="block-title">最近一条输入（user / tool）</div>
        <pre class="code light">{{ detailLLM.last_input || '（无）' }}</pre>
        <template v-if="detailLLM.reasoning">
          <div class="block-title">思考过程</div>
          <pre class="code light">{{ detailLLM.reasoning }}</pre>
        </template>
        <div class="block-title">输出文本</div>
        <pre class="code light">{{ detailLLM.output_text || '（无文本输出）' }}</pre>
        <div class="block-title">
          完整上下文
          <el-button v-if="!rawRecord" size="small" link type="primary" :loading="rawLoading" @click="loadRaw">加载原始记录</el-button>
          <el-radio-group v-else v-model="rawMode" size="small">
            <el-radio-button value="msgs">消息视图</el-radio-button>
            <el-radio-button value="json">原始 JSON</el-radio-button>
          </el-radio-group>
        </div>
        <template v-if="rawRecord">
          <div v-if="rawMode === 'msgs'" class="msgs">
            <div v-for="(msg, i) in rawMessages" :key="i" class="msg" :class="msg.role">
              <div class="msg-role">{{ msg.role }}<span v-if="msg.tool_call_id" class="muted mono"> · {{ msg.tool_call_id }}</span></div>
              <pre>{{ msgText(msg) }}</pre>
            </div>
          </div>
          <pre v-else class="code">{{ pretty(rawRecord.record) }}</pre>
        </template>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { CopyDocument, Download, EditPen, MagicStick, Refresh } from '@element-plus/icons-vue'
import { api, downloadConversation, pollJob, qs } from '../api'
import { STATUS_META, TYPE_COLORS, copyText, fmtMs, fmtNum, fmtTime, pretty } from '../utils'
import Timeline from '../components/Timeline.vue'

const route = useRoute()
const router = useRouter()
const conv = ref('')
const convs = ref([])
const skills = ref([])
const versions = ref([])
const data = ref(null)
const loading = ref(false)
const pulling = ref(false)
const reviewing = ref(false)
const tab = ref('align')
const alignMode = ref('heuristic')
const showAnswer = ref(false)
const opts = reactive({ skill: null, version: null, lang: null })
const cfgMain = ref('SKILL.md')
const eventFilter = ref(['llm', 'tool'])
const onlyErrors = ref(false)

const statusIcon = { done: 'CircleCheckFilled', out_of_order: 'WarningFilled', missing: 'CircleCloseFilled' }
const strategyText = { heading: '编号标题', 'ordered-list': '有序列表', section: '二级标题' }

const m = computed(() => data.value.metrics)
const question = computed(() => data.value?.chat?.question || data.value?.llm_calls?.[0]?.last_input || '')
const curAlign = computed(() => (alignMode.value === 'llm' && data.value?.llm_review?.alignment) ? data.value.llm_review.alignment : data.value.alignment)
const toolMap = computed(() => Object.fromEntries((data.value?.tools || []).map(t => [t.index, t])))
const llmMap = computed(() => Object.fromEntries((data.value?.llm_calls || []).map(c => [c.index, c])))
const unmatched = computed(() => curAlign.value.assignments.filter(a => a.kind === 'unmatched'))
const versionSourceText = computed(() => ({ deployed: '按发布记录推断', latest: '最新版本', manual: '手动指定' }[data.value?.version_source] || '-'))
const versionHint = computed(() => ({
  deployed: '根据会话开始前最后一次发布到相关 pod 的版本推断',
  latest: '没有找到会话前的发布记录，使用最新版本',
  manual: '手动选择的版本',
}[data.value?.version_source] || ''))
const llmStep = sid => (alignMode.value === 'llm' ? data.value?.llm_review?.steps?.find(s => s.step === sid) : null)
const stepTitle = sid => curAlign.value.steps.find(s => s.id === sid)?.title || ''
const scoreColor = s => (s == null ? 'inherit' : s >= 85 ? '#16a34a' : s >= 60 ? '#d97706' : '#dc2626')
const share = v => (m.value.total_ms ? Math.min(100, (100 * v) / m.value.total_ms) : 0)
const maxTok = computed(() => Math.max(1, ...(data.value?.llm_calls || []).map(c => c.usage.prompt + c.usage.completion)))
const tokPct = v => `${(100 * Math.max(0, v)) / maxTok.value}%`

const events = computed(() => {
  if (!data.value) return []
  const stepOf = Object.fromEntries(curAlign.value.assignments.map(a => [a.call, a.step]))
  const ev = []
  data.value.llm_calls.forEach(c => ev.push({
    key: 'l' + c.index, kind: 'llm', ref: c.index, type: 'LLM', name: c.model || 'LLM', start_ms: c.start_ms ?? 0,
    input: c.last_input, output: c.output_text || (c.tool_calls.length ? '→ ' + c.tool_calls.map(t => t.name).join(', ') : ''),
    duration_ms: c.duration_ms, tokens: c.usage.total, error: !!c.error,
  }))
  data.value.tools.forEach(t => ev.push({
    key: 't' + t.index, kind: 'tool', ref: t.index, type: t.type, name: t.name, start_ms: t.start_ms ?? 0,
    input: t.input_summary, output: t.output ?? '（无返回）', duration_ms: t.duration_ms, estimated: t.duration_estimated,
    error: t.is_error || t.suspect_error, step: stepOf[t.index],
  }))
  return ev.sort((a, b) => a.start_ms - b.start_ms || (a.kind === 'llm' ? -1 : 1))
})
const filteredEvents = computed(() => events.value.filter(e => eventFilter.value.includes(e.kind) && (!onlyErrors.value || e.error)))

// ---------------------------------------------------------------- 加载
async function loadConvs() { convs.value = await api.get('/conversations') }
function go(c) { router.push('/analysis/' + encodeURIComponent(c)) }

async function load() {
  if (!conv.value) return
  loading.value = true
  try {
    data.value = await api.get(`/analysis/${encodeURIComponent(conv.value)}` + qs(opts))
    opts.skill = data.value.skill
    opts.version = data.value.version
    opts.lang = data.value.lang
    if (!data.value.llm_review) alignMode.value = 'heuristic'
    if (data.value.skill) versions.value = await api.get(`/skills/${encodeURIComponent(data.value.skill)}/versions`)
  } finally { loading.value = false }
}

async function repull() {
  pulling.value = true
  try {
    const { job_id } = await api.post('/logs/pull', { conversation_id: conv.value })
    const j = await pollJob(job_id)
    ElMessage[j.status === 'success' ? 'success' : 'error'](j.logs[j.logs.length - 1]?.replace(/^\[.*?\]\s*/, '') || j.status)
    await load()
  } finally { pulling.value = false }
}

async function runReview() {
  reviewing.value = true
  try {
    await api.post(`/analysis/${encodeURIComponent(conv.value)}/review`, opts)
    await load()
    alignMode.value = 'llm'
    tab.value = 'align'
    ElMessage.success('LLM 评审完成')
  } finally { reviewing.value = false }
}

// ---------------------------------------------------------------- 详情
const drawer = ref(false)
const detailTool = ref(null)
const detailLLM = ref(null)
const rawRecord = ref(null)
const rawLoading = ref(false)
const rawMode = ref('msgs')
const activeKey = ref('')
const drawerTitle = computed(() => detailTool.value ? `工具调用 T${detailTool.value.index} · ${detailTool.value.name}` : detailLLM.value ? `LLM 调用 L${detailLLM.value.index}` : '')
const toolAssign = computed(() => detailTool.value && curAlign.value.assignments.find(a => a.call === detailTool.value.index))
const rawMessages = computed(() => {
  const r = rawRecord.value?.record || {}
  const req = r.request || r
  const msgs = [...(req.messages || r.messages || [])]
  if (req.system) msgs.unshift({ role: 'system', content: req.system })
  const resp = r.response || {}
  const out = resp.choices?.[0]?.message || (resp.content ? { role: 'assistant', content: resp.content } : null)
  if (out) msgs.push({ ...out, role: (out.role || 'assistant') + '（本次输出）' })
  return msgs
})
function msgText(msg) {
  let s = typeof msg.content === 'string' ? msg.content : msg.content ? JSON.stringify(msg.content, null, 2) : ''
  if (msg.tool_calls) s += (s ? '\n' : '') + msg.tool_calls.map(t => `→ ${t.function?.name}(${t.function?.arguments})`).join('\n')
  return s
}

function openTool(idx) {
  const t = toolMap.value[idx]
  if (!t) return
  detailLLM.value = null
  detailTool.value = t
  activeKey.value = 'tool' + idx
  drawer.value = true
}
function openToolById(id) {
  const t = data.value.tools.find(x => x.id === id)
  if (t) openTool(t.index)
}
function openLLM(idx) {
  const c = llmMap.value[idx]
  if (!c) return
  detailTool.value = null
  detailLLM.value = c
  rawRecord.value = null
  activeKey.value = 'llm' + idx
  drawer.value = true
}
function selectTimeline(ev) { ev.kind === 'llm' ? openLLM(ev.ref) : openTool(ev.ref) }
async function loadRaw() {
  rawLoading.value = true
  try { rawRecord.value = await api.get('/records/' + detailLLM.value.record_id) } finally { rawLoading.value = false }
}
async function copy(t) { await copyText(t); ElMessage.success('已复制') }

watch(() => route.params.conv, c => {
  conv.value = c || ''
  data.value = null
  Object.assign(opts, { skill: null, version: null, lang: null })
  load()
}, { immediate: true })

onMounted(async () => {
  loadConvs()
  skills.value = await api.get('/skills')
  const cfg = await api.get('/config')
  cfgMain.value = cfg.skills.main_file
})
</script>

<style scoped>
.topbar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 12px; }
.topbar .spacer { flex: 1; }
.conv-select { width: 260px; }
.opt-q { margin-left: 10px; max-width: 260px; display: inline-block; overflow: hidden; text-overflow: ellipsis; vertical-align: bottom; }
.qa { margin-bottom: 12px; }
.qa .q, .qa .a { display: flex; gap: 10px; align-items: flex-start; }
.qa .a { margin-top: 8px; }
.qa-label { flex: none; width: 22px; height: 22px; border-radius: 6px; background: #eff6ff; color: #1d4ed8; display: grid; place-items: center; font-size: 12px; font-weight: 600; }
.a-label { background: #f0fdf4; color: #15803d; }
.answer-text { white-space: pre-wrap; cursor: pointer; color: #374151; }
.answer-text.clamp { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.kpis { margin-bottom: 12px; }
.kpi .value small { font-size: 13px; color: var(--sl-muted); font-weight: 400; }
.kpi.score .value { font-size: 28px; }
.share { display: flex; height: 6px; border-radius: 3px; overflow: hidden; background: #f1f5f9; margin: 4px 0 2px; gap: 2px; }
.share i { display: block; height: 100%; }
.bad { color: #dc2626; }
.warn { color: #b45309; }
.small { font-size: 12px; }
.type-chip { display: inline-flex; align-items: center; gap: 4px; margin-right: 8px; }
.type-chip i { width: 8px; height: 8px; border-radius: 2px; display: inline-block; }
.tabs { padding-top: 4px; }
.align-grid { display: grid; grid-template-columns: 1fr 320px; gap: 16px; }
.step { display: flex; gap: 10px; }
.step-rail { width: 22px; display: flex; flex-direction: column; align-items: center; position: relative; }
.step-rail::after { content: ''; position: absolute; top: 24px; bottom: -6px; width: 2px; background: #e5e7eb; }
.step:last-child .step-rail::after { display: none; }
.step-dot { width: 22px; height: 22px; display: grid; place-items: center; font-size: 20px; margin-top: 8px; background: #fff; z-index: 1; }
.step.done .step-dot { color: #16a34a; }
.step.out_of_order .step-dot { color: #d97706; }
.step.missing .step-dot { color: #dc2626; }
.step-card { flex: 1; border: 1px solid var(--sl-border); border-radius: 10px; padding: 10px 12px; margin-bottom: 10px; min-width: 0; }
.step.missing .step-card { border-color: #fecaca; background: #fffafa; }
.step.out_of_order .step-card { border-color: #fde68a; }
.step-head { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.sid { font-family: var(--sl-mono); font-weight: 600; color: var(--sl-muted); }
.stitle { font-weight: 500; }
.cmds { margin-top: 6px; display: flex; gap: 6px; flex-wrap: wrap; }
.cmds code { background: #f1f5f9; border-radius: 4px; padding: 1px 6px; font-size: 12px; font-family: var(--sl-mono); max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.llm-comment { margin-top: 6px; font-size: 12.5px; color: #4338ca; display: flex; gap: 4px; align-items: center; }
.calls { margin-top: 8px; display: flex; flex-direction: column; gap: 4px; }
.call-chip { display: flex; align-items: center; gap: 6px; font-size: 12.5px; padding: 4px 8px; border-radius: 6px; background: #f8fafc; border: 1px solid var(--sl-border); cursor: pointer; min-width: 0; }
.call-chip:hover { border-color: #93c5fd; }
.call-chip i { width: 8px; height: 8px; border-radius: 2px; flex: none; }
.call-chip.err { border-color: #fecaca; background: #fef2f2; }
.call-chip.suspect { border-color: #fde68a; background: #fffbeb; }
.call-chip.answer { background: #f0fdf4; border-color: #bbf7d0; color: #15803d; cursor: default; align-self: flex-start; }
.chip-in { flex: 1; font-family: var(--sl-mono); color: #475569; }
.side-block { border: 1px solid var(--sl-border); border-radius: 10px; padding: 10px 12px; margin-bottom: 10px; }
.side-title { font-weight: 600; font-size: 13px; margin-bottom: 8px; display: flex; align-items: center; gap: 6px; }
.seq { display: flex; flex-wrap: wrap; gap: 6px; }
.seq-item { display: inline-flex; flex-direction: column; align-items: center; font-size: 11.5px; padding: 3px 7px; border-radius: 6px; background: #f0fdf4; border: 1px solid #bbf7d0; cursor: pointer; }
.seq-item.unmatched { background: #fffbeb; border-color: #fde68a; }
.seq-item.skill_load { background: #f1f5f9; border-color: #e2e8f0; }
.um { padding: 6px 0; border-top: 1px dashed var(--sl-border); cursor: pointer; }
.um:first-of-type { border-top: none; }
.review p { margin: 0 0 8px; font-size: 13px; }
.review ul { margin: 0 0 8px; padding-left: 18px; font-size: 13px; }
.sub-title { font-size: 12px; color: var(--sl-muted); margin-top: 6px; }
.cost-grid { display: grid; grid-template-columns: 1.3fr 1fr; gap: 20px; }
.legend-row { display: flex; gap: 14px; font-size: 12px; color: var(--sl-muted); margin-bottom: 8px; }
.legend-row i { display: inline-block; width: 10px; height: 8px; border-radius: 2px; margin-right: 4px; }
.tok-row { display: grid; grid-template-columns: 36px 1fr 110px 64px; gap: 8px; align-items: center; height: 24px; cursor: pointer; }
.tok-row:hover { background: #f8fafc; }
.tok-bar { display: flex; height: 12px; gap: 2px; }
.tok-bar i { display: block; height: 100%; border-radius: 0 3px 3px 0; }
.tok-bar i:first-child { border-radius: 3px 0 0 3px; }
.val { font-size: 12px; text-align: right; }
.idx { font-size: 11.5px; color: var(--sl-muted); }
.kv-grid { display: grid; grid-template-columns: 80px 1fr; gap: 8px 12px; font-size: 13px; margin-bottom: 12px; align-items: center; }
.kv-grid > span:nth-child(odd) { color: var(--sl-muted); }
.block-title { font-weight: 600; margin: 14px 0 6px; display: flex; align-items: center; gap: 8px; }
pre.errcode { background: #450a0a; }
.msgs { display: flex; flex-direction: column; gap: 8px; }
.msg { border: 1px solid var(--sl-border); border-radius: 8px; padding: 6px 10px; }
.msg.system { background: #f8fafc; }
.msg.user { background: #eff6ff; }
.msg.tool { background: #fff7ed; }
.msg-role { font-size: 11.5px; font-weight: 600; color: var(--sl-muted); text-transform: uppercase; }
.msg pre { margin: 4px 0 0; white-space: pre-wrap; word-break: break-word; font-family: var(--sl-mono); font-size: 12px; max-height: 300px; overflow: auto; }
:deep(.clickable) { cursor: pointer; }
@media (max-width: 1200px) { .align-grid, .cost-grid { grid-template-columns: 1fr; } }
</style>
