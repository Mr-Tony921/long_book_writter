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

REQUEST_TIMEOUT="${LONGBOOKWRITTER_REQUEST_TIMEOUT:-120}"
CLI_MAX_RETRIES="${LONGBOOKWRITTER_CLI_MAX_RETRIES:-2}"
EXTERNAL_RETRIES="${LONGBOOKWRITTER_EXTERNAL_RETRIES:-5}"

TS=$(date +%Y%m%d_%H%M%S)
LOG_FILE="$LOG_DIR/generate_102_120_experiment_$TS.log"
REPORT_FILE="$LOG_DIR/transition_report_102_120_$TS.json"

echo "[START] $(date '+%F %T')" | tee -a "$LOG_FILE"
echo "[CONF] timeout=${REQUEST_TIMEOUT}s cli_max_retries=${CLI_MAX_RETRIES} external_retries=${EXTERNAL_RETRIES}" | tee -a "$LOG_FILE"

for i in $(seq 102 120); do
  patt=$(printf "%04d_" "$i")
  if ls "$PUBLISH_DIR" | grep -q "^${patt}"; then
    echo "[SKIP] chapter=$i exists" | tee -a "$LOG_FILE"
    continue
  fi

  # chapter title is user-provided; orchestrator keeps it directly if valid.
  title=$(printf "主线推进%03d" "$i")
  tmp=$(mktemp)
  done_chapter=0

  echo "[RUN] chapter=$i title=$title" | tee -a "$LOG_FILE"
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
        --chapter-goal "苏哲推进主线，完成至少一个不可逆变化（关系/证据/胜负其一），并在必要时阶段性换地图" \
        --target-words 2200 \
        --min-words 1800 \
        --style-constraints "中文小说正文；紧凑推进；人物称呼统一且只用苏哲/王堔/张广涛/林溪/陈曦；城市统一临江市；避免模板总结；不每章强制换地图，但连续多章不得同地点同冲突打圈；阶段性转场需服务剧情推进" \
        --max-retries "$CLI_MAX_RETRIES" > "$tmp"
    ); then
      cmd_ok=0
    fi

    if [[ "$cmd_ok" -eq 0 ]]; then
      echo "[WARN] chapter=$i command_failed ext_try=$ext_try" | tee -a "$LOG_FILE"
      cat "$tmp" | tee -a "$LOG_FILE" || true
      if (( ext_try <= EXTERNAL_RETRIES )); then
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
ok = bool(obj.get("passed", False)) and bool(obj.get("published_path"))
print(f"[CHECK] chapter={obj.get('chapter_no')} passed={ok} score={obj.get('review_score')} path={obj.get('published_path')}")
raise SystemExit(0 if ok else 12)
PY2
    then
      done_chapter=1
      break
    else
      if (( ext_try <= EXTERNAL_RETRIES )); then
        echo "[RETRY] chapter=$i reason=quality_or_network" | tee -a "$LOG_FILE"
        sleep 2
        continue
      fi
      echo "[STOP] failed_at=$i reason=retry_exhausted" | tee -a "$LOG_FILE"
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

echo "[ANALYZE] transition scan for chapters 102-120" | tee -a "$LOG_FILE"
"$PY" - <<PY3 > "$REPORT_FILE"
import json
import re
from pathlib import Path

root = Path("/mnt/afs_ocr/tongronglei/workspace/mathocr/9_longbookwritter_doubao")
pub = root / "projects/reborn_coder_120/05_publish"
files = []
for i in range(102, 121):
    patt = f"{i:04d}_"
    cand = sorted([p for p in pub.glob(f"{patt}*.md")])
    if cand:
        files.append(cand[0])

map_tokens = ["办公室","会议室","机房","医院","码头","仓库","法庭","车库","写字楼","总部","园区","灯塔","酒店","机场","车站","餐厅","咖啡馆","公寓","别墅","工厂"]
action_tokens = ["取证","追查","对质","反制","救援","突围","交付","谈判","抓捕","撤离","潜入","埋伏","转移","围堵","摊牌"]

rows = []
for fp in files:
    text = fp.read_text(encoding="utf-8")
    body = re.sub(r"^#.*?\n", "", text, flags=re.S).strip()
    hit_map = sorted({t for t in map_tokens if t in body})
    hit_action = sorted({t for t in action_tokens if t in body})
    rows.append({
        "chapter_no": int(fp.name[:4]),
        "file": str(fp),
        "map_tokens": hit_map,
        "map_count": len(hit_map),
        "action_count": len(hit_action),
    })

for idx, r in enumerate(rows):
    prev = rows[idx-1] if idx > 0 else None
    if not prev:
        r["map_changed_vs_prev"] = None
        continue
    r["map_changed_vs_prev"] = bool(set(r["map_tokens"]) - set(prev["map_tokens"]))

window_flags = []
for idx in range(len(rows)):
    window = rows[max(0, idx-2):idx+1]
    same_scene_risk = all((w["map_count"] <= 1 and w["action_count"] <= 1) for w in window) and len(window) == 3
    if same_scene_risk:
        window_flags.append({
            "end_chapter": rows[idx]["chapter_no"],
            "risk": "3-chapter low-scene-variation",
            "chapters": [w["chapter_no"] for w in window]
        })

out = {
    "generated_range": [102, 120],
    "chapter_count_found": len(rows),
    "rows": rows,
    "window_risks": window_flags,
}
print(json.dumps(out, ensure_ascii=False, indent=2))
PY3

echo "[REPORT] $REPORT_FILE" | tee -a "$LOG_FILE"
echo "[FINISH] $(date '+%F %T')" | tee -a "$LOG_FILE"
