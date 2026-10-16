#!/usr/bin/env bash
# 一键启动本地演示环境：mock 环境（kubectl / 问答 / LLM）+ 后端。
# 用法：bash scripts/dev_mock.sh [--reset]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export MOCK_ROOT="$ROOT/data/mock"
export PATH="$ROOT/mock/bin:$PATH"
export SKILL_LAB_CONFIG="$ROOT/mock/config.mock.yaml"
# mock 模式下不读取 .env 里的真实 LLM 配置
export LLM_BASE_URL="http://127.0.0.1:9100/v1" LLM_API_KEY="mock" LLM_MODEL="mock-llm"

if [[ "${1:-}" == "--reset" ]]; then rm -rf "$MOCK_ROOT"; fi
if [[ ! -d "$MOCK_ROOT/workspace" ]]; then
  mkdir -p "$MOCK_ROOT/workspace" "$MOCK_ROOT/pods"
  cp -r "$ROOT/mock/demo_skills" "$MOCK_ROOT/workspace/skills"
  for p in agent-server-0 agent-server-1 agent-worker-5f7c9 gateway-6d8b7 redis-0; do mkdir -p "$MOCK_ROOT/pods/$p"; done
fi

python3 "$ROOT/mock/agent_server.py" &
MOCK_PID=$!
trap 'kill $MOCK_PID 2>/dev/null || true' EXIT
cd "$ROOT/backend"
python3 -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8600}"
