from longbookwritter.llm.base import BaseTextClient


class WriterAgent:
    def __init__(self, llm_client: BaseTextClient):
        self.llm_client = llm_client

    def draft_chapter(
        self,
        chapter_goal: str,
        style_constraints: str,
        target_words: int,
        context_summary: str,
        character_constraints: dict | None = None,
        planner_brief: str = "",
        scene_plan: list[str] | None = None,
        chapter_no: int = 1,
        chapter_title: str = "",
    ) -> dict:
        scene_text = ""
        if scene_plan:
            scene_lines = [f"{idx + 1}. {item}" for idx, item in enumerate(scene_plan)]
            scene_text = "参考推进点（仅供内在组织，不可写入正文）:\n" + "\n".join(scene_lines) + "\n"
        character_text = ""
        if character_constraints:
            fixed_names = character_constraints.get("fixed_names", [])
            banned_aliases = character_constraints.get("banned_aliases", {})
            character_text = "角色命名硬约束：\n"
            if fixed_names:
                character_text += "固定人名（只能使用这些名字）: " + "、".join(fixed_names) + "\n"
            if banned_aliases:
                pairs = [f"{k}->{v}" for k, v in banned_aliases.items()]
                character_text += "禁用别名映射（出现左侧名字视为错误）: " + "；".join(pairs) + "\n"

        prompt = (
            "你是中文网文小说作者，请直接输出可发布正文。\n"
            "硬性要求：\n"
            "1. 只输出小说正文，不输出提纲、注释、解释、标题说明\n"
            "2. 严禁出现“本章目标/场景1/第X章开始时/推进点”等流程词\n"
            "3. 分段自然，叙事、对话、动作交替推进\n"
            "4. 避免重复句式和模板化总结\n"
            "5. 字数尽量接近目标字数\n"
            "6. 与“策划写作简报”的主角名、城市、时代保持完全一致\n"
            "7. 本章必须承接“本章必须延续/未结线索”，禁止从零开新故事\n\n"
            "8. 时间线只能向前推进，不得回跳到更早月份（除非明确是回忆且有清晰提示）\n\n"
            f"章节号: {chapter_no}\n"
            f"章节名: {chapter_title}\n"
            f"章节目标: {chapter_goal}\n"
            f"风格约束: {style_constraints}\n"
            f"目标字数: {target_words}\n"
            f"{character_text}"
            f"策划写作简报: {planner_brief}\n"
            f"{scene_text}"
            f"上文摘要: {context_summary}\n"
        )
        result = self.llm_client.generate_text(prompt_text=prompt)
        if not result.success:
            return {"ok": False, "error": result.content, "draft": ""}
        return {"ok": True, "error": "", "draft": result.content}

    def suggest_chapter_title(
        self,
        chapter_text: str,
        chapter_no: int,
        chapter_goal: str,
    ) -> dict:
        prompt = (
            "你是中文网文作者，给本章起一个可发布标题。\n"
            "硬性要求：\n"
            "1. 只输出标题本身，不要解释，不要引号，不要序号\n"
            "2. 标题口语化、有记忆点，长度控制在4-12个汉字\n"
            "3. 不要使用“第X章”格式，不要过度书面化\n"
            "4. 标题要贴合本章核心冲突或转折\n\n"
            f"章节号：{chapter_no}\n"
            f"章节目标：{chapter_goal}\n"
            f"章节正文：\n{chapter_text}\n"
        )
        result = self.llm_client.generate_text(prompt_text=prompt)
        if not result.success:
            return {"ok": False, "error": result.content, "title": ""}
        return {"ok": True, "error": "", "title": result.content.strip()}
