import json
from datetime import datetime, timezone

from longbookwritter.llm.base import BaseTextClient
from longbookwritter.schemas import PlanInput, TitleStyle


class PlannerAgent:
    def __init__(self, llm_client: BaseTextClient):
        self.llm_client = llm_client

    def generate_book_plan(self, request: PlanInput) -> dict:
        mode_guide = {
            "novice": "用户是新手，请给出更易执行的结构和更明确的章节推进建议。",
            "pro": "用户是专业作者，请保留开放度并减少模板化建议。",
        }.get(request.user_mode, "用户是新手，请给出清晰结构。")
        prompt = (
            "你是小说策划编辑。请根据用户输入输出 JSON，不要输出多余文字。\n"
            "JSON字段：\n"
            "{\n"
            '  "book_positioning": "一句话定位",\n'
            '  "core_theme": "核心主题",\n'
            '  "core_conflict": "核心冲突",\n'
            '  "ending_direction": "结局方向",\n'
            '  "anchors": {"book":["全书锚点1"],"volume":["分卷锚点1"],"chapter":["章节锚点1"]},\n'
            '  "options_for_author": [{"id":"A","idea":"一个可选转折","pros":"优点","risks":"风险"}],\n'
            '  "volume_outline": [{"volume":1,"goal":"...","chapter_range":"1-20"}],\n'
            '  "chapter_seed": [{"chapter":1,"goal":"...","cliffhanger":"..."}]\n'
            "}\n\n"
            f"用户模式：{request.user_mode}\n"
            f"执行要求：{mode_guide}\n"
            f"用户简介：{request.brief}\n"
            f"目标字数：{request.target_words}\n"
            f"文风偏好：{request.tone}\n"
            f"必须保留：{request.must_keep or '无'}\n"
            f"禁止元素：{request.banned or '无'}\n"
            f"方向提示：{request.direction_hint or '无'}\n"
        )
        result = self.llm_client.generate_text(prompt_text=prompt)
        if not result.success:
            fallback = self._fallback_plan_from_request(request=request)
            fallback["fallback"] = True
            fallback["error"] = result.content
            return fallback
        try:
            parsed = json.loads(result.content)
            parsed["fallback"] = False
            parsed["generated_at_utc"] = datetime.now(timezone.utc).isoformat()
            return parsed
        except json.JSONDecodeError:
            fallback = self._fallback_plan_from_request(request=request)
            fallback["fallback"] = True
            fallback["error"] = "planner_json_parse_failed"
            fallback["raw_text"] = result.content
            return fallback

    def suggest_titles(
        self,
        book_brief: str,
        current_stage: str,
        style: TitleStyle = "mixed",
        count: int = 8,
    ) -> dict:
        style_rule = {
            "serious": "偏严谨克制，文学感和传播性平衡",
            "funny": "夸张、搞笑、抓眼球，带明显网文传播感",
            "mixed": "同时给出严谨和夸张风格，便于A/B测试",
        }[style]
        prompt = (
            "你是网文策划编辑，擅长起书名。\n"
            "请输出 JSON，不要输出其他内容。\n"
            "{\n"
            '  "titles": [\n'
            '    {"name":"标题","style":"serious|funny","reason":"一句理由"}\n'
            "  ]\n"
            "}\n"
            f"作品简介：{book_brief}\n"
            f"当前进度：{current_stage}\n"
            f"起名要求：{style_rule}\n"
            f"数量：{count}\n"
            "额外规则：避免泛词堆砌，尽量体现冲突、反差或记忆点。"
        )
        result = self.llm_client.generate_text(prompt_text=prompt)
        if result.success:
            try:
                parsed = json.loads(result.content)
                titles = parsed.get("titles", [])
                return {
                    "fallback": False,
                    "style": style,
                    "titles": titles[:count],
                    "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                }
            except json.JSONDecodeError:
                pass
        fallback = self._fallback_titles(book_brief=book_brief, style=style, count=count)
        fallback["fallback"] = True
        fallback["error"] = result.content if not result.success else "title_json_parse_failed"
        fallback["generated_at_utc"] = datetime.now(timezone.utc).isoformat()
        return fallback

    def build_human_alert(self, stage: str, reason: str, action_items: list[str] | None = None) -> str:
        actions = action_items or [
            "检查 Doubao 网络连通性与 API 配置",
            "确认 DOUBAO_API_KEY/DOUBAO_API_IP/DOUBAO_USE_IP_ROUTE",
            "恢复后重试当前章节生成",
        ]
        lines = [
            f"[策划提醒] 阶段：{stage}",
            f"失败原因：{reason}",
            "建议动作：",
        ]
        for idx, item in enumerate(actions, start=1):
            lines.append(f"{idx}. {item}")
        return "\n".join(lines)

    def build_writer_brief(
        self,
        chapter_no: int,
        chapter_title: str,
        chapter_goal: str,
        story_facts: dict,
        recap_state: dict,
    ) -> str:
        protagonist = story_facts.get("protagonist_name", "")
        city = story_facts.get("city", "")
        period = story_facts.get("time_period", "")
        must_mention = story_facts.get("must_mention", [])
        next_focus = recap_state.get("next_focus", [])
        open_threads = recap_state.get("open_threads", [])
        lines = [
            f"第{chapter_no}章策划简报",
            f"章节名：{chapter_title}",
            f"章节目标：{chapter_goal}",
            "硬性一致性清单：",
            f"- 主角固定名：{protagonist or '未配置'}",
            f"- 城市与时代：{city or '未配置'} / {period or '未配置'}",
            f"本章必须延续：{'；'.join(next_focus[:3]) if next_focus else '主线冲突连续推进'}",
            f"本章需照应未结线索：{'；'.join(open_threads[:3]) if open_threads else '无'}",
            f"本章建议出现关键词：{'、'.join(must_mention[:5]) if must_mention else '无'}",
            "风格执行要求：",
            "- 趣味性要更强：优先用冲突、反差、机智对话和动作推进，不要长段解释。",
            "- 降低重复：同一矛盾本章最多推进2轮，必须出现新变量或新证据，不可原地复读。",
            "- 地图推进要阶段化：不要求每章都换地图，但连续多章不得困在同一地点同一冲突里打圈。",
            "- 需要换地图时要有目的：转场必须服务剧情推进（取证/救援/对质/反制），不要为换而换。",
            "- 时间跨度保持小说连续感：相邻章节默认衔接在短时间窗口内，不要每章开头都跳过很久。",
            "- 节奏不打圈：每章至少完成一个不可逆推进结果（关系变化/证据变化/胜负变化）。",
            "执行要求：正文必须与以上硬性信息一致，不可改名、不可换城、不可改时代背景。",
        ]
        return "\n".join(lines)

    def _fallback_titles(self, book_brief: str, style: TitleStyle, count: int) -> dict:
        base_keywords = self._extract_keywords(book_brief)
        serious_templates = [
            f"{base_keywords[0]}纪事",
            f"{base_keywords[0]}与{base_keywords[1]}",
            f"在{base_keywords[1]}中重生",
            f"{base_keywords[0]}的边界",
        ]
        funny_templates = [
            f"我在{base_keywords[1]}当{base_keywords[0]}",
            f"{base_keywords[0]}，这剧情不对劲",
            f"开局一个{base_keywords[0]}，结局全靠编",
            f"别催更，我在{base_keywords[1]}救场",
        ]
        picked: list[dict] = []
        if style in {"serious", "mixed"}:
            for item in serious_templates:
                picked.append({"name": item, "style": "serious", "reason": "强调主题与辨识度"})
        if style in {"funny", "mixed"}:
            for item in funny_templates:
                picked.append({"name": item, "style": "funny", "reason": "夸张表达，适合吸睛传播"})
        return {"style": style, "titles": picked[:count]}

    def _extract_keywords(self, text: str) -> list[str]:
        cleaned = "".join(ch for ch in text if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
        if len(cleaned) < 4:
            return ["迷局", "都市"]
        return [cleaned[:2], cleaned[2:4]]

    def _fallback_plan_from_request(self, request: PlanInput) -> dict:
        total_chapters = max(20, int(round(request.target_words / 2200)))
        if "程序员" in request.brief and "穿越" in request.brief:
            volume_outline = [
                {"volume": 1, "goal": "重回入职期，确认穿越与能力边界", "chapter_range": f"1-{min(20, total_chapters)}"},
                {
                    "volume": 2,
                    "goal": "职场暗战升级，老油条抢功与反制",
                    "chapter_range": f"{min(21, total_chapters)}-{min(40, total_chapters)}",
                },
                {
                    "volume": 3,
                    "goal": "CTO线崛起与权斗爆发，男主建立技术话语权",
                    "chapter_range": f"{min(41, total_chapters)}-{min(70, total_chapters)}",
                },
                {
                    "volume": 4,
                    "goal": "联合好人离场创业，感情线进入深水区",
                    "chapter_range": f"{min(71, total_chapters)}-{min(95, total_chapters)}",
                },
                {
                    "volume": 5,
                    "goal": "创业扩张与商业战，阶段性大结局",
                    "chapter_range": f"{min(96, total_chapters)}-{total_chapters}",
                },
            ]
            chapter_seed = [
                {"chapter": 1, "goal": "泥头车后重回第一家公司入职日", "cliffhanger": "发现自己可用未来LLM辅助编程"},
                {"chapter": 2, "goal": "初识严格经理与老油条，首次小试能力", "cliffhanger": "关键任务入口被截胡"},
                {"chapter": 3, "goal": "用证据化方式反制抢功，拿回发言权", "cliffhanger": "女主在旁观中注意到男主变化"},
                {"chapter": 4, "goal": "旧女友回归视野，情感对比线启动", "cliffhanger": "女主以投资观察者身份接触男主"},
            ]
            return {
                "book_positioning": "职场重生+技术成长+创业逆袭的长连载",
                "core_theme": "从被动讨好到主动选择，靠能力与价值观重塑命运",
                "core_conflict": "个体成长与组织权力博弈的持续冲突",
                "ending_direction": "阶段性创业胜利，保留更大征程",
                "anchors": {
                    "book": [
                        "主角能力提升必须有代价与学习过程",
                        "情感线甜而不腻，女主保持独立决策权",
                        "压抑桥段连续不超过两章，第三章必须回弹",
                    ],
                    "volume": [
                        "每卷都有一次职场或商业反杀节点",
                        "每卷至少一次感情线高光互动",
                    ],
                    "chapter": [
                        "每章必须有明确推进点",
                        "每章字数不少于2000字",
                        "避免空话式总结和重复短语堆砌",
                    ],
                },
                "options_for_author": [
                    {"id": "A", "idea": "前期更偏职场，感情慢热", "pros": "现实感更强", "risks": "甜度起步较慢"},
                    {"id": "B", "idea": "前10章增加互动甜度", "pros": "读者黏性高", "risks": "需防止偏离主线"},
                ],
                "volume_outline": volume_outline,
                "chapter_seed": chapter_seed,
            }
        return {
            "book_positioning": "长篇剧情向中文小说",
            "core_theme": "在困境中自我重塑",
            "core_conflict": "个人信念与现实秩序冲突",
            "ending_direction": "开放式希望结局",
            "anchors": {
                "book": ["核心主题不漂移", "关键人物动机一致"],
                "volume": ["每卷必须推进主冲突", "每卷结尾保留悬念"],
                "chapter": ["每章至少完成一个剧情目标", "避免重复段落和口号式总结"],
            },
            "options_for_author": [
                {"id": "A", "idea": "主角提早暴露身份，换取关键信息", "pros": "冲突快速升级", "risks": "后续空间被压缩"},
                {"id": "B", "idea": "主角保持潜伏，先查清幕后关系", "pros": "逻辑更稳，铺垫充分", "risks": "前期节奏可能偏慢"},
            ],
            "volume_outline": [
                {"volume": 1, "goal": "建立世界规则与主冲突", "chapter_range": "1-10"},
                {"volume": 2, "goal": "冲突升级与关键抉择", "chapter_range": "11-20"},
            ],
            "chapter_seed": [
                {"chapter": 1, "goal": "抛出异常事件", "cliffhanger": "主角被迫做出选择"},
                {"chapter": 2, "goal": "揭示代价", "cliffhanger": "更大势力现身"},
            ],
        }
