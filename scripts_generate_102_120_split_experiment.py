#!/usr/bin/env python3
# DEPRECATED: 已被 `python -m longbookwritter.cli run-range` 取代。
# 本脚本的拆分式生成绕过了 ReviewerAgent，质量门槛弱于 1-101 章；如需续写请改用 run-range。
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from longbookwritter.config import load_settings
from longbookwritter.orchestrator import LongBookWritterOrchestrator
from longbookwritter.utils.io import append_text, write_json


ROOT = Path("/mnt/afs_ocr/tongronglei/workspace/mathocr/9_longbookwritter_doubao")
BOOK_ID = "reborn_coder_120"
START_CHAPTER = 102
END_CHAPTER = 120
MIN_FINAL_LEN = 2000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_len(text: str) -> int:
    return len(re.sub(r"\s+", "", text or ""))


def _first_file_for_no(publish_dir: Path, chapter_no: int) -> Path | None:
    patt = f"{chapter_no:04d}_*.md"
    files = sorted(publish_dir.glob(patt))
    return files[0] if files else None


def _chapter_len(md_file: Path) -> int:
    text = md_file.read_text(encoding="utf-8")
    body = re.sub(r"^#.*?\n", "", text, flags=re.S).strip()
    return _clean_len(body)


def _generate_piece(
    orch: LongBookWritterOrchestrator,
    chapter_no: int,
    chapter_title: str,
    part_goal: str,
    style_constraints: str,
    context_summary: str,
    character_constraints: dict,
    planner_brief: str,
    scene_plan: list[str],
    min_len: int,
    max_attempts: int,
) -> tuple[str, list[dict]]:
    logs: list[dict] = []
    retry_guidance = ""
    for i in range(1, max_attempts + 1):
        print(f"[GEN] chapter={chapter_no} part_goal={part_goal[:18]}... attempt={i}", flush=True)
        res = orch.writer.draft_chapter(
            chapter_goal=part_goal,
            style_constraints=style_constraints,
            target_words=1000,
            context_summary=context_summary,
            character_constraints=character_constraints,
            planner_brief=planner_brief,
            scene_plan=scene_plan,
            chapter_no=chapter_no,
            chapter_title=chapter_title,
            retry_guidance=retry_guidance,
        )
        if not res.get("ok", False):
            err = str(res.get("error", "writer_failed"))
            logs.append({"attempt": i, "ok": False, "error": err})
            print(f"[FAIL] chapter={chapter_no} attempt={i} error={err}", flush=True)
            # Gateway congestion: mysql lock wait timeout (1205). Use backoff before retry.
            if "1205" in err or "Lock wait timeout" in err:
                backoff = min(20, i * 4)
                print(f"[BACKOFF] chapter={chapter_no} attempt={i} sleep={backoff}s", flush=True)
                time.sleep(backoff)
            retry_guidance = f"上次失败：{err}。请仅输出小说正文，长度约1000字。"
            continue
        text = (res.get("draft") or "").strip()
        ln = _clean_len(text)
        if ln < min_len:
            logs.append({"attempt": i, "ok": False, "error": f"too_short:{ln}"})
            print(f"[FAIL] chapter={chapter_no} attempt={i} too_short={ln}", flush=True)
            retry_guidance = f"上次正文偏短（{ln}字）。请扩展到不少于{min_len}字。"
            continue
        if "场景1" in text or "本章目标" in text:
            logs.append({"attempt": i, "ok": False, "error": "script_like_output"})
            print(f"[FAIL] chapter={chapter_no} attempt={i} script_like_output", flush=True)
            retry_guidance = "不要输出流程化标签，只输出可发布小说正文。"
            continue
        logs.append({"attempt": i, "ok": True, "len": ln})
        print(f"[OK] chapter={chapter_no} attempt={i} len={ln}", flush=True)
        return text, logs
    return "", logs


