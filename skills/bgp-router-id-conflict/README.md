# BGP Router-ID 冲突注入与恢复（device_connector 版）

仅使用 `from device_connector import SSHConfig, execute_commands`，不再直接使用 Paramiko。

## 环境变量

```bash
export SSH_USERNAME='lab_user'
export SSH_PASSWORD='***'
export SSH_PORT='22'
export AUTH_MODEL='password'
# 可选：export KEY_FILENAME='/path/to/key'
```

## 使用

预演（查询并生成备份文件，不修改）：

```bash
python bgp_router_id.py inject --host 10.118.192.167 --asn 100 --peer 10.118.192.166 --state bgp_167.json
```

实际注入：**须先在隔离实验环境确认 execute_commands 同一次调用中会话持续保持、能够处理 `router-id` 的 `Continue? [Y/N]` 提示、并返回所有命令回显**。验证完成后设置 `DEVICE_CONNECTOR_INTERACTIVE_OK=1`，使用新状态文件执行：

```bash
export DEVICE_CONNECTOR_INTERACTIVE_OK=1
python bgp_router_id.py inject --state bgp_167_apply.json --apply
```

恢复：

```bash
python bgp_router_id.py recover --state bgp_167_apply.json --apply
```

## 适配器要点

- `output_text()` 可解析字符串、bytes、list、以及常见 output/stdout/results 等字段。若 `execute_commands()` 只打印而不返回文本，需要修改封装或增加可靠的回显捕获。
- 当前写操作使用 `execute_commands(config, ['system-view', 'bgp 100', 'router-id ...', 'commit', 'return'])`。不单独传入 `y`；由 `execute_commands` 内部自动处理交互式确认提示。如果该函数每条命令新建会话，或者不支持交互提示，**不可执行注入/恢复**，需先扩展连接器。
- 仅凭回显出现 `commit` 字样不构成提交成功；脚本通过再次执行 `display bgp peer` 验证 Router-ID，但设备侧是否出现部分提交或其它异常仍需人工检查。
- 每次注入需使用新状态文件以防覆盖配置备份；恢复使用注入时相同的状态文件。
- SSH 可登录仅表示技术上可访问，不等于资产系统已纳管；须结合授权/资产关系确认。
- 此脚本仅作授权隔离实验室使用，BGP Router-ID 修改可能重置多个邻居，恢复后需核对业务路由。


## 备份与无人值守确认

- `--apply` 且 `DEVICE_CONNECTOR_INTERACTIVE_OK=1` 时，不再要求手动输入设备 IP；这**不代表**连接器的交互行为已验证。实际设备变更会重置 BGP 邻居，只应在授权隔离环境操作。
- `--state` 对应文件不存在时直接创建；文件已存在且记录同一设备上未确认恢复的故障时拒绝再次注入；若旧记录已结束，创建带微秒时间戳的新备份，绝不覆盖原文件。
- 注入输出会打印最终的状态文件路径。**恢复时必须显式传入这一次产生的路径**，不能盲目沿用默认文件名。
- 若之前的连接器曾重复发送 `y`，务必先检查设备当前 Router-ID、Peer 和路由状态；修改确认流程不会自动修复连接器的重复应答问题。
