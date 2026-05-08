import json
import re

from longbookwritter.llm.base import BaseTextClient


class ReviewerAgent:
    def __init__(self, llm_client: BaseTextClient):
        self.llm_client = llm_client

    def review_chapter(
        self,
        chapter_text: str,
        chapter_goal: str,
        policy: str,
        chapter_no: int | None = None,
        anchors: dict | None = None,
        character_constraints: dict | None = None,
        story_facts: dict | None = None,
        planner_brief: str = "",
        min_words: int = 2000,
    ) -> dict:
        review_policy = policy
        if planner_brief:
            review_policy += "\n策划写作简报:\n" + planner_brief
        llm_review = self._review_by_llm(chapter_text=chapter_text, chapter_goal=chapter_goal, policy=review_policy)
        allowed_names: list[str] = []
        allowed_names.extend(character_constraints.get("fixed_names", []))
        protagonist = story_facts.get("protagonist_name", "")
        if protagonist:
            allowed_names.append(protagonist)
        llm_review = self._normalize_llm_review(llm_review=llm_review, allowed_names=allowed_names)
        local_review = self._review_by_rules(
            chapter_text=chapter_text,
            chapter_goal=chapter_goal,
            anchors=anchors or {},
            character_constraints=character_constraints or {},
            story_facts=story_facts or {},
            chapter_no=chapter_no or 0,
            planner_brief=planner_brief,
            min_words=min_words,
        )
        return self._merge_reviews(llm_review=llm_review, local_review=local_review)

    def _review_by_llm(self, chapter_text: str, chapter_goal: str, policy: str) -> dict:
        prompt = (
            "你是小说质检编辑。请做一致性和文风检查，输出JSON。\n"
            "{\n"
            '  "score": 0,\n'
            '  "issues": [{"type":"consistency|ai_tone|repetition|logic","detail":"...","severity":"high|mid|low"}],\n'
            '  "must_fix": ["..."],\n'
            '  "pass": true\n'
            "}\n"
            f"章节目标：{chapter_goal}\n"
            f"检查策略：{policy}\n"
            f"章节正文：\n{chapter_text}\n"
        )
        result = self.llm_client.generate_text(prompt_text=prompt)
        if not result.success:
            return {
                "source": "llm",
                "available": False,
                "score": 0,
                "issues": [{"type": "system", "detail": result.content, "severity": "mid"}],
                "must_fix": [],
                "pass": False,
            }
        try:
            parsed = json.loads(result.content)
            parsed["source"] = "llm"
            parsed["available"] = True
            return parsed
        except json.JSONDecodeError:
            return {
                "source": "llm",
                "available": False,
                "score": 0,
                "issues": [{"type": "format", "detail": "检查输出非JSON", "severity": "mid"}],
                "must_fix": ["重新执行检查，要求严格JSON"],
                "pass": False,
            }

    def _review_by_rules(
        self,
        chapter_text: str,
        chapter_goal: str,
        anchors: dict,
        character_constraints: dict,
        story_facts: dict,
        chapter_no: int,
        planner_brief: str,
        min_words: int,
    ) -> dict:
        issues: list[dict] = []
        must_fix: list[str] = []
        score = 100

        ai_phrases = ["总而言之", "总的来说", "不难看出", "值得一提的是", "可以说", "与此同时"]
        hit_ai = [p for p in ai_phrases if p in chapter_text]
        if hit_ai:
            issues.append(
                {"type": "ai_tone", "detail": f"疑似模板表达: {', '.join(hit_ai[:3])}", "severity": "mid"}
            )
            must_fix.append("替换模板化连接词，改为人物动作或情境推进。")
            score -= 10

        repeats = self._detect_repeated_phrases(chapter_text)
        if repeats:
            issues.append(
                {
                    "type": "repetition",
                    "detail": f"重复短语偏多: {', '.join(repeats[:3])}",
                    "severity": "low",
                }
            )
            must_fix.append("降低高频短语复用，改写相邻段落句式。")
            score -= 5

        if chapter_goal and not self._goal_covered(chapter_goal=chapter_goal, chapter_text=chapter_text):
            issues.append(
                {"type": "consistency", "detail": "章节目标未被明显覆盖", "severity": "mid"}
            )
            must_fix.append("补齐本章目标事件，避免偏离主任务。")
            score -= 10

        chapter_anchors = anchors.get("chapter", [])
        for anchor in chapter_anchors[:2]:
            if any(tag in anchor for tag in ("字数", "不少于", "2000")):
                continue
            if anchor and anchor[:4] not in chapter_text:
                issues.append(
                    {"type": "consistency", "detail": f"章节锚点未明显体现: {anchor}", "severity": "low"}
                )
                score -= 5

        chapter_len = len(re.sub(r"\s+", "", chapter_text))
        if chapter_len < min_words:
            issues.append(
                {
                    "type": "length",
                    "detail": f"章节字数不足：{chapter_len} < {min_words}",
                    "severity": "high",
                }
            )
            must_fix.append(f"将章节扩写到不少于{min_words}字，补足场景推进和人物互动。")
            score -= 30

        # Character consistency guard: banned aliases are hard failures.
        banned_aliases = character_constraints.get("banned_aliases", {})
        for wrong_name, right_name in banned_aliases.items():
            if wrong_name and wrong_name in chapter_text:
                issues.append(
                    {
                        "type": "consistency",
                        "detail": f"角色命名冲突：检测到禁用名“{wrong_name}”，应统一为“{right_name}”",
                        "severity": "high",
                    }
                )
                must_fix.append(f"将“{wrong_name}”统一替换为“{right_name}”，并检查全章称呼一致性。")
                score -= 40

        protagonist = story_facts.get("protagonist_name", "")
        if protagonist and protagonist not in chapter_text:
            issues.append(
                {
                    "type": "consistency",
                    "detail": f"主角名未出现：{protagonist}",
                    "severity": "high",
                }
            )
            must_fix.append(f"在正文中明确出现主角名“{protagonist}”，避免角色漂移。")
            score -= 40

        city = story_facts.get("city", "")
        if city and city not in chapter_text:
            issues.append(
                {
                    "type": "consistency",
                    "detail": f"关键背景缺失：城市“{city}”未出现",
                    "severity": "mid",
                }
            )
            must_fix.append(f"补充场景锚定信息，明确本章发生城市为“{city}”。")
            score -= 10

        period = story_facts.get("time_period", "")
        if period and period not in chapter_text:
            issues.append(
                {
                    "type": "consistency",
                    "detail": f"关键背景缺失：时代信息“{period}”未出现",
                    "severity": "low",
                }
            )
            score -= 5

        for keyword in story_facts.get("must_mention", [])[:3]:
            if keyword and keyword not in chapter_text:
                issues.append(
                    {
                        "type": "consistency",
                        "detail": f"关键连续性信息未出现：{keyword}",
                        "severity": "low",
                    }
                )
                score -= 3

        if chapter_no >= 10:
            early_terms = ["六月", "七月", "八月", "九月", "夏末", "初秋"]
            hit_early = [t for t in early_terms if t in chapter_text]
            if hit_early:
                issues.append(
                    {
                        "type": "consistency",
                        "detail": f"时间线疑似回跳：检测到早期时间词 {', '.join(hit_early[:4])}",
                        "severity": "mid",
                    }
                )
                must_fix.append("统一时间线向前推进，删除或改写回跳月份描述（除非明确回忆场景）。")
                score -= 10

        for item in self._extract_recap_focus(planner_brief)[:2]:
            if len(item) >= 2 and item not in chapter_text:
                issues.append(
                    {
                        "type": "consistency",
                        "detail": f"前情衔接不足：未体现策划要求“{item}”",
                        "severity": "low",
                    }
                )
                score -= 3

        # Chapter-level map shift is not mandatory, but long runs in one scene should be flagged.
        if chapter_no >= 30 and self._is_stuck_same_scene(chapter_text):
            issues.append(
                {
                    "type": "consistency",
                    "detail": "场景推进偏单一：章节疑似在同一地点反复打圈，缺少阶段性地图推进",
                    "severity": "low",
                }
            )
            must_fix.append("优化阶段推进：可不强制换地图，但应引入新行动目标/新场景节点，避免原地拉扯。")
            score -= 5

        if chapter_no >= 30 and self._has_hard_time_jump(chapter_text):
            issues.append(
                {
                    "type": "consistency",
                    "detail": "时间跨度偏大：章节开头或正文出现较大跳时，影响长篇连续阅读感",
                    "severity": "mid",
                }
            )
            must_fix.append("保持章节间连续时间线，非必要不要使用“数月后/一年后”等大幅跳时。")
            score -= 10

        passed = score >= 75 and not any(item["severity"] == "high" for item in issues)
        return {
            "source": "rules",
            "available": True,
            "score": max(score, 0),
            "issues": issues,
            "must_fix": must_fix,
            "pass": passed,
        }

    def _merge_reviews(self, llm_review: dict, local_review: dict) -> dict:
        merged_issues = list(local_review.get("issues", []))
        merged_issues.extend(llm_review.get("issues", []))

        must_fix = list(local_review.get("must_fix", []))
        for item in llm_review.get("must_fix", []):
            if item not in must_fix:
                must_fix.append(item)

        llm_available = llm_review.get("available", False)
        local_score = local_review.get("score", 0)
        if llm_available:
            llm_score = llm_review.get("score", 0)
            final_score = int((llm_score + local_score) / 2)
        else:
            final_score = local_score
        local_high = any(str(item.get("severity", "")).lower() == "high" for item in local_review.get("issues", []))
        llm_high = any(str(item.get("severity", "")).lower() == "high" for item in llm_review.get("issues", []))
        if llm_available:
            # Prefer semantic LLM judgement for publish gate, while keeping high-risk hard stops.
            final_pass = bool(llm_review.get("pass", False)) and not local_high and not llm_high and final_score >= 75
        else:
            final_pass = bool(local_review.get("pass", False)) and not local_high and final_score >= 75

        return {
            "score": final_score,
            "issues": merged_issues,
            "must_fix": must_fix,
            "pass": final_pass,
            "degraded_mode": not llm_available,
            "sources": {"llm": llm_review, "rules": local_review},
        }

    def _normalize_llm_review(self, llm_review: dict, allowed_names: list[str]) -> dict:
        if not llm_review.get("available", False):
            return llm_review
        normalized = dict(llm_review)
        raw_issues = llm_review.get("issues", [])
        kept_issues: list[dict] = []
        dropped = 0
        for issue in raw_issues:
            detail = str(issue.get("detail", ""))
            if self._is_known_character_false_positive(text=detail, allowed_names=allowed_names):
                dropped += 1
                continue
            kept_issues.append(issue)
        normalized["issues"] = kept_issues
        must_fix = []
        for item in llm_review.get("must_fix", []):
            if self._is_known_character_false_positive(text=str(item), allowed_names=allowed_names):
                continue
            must_fix.append(item)
        normalized["must_fix"] = must_fix
        if dropped > 0:
            has_high = any(str(item.get("severity", "")) == "high" for item in kept_issues)
            if not has_high:
                normalized["pass"] = True
            if int(normalized.get("score", 0)) < 80:
                normalized["score"] = 80
        return normalized

    def _is_known_character_false_positive(self, text: str, allowed_names: list[str]) -> bool:
        if not text:
            return False
        lowered = text.strip()
        if not any(name and name in lowered for name in allowed_names):
            return False
        if "铺垫" not in lowered and "突然出现" not in lowered:
            return False
        hit_pattern = ("新增", "新角色", "未做任何前置", "无交代", "人物出场")
        return any(token in lowered for token in hit_pattern)

    def _detect_repeated_phrases(self, text: str) -> list[str]:
        sentence_chunks = re.split(r"[。！？\n]", text)
        grams: dict[str, int] = {}
        for sent in sentence_chunks:
            sent = sent.strip()
            if len(sent) < 8:
                continue
            for i in range(len(sent) - 3):
                gram = sent[i : i + 4]
                if " " in gram:
                    continue
                if not all(ch.isalnum() or ("\u4e00" <= ch <= "\u9fff") for ch in gram):
                    continue
                grams[gram] = grams.get(gram, 0) + 1
        suspects = [k for k, v in grams.items() if v >= 4]
        return suspects[:8]

    def _extract_recap_focus(self, planner_brief: str) -> list[str]:
        if not planner_brief.strip():
            return []
        keys = ("本章必须延续：", "本章需照应未结线索：")
        values: list[str] = []
        for line in planner_brief.splitlines():
            text = line.strip()
            for key in keys:
                if text.startswith(key):
                    payload = text[len(key) :].strip()
                    parts = [p.strip() for p in re.split(r"[；;、]", payload) if p.strip() and p.strip() != "无"]
                    values.extend(parts)
        return values[:5]

    def _is_stuck_same_scene(self, text: str) -> bool:
        if not text:
            return True
        map_tokens = [
            "办公室", "会议室", "机房", "医院", "码头", "仓库", "法庭", "车库", "写字楼", "总部", "园区", "灯塔",
        ]
        hit = {tok for tok in map_tokens if tok in text}
        action_tokens = [
            "取证", "追查", "对质", "反制", "救援", "突围", "交付", "谈判", "抓捕", "撤离",
        ]
        action_hit = sum(1 for tok in action_tokens if tok in text)
        # if scene token too few and action variety too少，判定可能打圈
        return len(hit) <= 1 and action_hit <= 1

    def _has_hard_time_jump(self, text: str) -> bool:
        if not text:
            return False
        jump_terms = [
            "几个月后", "数月后", "半年后", "一年后", "两年后", "三年后",
            "很久以后", "不久后又过了", "转眼一年",
        ]
        return any(term in text for term in jump_terms)

    def _goal_covered(self, chapter_goal: str, chapter_text: str) -> bool:
        parts = re.split(r"[，,。；;、\s]+", chapter_goal)
        keywords = [p.strip() for p in parts if len(p.strip()) >= 2]
        if not keywords:
            return True
        hit = 0
        for kw in keywords[:8]:
            if kw in chapter_text:
                hit += 1
        return hit >= max(1, min(2, len(keywords)))
