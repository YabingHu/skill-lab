---
name: k8s-pod-troubleshoot
description: Standard procedure to troubleshoot abnormal Kubernetes Pods (CrashLoopBackOff, Pending, OOMKilled, etc.)
---

# Pod Troubleshooting

Use this skill when the user reports that a service is unavailable, a Pod keeps restarting, or stays Pending.

## Steps

1. Check Pod status: run `kubectl get pods -n <namespace> -o wide` and find abnormal Pods.
2. Inspect Pod events: run `kubectl describe pod <pod> -n <namespace>` and focus on Warning events.
3. Read container logs: run `kubectl logs <pod> -n <namespace> --tail=200`; add `--previous` if the container has restarted.
4. Check resource usage: run `kubectl top pod <pod> -n <namespace>` to see whether it is OOM or CPU-saturated.
5. Query the metrics API: call `GET /api/v1/metrics?pod=<pod>` to get the error rate of the last 30 minutes.
6. Conclude: summarize the findings, explain the root cause and suggest a fix.

## Notes

- Do not run destructive commands such as `kubectl delete`.
- Retry a failed tool call at most once; if it still fails, mention it in the conclusion.
