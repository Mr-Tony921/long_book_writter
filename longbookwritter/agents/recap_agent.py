import json
from pathlib import Path

from longbookwritter.llm.base import BaseTextClient


class RecapAgent:
    def __init__(self, llm_client: BaseTextClient):
        self.llm_client = llm_client

    def refresh_recap(
        self,
        book_id: str,
        publish_dir: Path,
        recap_file: Path,
        plan_data: dict,
        memory: dict,
        max_recent_chapters: int = 3,
    ) -> dict:
        chapters = sorted(publish_dir.glob("*.md"))
        recent = chapters[-max_recent_chapters:]
        recent_text = []
        for p in recent:
            text = p.read_text(encoding="utf-8")
            recent_text.append(f"[{p.name}]\n{text[:1500]}")
        joined_recent = "\n\n".join(recent_text)
        theme = plan_data.get("plan", {}).get("core_theme", "")
        conflict = plan_data.get("plan", {}).get("core_conflict", "")
        timeline_tail = memory.get("timeline", [])[-8:]

        prompt = (
            "你是小说前情概要编辑。请输出 JSON，不要输出额外文本。\n"
            "JSON schema:\n"
            "{\n"
            '  "global_summary": "全书压缩概要（200-400字）",\n'
            '  "recent_arc": "最近章节主线推进（120-220字）",\n'
            '  "open_threads": ["未回收伏笔1", "未解决冲突2"],\n'
            '  "next_focus": ["下一章必须推进点1", "下一章必须推进点2"],\n'
            '  "character_state": {"角色名":"当前状态一句话"}\n'
            "}\n\n"
            f"书籍ID：{book_id}\n"
            f"核心主题：{theme}\n"
            f"核心冲突：{conflict}\n"
            f"最近时间线：{timeline_tail}\n"
            f"最近章节文本：\n{joined_recent}\n"
        )
        result = self.llm_client.generate_text(prompt_text=prompt)
        if result.success:
            try:
                parsed = json.loads(result.content)
                parsed["source"] = "llm"
                self._persist(recap_file=recap_file, recap=parsed)
                return parsed
            except json.JSONDecodeError:
                pass

        fallback = self._fallback_recap(plan_data=plan_data, memory=memory)
        fallback["source"] = "fallback"
        fallback["error"] = result.content if not result.success else "recap_json_parse_failed"
        self._persist(recap_file=recap_file, recap=fallback)
        return fallback

    def _fallback_recap(self, plan_data: dict, memory: dict) -> dict:
        theme = plan_data.get("plan", {}).get("core_theme", "暂无")
        conflict = plan_data.get("plan", {}).get("core_conflict", "暂无")
        timeline = memory.get("timeline", [])
        tail = timeline[-5:]
        return {
            "global_summary": f"本书围绕“{theme}”展开，核心冲突为“{conflict}”，主角在职场与关系中持续成长。",
            "recent_arc": "最近剧情推进：" + "；".join(tail) if tail else "暂无已发布剧情。",
            "open_threads": ["老油条与主角的冲突后续", "主角能力暴露风险", "感情线信任推进"],
            "next_focus": ["延续最近章节冲突结果", "推进主线任务而非重复日常", "保持角色称呼一致"],
            "character_state": {},
        }

    def _persist(self, recap_file: Path, recap: dict) -> None:
        recap_file.parent.mkdir(parents=True, exist_ok=True)
        recap_file.write_text(json.dumps(recap, ensure_ascii=False, indent=2), encoding="utf-8")

