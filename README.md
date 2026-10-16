# Skill Lab

在环境上调试 Agent skill 的前后端工具。覆盖的闭环：

```
修改中文 skill ─→ 本地 LLM 同步英文（逐块人工确认）─→ 一键发布到 pod
      ↑                                                          │
      └── 分析遵从度 / 耗时 / token ←── 拉取 llm-calls.jsonl ←── 注入故障提问
```

| 页面 | 功能 |
|---|---|
| **Skill 编辑** | 网页直接编辑中/英文目录，中英对照，Markdown 预览；每次保存生成一个版本，可查看任意两个版本的差异，并回退（中英文都回退，或只回退其中一种）|
| **从环境拉取** | 选一个 pod，把 pod 内 skill 目录（当前 skill 或全部）读回来，逐文件和本地对比（本地没有 / 有差异 / 一致），勾选后导入为「环境拉取」版本；导入前会先把本地内容存成版本，可回退 |
| **LLM 同步英文** | 找出上次对齐后中文改过的文件，让本地 LLM 生成英文修改建议，按 diff 块逐个勾选，也可以手动编辑结果再应用；支持全量一致性检查 |
| **发布** | 选版本、选语言，输入 pod 名包含的字符串（支持「或 / 且 / 排除」），预览匹配的 pod 后一键发布；打包、复制、安装、reload 都是可配置的命令，保留发布记录 |
| **问答调试** | 选择问答接口，勾选故障预设，自定义 Header；支持 SSE；自动提取并记录 conversation-id（也可以手动修正）；一键「拉取并下载」该会话的 llm-calls.jsonl；可重复提问做稳定性对比 |
| **会话日志** | 在匹配的 pod 内 find 到 `llm-calls*.jsonl`，原样读回本地（可以只 grep 某个 ID）；**下载**：单个原始文件、全部打包 zip、按 conversation-id 下载对应的日志行（直接按文本查找，不依赖日志格式解析）；也支持导入本地 jsonl |
| **执行分析**（默认隐藏，见下方说明） | 遵从度评分、步骤覆盖 / 乱序 / 缺失、每次 CLI / API 调用的输入输出、与 skill 步骤的对齐关系、执行时间线、每次 LLM 调用的 token（含缓存命中）与耗时；可让 LLM 做语义评审并给出 skill 修改建议 |
| **环境** | pod 列表、环境信息脚本、在 pod 内执行命令、LLM 连通性测试、当前配置 |

技术栈：后端 Python FastAPI + SQLite（单进程，无需额外服务），前端 Vue 3 + Element Plus + CodeMirror。

---

## 快速体验（mock 环境，不需要 k8s 和 LLM）

```bash
pip install -r backend/requirements.txt
(cd frontend && npm install && npm run build)
bash scripts/dev_mock.sh --reset        # 启动 mock 环境 + 后端，打开 http://localhost:8600
python3 mock/smoke_test.py              # 另开终端：自动走一遍完整流程，生成演示数据
```

mock 环境包括：`mock/bin/kubectl`（用本地目录模拟 pod）、`mock/agent_server.py`（模拟问答接口，按 pod 里已发布的 skill 生成 `llm-calls.jsonl`，支持 `x-fault-inject` 故障；同时模拟 OpenAI 兼容的 LLM）。

## 接入真实环境

```bash
cp config.example.yaml config.yaml      # 按环境修改，说明见文件内注释
cp .env.example .env                    # 填写 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL
bash scripts/start.sh                   # 安装依赖、构建前端并启动，默认端口 8600（PORT=xxxx 可改）
```

开发模式：后端 `cd backend && uvicorn app.main:app --reload --port 8600`，前端 `cd frontend && npm run dev`（5173 端口，`/api` 代理到 8600）。

### 需要按你们环境修改的地方

