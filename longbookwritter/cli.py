import argparse
import json
import time
from pathlib import Path

from longbookwritter.agents.card_seeder import CardSeeder, format_card_review
from longbookwritter.cards.store import list_character_cards
from longbookwritter.config import load_settings
from longbookwritter.llm.doubao_client import DoubaoTextClient
from longbookwritter.orchestrator import LongBookWritterOrchestrator
from longbookwritter.plan_hitl import HitlPlanner, list_volume_statuses
from longbookwritter.schemas import PlanInput
from longbookwritter.utils.io import read_json, write_json, write_text
from longbookwritter.utils.profile import load_run_profile


CONNECTIVITY_HINTS = {
    "network_error": [
        "确认能解析 DOUBAO_API_HOST 或直接走 DOUBAO_API_IP",
        "若集群内仅允许 IP 出网，请将 DOUBAO_USE_IP_ROUTE 保持 true",
        "排查 http_proxy/https_proxy 是否拦截了内部网关",
    ],
    "timeout_error": [
        "增大 LONGBOOKWRITTER_REQUEST_TIMEOUT（单位秒）",
        "保持非流式（DOUBAO_ENABLE_STREAM=false）以避免长时序断流",
        "若网关繁忙，错峰重试或缩短 prompt",
    ],
    "http_error": [
        "检查 API key（DOUBAO_API_KEY）是否过期或权限不足",
        "确认 channel_code 与请求路径与网关侧一致",
    ],
    "business_error": [
        "查看返回 code 与 message，确认 model 名称（DOUBAO_MODEL/DOUBAO_LITE_MODEL）有效",
        "检查 transaction_id 是否被网关限流",
    ],
    "parse_error": [
        "网关返回结构异常，可能是模型路由错配；尝试切换 lite/full 模型",
        "保留 raw_response 反馈给网关侧排查",
    ],
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LongBookWritter CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init-project", help="Initialize a book project")
    p_init.add_argument("--book-id", required=True)
    p_init.add_argument("--title", required=True)
    p_init.add_argument(
        "--hitl",
        action="store_true",
        help="Mark book_config.hitl.required=true so run-range refuses unapproved volumes.",
    )

    p_plan = sub.add_parser("plan", help="Generate initial master plan")
    p_plan.add_argument("--book-id", required=True)
    p_plan.add_argument("--brief", required=True)
    p_plan.add_argument("--target-words", type=int, default=50000)
    p_plan.add_argument("--tone", default="中文网文，人物驱动，强情节")
    p_plan.add_argument("--user-mode", choices=["novice", "pro"], default="novice")
    p_plan.add_argument("--must-keep", default="")
    p_plan.add_argument("--banned", default="")
    p_plan.add_argument("--direction-hint", default="")

    p_title = sub.add_parser("suggest-titles", help="Suggest candidate titles")
    p_title.add_argument("--book-id", required=True)
    p_title.add_argument("--current-stage", required=True)
    p_title.add_argument("--style", choices=["serious", "funny", "mixed"], default="mixed")
    p_title.add_argument("--count", type=int, default=8)

    p_run = sub.add_parser("run-chapter", help="Run one chapter through write-review-edit-publish pipeline")
    p_run.add_argument("--book-id", required=True)
    p_run.add_argument("--chapter-no", required=True, type=int)
    p_run.add_argument("--chapter-title", required=True)
    p_run.add_argument("--chapter-goal", required=True)
    p_run.add_argument("--target-words", type=int, default=2200)
    p_run.add_argument("--min-words", type=int, default=2000)
    p_run.add_argument("--style-constraints", default="自然中文叙事，减少AI味，推进冲突")
    p_run.add_argument("--max-retries", type=int, default=2)
    p_run.add_argument("--save-artifacts", action="store_true", help="Save draft/review artifacts (off by default)")

    p_range = sub.add_parser(
        "run-range",
        help="Run chapters in [start, end] serially with external retries (network/quality/plot classification).",
    )
    p_range.add_argument("--book-id", required=True)
    p_range.add_argument("--start", required=True, type=int)
    p_range.add_argument("--end", required=True, type=int)
    p_range.add_argument("--profile", default=None, help="Optional path to run_profile.json override")
    p_range.add_argument("--chapter-goal", default=None)
    p_range.add_argument("--style-constraints", default=None)
    p_range.add_argument("--target-words", type=int, default=None)
    p_range.add_argument("--min-words", type=int, default=None)
    p_range.add_argument("--max-retries", type=int, default=None)
    p_range.add_argument("--external-retries", type=int, default=None)
    p_range.add_argument("--no-skip-existing", dest="skip_existing", action="store_false")
    p_range.add_argument("--no-stop-on-plot-block", dest="stop_on_plot_block", action="store_false")
    p_range.add_argument("--save-artifacts", action="store_true")
    p_range.set_defaults(skip_existing=True, stop_on_plot_block=True)

    p_draft = sub.add_parser(
        "plan-draft",
        help="Draft a per-volume plan into 01_plan/draft/volume_<N>.md (LLM, human-editable).",
    )
    p_draft.add_argument("--book-id", required=True)
    p_draft.add_argument("--volume", required=True, type=int)
    p_draft.add_argument("--brief", default="", help="Extra steering text for the planner.")
    p_draft.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing draft file (otherwise plan-draft is a no-op).",
    )

    p_review = sub.add_parser(
        "plan-review",
        help="List per-volume draft + approval state. SHA mismatch means the user edited the draft.",
    )
    p_review.add_argument("--book-id", required=True)

    p_approve = sub.add_parser(
        "plan-approve",
        help="Lock the (possibly edited) draft into master_plan.json with SHA stamp.",
    )
    p_approve.add_argument("--book-id", required=True)
    p_approve.add_argument("--volume", required=True, type=int)

    p_seed = sub.add_parser(
        "seed-cards",
        help=(
            "Auto-fill character/event card baselines from 00_config/seed_roster.json. "
            "Writes one JSON card per entry plus a _seed_review.md with A/B/C options."
        ),
    )
    p_seed.add_argument("--book-id", required=True)
    p_seed.add_argument(
        "--only", default=None,
        help="Restrict to a single character name or event_id (substring match).",
    )
    p_seed.add_argument(
        "--only-characters", action="store_true",
        help="Skip the events section of the roster.",
    )
    p_seed.add_argument(
        "--only-events", action="store_true",
        help="Skip the characters section of the roster.",
    )
    p_seed.add_argument(
        "--force", action="store_true",
        help="Overwrite existing cards (otherwise existing cards are skipped).",
    )

    p_seed_cfg = sub.add_parser(
        "seed-config",
        help=(
            "Derive 00_config/story_facts.json and character_constraints.json from "
            "the existing character cards. Run after seed-cards (and after any edits)."
        ),
    )
    p_seed_cfg.add_argument("--book-id", required=True)
    p_seed_cfg.add_argument(
        "--force", action="store_true",
        help="Overwrite story_facts / character_constraints content (otherwise merge).",
    )

    p_check = sub.add_parser(
        "check-connectivity",
        help="Probe Doubao API end-to-end and print actionable diagnostic.",
    )
    p_check.add_argument("--prompt", default="ping，请回复 ok")
    p_check.add_argument("--model", default=None, help="Override model name (defaults to DOUBAO_LITE_MODEL)")
    return parser


