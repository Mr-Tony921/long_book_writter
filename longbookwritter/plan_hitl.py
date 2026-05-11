"""Human-in-the-loop volume planning.

Three CLI verbs share this module:

- ``plan-draft``: ask the LLM for a per-volume plan, write it to
  ``01_plan/draft/volume_<N>.md`` (markdown, easy for the user to edit).
- ``plan-review``: enumerate every volume's draft + approval state.
- ``plan-approve``: lock the (possibly user-edited) draft into the master plan
  by computing a SHA256 of the draft contents and stamping the matching
  ``volume_outline`` entry with ``approved=True`` and the SHA.

A book that opts into HITL via ``book_config.json["hitl"]["required"] = true``
must approve a volume before any of its chapters can run via ``run-range``.
Older projects that don't carry the ``hitl`` key keep the legacy behaviour
(no gating).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from longbookwritter.llm.base import BaseTextClient
from longbookwritter.utils.io import ensure_dir, read_json, write_json, write_text


@dataclass
class VolumeStatus:
    volume: int
    label: str
    chapter_range: str
    approved: bool
    approved_at_utc: str
    draft_path: str
    draft_exists: bool
    draft_sha: str
    locked_sha: str
    sha_match: bool


def _draft_dir(project_dir: Path) -> Path:
    return project_dir / "01_plan" / "draft"


def _draft_path(project_dir: Path, volume: int) -> Path:
    return _draft_dir(project_dir) / f"volume_{volume:02d}.md"


def _master_plan_path(project_dir: Path) -> Path:
    return project_dir / "01_plan" / "master_plan.json"


def _book_config_path(project_dir: Path) -> Path:
    return project_dir / "00_config" / "book_config.json"


def _file_sha(path: Path) -> str:
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hitl_required(project_dir: Path) -> bool:
    cfg_path = _book_config_path(project_dir)
    if not cfg_path.exists():
        return False
    try:
        cfg = read_json(cfg_path)
    except Exception:
        return False
    return bool(((cfg.get("hitl") or {}).get("required", False)))


def list_volume_statuses(project_dir: Path) -> list[VolumeStatus]:
    plan_path = _master_plan_path(project_dir)
    if not plan_path.exists():
        return []
    plan = read_json(plan_path)
    outline = (plan.get("plan", {}) or {}).get("volume_outline", []) or []
    statuses: list[VolumeStatus] = []
    for entry in outline:
        volume = int(entry.get("volume", 0) or 0)
        if volume <= 0:
            continue
        draft_path = _draft_path(project_dir, volume)
        draft_sha = _file_sha(draft_path)
        locked_sha = str(entry.get("draft_sha", "") or "")
        statuses.append(
            VolumeStatus(
                volume=volume,
                label=str(entry.get("volume_label", "") or entry.get("goal", "")),
                chapter_range=str(entry.get("chapter_range", "") or ""),
                approved=bool(entry.get("approved", False)),
                approved_at_utc=str(entry.get("approved_at_utc", "") or ""),
                draft_path=str(draft_path),
                draft_exists=draft_path.exists(),
                draft_sha=draft_sha,
                locked_sha=locked_sha,
                sha_match=bool(locked_sha and locked_sha == draft_sha),
            )
        )
    return statuses


def first_unapproved_volume_for_chapter(
    project_dir: Path,
    chapter_no: int,
) -> int | None:
    """Return the volume number that owns ``chapter_no`` if it is not approved."""
    plan_path = _master_plan_path(project_dir)
    if not plan_path.exists():
        return None
    plan = read_json(plan_path)
    outline = (plan.get("plan", {}) or {}).get("volume_outline", []) or []
    for entry in outline:
        spec = str(entry.get("chapter_range", "") or "")
        if not spec or "-" not in spec:
            continue
        try:
            lo, hi = (int(x) for x in spec.split("-", 1))
        except (ValueError, TypeError):
            continue
        if lo <= chapter_no <= hi:
            if bool(entry.get("approved", False)):
                return None
            return int(entry.get("volume", 0) or 0)
    return None


class HitlPlanner:
    def __init__(self, llm_client: BaseTextClient):
        self.llm_client = llm_client

    def draft_volume(
        self,
        project_dir: Path,
        volume: int,
        extra_brief: str = "",
        force: bool = False,
    ) -> dict[str, Any]:
        plan_path = _master_plan_path(project_dir)
        if not plan_path.exists():
            return {"ok": False, "error": "master_plan_missing"}
        plan = read_json(plan_path)
        outline = (plan.get("plan", {}) or {}).get("volume_outline", []) or []
        target = next((e for e in outline if int(e.get("volume", 0) or 0) == volume), None)
        if target is None:
            return {"ok": False, "error": f"volume_{volume}_not_in_master_plan"}

        draft_path = _draft_path(project_dir, volume)
        if draft_path.exists() and not force:
            return {
                "ok": True,
                "draft_path": str(draft_path),
                "skipped": True,
                "reason": "draft_exists; pass --force to overwrite",
            }

        ensure_dir(_draft_dir(project_dir))
        canon_ref = ""
        cfg_path = _book_config_path(project_dir)
        if cfg_path.exists():
            try:
                cfg = read_json(cfg_path)
                canon_ref = str(cfg.get("canon_reference", "") or "")
            except Exception:
                canon_ref = ""
        prompt = self._draft_prompt(
            plan=plan,
            target=target,
            extra_brief=extra_brief,
            canon_reference=canon_ref,
        )
        result = self.llm_client.generate_text(prompt_text=prompt)
        if not result.success:
            return {"ok": False, "error": f"llm_failed:{result.content[:200]}"}

        body = result.content.strip()
        header = (
            f"---\n"
            f"volume: {volume}\n"
            f"chapter_range: {target.get('chapter_range', '')}\n"
            f"label: {target.get('volume_label', target.get('goal', ''))}\n"
            f"approved: false\n"
            f"generated_at_utc: {datetime.now(timezone.utc).isoformat()}\n"
            f"---\n\n"
            f"# 卷 {volume} · {target.get('volume_label', target.get('goal', ''))}\n\n"
            "（人类作者可以直接编辑本文件。修改完成后运行 plan-approve 锁定。）\n\n"
        )
        write_text(draft_path, header + body + "\n")
        return {
            "ok": True,
            "draft_path": str(draft_path),
            "skipped": False,
            "preview": body[:400],
        }

    def approve_volume(
        self,
        project_dir: Path,
        volume: int,
    ) -> dict[str, Any]:
        plan_path = _master_plan_path(project_dir)
        if not plan_path.exists():
            return {"ok": False, "error": "master_plan_missing"}
        draft_path = _draft_path(project_dir, volume)
        if not draft_path.exists():
            return {"ok": False, "error": f"draft_missing: {draft_path}"}
        sha = _file_sha(draft_path)

        plan = read_json(plan_path)
        outline = (plan.get("plan", {}) or {}).get("volume_outline", []) or []
        matched = False
        for entry in outline:
            if int(entry.get("volume", 0) or 0) == volume:
                entry["approved"] = True
                entry["approved_at_utc"] = datetime.now(timezone.utc).isoformat()
                entry["draft_sha"] = sha
                entry["draft_path"] = str(draft_path.relative_to(project_dir))
                matched = True
                break
        if not matched:
            return {"ok": False, "error": f"volume_{volume}_not_in_master_plan"}

        plan.setdefault("plan", {})["volume_outline"] = outline
        write_json(plan_path, plan)
        return {"ok": True, "draft_sha": sha, "draft_path": str(draft_path)}

    def _draft_prompt(
        self,
        plan: dict,
        target: dict,
        extra_brief: str,
        canon_reference: str = "",
    ) -> str:
        master = plan.get("plan", {}) or {}
        positioning = master.get("book_positioning", "")
        theme = master.get("core_theme", "")
        conflict = master.get("core_conflict", "")
        ending = master.get("ending_direction", "")
        chapter_range = target.get("chapter_range", "")
        goal = target.get("goal", "")
        label = target.get("volume_label", "")
        transition_in = target.get("transition_in", "")
        transition_out = target.get("transition_out", "")
        canon_block = (
            f"原作/前作参考资料（用于本卷剧情排序建议）：\n{canon_reference}\n"
            if canon_reference.strip()
            else "原作/前作参考资料：作者未在 book_config.canon_reference 中提供——请基于"
            "全书定位与卷目标推断本卷参考脉络；不熟悉的原作请直说，不要凭空编造。\n"
        )
        return (
            "你是长篇小说卷级策划编辑。请输出可读 markdown 草案，供人类作者审改。\n"
            "硬性要求：\n"
            "1. 输出 markdown，不要 JSON、不要解释、不要免责声明。\n"
            "2. 章节列表覆盖 chapter_range 全部章节，不要漏章；每章 1-2 行（章号 + 推进点 + 钩子）。\n"
            "3. 突出本卷的换地图 / 新人物 / 新事件，给出 transition_in 与 transition_out 落地办法。\n"
            "4. 列出本卷涉及的角色卡 / 事件卡（如已存在），并标注新增者。\n"
            "5. 在“原作剧情排序建议”章节，按时间线列出本卷参考的原作/前作事件，"
            "每条注明：事件名 / 在原作中的位置 / 本书将如何对应或改写。\n"
            "6. 在“待人类作者补充”章节，留 4-6 条带 [TODO] 标记的空位（如改写偏向、要保留的"
            "原作台词、要替换的角色等），方便作者直接填写。\n"
            "7. 在末尾给出 2-3 个 A/B/C 决策点，让人类作者勾选偏好。\n\n"
            "草案结构（请严格遵守，标题顺序不可调换）：\n"
            "## 卷定位\n"
            "## 卷目标与高潮\n"
            "## 转场设计\n"
            "## 原作剧情排序建议\n"
            "## 章节列表\n"
            "## 涉及角色 / 事件卡\n"
            "## 待人类作者补充\n"
            "## 决策点\n\n"
            f"全书定位：{positioning}\n"
            f"核心主题：{theme}\n"
            f"核心冲突：{conflict}\n"
            f"结局方向：{ending}\n"
            f"本卷号：{target.get('volume', '?')}\n"
            f"卷标签：{label}\n"
            f"卷目标：{goal}\n"
            f"章节范围：{chapter_range}\n"
            f"transition_in：{transition_in or '（待编辑）'}\n"
            f"transition_out：{transition_out or '（待编辑）'}\n"
            f"{canon_block}"
            f"作者额外指示：{extra_brief or '无'}\n"
        )
