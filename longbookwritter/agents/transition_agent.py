"""Volume-boundary transition support.

The transition agent reads the master plan's ``volume_outline`` and decides
whether the upcoming chapter sits at a volume boundary (open, last few chapters
of a volume) or a daily-life buffer at the start of a new volume. It returns
guidance text that the orchestrator splices into the planner brief, and a flag
the orchestrator uses to escalate human-alert prompts before the next volume
runs out of approved plan.

The agent is intentionally codebase-generic: it does not know about a specific
book. All inputs come from the project's ``master_plan.json`` and
``run_profile.json``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TransitionContext:
    volume_no: int
    volume_label: str
    transition_in: str  # text to splice for chapters near the start of a volume
    transition_out: str  # text to splice for chapters near the end of a volume
    chapter_in_volume: int  # 1-indexed position within the volume
    chapters_remaining: int  # how many chapters left in this volume (>=0)
    is_volume_opener: bool  # true for the first ``opener_window`` chapters
    is_volume_closer: bool  # true for the last ``closer_window`` chapters
    daily_life_buffer: bool  # true when the chapter falls in the opener buffer

    def guidance_text(self) -> str:
        if self.is_volume_opener and self.transition_in:
            return (
                f"【卷 {self.volume_no} 开篇·换场指引】{self.transition_in}\n"
                f"本章在卷内第 {self.chapter_in_volume} 章；前几章应保留日常缓冲，"
                "新地点/新人物/新钩子要逐步铺开，不要在开卷第一章就压垮节奏。"
            )
        if self.is_volume_closer and self.transition_out:
            return (
                f"【卷 {self.volume_no} 收尾·钩子指引】{self.transition_out}\n"
                f"本章距卷末 {self.chapters_remaining} 章；推进必须服务于卷末高潮 + "
                "下卷悬念钩子，不可再开新支线。"
            )
        return ""


class TransitionAgent:
    def __init__(self, opener_window: int = 5, closer_window: int = 5) -> None:
        self.opener_window = max(1, opener_window)
        self.closer_window = max(1, closer_window)

    def context_for_chapter(
        self,
        chapter_no: int,
        plan_data: dict,
    ) -> TransitionContext | None:
        outline = (plan_data or {}).get("plan", {}).get("volume_outline", []) or []
        for entry in outline:
            spec = str(entry.get("chapter_range", "") or "")
            if not spec or "-" not in spec:
                continue
            try:
                lo, hi = (int(x) for x in spec.split("-", 1))
            except (ValueError, TypeError):
                continue
            if lo <= chapter_no <= hi:
                volume_no = int(entry.get("volume", 0) or 0)
                label = str(entry.get("volume_label", "") or entry.get("goal", "") or f"卷{volume_no}")
                in_text = str(entry.get("transition_in", "") or "")
                out_text = str(entry.get("transition_out", "") or "")
                pos = chapter_no - lo + 1
                remaining = max(0, hi - chapter_no)
                return TransitionContext(
                    volume_no=volume_no,
                    volume_label=label,
                    transition_in=in_text,
                    transition_out=out_text,
                    chapter_in_volume=pos,
                    chapters_remaining=remaining,
                    is_volume_opener=pos <= self.opener_window,
                    is_volume_closer=remaining < self.closer_window and remaining >= 0,
                    daily_life_buffer=pos <= self.opener_window,
                )
        return None

    def should_alert_next_volume(
        self,
        chapter_no: int,
        plan_data: dict,
        alert_window: int = 5,
    ) -> tuple[bool, int | None]:
        """Return (should_alert, next_volume_no).

        Trigger when chapter_no falls within ``alert_window`` chapters of the
        current volume's end and the next volume's plan has not been approved.
        """
        outline = (plan_data or {}).get("plan", {}).get("volume_outline", []) or []
        cur_idx: int | None = None
        for idx, entry in enumerate(outline):
            spec = str(entry.get("chapter_range", "") or "")
            if not spec or "-" not in spec:
                continue
            try:
                lo, hi = (int(x) for x in spec.split("-", 1))
            except (ValueError, TypeError):
                continue
            if lo <= chapter_no <= hi:
                cur_idx = idx
                if hi - chapter_no >= alert_window:
                    return False, None
                break
        if cur_idx is None:
            return False, None
        next_entry = outline[cur_idx + 1] if cur_idx + 1 < len(outline) else None
        if not next_entry:
            return False, None
        if bool(next_entry.get("approved", False)):
            return False, None
        return True, int(next_entry.get("volume", cur_idx + 2) or (cur_idx + 2))
