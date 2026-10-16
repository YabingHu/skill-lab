#!/usr/bin/env bash
# 生产启动：首次会安装依赖并构建前端，然后由后端统一托管前端页面与 API。
# 用法：bash scripts/start.sh            （默认端口 8600，可用 PORT=xxxx 覆盖）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[[ -f config.yaml ]] || { cp config.example.yaml config.yaml; echo "已生成 config.yaml，请按环境修改"; }
[[ -f .env ]] || { cp .env.example .env; echo "已生成 .env，请填写 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL"; }
python3 -m pip install -q -r backend/requirements.txt
if [[ ! -d frontend/dist ]]; then
  (cd frontend && npm install && npm run build)
fi
cd backend
exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8600}"
