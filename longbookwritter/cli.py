import argparse
import json
import time
from pathlib import Path

from longbookwritter.config import load_settings
from longbookwritter.llm.doubao_client import DoubaoTextClient
from longbookwritter.orchestrator import LongBookWritterOrchestrator
from longbookwritter.schemas import PlanInput
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
        project_dir = orchestrator.init_project(book_id=args.book_id, title=args.title)
        print(json.dumps({"ok": True, "project_dir": str(project_dir)}, ensure_ascii=False, indent=2))
        return 0

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
