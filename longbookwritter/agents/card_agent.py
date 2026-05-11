"""Read-and-write agent for character / event cards.

The agent has two responsibilities:

1. ``build_card_brief`` selects the cards relevant to the upcoming chapter and
   formats a compact, human-readable brief that the writer can ingest as part
   of its prompt.
2. ``apply_chapter_updates`` asks the LLM to propose minimal-diff updates to
   the cards based on the published chapter text, then writes them back to
   disk. The agent never invents new cornerstone cards; it can append a
   minor character card if the chapter introduces an obviously new named
   role, but importance is capped at 1.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from longbookwritter.cards.store import (
    CharacterCard,
    EventCard,
    list_character_cards,
    list_event_cards,
    load_character_card,
    load_event_card,
    save_character_card,
    save_event_card,
    select_relevant,
)
from longbookwritter.llm.base import BaseTextClient


class CardAgent:
    def __init__(self, llm_client: BaseTextClient):
        self.llm_client = llm_client

    def build_card_brief(
        self,
        project_dir: Path,
        chapter_no: int,
        chapter_goal: str,
        chapter_seed_focus: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        chars = list_character_cards(project_dir)
        events = list_event_cards(project_dir)
        if not chars and not events:
            return {"text": "", "character_names": [], "event_ids": []}

        focus = list(chapter_seed_focus or [])
        chosen_chars, chosen_events = select_relevant(
            chapter_no=chapter_no,
            chapter_goal=chapter_goal,
            chapter_seed_focus=focus,
            character_cards=chars,
            event_cards=events,
        )

        lines: list[str] = []
        if chosen_chars:
            lines.append("【本章相关角色卡（写作必读）】")
            for c in chosen_chars:
                lines.append(self._format_character(c))
        if chosen_events:
            lines.append("【本章相关事件卡（写作必读）】")
            for e in chosen_events:
                lines.append(self._format_event(e))

        return {
            "text": "\n".join(lines),
            "character_names": [c.name for c in chosen_chars if c.name],
            "event_ids": [e.event_id for e in chosen_events if e.event_id],
        }

    def apply_chapter_updates(
        self,
        project_dir: Path,
        chapter_no: int,
        chapter_title: str,
        chapter_text: str,
        relevant_character_names: list[str],
        relevant_event_ids: list[str],
    ) -> dict[str, Any]:
        if not relevant_character_names and not relevant_event_ids:
            return {"updated_characters": [], "updated_events": [], "skipped": "no_relevant_cards"}

        # Load only the cards that were declared relevant; leave the rest alone.
        char_cards: list[CharacterCard] = []
        for name in relevant_character_names:
            card = load_character_card(project_dir, name)
            if card is not None:
                char_cards.append(card)
        event_cards: list[EventCard] = []
        for eid in relevant_event_ids:
            card = load_event_card(project_dir, eid)
            if card is not None:
                event_cards.append(card)

        if not char_cards and not event_cards:
            return {"updated_characters": [], "updated_events": [], "skipped": "cards_missing"}

        prompt = self._build_update_prompt(
            chapter_no=chapter_no,
            chapter_title=chapter_title,
            chapter_text=chapter_text,
            char_cards=char_cards,
            event_cards=event_cards,
        )
        result = self.llm_client.generate_text(prompt_text=prompt)
        if not result.success:
            return {
                "updated_characters": [],
                "updated_events": [],
                "skipped": f"llm_failed:{result.content[:200]}",
            }
        try:
            patch = json.loads(result.content)
        except json.JSONDecodeError:
            return {
                "updated_characters": [],
                "updated_events": [],
                "skipped": "llm_non_json",
                "raw": result.content[:400],
            }

        updated_chars: list[str] = []
        for ch_patch in patch.get("character_updates", []) or []:
            target = self._match_character(ch_patch, char_cards)
            if target is None:
                continue
            self._apply_character_patch(target, ch_patch, chapter_no)
            save_character_card(project_dir, target)
            updated_chars.append(target.name)

        updated_events: list[str] = []
        for ev_patch in patch.get("event_updates", []) or []:
            target = self._match_event(ev_patch, event_cards)
            if target is None:
                continue
            self._apply_event_patch(target, ev_patch, chapter_no)
            save_event_card(project_dir, target)
            updated_events.append(target.event_id)

        return {
            "updated_characters": updated_chars,
            "updated_events": updated_events,
            "skipped": "",
        }

    def _format_character(self, c: CharacterCard) -> str:
        lines = [f"- 角色【{c.name}】（{c.role}，重要度{c.importance}）"]
        if c.aliases:
            lines.append(f"  别名/代号：{'、'.join(c.aliases)}")
        if c.traits:
            lines.append(f"  性格特质：{'、'.join(c.traits[:6])}")
        if c.goals:
            lines.append(f"  当前目标：{'；'.join(c.goals[:3])}")
        if c.likes or c.dislikes:
            likes = "、".join(c.likes[:4])
            dislikes = "、".join(c.dislikes[:4])
            lines.append(f"  喜好/厌恶：{likes or '—'} / {dislikes or '—'}")
        if c.background:
            lines.append(f"  背景：{c.background[:120]}")
        if c.current_state:
            lines.append(f"  当前状态：{c.current_state[:120]}")
        if c.relationships:
            rel_text = "；".join(f"{k}:{v}" for k, v in list(c.relationships.items())[:4])
            lines.append(f"  关系：{rel_text}")
        if c.secret:
            lines.append(f"  隐藏底牌（写作中绝不能直接暴露）：{c.secret[:120]}")
        if c.appearance_keywords:
            lines.append(f"  外貌关键词：{'、'.join(c.appearance_keywords[:5])}")
        if c.speech_style:
            lines.append(f"  说话风格：{c.speech_style[:80]}")
        if c.deviation_from_canon:
            lines.append(f"  对原作的偏离：{c.deviation_from_canon[:120]}")
        return "\n".join(lines)

    def _format_event(self, e: EventCard) -> str:
        lines = [f"- 事件【{e.name or e.event_id}】（{e.status}）"]
        if e.planned_chapter_range:
            lines.append(f"  计划章节：{e.planned_chapter_range}")
        if e.related_characters:
            lines.append(f"  涉及角色：{'、'.join(e.related_characters[:6])}")
        if e.cause:
            lines.append(f"  起因：{e.cause[:140]}")
        if e.process:
            lines.append(f"  过程节点：{'；'.join(e.process[:4])}")
        if e.current_state:
            lines.append(f"  现状：{e.current_state[:140]}")
        if e.future_direction:
            lines.append(f"  发展方向：{e.future_direction[:140]}")
        if e.anchors_to_keep:
            lines.append(f"  必须保留的锚点：{'；'.join(e.anchors_to_keep[:3])}")
        if e.deviation_from_canon:
            lines.append(f"  对原作的偏离：{e.deviation_from_canon[:140]}")
        return "\n".join(lines)

    def _build_update_prompt(
        self,
        chapter_no: int,
        chapter_title: str,
        chapter_text: str,
        char_cards: list[CharacterCard],
        event_cards: list[EventCard],
    ) -> str:
        char_summary = "\n".join(self._format_character(c) for c in char_cards)
        event_summary = "\n".join(self._format_event(e) for e in event_cards)
        return (
            "你是小说连续性编辑。读完本章正文后，对涉及到的角色卡 / 事件卡做最小幅度的更新。\n"
            "硬性要求：\n"
            "1. 只输出 JSON，不输出多余文字。\n"
            "2. 只更新本章发生了实际变化的字段；没变化的字段不要回传。\n"
            "3. 不要新增角色或事件卡（这一步只做更新）。\n"
            "4. 不要泄露/改写已有 secret 字段；如本章泄露了某个 secret，把 secret 移到 deviation_from_canon 并清空 secret 即可。\n"
            "5. 字段如下（角色与事件分别在 character_updates / event_updates 数组中）：\n"
            '   {"character_updates": [{"name": "...", "current_state": "...", "arc_progress": "...", '
            '"relationships": {"对方": "新关系"}, "traits_add": ["..."], "goals_add": ["..."], '
            '"goals_remove": ["..."]}], "event_updates": [{"event_id": "...", "status": "ongoing|resolved", '
            '"current_state": "...", "future_direction": "...", "process_add": ["..."]}]}\n\n'
            f"章节号：{chapter_no}\n"
            f"章节名：{chapter_title}\n"
            "---当前角色卡---\n"
            f"{char_summary}\n"
            "---当前事件卡---\n"
            f"{event_summary}\n"
            "---本章正文---\n"
            f"{chapter_text}\n"
        )

    def _match_character(
        self,
        patch: dict[str, Any],
        char_cards: list[CharacterCard],
    ) -> CharacterCard | None:
        target_name = str(patch.get("name", "") or "").strip()
        if not target_name:
            return None
        for c in char_cards:
            if c.name == target_name or target_name in c.aliases:
                return c
        return None

    def _match_event(
        self,
        patch: dict[str, Any],
        event_cards: list[EventCard],
    ) -> EventCard | None:
        target_id = str(patch.get("event_id", "") or "").strip()
        if not target_id:
            return None
        for e in event_cards:
            if e.event_id == target_id:
                return e
        return None

    def _apply_character_patch(
        self,
        card: CharacterCard,
        patch: dict[str, Any],
        chapter_no: int,
    ) -> None:
        if "current_state" in patch and patch["current_state"]:
            card.current_state = str(patch["current_state"]).strip()[:240]
        if "arc_progress" in patch and patch["arc_progress"]:
            card.arc_progress = str(patch["arc_progress"]).strip()[:200]
        rel_patch = patch.get("relationships") or {}
        if isinstance(rel_patch, dict):
            for k, v in rel_patch.items():
                key = str(k).strip()
                val = str(v).strip()[:160]
                if key:
                    card.relationships[key] = val
        for trait in patch.get("traits_add", []) or []:
            t = str(trait).strip()
            if t and t not in card.traits:
                card.traits.append(t)
        for goal in patch.get("goals_add", []) or []:
            g = str(goal).strip()
            if g and g not in card.goals:
                card.goals.append(g)
        for goal in patch.get("goals_remove", []) or []:
            g = str(goal).strip()
            if g and g in card.goals:
                card.goals.remove(g)
        secret_patch = patch.get("secret_revealed")
        if secret_patch and card.secret:
            card.deviation_from_canon = (
                (card.deviation_from_canon + " | " if card.deviation_from_canon else "")
                + f"第{chapter_no}章已揭露：{card.secret}"
            )[:400]
            card.secret = ""
        card.last_updated_chapter = max(card.last_updated_chapter, chapter_no)

    def _apply_event_patch(
        self,
        card: EventCard,
        patch: dict[str, Any],
        chapter_no: int,
    ) -> None:
        status = str(patch.get("status", "") or "").strip()
        if status in ("planned", "ongoing", "resolved", "cancelled"):
            card.status = status
        if "current_state" in patch and patch["current_state"]:
            card.current_state = str(patch["current_state"]).strip()[:240]
        if "future_direction" in patch and patch["future_direction"]:
            card.future_direction = str(patch["future_direction"]).strip()[:240]
        for step in patch.get("process_add", []) or []:
            s = str(step).strip()
            if s and s not in card.process:
                card.process.append(s)
        card.last_updated_chapter = max(card.last_updated_chapter, chapter_no)