def _run_check_connectivity(args) -> int:
    settings = load_settings()
    client = DoubaoTextClient(settings=settings)
    diagnostics = {
        "api_host": settings.doubao_api_host,
        "api_url": client.api_url,
        "use_ip_route": settings.doubao_use_ip_route,
        "api_ip": settings.doubao_api_ip,
        "connect_timeout_s": settings.connect_timeout_seconds,
        "request_timeout_s": settings.request_timeout_seconds,
        "enable_stream": settings.doubao_enable_stream,
        "stream_first": settings.doubao_stream_first,
        "proxy_url": settings.doubao_proxy_url,
        "model": args.model or settings.doubao_lite_model,
    }
    started = time.monotonic()
    result = client.generate_text(prompt_text=args.prompt, model=args.model)
    latency_s = round(time.monotonic() - started, 3)

    payload = {
        "ok": result.success,
        "latency_s": latency_s,
        "diagnostics": diagnostics,
        "content_preview": (result.content or "")[:200],
        "error_type": result.error_type,
        "status_code": result.status_code,
    }
    if not result.success:
        payload["hints"] = CONNECTIVITY_HINTS.get(
            result.error_type or "",
            ["未识别错误类型，请把 raw_response 提供给网关侧排查"],
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if result.success:
        return 0
    if result.error_type == "parse_error":
        return 2
    return 1


def _gather_book_context(project_dir: Path) -> dict:
    book_cfg_path = project_dir / "00_config" / "book_config.json"
    plan_path = project_dir / "01_plan" / "master_plan.json"
    ctx: dict = {}
    if book_cfg_path.exists():
        try:
            cfg = read_json(book_cfg_path)
            ctx["title"] = cfg.get("title", "")
            ctx["canon_reference"] = cfg.get("canon_reference", "")
        except Exception:
            pass
    if plan_path.exists():
        try:
            plan = read_json(plan_path)
            inner = plan.get("plan", {}) or {}
            ctx["book_positioning"] = inner.get("book_positioning", "")
            ctx["core_theme"] = inner.get("core_theme", "")
            ctx["core_conflict"] = inner.get("core_conflict", "")
            ctx["style"] = inner.get("tone", "") or plan.get("input", {}).get("tone", "")
        except Exception:
            pass
    return ctx


def _run_seed_cards(settings, args) -> int:
    project_dir = settings.projects_dir / args.book_id
    if not project_dir.exists():
        print(json.dumps({"ok": False, "error": f"project_dir_missing: {project_dir}"}, ensure_ascii=False, indent=2))
        return 2
    roster_path = project_dir / "00_config" / "seed_roster.json"
    if not roster_path.exists():
        print(json.dumps(
            {"ok": False, "error": f"seed_roster_missing: {roster_path}",
             "hint": "Run init-project first, then edit seed_roster.json with your characters/events."},
            ensure_ascii=False, indent=2,
        ))
        return 2
    try:
        roster = read_json(roster_path)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"seed_roster_unreadable: {exc}"}, ensure_ascii=False, indent=2))
        return 2

    book_ctx = _gather_book_context(project_dir)
    client = DoubaoTextClient(settings=settings)
    seeder = CardSeeder(llm_client=client)

    char_results: list[dict] = []
    event_results: list[dict] = []

    if not args.only_events:
        for entry in roster.get("characters", []) or []:
            name = str(entry.get("name", "")).strip()
            if not name or name.startswith("示例") or name.startswith("_"):
                continue
            if args.only and args.only not in name:
                continue
            res = seeder.seed_character_card(
                name=name,
                is_canon=bool(entry.get("is_canon", False)),
                brief=str(entry.get("brief", "") or ""),
                book_context=book_ctx,
                project_dir=project_dir,
                force=args.force,
            )
            char_results.append(res)

    if not args.only_characters:
        for entry in roster.get("events", []) or []:
            event_id = str(entry.get("event_id", "")).strip()
            if not event_id or event_id.startswith("example_") or event_id.startswith("_"):
                continue
            if args.only and args.only not in event_id:
                continue
            res = seeder.seed_event_card(
                event_id=event_id,
                is_canon=bool(entry.get("is_canon", False)),
                name=str(entry.get("name", "") or ""),
                brief=str(entry.get("brief", "") or ""),
                planned_chapter_range=str(entry.get("planned_chapter_range", "") or ""),
                book_context=book_ctx,
                project_dir=project_dir,
                force=args.force,
            )
            event_results.append(res)

    review_path = project_dir / "00_config" / "cards" / "_seed_review.md"
    write_text(review_path, format_card_review(char_results, event_results))

    payload = {
        "ok": True,
        "book_id": args.book_id,
        "characters_seeded": sum(1 for r in char_results if r.get("path")),
        "characters_skipped": sum(1 for r in char_results if r.get("skipped")),
        "characters_failed": sum(1 for r in char_results if r.get("error")),
        "events_seeded": sum(1 for r in event_results if r.get("path")),
        "events_skipped": sum(1 for r in event_results if r.get("skipped")),
        "events_failed": sum(1 for r in event_results if r.get("error")),
        "review_path": str(review_path),
        "characters": char_results,
        "events": event_results,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if payload["characters_failed"] or payload["events_failed"]:
        return 3
    return 0


def _run_seed_config(settings, args) -> int:
    project_dir = settings.projects_dir / args.book_id
    if not project_dir.exists():
        print(json.dumps({"ok": False, "error": f"project_dir_missing: {project_dir}"}, ensure_ascii=False, indent=2))
        return 2
    cards = list_character_cards(project_dir)
    if not cards:
        print(json.dumps(
            {"ok": False, "error": "no_character_cards", "hint": "run seed-cards first"},
            ensure_ascii=False, indent=2,
        ))
        return 2

    # Pick the highest-importance protagonist (or fall back to importance order).
    cards_sorted = sorted(cards, key=lambda c: (c.role == "protagonist", c.importance), reverse=True)
    protagonist = cards_sorted[0]

    fixed_names = sorted({c.name for c in cards if c.name})
    banned_phrases: list[str] = []
    # Collect protagonist + female_lead secrets as banned phrases (rough proxy: hard mentions).
    for c in cards:
        if c.role in ("protagonist", "female_lead") and c.secret:
            # The secret value itself is rarely a leak phrase; we add the most explicit short forms.
            for token in ("我来自现代", "我穿越来的", "21世纪", "现代地球", "穿越者"):
                if token not in banned_phrases:
                    banned_phrases.append(token)
            break

    story_facts_path = project_dir / "00_config" / "story_facts.json"
    char_constraints_path = project_dir / "00_config" / "character_constraints.json"

    if args.force or not story_facts_path.exists():
        existing_facts: dict = {}
    else:
        try:
            existing_facts = read_json(story_facts_path)
        except Exception:
            existing_facts = {}
    facts = {
        "protagonist_name": existing_facts.get("protagonist_name") or protagonist.name,
        "city": existing_facts.get("city", ""),
        "time_period": existing_facts.get("time_period", ""),
        "must_mention": existing_facts.get("must_mention", []) or [],
        "notes": existing_facts.get("notes", "由 seed-config 从角色卡派生；可手动覆盖。"),
    }
    write_json(story_facts_path, facts)

    if args.force or not char_constraints_path.exists():
        existing_cc: dict = {}
    else:
        try:
            existing_cc = read_json(char_constraints_path)
        except Exception:
            existing_cc = {}
    cc = {
        "fixed_names": sorted(set((existing_cc.get("fixed_names", []) or []) + fixed_names)),
        "banned_aliases": existing_cc.get("banned_aliases", {}) or {},
        "banned_phrases": sorted(set((existing_cc.get("banned_phrases", []) or []) + banned_phrases)),
        "notes": existing_cc.get(
            "notes",
            "fixed_names 自动汇总自角色卡；banned_phrases 包含通用穿越者身份保密关键词。可手动补充。",
        ),
    }
    write_json(char_constraints_path, cc)

    payload = {
        "ok": True,
        "protagonist_name": facts["protagonist_name"],
        "fixed_names_count": len(cc["fixed_names"]),
        "banned_phrases_count": len(cc["banned_phrases"]),
        "story_facts_path": str(story_facts_path),
        "character_constraints_path": str(char_constraints_path),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _run_run_range(args) -> int:
    settings = load_settings()
    project_dir = settings.projects_dir / args.book_id
    if not project_dir.exists():
        print(json.dumps(
            {"ok": False, "error": f"project_dir_missing: {project_dir}"},
            ensure_ascii=False, indent=2,
        ))
        return 2

    override_path = Path(args.profile) if args.profile else None
    profile = load_run_profile(project_dir=project_dir, override_path=override_path)

    target_words = args.target_words if args.target_words is not None else profile.target_words
    min_words = args.min_words if args.min_words is not None else profile.min_words
    max_retries = args.max_retries if args.max_retries is not None else profile.max_retries
    external_retries = (
        args.external_retries if args.external_retries is not None else profile.external_retries
    )
    chapter_goal = args.chapter_goal if args.chapter_goal is not None else profile.chapter_goal
    style_constraints = (
        args.style_constraints if args.style_constraints is not None else profile.style_constraints
    )

    orchestrator = LongBookWritterOrchestrator(settings=settings)
    payload = orchestrator.run_range(
        book_id=args.book_id,
        chapter_start=args.start,
        chapter_end=args.end,
        chapter_goal=chapter_goal,
        style_constraints=style_constraints,
        target_words=target_words,
        min_words=min_words,
        max_retries=max_retries,
        external_retries=external_retries,
        skip_existing=args.skip_existing,
        stop_on_plot_block=args.stop_on_plot_block,
        save_artifacts=args.save_artifacts,
    )
    payload["profile_source"] = profile.source
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if payload.get("ok"):
        return 0
    if payload.get("stop_reason") == "plot_block":
        return 3
    return 2


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "check-connectivity":
        return _run_check_connectivity(args)
    if args.command == "run-range":
        return _run_run_range(args)

    settings = load_settings()
    orchestrator = LongBookWritterOrchestrator(settings=settings)

    if args.command == "init-project":
        project_dir = orchestrator.init_project(
            book_id=args.book_id,
            title=args.title,
            hitl_required=args.hitl,
        )
        print(
            json.dumps(
                {"ok": True, "project_dir": str(project_dir), "hitl_required": args.hitl},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.command == "plan-draft":
        project_dir = settings.projects_dir / args.book_id
        if not project_dir.exists():
            print(json.dumps({"ok": False, "error": f"project_dir_missing: {project_dir}"}, ensure_ascii=False, indent=2))
            return 2
        client = DoubaoTextClient(settings=settings)
        planner = HitlPlanner(llm_client=client)
        payload = planner.draft_volume(
            project_dir=project_dir,
            volume=args.volume,
            extra_brief=args.brief,
            force=args.force,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload.get("ok") else 2

    if args.command == "plan-review":
        project_dir = settings.projects_dir / args.book_id
        if not project_dir.exists():
            print(json.dumps({"ok": False, "error": f"project_dir_missing: {project_dir}"}, ensure_ascii=False, indent=2))
            return 2
        statuses = list_volume_statuses(project_dir=project_dir)
        payload = {
            "ok": True,
            "book_id": args.book_id,
            "volumes": [
                {
                    "volume": s.volume,
                    "label": s.label,
                    "chapter_range": s.chapter_range,
                    "approved": s.approved,
                    "approved_at_utc": s.approved_at_utc,
                    "draft_exists": s.draft_exists,
                    "draft_path": s.draft_path,
                    "draft_sha": s.draft_sha,
                    "locked_sha": s.locked_sha,
                    "sha_match": s.sha_match,
                }
                for s in statuses
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.command == "plan-approve":
        project_dir = settings.projects_dir / args.book_id
        if not project_dir.exists():
            print(json.dumps({"ok": False, "error": f"project_dir_missing: {project_dir}"}, ensure_ascii=False, indent=2))
            return 2
        client = DoubaoTextClient(settings=settings)
        planner = HitlPlanner(llm_client=client)
        payload = planner.approve_volume(project_dir=project_dir, volume=args.volume)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload.get("ok") else 2

    if args.command == "seed-cards":
        return _run_seed_cards(settings=settings, args=args)

    if args.command == "seed-config":
        return _run_seed_config(settings=settings, args=args)

    if args.command == "plan":
        payload = orchestrator.generate_plan(
            PlanInput(
                book_id=args.book_id,
                brief=args.brief,
                target_words=args.target_words,
                tone=args.tone,
                user_mode=args.user_mode,
                must_keep=args.must_keep,
                banned=args.banned,
                direction_hint=args.direction_hint,
            )
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.command == "suggest-titles":
        payload = orchestrator.suggest_titles(
            book_id=args.book_id,
            current_stage=args.current_stage,
            style=args.style,
            count=args.count,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.command == "run-chapter":
        payload = orchestrator.run_chapter_pipeline(
            book_id=args.book_id,
            chapter_no=args.chapter_no,
            chapter_title=args.chapter_title,
            chapter_goal=args.chapter_goal,
            target_words=args.target_words,
            style_constraints=args.style_constraints,
            max_retries=args.max_retries,
            min_words=args.min_words,
            save_artifacts=args.save_artifacts,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
