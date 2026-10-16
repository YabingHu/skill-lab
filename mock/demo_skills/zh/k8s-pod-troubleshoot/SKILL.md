---
name: k8s-pod-troubleshoot
description: 排查 Kubernetes 中 Pod 异常（CrashLoopBackOff、Pending、OOMKilled 等）的标准流程
---

# Pod 异常排查

当用户反馈服务不可用、Pod 反复重启或一直 Pending 时使用本 skill。

## 步骤

1. 确认 Pod 状态：执行 `kubectl get pods -n <namespace> -o wide`，找出状态异常的 Pod。
2. 查看 Pod 事件：执行 `kubectl describe pod <pod> -n <namespace>`，重点关注 Events 中的 Warning。
3. 查看容器日志：执行 `kubectl logs <pod> -n <namespace> --tail=200`，如果容器已重启，加上 `--previous`。
4. 检查资源使用：执行 `kubectl top pod <pod> -n <namespace>`，判断是否存在 OOM 或 CPU 打满。
5. 查询监控接口：调用 `GET /api/v1/metrics?pod=<pod>` 获取最近 30 分钟的错误率。
6. 给出结论：汇总以上信息，说明根因和修复建议。

## 注意事项

- 不要执行 `kubectl delete` 等破坏性命令。
- 工具调用失败时最多重试 1 次，仍失败需要在结论中说明。
