from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import re

from longbookwritter.agents.editor_agent import EditorAgent
from longbookwritter.agents.memory_agent import MemoryAgent
from longbookwritter.agents.planner_agent import PlannerAgent
from longbookwritter.agents.publisher_agent import PublisherAgent
from longbookwritter.agents.recap_agent import RecapAgent
from longbookwritter.agents.reviewer_agent import ReviewerAgent
from longbookwritter.agents.writer_agent import WriterAgent
from longbookwritter.config import Settings
from longbookwritter.llm.doubao_client import DoubaoTextClient
from longbookwritter.schemas import PlanInput
from longbookwritter.utils.io import append_text, ensure_dir, read_json, write_json, write_text


class LongBookWritterOrchestrator:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.llm_client = DoubaoTextClient(settings=settings)
        self.planner = PlannerAgent(self.llm_client)
        self.writer = WriterAgent(self.llm_client)
        self.reviewer = ReviewerAgent(self.llm_client)
        self.editor = EditorAgent(self.llm_client)
        self.publisher = PublisherAgent()
        self.memory = MemoryAgent()
        self.recap = RecapAgent(self.llm_client)

    def init_project(self, book_id: str, title: str) -> Path:
        project_dir = self.settings.projects_dir / book_id
        ensure_dir(project_dir / "00_config")
        ensure_dir(project_dir / "01_plan")
        ensure_dir(project_dir / "02_memory")
        ensure_dir(project_dir / "03_draft")
        ensure_dir(project_dir / "04_review")
        ensure_dir(project_dir / "05_publish")
        ensure_dir(project_dir / "logs")
        ensure_dir(project_dir / "99_engineering")

        write_json(
            project_dir / "00_config" / "book_config.json",
            {
                "book_id": book_id,
                "title": title,
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "model_defaults": {
                    "planning": self.settings.doubao_lite_model,
                    "writing": self.settings.doubao_lite_model,
                    "review": self.settings.doubao_lite_model,
                    "editing": self.settings.doubao_lite_model,
                },
            },
        )
        write_json(
            project_dir / "00_config" / "character_constraints.json",
            {
                "fixed_names": [],
                "banned_aliases": {},
                "notes": "Fill fixed_names and banned_aliases to enforce long-form character consistency.",
            },
        )
        write_json(
            project_dir / "00_config" / "story_facts.json",
            {
                "protagonist_name": "",
                "city": "",
                "time_period": "",
                "must_mention": [],
                "notes": "Core continuity facts for planner->writer->reviewer chain.",
            },
        )
        write_json(
            project_dir / "index.json",
            {
                "book_id": book_id,
                "title": title,
                "status": "initialized",
                "chapters_published": 0,
                "last_chapter_no": 0,
                "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            },
        )
        write_json(
            project_dir / "02_memory" / "memory.json",
            self.memory.load_memory(project_dir / "02_memory" / "memory.json"),
        )
        self._init_engineering_logs(project_dir=project_dir)
        self._record_engineering_event(
            project_dir=project_dir,
            category="decision",
            title="项目初始化",
            details=f"book_id={book_id}, title={title}",
            level="info",
        )
        return project_dir

    def generate_plan(self, request: PlanInput) -> dict:
        project_dir = self.settings.projects_dir / request.book_id
        ensure_dir(project_dir / "01_plan")
        result = self.planner.generate_book_plan(request=request)
        output = {
            "book_id": request.book_id,
            "input": asdict(request),
            "plan": result,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        write_json(project_dir / "01_plan" / "master_plan.json", output)
        if result.get("fallback"):
            self._record_engineering_event(
                project_dir=project_dir,
                category="issue",
                title="策划模型降级",
                details=f"plan fallback triggered, error={result.get('error', '')}",
                level="warn",
            )
        self._record_engineering_event(
            project_dir=project_dir,
            category="change",
            title="产出主规划",
            details=f"user_mode={request.user_mode}, target_words={request.target_words}",
            level="info",
        )
        index_file = project_dir / "index.json"
        if index_file.exists():
            index = read_json(index_file)
            index["status"] = "planned"
            index["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
            write_json(index_file, index)
        return output

    def suggest_titles(self, book_id: str, current_stage: str, style: str, count: int) -> dict:
        project_dir = self.settings.projects_dir / book_id
        config_file = project_dir / "00_config" / "book_config.json"
        book_title = "未命名作品"
        if config_file.exists():
            cfg = read_json(config_file)
            book_title = cfg.get("title", book_title)
        naming = self.planner.suggest_titles(
            book_brief=f"{book_title}。当前阶段：{current_stage}",
            current_stage=current_stage,
            style=style,
            count=count,
        )
        output = {
            "book_id": book_id,
            "current_stage": current_stage,
            "naming": naming,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        write_json(project_dir / "01_plan" / "title_suggestions.json", output)
        if naming.get("fallback"):
            self._record_engineering_event(
                project_dir=project_dir,
                category="issue",
                title="起名模型降级",
                details=f"title fallback triggered, error={naming.get('error', '')}",
                level="warn",
            )
        self._record_engineering_event(
            project_dir=project_dir,
            category="change",
            title="产出标题候选",
            details=f"style={style}, count={count}",
            level="info",
        )
        return output

    def run_chapter_pipeline(
        self,
        book_id: str,
        chapter_no: int,
        chapter_title: str,
        chapter_goal: str,
        target_words: int,
        style_constraints: str,
        max_retries: int = 2,
        min_words: int = 2000,
        save_artifacts: bool = False,
    ) -> dict:
        project_dir = self.settings.projects_dir / book_id
        requested_title = chapter_title
        chapter_title = self._normalize_chapter_title(chapter_title)
        plan_data = self._load_plan(project_dir)
        anchors = plan_data.get("plan", {}).get("anchors", {})
        character_constraints = self._load_character_constraints(project_dir)
        story_facts = self._load_story_facts(project_dir)
        memory = self.memory.load_memory(project_dir / "02_memory" / "memory.json")
        context_summary = self._build_context_summary(
            plan_data=plan_data,
            memory=memory,
            chapter_no=chapter_no,
            character_constraints=character_constraints,
            story_facts=story_facts,
        )
        scene_plan = self._build_scene_plan(chapter_goal=chapter_goal, anchors=anchors)
        planner_brief = self.planner.build_writer_brief(
            chapter_no=chapter_no,
            chapter_title=chapter_title,
            chapter_goal=chapter_goal,
            story_facts=story_facts,
            recap_state=memory.get("recap_state", {}),
        )

        attempts: list[dict] = []
        final_text = ""
        review = {}
        passed = False
        draft_text = ""
        retry_guidance = ""

        for attempt in range(1, max_retries + 2):
            draft_res = self.writer.draft_chapter(
                chapter_goal=chapter_goal,
                style_constraints=style_constraints,
                target_words=target_words,
                context_summary=context_summary,
                character_constraints=character_constraints,
                planner_brief=planner_brief,
                scene_plan=scene_plan,
                chapter_no=chapter_no,
                chapter_title=chapter_title,
                retry_guidance=retry_guidance,
            )
            draft_text = draft_res.get("draft", "")
            if not draft_res.get("ok", False) or not draft_text.strip():
                attempts.append(
                    {
                        "attempt": attempt,
                        "draft_ok": False,
                        "draft_error": draft_res.get("error", "writer_failed"),
                        "review": {},
                        "edit_ok": False,
                        "edit_error": "",
                    }
                )
                retry_guidance = self._build_retry_guidance(
                    review={},
                    chapter_goal=chapter_goal,
                    chapter_no=chapter_no,
                    previous_guidance=retry_guidance,
                    fallback_reason=f"写作接口失败：{draft_res.get('error', 'writer_failed')}",
                )
                continue
            review = self.reviewer.review_chapter(
                chapter_text=draft_text,
                chapter_goal=chapter_goal,
                policy="一致性优先，避免AI味和重复，剧情推进必须明确",
                chapter_no=chapter_no,
                anchors=anchors,
                character_constraints=character_constraints,
                story_facts=story_facts,
                planner_brief=planner_brief,
                min_words=min_words,
            )
            must_fix_items = review.get("must_fix", []) or []
            review_passed = bool(review.get("pass", False))
            if review_passed or not must_fix_items:
                edit_res = {"ok": True, "error": "skipped_editor", "text": draft_text}
            else:
                edit_res = self.editor.polish(
                    chapter_text=draft_text,
                    must_fix=must_fix_items,
                    style_constraints=style_constraints,
                )
            final_text = edit_res.get("text", draft_text)
            attempts.append(
                {
                    "attempt": attempt,
                    "draft_ok": draft_res.get("ok", False),
                    "draft_error": draft_res.get("error", ""),
                    "review": review,
                    "edit_ok": edit_res.get("ok", False),
                    "edit_error": edit_res.get("error", ""),
                }
                )
            if save_artifacts:
                self._persist_attempt_files(
                    project_dir=project_dir,
                    chapter_no=chapter_no,
                    attempt=attempt,
                    chapter_title=chapter_title,
                    draft_text=draft_text,
                    review=review,
                    final_text=final_text,
                )
            if review.get("pass", False):
                passed = True
                break
            retry_guidance = self._build_retry_guidance(
                review=review,
                chapter_goal=chapter_goal,
                chapter_no=chapter_no,
                previous_guidance=retry_guidance,
            )

        if not passed or not final_text.strip():
            reason = "writer_failed_or_quality_gate_failed"
            if attempts:
                last = attempts[-1]
                reason = last.get("draft_error") or last.get("edit_error") or reason
            self._notify_human_with_planner(
                project_dir=project_dir,
                chapter_no=chapter_no,
                chapter_title=chapter_title,
                reason=reason,
            )
            result = {
                "book_id": book_id,
                "chapter_no": chapter_no,
                "chapter_title": chapter_title,
                "passed": False,
                "review_score": review.get("score", 0) if review else 0,
                "attempts": attempts,
                "published_path": "",
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "human_intervention_required": True,
            }
            self._record_engineering_event(
                project_dir=project_dir,
                category="issue",
                title=f"章节流水线失败 chapter={chapter_no}",
                details=f"reason={reason}",
                level="warn",
            )
            return result

        final_text = self._sanitize_for_publish(final_text)
        title_generation_error = ""
        generated_title = ""
        # If caller already provides a valid title, publish directly to avoid extra LLM latency/failure.
        if requested_title and chapter_title:
            generated_title = chapter_title
        else:
            for _ in range(2):
                title_by_writer = self.writer.suggest_chapter_title(
                    chapter_text=final_text,
                    chapter_no=chapter_no,
                    chapter_goal=chapter_goal,
                )
                if not title_by_writer.get("ok", False):
                    title_generation_error = title_by_writer.get("error", "title_generation_failed")
                    continue
                generated_title = self._normalize_chapter_title(title_by_writer.get("title", ""))
                if generated_title:
                    break
                title_generation_error = "empty_or_too_short_title"

        if not generated_title:
            self._notify_human_with_planner(
                project_dir=project_dir,
                chapter_no=chapter_no,
                chapter_title=requested_title or chapter_title,
                reason=f"title_generation_failed: {title_generation_error}",
            )
            result = {
                "book_id": book_id,
                "chapter_no": chapter_no,
                "chapter_title": requested_title or chapter_title,
                "requested_chapter_title": requested_title,
                "passed": False,
                "review_score": review.get("score", 0),
                "attempts": attempts,
                "published_path": "",
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "human_intervention_required": True,
                "title_generation_error": title_generation_error,
            }
            self._record_engineering_event(
                project_dir=project_dir,
                category="issue",
                title=f"章节标题生成失败 chapter={chapter_no}",
                details=title_generation_error,
                level="warn",
            )
            return result

        chapter_title = self._ensure_unique_chapter_title(
            project_dir=project_dir,
            chapter_no=chapter_no,
            chapter_title=generated_title,
        )
        published_path = self.publisher.publish_chapter(
            publish_dir=project_dir / "05_publish",
            chapter_no=chapter_no,
            chapter_title=chapter_title,
            chapter_text=final_text,
        )
        self._update_project_index(project_dir=project_dir, chapter_no=chapter_no)
        self._update_memory(project_dir=project_dir, chapter_no=chapter_no, chapter_title=chapter_title, chapter_goal=chapter_goal)
        recap_payload = self._refresh_recap(project_dir=project_dir, plan_data=plan_data)

        result = {
            "book_id": book_id,
            "chapter_no": chapter_no,
            "chapter_title": chapter_title,
            "requested_chapter_title": requested_title,
            "passed": passed,
            "review_score": review.get("score", 0),
            "attempts": attempts,
            "published_path": str(published_path),
            "recap_source": recap_payload.get("source", "unknown"),
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        if save_artifacts:
            write_json(project_dir / "04_review" / f"chapter_{chapter_no:04d}_final_report.json", result)
        self._record_engineering_event(
            project_dir=project_dir,
            category="change",
            title=f"章节流水线完成 chapter={chapter_no}",
            details=f"passed={passed}, review_score={result.get('review_score')}, attempts={len(attempts)}, save_artifacts={save_artifacts}",
            level="info",
        )
        for idx, attempt_data in enumerate(attempts, start=1):
            review_data = attempt_data.get("review", {})
            if review_data.get("degraded_mode"):
                self._record_engineering_event(
                    project_dir=project_dir,
                    category="issue",
                    title=f"章节{chapter_no}第{idx}次检查进入降级模式",
                    details="LLM review unavailable, rule-based review used",
                    level="warn",
                )
        return result

    NETWORK_ERROR_MARKERS = (
        "read timed out",
        "request failed",
        "network_error",
        "operation not permitted",
        "max retries exceeded",
        "failed to establish a new connection",
        "response read failed",
        "stream request failed",
    )

    def run_range(
        self,
        book_id: str,
        chapter_start: int,
        chapter_end: int,
        chapter_goal: str,
        style_constraints: str,
        target_words: int,
        min_words: int,
        max_retries: int,
        external_retries: int,
        skip_existing: bool = True,
        stop_on_plot_block: bool = True,
        save_artifacts: bool = False,
    ) -> dict:
        if chapter_end < chapter_start:
            raise ValueError(f"chapter_end({chapter_end}) < chapter_start({chapter_start})")

        project_dir = self.settings.projects_dir / book_id
        publish_dir = project_dir / "05_publish"
        logs_dir = project_dir / "logs"
        ensure_dir(logs_dir)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        log_path = logs_dir / f"run_range_{chapter_start:04d}_{chapter_end:04d}_{ts}.json"

        items: list[dict] = []
        ok = True
        stop_reason = "completed"
        stop_chapter_no = 0

        for chapter_no in range(chapter_start, chapter_end + 1):
            if skip_existing and any(publish_dir.glob(f"{chapter_no:04d}_*.md")):
                existing = sorted(publish_dir.glob(f"{chapter_no:04d}_*.md"))[0]
                items.append(
                    {
                        "chapter_no": chapter_no,
                        "status": "skipped_exists",
                        "file": str(existing),
                    }
                )
                continue

            chapter_outcome = self._run_chapter_with_external_retry(
                book_id=book_id,
                chapter_no=chapter_no,
                chapter_goal=chapter_goal,
                style_constraints=style_constraints,
                target_words=target_words,
                min_words=min_words,
                max_retries=max_retries,
                external_retries=external_retries,
                stop_on_plot_block=stop_on_plot_block,
                save_artifacts=save_artifacts,
            )
            items.append(chapter_outcome)
            if chapter_outcome.get("status") == "published":
                continue

            ok = False
            stop_reason = chapter_outcome.get("stop_reason", "unknown")
            stop_chapter_no = chapter_no
            break

        payload = {
            "book_id": book_id,
            "range": [chapter_start, chapter_end],
            "ok": ok,
            "stop_reason": stop_reason,
            "stop_chapter_no": stop_chapter_no,
            "items": items,
            "logs_path": str(log_path),
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        write_json(log_path, payload)
        self._record_engineering_event(
            project_dir=project_dir,
            category="change",
            title=f"批量章节生成 range={chapter_start}-{chapter_end}",
            details=f"ok={ok}, stop_reason={stop_reason}, stop_chapter_no={stop_chapter_no}",
            level="info" if ok else "warn",
        )
        return payload

    def _run_chapter_with_external_retry(
        self,
        book_id: str,
        chapter_no: int,
        chapter_goal: str,
        style_constraints: str,
        target_words: int,
        min_words: int,
        max_retries: int,
        external_retries: int,
        stop_on_plot_block: bool,
        save_artifacts: bool,
    ) -> dict:
        last_payload: dict = {}
        for ext_try in range(1, external_retries + 2):
            payload = self.run_chapter_pipeline(
                book_id=book_id,
                chapter_no=chapter_no,
                chapter_title="",
                chapter_goal=chapter_goal,
                target_words=target_words,
                style_constraints=style_constraints,
                max_retries=max_retries,
                min_words=min_words,
                save_artifacts=save_artifacts,
            )
            last_payload = payload
            if payload.get("passed") and payload.get("published_path"):
                return {
                    "chapter_no": chapter_no,
                    "status": "published",
                    "ext_try": ext_try,
                    "title": payload.get("chapter_title", ""),
                    "review_score": payload.get("review_score", 0),
                    "published_path": payload.get("published_path", ""),
                }

            classification = self._classify_failure(payload)
            if classification == "plot_block" and stop_on_plot_block:
                return {
                    "chapter_no": chapter_no,
                    "status": "stopped",
                    "ext_try": ext_try,
                    "stop_reason": "plot_block",
                    "review_score": payload.get("review_score", 0),
                    "last_payload": payload,
                }

            if ext_try > external_retries:
                return {
                    "chapter_no": chapter_no,
                    "status": "failed",
                    "ext_try": ext_try,
                    "stop_reason": f"retry_exhausted_{classification}",
                    "review_score": payload.get("review_score", 0),
                    "last_payload": payload,
                }

        return {
            "chapter_no": chapter_no,
            "status": "failed",
            "stop_reason": "unknown",
            "last_payload": last_payload,
        }

    def _classify_failure(self, payload: dict) -> str:
        attempts = payload.get("attempts", []) or []
        errors_blob = " | ".join(
            str(a.get("draft_error", "")) for a in attempts
        ).lower()
        title_err = str(payload.get("title_generation_error", "")).lower()
        if any(marker in errors_blob for marker in self.NETWORK_ERROR_MARKERS):
            return "network"
        if any(marker in title_err for marker in self.NETWORK_ERROR_MARKERS):
            return "network"
        for attempt in attempts:
            review = attempt.get("review") or {}
            for issue in review.get("issues") or []:
                severity = str(issue.get("severity", "")).lower()
                issue_type = str(issue.get("type", "")).lower()
                if severity == "high" and issue_type in {"consistency", "logic"}:
                    return "plot_block"
        return "quality"

    def _build_retry_guidance(
        self,
        review: dict,
        chapter_goal: str,
        chapter_no: int,
        previous_guidance: str = "",
        fallback_reason: str = "",
    ) -> str:
        lines: list[str] = []
        lines.append(f"第{chapter_no}章重写要求：必须覆盖章节目标“{chapter_goal}”。")
        if fallback_reason:
            lines.append(f"- 上轮失败原因：{fallback_reason}")

        issues = review.get("issues", []) if isinstance(review, dict) else []
        # 优先放入 high/mid 的具体问题，避免每轮盲重试。
        for item in issues:
            sev = str(item.get("severity", "")).lower()
            if sev in {"high", "mid"}:
                detail = str(item.get("detail", "")).strip()
                if detail:
                    lines.append(f"- 必修问题（{sev}）：{detail}")

        must_fix = review.get("must_fix", []) if isinstance(review, dict) else []
        for mf in must_fix[:8]:
            mf_text = str(mf).strip()
            if mf_text:
                lines.append(f"- 必须落实：{mf_text}")

        if len(lines) <= 1 and previous_guidance:
            return previous_guidance
        return "\n".join(lines)

    def _ensure_unique_chapter_title(self, project_dir: Path, chapter_no: int, chapter_title: str) -> str:
        publish_dir = project_dir / "05_publish"
        if not publish_dir.exists():
            return chapter_title

        used_titles: set[str] = set()
        for md in publish_dir.glob("*.md"):
            try:
                no = int(md.name[:4])
            except Exception:
                continue
            if no == chapter_no:
                continue
            stem = md.stem
            if "_" not in stem:
                continue
            used_titles.add(stem.split("_", 1)[1])

        if chapter_title not in used_titles:
            return chapter_title

        suffixes = ["（二）", "（续）", "（下）", "·再起", "·反转", "·加压", "·升级"]
        for sfx in suffixes:
            candidate = self._normalize_chapter_title(f"{chapter_title}{sfx}")
            if candidate not in used_titles:
                return candidate
        for idx in range(2, 20):
            candidate = self._normalize_chapter_title(f"{chapter_title}{idx}")
            if candidate not in used_titles:
                return candidate
        return chapter_title

    def _normalize_chapter_title(self, title: str) -> str:
        t = (title or "").strip()
        if not t:
            return ""
        t = t.replace("\n", " ").strip()
        t = re.sub(r"^#*\s*第\s*\d+\s*章[:：\s-]*", "", t)
        t = re.sub(r"[\"'“”‘’《》【】\[\]]", "", t)
        t = re.sub(r"\s+", "", t)
        t = re.sub(r"[\\/:*?\"<>|]", "", t)
        if len(t) > 16:
            t = t[:16]
        if len(t) < 4:
            return ""
        return t

    def _load_plan(self, project_dir: Path) -> dict:
        plan_file = project_dir / "01_plan" / "master_plan.json"
        if not plan_file.exists():
            return {"plan": {"anchors": {"book": [], "volume": [], "chapter": []}}}
        return read_json(plan_file)

    def _build_context_summary(
        self,
        plan_data: dict,
        memory: dict,
        chapter_no: int,
        character_constraints: dict,
        story_facts: dict,
    ) -> str:
        book_theme = plan_data.get("plan", {}).get("core_theme", "未设定")
        core_conflict = plan_data.get("plan", {}).get("core_conflict", "未设定")
        fixed_names = character_constraints.get("fixed_names", [])
        timeline = memory.get("timeline", [])
        tail = timeline[-3:] if timeline else []
        history = " | ".join(tail) if tail else "暂无历史章节摘要"
        recap = memory.get("recap_state", {})
        global_summary = recap.get("global_summary", "")
        recent_arc = recap.get("recent_arc", "")
        next_focus = recap.get("next_focus", [])
        next_focus_text = "；".join(next_focus[:3]) if next_focus else "未配置"
        city = story_facts.get("city", "")
        period = story_facts.get("time_period", "")
        protagonist = story_facts.get("protagonist_name", "")
        return (
            f"核心主题：{book_theme}\n"
            f"核心冲突：{core_conflict}\n"
            f"固定人名：{'、'.join(fixed_names) if fixed_names else '未配置'}\n"
            f"主角与时空：{protagonist or '未配置'} / {city or '未配置'} / {period or '未配置'}\n"
            f"当前章节：第{chapter_no}章\n"
            f"近三章进展：{history}\n"
            f"全书压缩概要：{global_summary or '暂无'}\n"
            f"近期主线：{recent_arc or '暂无'}\n"
            f"下一章重点：{next_focus_text}\n"
        )

    def _load_character_constraints(self, project_dir: Path) -> dict:
        path = project_dir / "00_config" / "character_constraints.json"
        if not path.exists():
            return {"fixed_names": [], "banned_aliases": {}}
        try:
            obj = read_json(path)
        except Exception:
            return {"fixed_names": [], "banned_aliases": {}}
        return {
            "fixed_names": obj.get("fixed_names", []),
            "banned_aliases": obj.get("banned_aliases", {}),
        }

    def _load_story_facts(self, project_dir: Path) -> dict:
        path = project_dir / "00_config" / "story_facts.json"
        if not path.exists():
            return {"protagonist_name": "", "city": "", "time_period": "", "must_mention": []}
        try:
            obj = read_json(path)
        except Exception:
            return {"protagonist_name": "", "city": "", "time_period": "", "must_mention": []}
        return {
            "protagonist_name": obj.get("protagonist_name", ""),
            "city": obj.get("city", ""),
            "time_period": obj.get("time_period", ""),
            "must_mention": obj.get("must_mention", []),
        }

    def _build_scene_plan(self, chapter_goal: str, anchors: dict) -> list[str]:
        chapter_anchors = anchors.get("chapter", [])
        scenes = [
            f"开场引入当前压力，围绕目标：{chapter_goal}",
            "中段设置阻碍并强化人物选择代价",
            "后段抛出新线索或冲突升级点，制造钩子",
        ]
        if chapter_anchors:
            scenes.append(f"明确体现章节锚点：{chapter_anchors[0]}")
        return scenes

    def _persist_attempt_files(
        self,
        project_dir: Path,
        chapter_no: int,
        attempt: int,
        chapter_title: str,
        draft_text: str,
        review: dict,
        final_text: str,
    ) -> None:
        draft_file = project_dir / "03_draft" / f"chapter_{chapter_no:04d}_v{attempt}.md"
        review_file = project_dir / "04_review" / f"chapter_{chapter_no:04d}_v{attempt}.json"
        final_file = project_dir / "03_draft" / f"chapter_{chapter_no:04d}_v{attempt}_edited.md"
        write_text(draft_file, f"# 第{chapter_no}章 {chapter_title}\n\n{draft_text}\n")
        write_json(review_file, review)
        write_text(final_file, f"# 第{chapter_no}章 {chapter_title}（审校后）\n\n{final_text}\n")

    def _update_project_index(self, project_dir: Path, chapter_no: int) -> None:
        index_file = project_dir / "index.json"
        if not index_file.exists():
            return
        index = read_json(index_file)
        index["status"] = "writing"
        index["chapters_published"] = max(index.get("chapters_published", 0), chapter_no)
        index["last_chapter_no"] = max(index.get("last_chapter_no", 0), chapter_no)
        index["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
        write_json(index_file, index)

    def _update_memory(self, project_dir: Path, chapter_no: int, chapter_title: str, chapter_goal: str) -> None:
        memory_file = project_dir / "02_memory" / "memory.json"
        memory = self.memory.load_memory(memory_file)
        timeline = memory.get("timeline", [])
        timeline.append(f"第{chapter_no}章《{chapter_title}》：{chapter_goal}")
        memory["timeline"] = timeline[-100:]
        self.memory.save_memory(memory_file, memory)

    def _refresh_recap(self, project_dir: Path, plan_data: dict) -> dict:
        memory_file = project_dir / "02_memory" / "memory.json"
        recap_file = project_dir / "02_memory" / "recap_state.json"
        memory = self.memory.load_memory(memory_file)
        recap_payload = self.recap.refresh_recap(
            book_id=project_dir.name,
            publish_dir=project_dir / "05_publish",
            recap_file=recap_file,
            plan_data=plan_data,
            memory=memory,
        )
        memory["recap_state"] = recap_payload
        self.memory.save_memory(memory_file, memory)
        return recap_payload

    def _sanitize_for_publish(self, text: str) -> str:
        lines = []
        skip_prefixes = (
            "本章目标：",
            "风格约束：",
            "上下文摘要：",
            "【场景",
            "场景拆解：",
            "参考推进点",
        )
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                lines.append("")
                continue
            if stripped.startswith(skip_prefixes):
                continue
            if "第" in stripped and "章《" in stripped and "开始时" in stripped:
                continue
            if "推进点" in stripped and "仅供" in stripped:
                continue
            if re.match(r"^补充段落\d+[:：]", stripped):
                continue
            lines.append(line)
        return "\n".join(lines).strip() + "\n"

    def _notify_human_with_planner(
        self,
        project_dir: Path,
        chapter_no: int,
        chapter_title: str,
        reason: str,
    ) -> None:
        notice = self.planner.build_human_alert(
            stage=f"chapter_{chapter_no:04d}_{chapter_title}",
            reason=reason,
            action_items=[
                "检查 Doubao 连通性和 API 配置",
                "确认写作模型返回的是小说正文而非流程文本",
                f"问题修复后重新执行第{chapter_no}章生成",
            ],
        )
        append_text(project_dir / "01_plan" / "HUMAN_ALERTS.md", notice + "\n\n")

    def _init_engineering_logs(self, project_dir: Path) -> None:
        base = project_dir / "99_engineering"
        files = {
            "DECISIONS.md": "# Decisions\n\n",
            "ISSUES.md": "# Issues\n\n",
            "CHANGES.md": "# Changes\n\n",
            "RUN_LOG.md": "# Run Log\n\n",
        }
        for name, header in files.items():
            path = base / name
            if not path.exists():
                write_text(path, header)

    def _record_engineering_event(
        self,
        project_dir: Path,
        category: str,
        title: str,
        details: str,
        level: str = "info",
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        line = f"- [{now}] [{level}] {title}: {details}\n"
        file_map = {
            "decision": project_dir / "99_engineering" / "DECISIONS.md",
            "issue": project_dir / "99_engineering" / "ISSUES.md",
            "change": project_dir / "99_engineering" / "CHANGES.md",
        }
        target = file_map.get(category, project_dir / "99_engineering" / "RUN_LOG.md")
        append_text(target, line)
        append_text(project_dir / "99_engineering" / "RUN_LOG.md", f"- [{now}] [{category}/{level}] {title}\n")
