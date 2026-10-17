# BGP Router-ID 冲突故障注入与恢复：使用说明

本工具通过统一入口 `fault.ps1` 调用 `bgp_router_id.py`，使用 `device_connector.SSHConfig` 和 `execute_commands` 连接设备，执行 BGP Router-ID 冲突的前置检查、注入、状态备份和恢复。

> **使用范围**：仅在经过授权的隔离实验网络中使用。修改 BGP Router-ID 会重置本端 BGP 邻居，并可能影响其他地址族与业务路由。当前工具未在你的设备环境中完成端到端验证，特别是底层 `execute_commands` 曾出现重复发送 `y` 的问题，必须先修复和验证后才能开启真实写操作。

## 1. 文件结构

```text
bgp-router-id-conflict/
├── fault.ps1                # PowerShell 统一入口
├── bgp_router_id.py         # 故障逻辑及 SSH 设备操作
├── SKILL.md                 # Skill 定义（如有）
├── USAGE.md                 # 本说明
└── fault_states/            # 自动生成：每次注入的状态/备份 JSON
```

`fault.ps1` 会以自身所在目录查找 Python 脚本，不要求在特定工作目录执行。`fault_states/` 由脚本自动创建。

## 2. 环境准备（首次使用）

- Windows PowerShell 5.1 或 PowerShell 7；可调用 `python` 的 Python 环境。
- Python 可导入 `device_connector`，其中提供 `SSHConfig` 和 `execute_commands`。
- 运行主机可以通过 SSH 访问**授权实验设备**。
- 按实际连接方式设置 SSH 环境变量（示例仅作用于当前 PowerShell 窗口）：

```powershell
$env:SSH_USERNAME = "<实验账号>"
$env:SSH_PASSWORD = "<从安全渠道获取的密码>"
$env:SSH_PORT = "22"
$env:AUTH_MODEL = "password"
# 若采用密钥认证，按 device_connector 的要求设置：
# $env:KEY_FILENAME = "C:\path\to\private_key"
```

建议通过安全凭据管理方式注入密码，不要把真实密码写进脚本或提交到 Git。统一脚本会在当前 PowerShell 进程中设置 `DEVICE_CONNECTOR_INTERACTIVE_OK=1`，**这只是已有 Python 脚本要求的开关，不代表 `[Y/N]` 应答已被验证**。

## 3. 统一调用

以下 IP `192.0.2.167`、`192.0.2.166` 是文档示例地址，运行时替换成你的实验设备地址。

### 注入

```powershell
.\bgp-router-id-conflict\fault.ps1 -Action inject -HostIp 192.0.2.167 -Peer 192.0.2.166
```

自动完成：检查本端 Peer 和 AS、登录 Peer 获取对端 Router-ID、备份配置、生成**唯一** JSON 状态文件、在开启 `--apply` 的路径中执行变更、读回本端 Router-ID。默认 ASN 为 `100`。`fault.ps1` 的注入命令会直接启用 `--apply`，**不是 Dry Run**。

### 恢复

```powershell
.\bgp-router-id-conflict\fault.ps1 -Action recover -HostIp 192.0.2.167
```

统一脚本会尝试在 `fault_states/` 下找到该设备、该 ASN **恰好一个**未完成恢复的状态文件。若没有匹配或存在多个匹配，会报错，要求显式指定文件。

```powershell
.\bgp-router-id-conflict\fault.ps1 -Action recover -HostIp 192.0.2.167 -StateFile ".\bgp-router-id-conflict\fault_states\bgp_192.0.2.167_示例标识.json"
```

实际恢复时应复制**注入输出打印的完整状态文件路径**，不要直接使用上述示例文件名。

## 4. 参数说明

| 参数 | 注入 | 恢复 | 说明 |
|---|---|---|---|
| `-Action` | 必填 | 必填 | `inject` 或 `recover` |
| `-HostIp` | 必填 | 必填 | 本端设备 SSH 管理地址 |
| `-Peer` | 必填 | 可选 | BGP 对端设备 IP；恢复时可用于额外匹配 |
| `-Asn` | 可选 | 可选 | 本端 AS，默认为 `100` |
| `-PeerSubnet` | 可选 | — | Peer 候选网段，未传时默认 `Peer/32` |
| `-StateFile` | 可选 | 可选 | 指定状态文件；注入不传则自动生成，恢复不传则自动查找 |
| `-PythonCommand` | 可选 | 可选 | Python 可执行命令，默认 `python` |

指定 AS 与网段的示例：

```powershell
.\bgp-router-id-conflict\fault.ps1 -Action inject -HostIp 192.0.2.167 -Peer 192.0.2.166 -Asn 100 -PeerSubnet "192.0.2.0/24"
```

## 5. 状态文件及恢复关系

每次注入自动生成不同的 `fault_states/*.json`，保存本端、Peer、原始 Router-ID、目标 Router-ID、注入状态以及配置回显。**恢复与对应的那次注入使用同一个 JSON**。

注意：`_apply` 不是脚本要求的文件后缀，只是旧版调用示例中的手工命名；真正控制写操作的是 Python 的 `--apply`，而统一 PowerShell 脚本已自动使用该参数。不要将状态文件放进公开 Git 仓库，因为可能包含设备配置和内部网络信息。

如 Python 执行失败，但设备已经提交了变更，状态文件可能停留在 `apply_started` 或 `recovery_started`。这时**不要重复注入**；应先通过只读命令确认当前 BGP Router-ID、Peer 和业务路由状态，使用正确状态文件恢复。恢复命令完成后仍需确认邻居进入 `Established`，必要时等待收敛并核查关键业务路由。

## 6. 常见问题

**PowerShell 提示 `export` 不是命令**：PowerShell 设置环境变量使用 `$env:NAME = "value"`，不是 Bash 的 `export`。

**提示 `Unrecognized command` 且回显中有多余的 `y`**：底层 `device_connector.execute_commands` 可能在 `[Y/N]` 已确认后又发送了额外字符。修复其交互确认逻辑；不要简单屏蔽所有 `Error:` 回显。

**状态文件已经存在**：统一入口默认生成唯一文件名，避免覆盖。若手工通过 `-StateFile` 指向已存在文件，脚本会拒绝覆盖。

**恢复找不到状态文件**：确认是否使用同一目录的 `fault_states/`，或用 `-StateFile` 显式提供注入时输出的路径。

**恢复时匹配到多个 JSON**：显式选择正确的 `-StateFile`；不能仅凭文件时间推断哪个记录应恢复。

**只有配置提交成功，没有观察到邻居 Down**：Router-ID 改变并不保证目标故障按预期生效。需结合目标 Peer 状态、持续观察窗口和网络拓扑进一步确认。

## 7. 与 Skill Lab 集成

`fault.ps1` 是便于研发人员本地操作的 CLI 入口，并非 Skill Lab 自动注册的故障插件。若接入 `chat.faults` / Fault Injection Engine，建议把注入与恢复封装成独立 Provider，持久化 `fault_run_id` 与状态文件，增加设备级锁、授权审批和独立恢复任务；不要将有副作用的真实设备命令直接交给未经限制的 Pod 命令模板。