def _analyze_transition(publish_dir: Path) -> dict:
    map_tokens = [
        "办公室",
        "会议室",
        "机房",
        "医院",
        "码头",
        "仓库",
        "法庭",
        "车库",
        "写字楼",
        "总部",
        "园区",
        "灯塔",
        "酒店",
        "机场",
        "车站",
        "餐厅",
        "咖啡馆",
        "公寓",
        "别墅",
        "工厂",
    ]
    action_tokens = [
        "取证",
        "追查",
        "对质",
        "反制",
        "救援",
        "突围",
        "交付",
        "谈判",
        "抓捕",
        "撤离",
        "潜入",
        "埋伏",
        "转移",
        "围堵",
        "摊牌",
    ]

    rows: list[dict] = []
    for cno in range(START_CHAPTER, END_CHAPTER + 1):
        fp = _first_file_for_no(publish_dir=publish_dir, chapter_no=cno)
        if not fp:
            continue
        text = fp.read_text(encoding="utf-8")
        body = re.sub(r"^#.*?\n", "", text, flags=re.S).strip()
        hit_map = sorted({tok for tok in map_tokens if tok in body})
        hit_action = sorted({tok for tok in action_tokens if tok in body})
        rows.append(
            {
                "chapter_no": cno,
                "file": str(fp),
                "map_tokens": hit_map,
                "map_count": len(hit_map),
                "action_count": len(hit_action),
            }
        )

    for i, row in enumerate(rows):
        if i == 0:
            row["map_changed_vs_prev"] = None
            continue
        prev = rows[i - 1]
        row["map_changed_vs_prev"] = bool(set(row["map_tokens"]) - set(prev["map_tokens"]))

    window_risks: list[dict] = []
    for i in range(2, len(rows)):
        w = rows[i - 2 : i + 1]
        same_scene_risk = all((x["map_count"] <= 1 and x["action_count"] <= 1) for x in w)
        if same_scene_risk:
            window_risks.append(
                {
                    "end_chapter": rows[i]["chapter_no"],
                    "risk": "3-chapter low-scene-variation",
                    "chapters": [x["chapter_no"] for x in w],
                }
            )

    return {
        "range": [START_CHAPTER, END_CHAPTER],
        "chapter_count_found": len(rows),
        "rows": rows,
        "window_risks": window_risks,
    }


