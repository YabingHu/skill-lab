#!/usr/bin/env bash
# skill 打包脚本示例：把 staging 目录打成 tar.gz。
# 用法：package_skill.sh <staging_dir> <out_file>
# staging 目录已按 skills.layout 组织（例如 zh/<skill>/...、en/<skill>/...）。
# 可以替换成你们自己的打包逻辑（加校验、生成 manifest、签名等），只要最后在 <out_file> 生成产物即可。
set -euo pipefail
staging="$1"
out="$2"
tar -czf "$out" -C "$staging" .
echo "packaged $(du -h "$out" | cut -f1) -> $out"
