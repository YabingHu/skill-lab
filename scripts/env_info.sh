#!/usr/bin/env bash
# 环境信息脚本示例：输出原样展示在「环境」页面。按需替换成你们的脚本。
# 用法：env_info.sh <namespace>
ns="${1:-default}"
echo "== kubectl context =="
kubectl config current-context 2>/dev/null || echo "(无)"
echo
echo "== pods in $ns =="
kubectl get pods -n "$ns" 2>&1