def main() -> int:
    settings = load_settings()
    orch = LongBookWritterOrchestrator(settings=settings)

    project_dir = settings.projects_dir / BOOK_ID
    publish_dir = project_dir / "05_publish"
    logs_dir = project_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_log = logs_dir / f"generate_102_120_split_experiment_{ts}.json"
    transition_report = logs_dir / f"transition_report_102_120_split_{ts}.json"

    style_constraints = (
        "中文小说正文；紧凑推进；人物称呼统一且只用苏哲/王堔/张广涛/林溪/陈曦；"
        "城市统一临江市；避免模板总结；不每章强制换地图，但连续多章不得同地点同冲突打圈；"
        "阶段性转场需服务剧情推进。"
    )

    plan_data = orch._load_plan(project_dir)
    anchors = plan_data.get("plan", {}).get("anchors", {})
    character_constraints = orch._load_character_constraints(project_dir)
    story_facts = orch._load_story_facts(project_dir)

    run_items: list[dict] = []
    failed = False
    failed_reason = ""

    for cno in range(START_CHAPTER, END_CHAPTER + 1):
        print(f"[CHAPTER] start={cno}", flush=True)
        existing = _first_file_for_no(publish_dir=publish_dir, chapter_no=cno)
        if existing:
            existing_len = _chapter_len(existing)
            if existing_len >= MIN_FINAL_LEN:
                print(f"[CHAPTER] skip_exists={cno} file={existing.name} len={existing_len}", flush=True)
                run_items.append(
                    {"chapter_no": cno, "status": "skipped_exists", "file": str(existing), "len": existing_len}
                )
                continue
            print(f"[CHAPTER] rewrite_short={cno} file={existing.name} len={existing_len}", flush=True)

        chapter_title = f"主线推进{cno}"
        if existing and "_" in existing.stem:
            chapter_title = existing.stem.split("_", 1)[1]
        chapter_goal = "苏哲推进主线，完成至少一个不可逆变化（关系/证据/胜负其一），必要时阶段性换地图。"

        memory = orch.memory.load_memory(project_dir / "02_memory" / "memory.json")
        context_summary = orch._build_context_summary(
            plan_data=plan_data,
            memory=memory,
            chapter_no=cno,
            character_constraints=character_constraints,
            story_facts=story_facts,
        )
        scene_plan = orch._build_scene_plan(chapter_goal=chapter_goal, anchors=anchors)
        planner_brief = orch.planner.build_writer_brief(
            chapter_no=cno,
            chapter_title=chapter_title,
            chapter_goal=chapter_goal,
            story_facts=story_facts,
            recap_state=memory.get("recap_state", {}),
        )

        part1_goal = "本章上半：承接上一章，推进当前冲突并明确新的行动目标。"
        part1, part1_logs = _generate_piece(
            orch=orch,
            chapter_no=cno,
            chapter_title=chapter_title,
            part_goal=part1_goal,
            style_constraints=style_constraints,
            context_summary=context_summary,
            character_constraints=character_constraints,
            planner_brief=planner_brief,
            scene_plan=scene_plan,
            min_len=1000,
            max_attempts=10,
        )
        if not part1:
            print(f"[STOP] chapter={cno} part1_failed", flush=True)
            failed = True
            failed_reason = f"chapter_{cno}_part1_failed"
            run_items.append(
                {
                    "chapter_no": cno,
                    "status": "failed",
                    "stage": "part1",
                    "logs": part1_logs,
                }
            )
            break

        part2_goal = "本章下半：执行行动并形成不可逆推进，结尾留下一章钩子。"
        part2_context = context_summary + "\n本章上半摘要：" + part1[:500]
        part2, part2_logs = _generate_piece(
            orch=orch,
            chapter_no=cno,
            chapter_title=chapter_title,
            part_goal=part2_goal,
            style_constraints=style_constraints,
            context_summary=part2_context,
            character_constraints=character_constraints,
            planner_brief=planner_brief,
            scene_plan=scene_plan,
            min_len=1000,
            max_attempts=10,
        )
        if not part2:
            print(f"[STOP] chapter={cno} part2_failed", flush=True)
            failed = True
            failed_reason = f"chapter_{cno}_part2_failed"
            run_items.append(
                {
                    "chapter_no": cno,
                    "status": "failed",
                    "stage": "part2",
                    "logs": {"part1": part1_logs, "part2": part2_logs},
                }
            )
            break

        final_text = orch._sanitize_for_publish((part1.strip() + "\n\n" + part2.strip()).strip())
        final_len = _clean_len(final_text)
        if final_len < MIN_FINAL_LEN:
            print(f"[STOP] chapter={cno} merged_too_short={final_len}", flush=True)
            failed = True
            failed_reason = f"chapter_{cno}_merged_too_short_{final_len}"
            run_items.append(
                {
                    "chapter_no": cno,
                    "status": "failed",
                    "stage": "merge",
                    "final_len": final_len,
                    "logs": {"part1": part1_logs, "part2": part2_logs},
                }
            )
            break

        publish_path = orch.publisher.publish_chapter(
            publish_dir=publish_dir,
            chapter_no=cno,
            chapter_title=chapter_title,
            chapter_text=final_text,
        )
        # remove older duplicate chapter files with same chapter_no but different title
        for stale in sorted(publish_dir.glob(f"{cno:04d}_*.md")):
            if stale.resolve() != publish_path.resolve():
                stale.unlink(missing_ok=True)
        orch._update_project_index(project_dir=project_dir, chapter_no=cno)
        orch._update_memory(project_dir=project_dir, chapter_no=cno, chapter_title=chapter_title, chapter_goal=chapter_goal)
        orch._refresh_recap(project_dir=project_dir, plan_data=plan_data)

        run_items.append(
            {
                "chapter_no": cno,
                "status": "published",
                "title": chapter_title,
                "len": final_len,
                "file": str(publish_path),
                "logs": {"part1": part1_logs, "part2": part2_logs},
            }
        )
        print(f"[CHAPTER] published={cno} len={final_len}", flush=True)

    transition = _analyze_transition(publish_dir=publish_dir)
    run_payload = {
        "book_id": BOOK_ID,
        "range": [START_CHAPTER, END_CHAPTER],
        "failed": failed,
        "failed_reason": failed_reason,
        "items": run_items,
        "transition_report_file": str(transition_report),
        "generated_at_utc": _now(),
        "env_timeout": os.getenv("LONGBOOKWRITTER_REQUEST_TIMEOUT", ""),
    }

    write_json(run_log, run_payload)
    write_json(transition_report, transition)
    append_text(
        project_dir / "99_engineering" / "RUN_LOG.md",
        f"\n- [{_now()}] [change/info] split experiment 102-120 finished failed={failed} reason={failed_reason}\n",
    )

    print(json.dumps({"run_log": str(run_log), "transition_report": str(transition_report), "failed": failed, "failed_reason": failed_reason}, ensure_ascii=False))
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
