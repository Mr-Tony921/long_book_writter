# LongBookWritter Doubao

LongBookWritter is an agent-driven co-writing system for long-form Chinese fiction.

Engineering references:

- `DEVELOPMENT_JOURNAL.md`: cross-project technical evolution notes
- `projects/<book_id>/99_engineering/`: per-project run issues/changes/decisions

Current stage: MVP foundation (M1) with:

- Project config and environment loading
- Doubao client (default lite model)
- Core agent skeletons
- Planner naming capability (serious + exaggerated + funny/eye-catching)
- Simple CLI for project init, planning, and title suggestion

## Quick Start

```bash
cd /mnt/afs_ocr/tongronglei/workspace/mathocr/9_longbookwritter_doubao
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Set env vars:

```bash
export DOUBAO_API_KEY="your_key"
export DOUBAO_MODEL="doubao-seed-2.0-260215"
export DOUBAO_LITE_MODEL="doubao-seed-2.0-lite-260215"
# default is non-stream with timeout; enable stream only when needed
# export DOUBAO_ENABLE_STREAM=true
# export DOUBAO_STREAM_FIRST=true
```

Create a book project:

```bash
python -m longbookwritter.cli init-project \
  --book-id demo_novel \
  --title "暂定名"
```

Generate initial plan:

```bash
python -m longbookwritter.cli plan \
  --book-id demo_novel \
  --brief "一个现代都市悬疑故事，主角是退役刑警。" \
  --user-mode novice \
  --must-keep "核心冲突必须围绕失踪案" \
  --banned "穿越,系统流" \
  --direction-hint "中期允许一次方向变更，但不能破坏现实逻辑"
```

Suggest titles (new planner naming feature):

```bash
python -m longbookwritter.cli suggest-titles \
  --book-id demo_novel \
  --current-stage "第10章后，主线冲突初步揭示" \
  --style mixed \
  --count 8
```

Run one chapter pipeline:

```bash
python -m longbookwritter.cli run-chapter \
  --book-id demo_novel \
  --chapter-no 1 \
  --chapter-title "雨夜失踪案" \
  --chapter-goal "主角确认第一名失踪者与旧案有关" \
  --target-words 2200 \
  --min-words 2000 \
  --style-constraints "冷峻现实，动作和对话推动，不要说教" \
  --max-retries 2
```

By default, `run-chapter` only keeps publish-ready output in `05_publish/`.
Use `--save-artifacts` only when you need draft/review files for debugging.

## Output Layout

Each book is created under:

`projects/<book_id>/`

- `00_config/`: project config
- `01_plan/`: outlines and strategy
- `02_memory/`: long-term memory files
- `03_draft/`: chapter drafts
- `04_review/`: review reports
- `05_publish/`: final chapters
- `logs/`: run logs
- `99_engineering/`: engineering notes (decisions / issues / changes / run log)