1. **skill 目录**：`skills.root` 和 `skills.layout`（`{lang}/{skill}` 或 `{skill}/{lang}`）。第一次打开某个 skill 时会自动建立 v1。
2. **LLM**：`.env` 里的 `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`，任何 OpenAI 兼容接口都可以（vLLM、Ollama、内部网关等）。
3. **pod 操作命令**：`env.commands` 下的 `list_pods` / `exec` / `cp_to`。默认用 kubectl；经过跳板机或者用你们自己的脚本，改这几条模板即可。
4. **从环境拉取**：`deploy.fetch_cmd` 是在 pod 内执行、往 stdout 输出 tar/tar.gz 的命令（默认 `cd {remote_dir} && tar -czf - ...`），目录结构和发布时一致。
5. **打包与安装**：`deploy.package_cmd` 指向你们的打包脚本（参数约定见 `scripts/package_skill.sh`），`deploy.install_cmd` / `post_cmd` 是在 pod 内执行的安装和 reload 命令。
6. **环境信息脚本**：`env.commands.env_info`。
7. **问答接口**：`chat.endpoints` 的 url、body 模板，以及 `conversation_id_rules`（从响应头 / JSON 字段 / 正则提取 conversation-id）。如果环境允许调用方指定 ID，配置 `send_conversation_id_header`，这样最稳。
8. **故障注入**：`chat.faults`。每个故障可以加 Header、合并请求体、提问前在 pod 内执行命令、提问后清理。
9. **日志**：`logs.paths` / `logs.glob` / `logs.conversation_id_paths`。
10. **日志格式**：默认自动识别 OpenAI Chat Completions、Anthropic Messages 和 OpenAI Responses 三种格式（工具调用、工具结果、usage、耗时）。字段位置不一样的话，在 `analysis.fields` 里用 JSON 路径指定。

## 执行分析（默认隐藏）

执行分析页目前从菜单和各页面按钮中隐藏，路由仍保留（可直接访问 `/analysis`）。
恢复入口：把 `frontend/src/utils.js` 里的 `FEATURES.analysis` 改为 `true` 后重新构建前端。

## 分析是怎么算的

- **步骤解析**：从 `SKILL.md` 中按以下顺序识别步骤：编号标题（`## 1.` / `## 第一步` / `## Step 2`），步骤类章节下的有序列表，二级标题。同时提取每个步骤里的命令（代码块和行内代码）。
- **工具调用**：取自每次 LLM 响应里的 `tool_calls` / `tool_use`，工具结果从下一次请求的 `tool` / `tool_result` 消息里取。
- **工具耗时**：日志里有独立的工具记录就用它；否则按「发起调用的 LLM 结束 → 下一次 LLM 请求开始」估算，界面上标为估算值。
- **启发式对齐**：命令前缀匹配（如 `kubectl logs`）加关键词（IDF 加权）打分，并偏好按步骤顺序匹配。读取 SKILL.md 的调用标为「加载 skill」；「给出结论」这类步骤由最终回答完成。
- **遵从度** = 0.7 × 步骤覆盖率 + 0.3 × 顺序正确率 − 偏离调用的惩罚（每个偏离调用扣 2 分，最多扣 10 分）。
- **LLM 评审**：把步骤和压缩后的调用序列交给本地 LLM，得到语义对齐、每个步骤的执行评价、发现的问题和 skill 修改建议；可以和启发式结果切换对比。
- **对比的 skill 版本**：默认取会话开始前最后一次发布到相关 pod 的版本，找不到就用最新版本，也可以手动选择。

## 目录

```
backend/app/
  main.py               API 路由
  config.py             配置加载（config.yaml + .env）
  services/skills.py    工作区与多版本
  services/sync.py      中文 → 英文 LLM 同步（hunk 级确认）
  services/deploy.py    打包与发布
  services/chat.py      提问、故障注入、conversation-id 提取
  services/logs.py      拉取日志与索引
  services/trace.py     日志归一化为执行轨迹
  services/align.py     步骤解析与对齐打分
  services/analysis.py  分析汇总与 LLM 评审
frontend/src/views/     各页面
mock/                   本地演示环境与冒烟测试
scripts/                打包 / 环境信息示例脚本、启动脚本
```

测试：`cd backend && python3 -m pytest -q tests`
