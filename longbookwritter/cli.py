import argparse
import json

from longbookwritter.config import load_settings
from longbookwritter.orchestrator import LongBookWritterOrchestrator
from longbookwritter.schemas import PlanInput


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
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    settings = load_settings()
    orchestrator = LongBookWritterOrchestrator(settings=settings)

    if args.command == "init-project":
        project_dir = orchestrator.init_project(book_id=args.book_id, title=args.title)
        print(json.dumps({"ok": True, "project_dir": str(project_dir)}, ensure_ascii=False, indent=2))
        return

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
        return

    if args.command == "suggest-titles":
        payload = orchestrator.suggest_titles(
            book_id=args.book_id,
            current_stage=args.current_stage,
            style=args.style,
            count=args.count,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

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
        return


if __name__ == "__main__":
    main()
