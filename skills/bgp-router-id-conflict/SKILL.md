---
name: bgp-router-id-conflict
description: 在授权的华为 VRP 隔离实验环境中，注入和恢复 BGP Router-ID 冲突，并保留原配置以支持可审计恢复。
---

# BGP Router-ID 冲突故障注入与恢复

## 适用范围

仅限已经授权、具备回退窗口的测试设备。脚本默认本端 10.118.192.167，AS 100，候选 Peer 网段 10.118.192.0/24。必须使用操作者明确提供的管理凭据；禁止向生产业务设备自动注入。变化可能重置本端所有 BGP 会话。

## 触发意图

- “注入 BGP Router-ID 冲突”：调用脚本 `inject`。
- “恢复 BGP Router-ID 冲突”：调用脚本 `recover`，读取此前生成的状态文件。
- “检查能否注入”：调用 `inject`（不带 `--apply`），只执行只读查询与备份。

## 注入工作流

1. SSH 登录本端，执行 `display bgp peer`，提取 BGP local router ID、local AS、Peer 状态。
2. 按 Established、指定网段过滤，且必须显式选择 Peer（多候选时拒绝自动选择）；使用 device_connector.execute_commands 登录对端验证纳管。
3. 在对端 `display bgp peer` 中获取 BGP local router ID，不使用 Peer IP 替代 Router-ID。
4. 在本端进入 `system-view` / `bgp 100`，运行 `display this` 保存原 BGP 配置，确保 `router-id` 存在且与运行态一致。
5. 将原值、注入值、设备、Peer、运行态和 BGP 配置保存至本地状态 JSON。保存失败不得修改配置。
6. 用户明确确认后执行 `router-id <对端Router-ID>`，由 execute_commands 内部处理 `Continue? [Y/N]`，再执行 `commit`。
7. 再次查询 `display bgp peer`。若本端 Router-ID 与对端相同，说明配置冲突已形成；必须继续验证目标 Peer 会话是否产生故障，且允许协议定时器收敛。

## 恢复工作流

1. 读取注入前状态 JSON，验证 host、AS 和当前 Router-ID。
2. 仅当当前 Router-ID 等于记录的注入值时允许回滚；其他配置变化一律拒绝覆盖。
3. 用户再次确认，进入 `system-view` / `bgp 100`，执行 `router-id <原始值>` 并应答确认，执行 `commit`。
4. 验证本端 Router-ID 回到原值；再次检查 Peer Established 状态、路由数。不同设备协议收敛时间不同，不假定立即建立。
5. 如果回显解析失败、提交失败或目标未恢复，立即停止自动化并升级人工处理；不得反复盲目提交。

## 成功标准

- 注入配置成功：本端 Router-ID == 对端 Router-ID。
- 故障注入生效：目标 BGP Peer 从 Established 变化为非 Established，并在观察窗口内持续异常，且以设备告警、日志或 verbose 状态佐证。仅配置相等不等于故障实际触发。
- 恢复配置成功：本端 Router-ID == 原始 Router-ID。
- 业务恢复成功：原先正常的目标 BGP Peer 再次 Established，且关键路由与业务状态一致。

## 关键约束

- 对端 SSH 可达仅为纳管的初步技术验证；实际需另核对资产系统的纳管关系与变更授权。
- 如目标 Peer 通过 LoopBack 或非直连建立会话，不能将同一 /24 作为通用规则，应由调用者明确指定候选 Peer。
- 对端的 Router-ID 可能碰巧等于本端其他设备的地址；不得仅根据地址猜测对端 Router-ID。
- 如果更改设备版本导致确认提示、配置视图、错误回显或 `commit` 交互发生变化，应先适配/测试脚本。
- 状态文件可能包含设备配置和网络拓扑信息，必须安全存储并限制权限，不应传播或直接纳入公共代码仓。

## 工具调用

统一通过 `from device_connector import SSHConfig, execute_commands` 操作设备。写入操作依赖 execute_commands 同一调用中会话保持与 [Y/N] 交互处理；未经测试不得 --apply。参考 README.md 和 bgp_router_id.py。
