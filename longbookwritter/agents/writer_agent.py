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
        retry_guidance: str = "",
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
        retry_text = ""
        if retry_guidance:
            retry_text = "上一轮失败修复指令（必须逐条落实）:\n" + retry_guidance + "\n"

        prompt = (
            "你是中文小说作者，请直接输出可发布正文。\n"
            "硬性要求：\n"
            "1. 只输出小说正文，不输出提纲、注释、解释、标题说明\n"
            "2. 严禁出现“本章目标/场景1/第X章开始时/推进点”等流程词\n"
            "3. 分段自然，叙事、对话、动作交替推进\n"
            "4. 避免重复句式和模板化总结\n"
            "5. 字数尽量接近目标字数；不得低于硬性下限（min_words）\n"
            "6. 与“策划写作简报”的主角名、城市、时代保持完全一致\n"
            "7. 本章必须承接“本章必须延续/未结线索”，禁止从零开新故事\n"
            "8. 时间线只能向前推进，不得回跳到更早月份（除非明确是回忆且有清晰提示）\n"
            "9. 文风要更有趣：多用人物博弈、反差、机智对话推进，少用总结解释\n"
            "10. 避免打圈：同一冲突不要反复重复，必须引入新变量并推进结果\n"
            "11. 地图变化是阶段性要求：不必每章都换地图，但连续多章不能在同一地点同一冲突里原地打转\n"
            "12. 时间跨度要克制：与上一章保持连续叙事，不要每章开头就隔很久，除非剧情明确需要\n"
            "13. 角色卡 / 事件卡（若简报中提供）属于硬约束：traits/goals/secret 必须与正文表现一致；secret 字段是隐藏底牌，正文不得直接揭露\n"
            "14. 风格约束（style_constraints）含的所有指令——含日轻吐槽风、独白层级、感情线尺度、原作鉴赏密度等——一律视为硬性要求执行\n\n"
            f"章节号: {chapter_no}\n"
            f"章节名: {chapter_title}\n"
            f"章节目标: {chapter_goal}\n"
            f"风格约束: {style_constraints}\n"
            f"目标字数: {target_words}\n"
            f"{character_text}"
            f"策划写作简报: {planner_brief}\n"
            f"{scene_text}"
            f"上文摘要: {context_summary}\n"
            f"{retry_text}"
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
        avoid_titles: list[str] | None = None,
        attempt: int = 1,
    ) -> dict:
        avoid_text = ""
        if avoid_titles:
            head = avoid_titles[-30:]  # only show recent ones to stay within prompt budget
            avoid_text = (
                "已使用过的标题（必须避开，且不能仅靠加数字/后缀小改区分）：\n- "
                + "\n- ".join(head)
                + "\n"
            )
        retry_hint = ""
        if attempt > 1:
            retry_hint = (
                f"这是第 {attempt} 次尝试：上一轮的标题与已用标题冲突，必须换角度。\n"
                "请尝试从不同的镜头切换：从动作切到物件、从人物切到地点、从结果切到细节，\n"
                "或者用反差感/吐槽腔重写。\n"
            )
        prompt = (
            "你是日式轻小说编辑，给本章起一个可发布标题。\n"
            "硬性要求：\n"
            "1. 只输出标题本身，不要解释，不要引号，不要序号\n"
            "2. 标题口语化，长度 4-14 个汉字；可以带轻微吐槽腔/反差感（如“米蕾会长又开始闹了”、“这个学生有点危险”）\n"
            "3. 不要使用“第X章”格式，不要堆模板词（“风云再起/暗潮涌动”等通用语）\n"
            "4. 不要工整对仗；用自然口语描述本章核心冲突或转折\n"
            "5. 严禁通过“加数字/加（二）/加·再起”等后缀来规避标题冲突\n"
            f"{retry_hint}"
            f"{avoid_text}"
            f"章节号：{chapter_no}\n"
            f"章节目标：{chapter_goal}\n"
            f"章节正文：\n{chapter_text}\n"
        )
        result = self.llm_client.generate_text(prompt_text=prompt)
        if not result.success:
            return {"ok": False, "error": result.content, "title": ""}
        return {"ok": True, "error": "", "title": result.content.strip()}
