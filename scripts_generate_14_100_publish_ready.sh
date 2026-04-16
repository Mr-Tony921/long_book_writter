#!/usr/bin/env bash
set -euo pipefail

ROOT="/mnt/afs_ocr/tongronglei/workspace/mathocr/9_longbookwritter_doubao"
BOOK_ID="reborn_coder_120"
PY="/mnt/afs_ocr/tongronglei/miniconda3/envs/myenv/bin/python"
PUBLISH_DIR="$ROOT/projects/$BOOK_ID/05_publish"
LOG_DIR="$ROOT/projects/$BOOK_ID/logs"
REQUEST_TIMEOUT="${LONGBOOKWRITTER_REQUEST_TIMEOUT:-420}"
CLI_MAX_RETRIES="${LONGBOOKWRITTER_CLI_MAX_RETRIES:-2}"
EXTERNAL_RETRIES="${LONGBOOKWRITTER_EXTERNAL_RETRIES:-4}"
mkdir -p "$LOG_DIR"

TS=$(date +%Y%m%d_%H%M%S)
LOG_FILE="$LOG_DIR/generate_14_100_publish_ready_$TS.log"

echo "[START] $(date '+%F %T')" | tee -a "$LOG_FILE"
echo "[CONF] timeout=${REQUEST_TIMEOUT}s cli_max_retries=${CLI_MAX_RETRIES} external_retries=${EXTERNAL_RETRIES}" | tee -a "$LOG_FILE"

for i in $(seq 14 100); do
  patt=$(printf "%04d_" "$i")
  if ls "$PUBLISH_DIR" | grep -q "^${patt}"; then
    echo "[SKIP] chapter=$i exists" | tee -a "$LOG_FILE"
    continue
  fi

  # 章节名交由写作角色基于正文自动生成与去重
  title="AUTO_TITLE"
  tmp=$(mktemp)

  echo "[RUN] chapter=$i title=$title" | tee -a "$LOG_FILE"

  done_chapter=0
  for ext_try in $(seq 1 $((EXTERNAL_RETRIES + 1))); do
    echo "[TRY] chapter=$i ext_try=$ext_try/$((EXTERNAL_RETRIES + 1))" | tee -a "$LOG_FILE"
    cmd_ok=1
    if ! (
      cd "$ROOT"
      unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
      LONGBOOKWRITTER_REQUEST_TIMEOUT="$REQUEST_TIMEOUT" "$PY" -m longbookwritter.cli run-chapter \
        --book-id "$BOOK_ID" \
        --chapter-no "$i" \
        --chapter-title "$title" \
        --chapter-goal "苏哲 推进 万客来" \
        --target-words 2600 \
        --min-words 2000 \
        --style-constraints "中文小说正文；单线紧凑推进；人物称呼统一且只用苏哲/王堔/张广涛/林溪/陈曦；严禁出现王经理；城市统一临江市且不得出现真实地名；必须体现重生与LLM辅助；时间线只能前进，禁止写六月/七月/八月/九月/夏末/初秋等回跳时间词（除非明确回忆）；避免复读词和模板总结；章节收尾有小钩子" \
        --max-retries "$CLI_MAX_RETRIES" > "$tmp"
    ); then
      cmd_ok=0
    fi

    if [[ $cmd_ok -eq 0 ]]; then
      echo "[WARN] chapter=$i command_failed ext_try=$ext_try" | tee -a "$LOG_FILE"
      cat "$tmp" | tee -a "$LOG_FILE" || true
      if (( ext_try <= EXTERNAL_RETRIES )); then
        echo "[RETRY] chapter=$i reason=command_failed" | tee -a "$LOG_FILE"
        sleep 2
        continue
      fi
      echo "[STOP] failed_at=$i reason=command_failed_retry_exhausted" | tee -a "$LOG_FILE"
      rm -f "$tmp"
      exit 2
    fi

    cat "$tmp" | tee -a "$LOG_FILE"
    if "$PY" - <<PY2
import json
from pathlib import Path
obj = json.loads(Path("$tmp").read_text())
ok = bool(obj.get("passed", False))
print(f"[CHECK] chapter={obj.get('chapter_no')} passed={ok} score={obj.get('review_score')} path={obj.get('published_path')}")
if ok:
    raise SystemExit(0)

attempts = obj.get("attempts", []) or []
errors = " | ".join(str(a.get("draft_error", "")) for a in attempts).lower()
network_markers = [
    "read timed out",
    "request failed",
    "network_error",
    "operation not permitted",
    "max retries exceeded",
    "failed to establish a new connection",
]
if any(k in errors for k in network_markers):
    print("[ACTION] retry_network")
    raise SystemExit(11)

issues = []
for a in attempts:
    review = a.get("review") or {}
    issues.extend(review.get("issues") or [])
plot_block = any(
    str(x.get("severity", "")).lower() == "high"
    and str(x.get("type", "")).lower() in {"consistency", "logic"}
    for x in issues
)
if plot_block:
    print("[ACTION] stop_need_user_plot_confirmation")
    raise SystemExit(21)

print("[ACTION] retry_quality")
raise SystemExit(12)
PY2
    then
      done_chapter=1
      break
    else
      rc=$?
      if [[ "$rc" == "11" || "$rc" == "12" ]]; then
        if (( ext_try <= EXTERNAL_RETRIES )); then
          echo "[RETRY] chapter=$i reason=rc_${rc}" | tee -a "$LOG_FILE"
          sleep 2
          continue
        fi
        echo "[STOP] failed_at=$i reason=retry_exhausted rc=${rc}" | tee -a "$LOG_FILE"
        rm -f "$tmp"
        exit 2
      fi
      if [[ "$rc" == "21" ]]; then
        echo "[STOP] need_user_confirmation_at=$i reason=plot_consistency_high" | tee -a "$LOG_FILE"
        rm -f "$tmp"
        exit 3
      fi
      echo "[STOP] failed_at=$i reason=unknown_check_rc_${rc}" | tee -a "$LOG_FILE"
      rm -f "$tmp"
      exit 2
    fi
  done

  if [[ "$done_chapter" != "1" ]]; then
    echo "[STOP] failed_at=$i reason=unexpected_loop_exit" | tee -a "$LOG_FILE"
    rm -f "$tmp"
    exit 2
  fi
  rm -f "$tmp"
  echo "[DONE] chapter=$i" | tee -a "$LOG_FILE"
done

echo "[FINISH] $(date '+%F %T')" | tee -a "$LOG_FILE"
