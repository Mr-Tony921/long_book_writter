from longbookwritter.llm.base import BaseTextClient


class EditorAgent:
    def __init__(self, llm_client: BaseTextClient):
        self.llm_client = llm_client

    def polish(self, chapter_text: str, must_fix: list[str], style_constraints: str) -> dict:
        prompt = (
            "你是小说审校编辑。请在不改变核心剧情事件的前提下优化文本。\n"
            "输出最终正文，不要解释。\n"
            f"必须修复：{'; '.join(must_fix) if must_fix else '无'}\n"
            f"风格约束：{style_constraints}\n"
            f"原文：\n{chapter_text}\n"
        )
        result = self.llm_client.generate_text(prompt_text=prompt)
        if not result.success:
            return {"ok": False, "error": result.content, "text": chapter_text}
        return {"ok": True, "error": "", "text": result.content}

