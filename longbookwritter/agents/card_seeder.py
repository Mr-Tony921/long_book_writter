"""LLM-driven seeder for character / event cards.

The seeder turns a small ``seed_roster.json`` (declared by the author) into
fully-populated baseline cards. Two modes:

* **Canon character / event** — the LLM imports canonical traits, goals, and
  relationships from the original work referenced by ``book_config.canon_reference``,
  then adds a ``deviation_from_canon`` field describing how *this* book will
  modify them. For "needs human pick" fields (secret, role, key traits combo,
  ...) the prompt asks for 2-3 alternatives that get persisted into the card's
  ``_options_for_review`` extra so the author can pick later.

* **Original character / event** — the LLM works from a free-text brief (e.g.
  the protagonist sketch we worked out in the plan file). Again it must offer
  2-3 alternative variants for the most consequential fields.

The seeder is intentionally idempotent: an existing card on disk is left
alone unless ``force=True``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from longbookwritter.cards.store import (
    CharacterCard,
    EventCard,
    load_character_card,
    load_event_card,
    save_character_card,
    save_event_card,
)
from longbookwritter.llm.base import BaseTextClient


class CardSeeder:
    def __init__(self, llm_client: BaseTextClient):
        self.llm_client = llm_client

    def seed_character_card(
        self,
        name: str,
        is_canon: bool,
        brief: str,
        book_context: dict[str, Any],
        project_dir: Path,
        force: bool = False,
    ) -> dict[str, Any]:
        existing = load_character_card(project_dir, name)
        if existing is not None and not force:
            return {"name": name, "skipped": "exists", "path": ""}

        prompt = self._character_prompt(
            name=name,
            is_canon=is_canon,
            brief=brief,
            book_context=book_context,
        )
        result = self.llm_client.generate_text(prompt_text=prompt)
        if not result.success:
            return {"name": name, "error": f"llm_failed:{result.content[:200]}"}

        parsed = self._parse_json(result.content)
        if parsed is None:
            return {
                "name": name,
                "error": "llm_returned_non_json",
                "raw": result.content[:400],
            }

        card_payload = parsed.get("card") or {}
        if not isinstance(card_payload, dict) or not card_payload.get("name"):
            return {"name": name, "error": "card_payload_invalid", "raw": result.content[:400]}
        # Stamp options + seed metadata into the card's preserved extras.
        card_payload["_options_for_review"] = parsed.get("options", {}) or {}
        card_payload["_seed_notes"] = parsed.get("notes", "") or ""
        card_payload["_seed_source"] = "canon" if is_canon else "original"
        card = CharacterCard.from_json(card_payload)
        if not card.name:
            card.name = name
        path = save_character_card(project_dir, card)
        return {
            "name": card.name,
            "path": str(path),
            "options": parsed.get("options", {}),
            "notes": parsed.get("notes", ""),
        }

    def seed_event_card(
        self,
        event_id: str,
        is_canon: bool,
        name: str,
        brief: str,
        planned_chapter_range: str,
        book_context: dict[str, Any],
        project_dir: Path,
        force: bool = False,
    ) -> dict[str, Any]:
        existing = load_event_card(project_dir, event_id)
        if existing is not None and not force:
            return {"event_id": event_id, "skipped": "exists", "path": ""}

        prompt = self._event_prompt(
            event_id=event_id,
            name=name,
            is_canon=is_canon,
            brief=brief,
            planned_chapter_range=planned_chapter_range,
            book_context=book_context,
        )
        result = self.llm_client.generate_text(prompt_text=prompt)
        if not result.success:
            return {"event_id": event_id, "error": f"llm_failed:{result.content[:200]}"}
        parsed = self._parse_json(result.content)
        if parsed is None:
            return {
                "event_id": event_id,
                "error": "llm_returned_non_json",
                "raw": result.content[:400],
            }
        card_payload = parsed.get("card") or {}
        if not isinstance(card_payload, dict):
            return {"event_id": event_id, "error": "card_payload_invalid"}
        card_payload.setdefault("event_id", event_id)
        card_payload.setdefault("name", name)
        card_payload.setdefault("planned_chapter_range", planned_chapter_range)
        card_payload["_options_for_review"] = parsed.get("options", {}) or {}
        card_payload["_seed_notes"] = parsed.get("notes", "") or ""
        card_payload["_seed_source"] = "canon" if is_canon else "original"
        card = EventCard.from_json(card_payload)
        if not card.event_id:
            card.event_id = event_id
        path = save_event_card(project_dir, card)
        return {
            "event_id": card.event_id,
            "path": str(path),
            "options": parsed.get("options", {}),
            "notes": parsed.get("notes", ""),
        }

    def _character_prompt(
        self,
        name: str,
        is_canon: bool,
        brief: str,
        book_context: dict[str, Any],
    ) -> str:
        canon_ref = str(book_context.get("canon_reference", "") or "")
        positioning = str(book_context.get("book_positioning", "") or "")
        theme = str(book_context.get("core_theme", "") or "")
        conflict = str(book_context.get("core_conflict", "") or "")
        book_title = str(book_context.get("title", "") or "")
        style = str(book_context.get("style", "") or "")
        canon_guidance = (
            "你必须基于原作中该角色的设定（性格/目标/关系/秘密/外貌/说话风格）填写主字段。\n"
            "如果你对原作不熟悉，请把 card.background 留空，traits/goals 留空，"
            "并在 notes 字段直接说明“原作不熟，请人工补充”——不要凭空编造。\n"
            "本书对原作角色会有改编（角色弧线 / 关系走向 / 命运结局），请在 card.deviation_from_canon 写明本书要做的改编方向。\n"
            if is_canon
            else "这是本书的原创角色（不在任何原作中）。基于下方“作者已沟通的角色简介”填写主字段。\n"
            "不要拿其他作品的同名角色硬套；如果简介信息不够，请在 notes 中说明哪些字段缺信息。\n"
        )
        return (
            "你是日式轻小说角色卡设计师。任务：为新书设计一张《角色卡》初稿，保存到磁盘后让作者审改。\n\n"
            "硬性要求：\n"
            "1. 只输出 JSON，不要解释、不要 markdown 标题、不要免责声明。\n"
            "2. JSON 顶层结构如下：\n"
            "   {\n"
            "     \"card\": { 角色卡所有字段（见下方 schema） },\n"
            "     \"options\": {\n"
            "       \"secret\": [{\"label\":\"A\",\"value\":\"...\"}, ...],\n"
            "       \"role_in_this_book\": [{\"label\":\"A\",\"value\":\"...\"}, ...],\n"
            "       \"key_traits_combo\": [{\"label\":\"A\",\"value\":\"...\"}, ...]\n"
            "     },\n"
            "     \"notes\": \"对作者的简短提示（缺资料、待确认事项等）\"\n"
            "   }\n"
            "3. card 字段含义（必填，缺资料留空字符串而不是 null）：\n"
            "   name, aliases[], role (protagonist|female_lead|companion|antagonist|side), importance(1-5),\n"
            "   traits[], goals[], likes[], dislikes[], background, current_state,\n"
            "   relationships{对方:关系一句话}, secret, arc_progress, appearance_keywords[],\n"
            "   speech_style, deviation_from_canon, last_updated_chapter(=0).\n"
            "4. options 必须对“secret / role_in_this_book / key_traits_combo”三个字段各给 2-3 个不同候选。\n"
            "5. card.secret 字段写你最推荐的版本；options.secret 列出其他候选。本字段是隐藏底牌，正文绝不能直接揭露。\n"
            "6. 不要在 card.background 中复述原作大段剧情，控制在 150 字内，重点是“到本书开篇为止这个角色的状态”。\n"
            f"{canon_guidance}\n"
            f"角色名：{name}\n"
            f"是否原作角色：{'是（请基于原作填写）' if is_canon else '否（原创角色）'}\n"
            f"作者已沟通的角色简介 / 设定要点：{brief or '无'}\n"
            f"本书定位：{positioning or '未配置'}\n"
            f"核心主题：{theme or '未配置'}\n"
            f"核心冲突：{conflict or '未配置'}\n"
            f"风格基调：{style or '未配置'}\n"
            f"原作参考：{canon_ref or '未配置（如该角色为原作角色，请基于你的知识填写，不熟则留空）'}\n"
            f"作品标题：{book_title or '未配置'}\n"
        )

    def _event_prompt(
        self,
        event_id: str,
        name: str,
        is_canon: bool,
        brief: str,
        planned_chapter_range: str,
        book_context: dict[str, Any],
    ) -> str:
        canon_ref = str(book_context.get("canon_reference", "") or "")
        positioning = str(book_context.get("book_positioning", "") or "")
        theme = str(book_context.get("core_theme", "") or "")
        canon_guidance = (
            "请基于原作中该事件的剧情（起因 / 过程 / 结果 / 关键人物）填写主字段，"
            "并在 deviation_from_canon 中说明本书对它的改写方向（如尤菲事件本书要保下尤菲）。"
            "不熟悉原作就在 notes 写明并留空相关字段。\n"
            if is_canon
            else "这是本书原创事件，请基于下方简介补全主字段。\n"
        )
        return (
            "你是小说事件卡设计师。任务：为新书设计一张《事件卡》初稿。\n\n"
            "硬性要求：\n"
            "1. 只输出 JSON。\n"
            "2. 顶层结构：{\"card\":{...}, \"options\":{...}, \"notes\":\"...\"}\n"
            "3. card 字段：event_id, name, status('planned'|'ongoing'|'resolved'|'cancelled'), "
            "planned_chapter_range, cause, process[], related_characters[], current_state, "
            "future_direction, anchors_to_keep[], deviation_from_canon, last_updated_chapter(=0).\n"
            "4. options 必须给以下字段各 2-3 个候选：\n"
            "   - deviation_from_canon（本书的改写方向，每个候选是一段一句话表述）\n"
            "   - climax_design（本事件高潮场面的呈现方式候选）\n"
            "   - cost_paid（男主在改写时付出的“未预料代价”候选；与本书弥补遗憾主题挂钩）\n"
            "5. card.future_direction 写最推荐的方向；其余候选放 options。\n"
            f"{canon_guidance}\n"
            f"事件 ID：{event_id}\n"
            f"事件名：{name}\n"
            f"是否原作事件：{'是' if is_canon else '否'}\n"
            f"作者简介 / 设定要点：{brief or '无'}\n"
            f"计划章节范围：{planned_chapter_range or '未定'}\n"
            f"本书定位：{positioning or '未配置'}\n"
            f"核心主题：{theme or '未配置'}\n"
            f"原作参考：{canon_ref or '未配置'}\n"
        )

    def _parse_json(self, content: str) -> dict | None:
        text = content.strip()
        # Tolerate ```json ... ``` fences.
        if text.startswith("```"):
            text = text.strip("`")
            # Strip leading "json\n" if present
            if text.lower().startswith("json"):
                text = text[4:].lstrip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Try to locate the first/last brace.
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    return None
            return None


def format_card_review(card_results: list[dict], event_results: list[dict]) -> str:
    """Aggregate seed results into a single markdown review file for the author."""
    lines = [
        "# Seed Review",
        "",
        "本文件是 seed-cards 自动生成的初稿审查列表。每个角色/事件下列出 LLM 给出的备选项，",
        "你可以直接编辑对应的 JSON 卡片（`00_config/cards/characters/*.json` 或 `events/*.json`），",
        "或把你选中的候选粘贴到对应卡片的字段里。",
        "",
        "## 角色卡",
        "",
    ]
    if not card_results:
        lines.append("_无_\n")
    for r in card_results:
        if r.get("skipped"):
            lines.append(f"### {r.get('name', '?')}  — _跳过（已存在）_")
            lines.append("")
            continue
        if r.get("error"):
            lines.append(f"### {r.get('name', '?')}  — ❌ 错误：{r.get('error')}")
            lines.append("")
            continue
        lines.append(f"### {r.get('name', '?')}")
        if r.get("notes"):
            lines.append(f"_seed notes_：{r['notes']}")
        opts = r.get("options") or {}
        for field, candidates in opts.items():
            lines.append(f"**字段 `{field}` 备选**：")
            for cand in candidates or []:
                label = cand.get("label", "?")
                value = cand.get("value", "")
                lines.append(f"- **{label}**: {value}")
        lines.append("")

    lines.extend(["## 事件卡", ""])
    if not event_results:
        lines.append("_无_\n")
    for r in event_results:
        if r.get("skipped"):
            lines.append(f"### {r.get('event_id', '?')}  — _跳过（已存在）_")
            lines.append("")
            continue
        if r.get("error"):
            lines.append(f"### {r.get('event_id', '?')}  — ❌ 错误：{r.get('error')}")
            lines.append("")
            continue
        lines.append(f"### {r.get('event_id', '?')}")
        if r.get("notes"):
            lines.append(f"_seed notes_：{r['notes']}")
        opts = r.get("options") or {}
        for field, candidates in opts.items():
            lines.append(f"**字段 `{field}` 备选**：")
            for cand in candidates or []:
                label = cand.get("label", "?")
                value = cand.get("value", "")
                lines.append(f"- **{label}**: {value}")
        lines.append("")
    return "\n".join(lines) + "\n"
