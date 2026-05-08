from dataclasses import dataclass
from pathlib import Path

from longbookwritter.utils.io import read_json


DEFAULT_TARGET_WORDS = 2600
DEFAULT_MIN_WORDS = 2000
DEFAULT_MAX_RETRIES = 2
DEFAULT_EXTERNAL_RETRIES = 4
DEFAULT_STYLE_CONSTRAINTS = (
    "中文小说正文；单线紧凑推进；称呼前后一致；避免AI腔与模板总结；"
    "时间线只能前进，禁止回跳时间词（除非明确回忆）；章节收尾留小钩子。"
)
DEFAULT_CHAPTER_GOAL = "主角推进主线，完成至少一个不可逆变化（关系/证据/胜负其一）。"
DEFAULT_STOP_ON_PLOT_BLOCK = True


@dataclass
class RunProfile:
    target_words: int = DEFAULT_TARGET_WORDS
    min_words: int = DEFAULT_MIN_WORDS
    max_retries: int = DEFAULT_MAX_RETRIES
    external_retries: int = DEFAULT_EXTERNAL_RETRIES
    style_constraints: str = DEFAULT_STYLE_CONSTRAINTS
    chapter_goal: str = DEFAULT_CHAPTER_GOAL
    stop_on_plot_block: bool = DEFAULT_STOP_ON_PLOT_BLOCK
    source: str = "defaults"


def _coerce_int(raw, fallback: int) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return fallback


def _coerce_bool(raw, fallback: bool) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return fallback


def load_run_profile(project_dir: Path, override_path: Path | None = None) -> RunProfile:
    """Resolve the per-book run profile.

    Priority: explicit override_path > <project_dir>/00_config/run_profile.json > defaults.
    Missing keys fall back to defaults so partial files are valid.
    """
    candidates: list[Path] = []
    if override_path is not None:
        candidates.append(override_path)
    candidates.append(project_dir / "00_config" / "run_profile.json")

    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            data = read_json(candidate)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        return RunProfile(
            target_words=_coerce_int(data.get("target_words"), DEFAULT_TARGET_WORDS),
            min_words=_coerce_int(data.get("min_words"), DEFAULT_MIN_WORDS),
            max_retries=_coerce_int(data.get("max_retries"), DEFAULT_MAX_RETRIES),
            external_retries=_coerce_int(data.get("external_retries"), DEFAULT_EXTERNAL_RETRIES),
            style_constraints=str(data.get("style_constraints") or DEFAULT_STYLE_CONSTRAINTS),
            chapter_goal=str(data.get("chapter_goal") or DEFAULT_CHAPTER_GOAL),
            stop_on_plot_block=_coerce_bool(data.get("stop_on_plot_block"), DEFAULT_STOP_ON_PLOT_BLOCK),
            source=str(candidate),
        )

    return RunProfile()
