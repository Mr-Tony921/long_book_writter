from pathlib import Path

from longbookwritter.utils.io import ensure_dir, write_text


class PublisherAgent:
    def publish_chapter(
        self,
        publish_dir: Path,
        chapter_no: int,
        chapter_title: str,
        chapter_text: str,
    ) -> Path:
        ensure_dir(publish_dir)
        safe_title = "".join(ch for ch in chapter_title if ch not in '\\/:*?"<>|').strip() or f"chapter_{chapter_no}"
        filename = f"{chapter_no:04d}_{safe_title}.md"
        target = publish_dir / filename
        content = f"# 第{chapter_no}章 {chapter_title}\n\n{chapter_text}\n"
        write_text(target, content)
        return target

