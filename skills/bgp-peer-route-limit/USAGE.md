# BGP Peer Route-Limit 故障注入与恢复

仅用于**授权隔离实验网络**。修改 Route-Limit 会影响 BGP 邻居；故障恢复可能需要人工复位邻居。不能用在生产网。

## 文件
- `fault.ps1`：统一 PowerShell 入口
- `bgp_route_limit.py`：前置检查、JSON 状态、双端注入/恢复
- `device_connector.py`：空接口占位，需放入已验证的 `SSHConfig`/`execute_commands` 实现
- `device.env.example.ps1`：凭据环境模板，复制成不提交的 `device.env.ps1`
- `fault_states/`：自动生成 JSON，请勿加入 Git

## 首次准备

在仓库根目录运行：

```powershell
Copy-Item .\skills\bgp-peer-route-limit\device.env.example.ps1 .\skills\bgp-peer-route-limit\device.env.ps1
```

配置 SSH 用户、密钥或密码（不要提交真实凭据）；确保 `python` 可调用，并实现 `device_connector.py`。先用只读命令验证连接器输出格式、上下文视图保持及 `commit` 交互，避免出现多发一个 `y` 的问题。

## 调用

**本端 SSH 地址**与**Peer BGP 地址**可能不同，`-PeerHost` 是对端 SSH 管理地址。

预演（仅读取设备和检查，不创建 JSON）：

```powershell
.\skills\bgp-peer-route-limit\fault.ps1 -Action inject -HostIp <本端SSH地址> -PeerHost <对端SSH地址> -Peer 12.12.12.24 -Asn 200 -DryRun
```

执行真实注入（默认 `--apply`）：

```powershell
.\skills\bgp-peer-route-limit\fault.ps1 -Action inject -HostIp <本端SSH地址> -PeerHost <对端SSH地址> -Peer 12.12.12.24 -Asn 200 -Prefixes '1.2.3.4/32','2.2.3.4/32'
```

恢复：

```powershell
.\skills\bgp-peer-route-limit\fault.ps1 -Action recover -HostIp <本端SSH地址> -Asn 200
```

多个未恢复 JSON 时必须加 `-StateFile` 指定完整文件路径。可通过 `-PythonCommand` 指定虚拟环境的 python.exe。

## 行为和恢复保障

注入前要求 Peer 已 Established；本端无显式 Route-Limit 或 Peer Group；对端两条静态路由和 BGP network 尚不存在；能准确读取接收路由总数 N。按 `N+1` 设置 Route-Limit，再注入两条 /32 静态路由并在 BGP IPv4 Unicast 宣告。

生成的 JSON 保存双端管理地址、Peer、AS、原始路由数、目标限制、两个前缀、原始配置回显、进度与错误状态。恢复优先撤销对端 network，撤销本端 route-limit，再撤销本次静态路由；会逐阶段更新 JSON。

注入后 Peer 变为非 Established 只是初步观察；必须再核实路由超限告警和 LastError。用户样例中的 `LastError=60` 是待核对指标，不是唯一成功标准。恢复配置后仍需人工核查 BGP 重新 Established、告警消失及业务路由收敛。部分设备需要人工执行 `reset bgp <peer>`。

**重要限制**：当前拒绝继承 Peer Group 限制及已经存在的 Route-Limit；只处理显式单 Peer 简单场景。跨设备提交无法原子回滚；出现异常应按 JSON 手动核查两端配置再恢复，不能盲目重试。
