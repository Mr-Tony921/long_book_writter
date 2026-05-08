#!/usr/bin/env bash
# Thin wrapper for `python -m longbookwritter.cli run-range`.
# 不承载任何业务逻辑，仅锁定解释器、清理代理变量，再透传所有参数。

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${LONGBOOKWRITTER_PY:-/mnt/afs_ocr/tongronglei/miniconda3/envs/myenv/bin/python}"

unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY

cd "$ROOT"
exec "$PY" -m longbookwritter.cli run-range "$@"
