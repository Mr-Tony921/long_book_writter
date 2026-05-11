"""Persistent character and event cards.

Card files live under ``00_config/cards/characters/<slug>.json`` and
``00_config/cards/events/<slug>.json``. The schema is intentionally permissive:
every field has a sensible default so partially-filled human-authored cards
remain valid. Unknown extra fields are preserved through round-tripping.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from longbookwritter.utils.io import read_json, write_json


CHARACTER_ROLES = ("protagonist", "female_lead", "companion", "antagonist", "side")
EVENT_STATUSES = ("planned", "ongoing", "resolved", "cancelled")


@dataclass
class CharacterCard:
    name: str
    aliases: list[str] = field(default_factory=list)
    role: str = "side"
    importance: int = 1  # 1 (minor) ~ 5 (cornerstone)
    traits: list[str] = field(default_factory=list)
    goals: list[str] = field(default_factory=list)
    likes: list[str] = field(default_factory=list)
    dislikes: list[str] = field(default_factory=list)
    background: str = ""
    current_state: str = ""
    relationships: dict[str, str] = field(default_factory=dict)
    secret: str = ""
    arc_progress: str = ""
    appearance_keywords: list[str] = field(default_factory=list)
    speech_style: str = ""
    deviation_from_canon: str = ""
    last_updated_chapter: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        payload = {
            "name": self.name,
            "aliases": self.aliases,
            "role": self.role,
            "importance": self.importance,
            "traits": self.traits,
            "goals": self.goals,
            "likes": self.likes,
            "dislikes": self.dislikes,
            "background": self.background,
            "current_state": self.current_state,
            "relationships": self.relationships,
            "secret": self.secret,
            "arc_progress": self.arc_progress,
            "appearance_keywords": self.appearance_keywords,
            "speech_style": self.speech_style,
            "deviation_from_canon": self.deviation_from_canon,
            "last_updated_chapter": self.last_updated_chapter,
        }
        for k, v in self.extra.items():
            payload.setdefault(k, v)
        return payload

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "CharacterCard":
        known = {
            "name",
            "aliases",
            "role",
            "importance",
            "traits",
            "goals",
            "likes",
            "dislikes",
            "background",
            "current_state",
            "relationships",
            "secret",
            "arc_progress",
            "appearance_keywords",
            "speech_style",
            "deviation_from_canon",
            "last_updated_chapter",
        }
        extra = {k: v for k, v in payload.items() if k not in known}
        return cls(
            name=str(payload.get("name", "")).strip(),
            aliases=list(payload.get("aliases", []) or []),
            role=str(payload.get("role", "side") or "side"),
            importance=int(payload.get("importance", 1) or 1),
            traits=list(payload.get("traits", []) or []),
            goals=list(payload.get("goals", []) or []),
            likes=list(payload.get("likes", []) or []),
            dislikes=list(payload.get("dislikes", []) or []),
            background=str(payload.get("background", "") or ""),
            current_state=str(payload.get("current_state", "") or ""),
            relationships=dict(payload.get("relationships", {}) or {}),
            secret=str(payload.get("secret", "") or ""),
            arc_progress=str(payload.get("arc_progress", "") or ""),
            appearance_keywords=list(payload.get("appearance_keywords", []) or []),
            speech_style=str(payload.get("speech_style", "") or ""),
            deviation_from_canon=str(payload.get("deviation_from_canon", "") or ""),
            last_updated_chapter=int(payload.get("last_updated_chapter", 0) or 0),
            extra=extra,
        )

    def keywords(self) -> list[str]:
        out = [self.name]
        out.extend(self.aliases)
        return [k for k in out if k]


@dataclass
class EventCard:
    event_id: str
    name: str = ""
    status: str = "planned"
    planned_chapter_range: str = ""
    cause: str = ""
    process: list[str] = field(default_factory=list)
    related_characters: list[str] = field(default_factory=list)
    current_state: str = ""
    future_direction: str = ""
    anchors_to_keep: list[str] = field(default_factory=list)
    deviation_from_canon: str = ""
    last_updated_chapter: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        payload = {
            "event_id": self.event_id,
            "name": self.name,
            "status": self.status,
            "planned_chapter_range": self.planned_chapter_range,
            "cause": self.cause,
            "process": self.process,
            "related_characters": self.related_characters,
            "current_state": self.current_state,
            "future_direction": self.future_direction,
            "anchors_to_keep": self.anchors_to_keep,
            "deviation_from_canon": self.deviation_from_canon,
            "last_updated_chapter": self.last_updated_chapter,
        }
        for k, v in self.extra.items():
            payload.setdefault(k, v)
        return payload

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "EventCard":
        known = {
            "event_id",
            "name",
            "status",
            "planned_chapter_range",
            "cause",
            "process",
            "related_characters",
            "current_state",
            "future_direction",
            "anchors_to_keep",
            "deviation_from_canon",
            "last_updated_chapter",
        }
        extra = {k: v for k, v in payload.items() if k not in known}
        return cls(
            event_id=str(payload.get("event_id", "")).strip(),
            name=str(payload.get("name", "") or ""),
            status=str(payload.get("status", "planned") or "planned"),
            planned_chapter_range=str(payload.get("planned_chapter_range", "") or ""),
            cause=str(payload.get("cause", "") or ""),
            process=list(payload.get("process", []) or []),
            related_characters=list(payload.get("related_characters", []) or []),
            current_state=str(payload.get("current_state", "") or ""),
            future_direction=str(payload.get("future_direction", "") or ""),
            anchors_to_keep=list(payload.get("anchors_to_keep", []) or []),
            deviation_from_canon=str(payload.get("deviation_from_canon", "") or ""),
            last_updated_chapter=int(payload.get("last_updated_chapter", 0) or 0),
            extra=extra,
        )

    def keywords(self) -> list[str]:
        out = [self.name, self.event_id]
        out.extend(self.related_characters)
        out.extend(self.anchors_to_keep)
        return [k for k in out if k]


_SLUG_RE = re.compile(r"[^0-9A-Za-z一-鿿_-]+")


def _slugify(name: str) -> str:
    cleaned = _SLUG_RE.sub("_", name.strip())
    cleaned = cleaned.strip("_") or "unnamed"
    return cleaned[:80]


def _characters_dir(project_dir: Path) -> Path:
    return project_dir / "00_config" / "cards" / "characters"


def _events_dir(project_dir: Path) -> Path:
    return project_dir / "00_config" / "cards" / "events"


def list_character_cards(project_dir: Path) -> list[CharacterCard]:
    base = _characters_dir(project_dir)
    if not base.exists():
        return []
    cards: list[CharacterCard] = []
    for path in sorted(base.glob("*.json")):
        if path.name.startswith("_"):
            continue
        try:
            cards.append(CharacterCard.from_json(read_json(path)))
        except Exception:
            continue
    return cards


def list_event_cards(project_dir: Path) -> list[EventCard]:
    base = _events_dir(project_dir)
    if not base.exists():
        return []
    cards: list[EventCard] = []
    for path in sorted(base.glob("*.json")):
        if path.name.startswith("_"):
            continue
        try:
            cards.append(EventCard.from_json(read_json(path)))
        except Exception:
            continue
    return cards


def load_character_card(project_dir: Path, name: str) -> CharacterCard | None:
    base = _characters_dir(project_dir)
    if not base.exists():
        return None
    path = base / f"{_slugify(name)}.json"
    if not path.exists():
        # Fallback: search by name match across files.
        for cand in base.glob("*.json"):
            if cand.name.startswith("_"):
                continue
            try:
                payload = read_json(cand)
            except Exception:
                continue
            if str(payload.get("name", "")).strip() == name.strip():
                return CharacterCard.from_json(payload)
        return None
    return CharacterCard.from_json(read_json(path))


def load_event_card(project_dir: Path, event_id: str) -> EventCard | None:
    base = _events_dir(project_dir)
    if not base.exists():
        return None
    path = base / f"{_slugify(event_id)}.json"
    if not path.exists():
        for cand in base.glob("*.json"):
            if cand.name.startswith("_"):
                continue
            try:
                payload = read_json(cand)
            except Exception:
                continue
            if str(payload.get("event_id", "")).strip() == event_id.strip():
                return EventCard.from_json(payload)
        return None
    return EventCard.from_json(read_json(path))


def save_character_card(project_dir: Path, card: CharacterCard) -> Path:
    base = _characters_dir(project_dir)
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{_slugify(card.name or 'unnamed')}.json"
    write_json(path, card.to_json())
    return path


def save_event_card(project_dir: Path, card: EventCard) -> Path:
    base = _events_dir(project_dir)
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{_slugify(card.event_id or 'unnamed')}.json"
    write_json(path, card.to_json())
    return path


def select_relevant(
    chapter_no: int,
    chapter_goal: str,
    chapter_seed_focus: Iterable[str],
    character_cards: list[CharacterCard],
    event_cards: list[EventCard],
    top_k_characters: int = 8,
    top_k_events: int = 5,
) -> tuple[list[CharacterCard], list[EventCard]]:
    """Pick the cards most relevant to this chapter.

    Heuristic: weighted keyword hits on chapter_goal + chapter_seed_focus.
    Always include all protagonists and female_leads (importance >= 4) so the
    main cast travels with every chapter. Events whose planned_chapter_range
    contains chapter_no are auto-included.
    """
    haystack = chapter_goal + " " + " ".join(chapter_seed_focus or [])

    def char_score(card: CharacterCard) -> int:
        score = 0
        for kw in card.keywords():
            if kw and kw in haystack:
                score += 3
        if card.role in ("protagonist", "female_lead"):
            score += card.importance + 2
        elif card.role == "companion":
            score += card.importance
        return score

    def event_score(card: EventCard) -> int:
        score = 0
        for kw in card.keywords():
            if kw and kw in haystack:
                score += 3
        if _range_contains(card.planned_chapter_range, chapter_no):
            score += 10
        if card.status == "ongoing":
            score += 4
        return score

    chosen_chars = sorted(character_cards, key=char_score, reverse=True)[:top_k_characters]
    chosen_events = sorted(event_cards, key=event_score, reverse=True)[:top_k_events]
    # Drop pure zero-score events to avoid noise; but keep at least the highest-priority.
    chosen_events = [e for e in chosen_events if event_score(e) > 0]
    return chosen_chars, chosen_events


def _range_contains(spec: str, chapter_no: int) -> bool:
    if not spec:
        return False
    try:
        if "-" in spec:
            lo, hi = spec.split("-", 1)
            return int(lo) <= chapter_no <= int(hi)
        return int(spec) == chapter_no
    except (ValueError, TypeError):
        return False
