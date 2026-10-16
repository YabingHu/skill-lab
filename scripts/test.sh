#!/usr/bin/env bash
# 一键测试：单元测试 + 基于 MOCK 数据的流程测试（自动搭建隔离的模拟环境，无需 k8s / LLM / 预先启动服务）
# 用法：bash scripts/test.sh [pytest 参数]，例如 bash scripts/test.sh -k deploy -v
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"
python3 -m pytest -q -p no:cacheprovider tests "$@"
