#!/usr/bin/env bash
# DEPRECATED: 已被 `python -m longbookwritter.cli run-range` 取代，新章节请用 scripts/run_range.sh。
# 本脚本保留是为了过渡期复跑既有日志，业务逻辑不再维护。
set -euo pipefail

ROOT="/mnt/afs_ocr/tongronglei/workspace/mathocr/9_longbookwritter_doubao"
BOOK_ID="reborn_coder_120"
PY="/mnt/afs_ocr/tongronglei/miniconda3/envs/myenv/bin/python"
PUBLISH_DIR="$ROOT/projects/$BOOK_ID/05_publish"
LOG_DIR="$ROOT/projects/$BOOK_ID/logs"
mkdir -p "$LOG_DIR"

TS=$(date +%Y%m%d_%H%M%S)
LOG_FILE="$LOG_DIR/generate_11_100_$TS.log"

echo "[START] $(date '+%F %T')" | tee -a "$LOG_FILE"

echo "[INFO] publish_dir=$PUBLISH_DIR" | tee -a "$LOG_FILE"

for i in $(seq 11 100); do
  file_pattern=$(printf "%04d_" "$i")
  if ls "$PUBLISH_DIR" | grep -q "^${file_pattern}"; then
    echo "[SKIP] chapter=$i already exists" | tee -a "$LOG_FILE"
    continue
  fi

  title=$(printf "主线推进%03d" "$i")
  tmp=$(mktemp)

  echo "[RUN] chapter=$i title=$title" | tee -a "$LOG_FILE"
  if ! (
    cd "$ROOT"
    unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
    LONGBOOKWRITTER_REQUEST_TIMEOUT=180 "$PY" -m longbookwritter.cli run-chapter \
      --book-id "$BOOK_ID" \
      --chapter-no "$i" \
      --chapter-title "$title" \
      --chapter-goal "苏哲" \
      --target-words 2600 \
      --min-words 2000 \
      --style-constraints "中文小说正文；单线紧凑推进；人物称呼统一且只用苏哲/王堔/张广涛/林溪/陈曦；严禁出现王经理；城市统一临江市且不得出现真实地名；必须体现重生与LLM辅助；避免复读词和模板总结" \
      --max-retries 0 > "$tmp"
  ); then
    echo "[ERROR] chapter=$i command_failed" | tee -a "$LOG_FILE"
    cat "$tmp" | tee -a "$LOG_FILE" || true
    rm -f "$tmp"
    echo "[STOP] failed at chapter=$i" | tee -a "$LOG_FILE"
    exit 2
  fi

  cat "$tmp" | tee -a "$LOG_FILE"

  if ! "$PY" - <<PY2
import json
from pathlib import Path
obj = json.loads(Path("$tmp").read_text())
ok = bool(obj.get("passed", False))
print(f"[CHECK] chapter={obj.get('chapter_no')} passed={ok} score={obj.get('review_score')} path={obj.get('published_path')}")
raise SystemExit(0 if ok else 2)
PY2
  then
    echo "[STOP] quality_gate_failed at chapter=$i" | tee -a "$LOG_FILE"
    rm -f "$tmp"
    exit 2
  fi

  rm -f "$tmp"
  echo "[DONE] chapter=$i" | tee -a "$LOG_FILE"
done

echo "[FINISH] $(date '+%F %T') all target chapters generated" | tee -a "$LOG_FILE"
