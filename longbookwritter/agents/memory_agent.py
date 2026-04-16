from pathlib import Path

from longbookwritter.utils.io import read_json, write_json


class MemoryAgent:
    def load_memory(self, memory_file: Path) -> dict:
        if not memory_file.exists():
            return {
                "world_rules": [],
                "characters": [],
                "timeline": [],
                "foreshadowing": [],
                "banned_phrases": [],
            }
        return read_json(memory_file)

    def save_memory(self, memory_file: Path, payload: dict) -> None:
        write_json(memory_file, payload)

