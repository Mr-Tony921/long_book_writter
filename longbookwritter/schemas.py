from dataclasses import dataclass
from typing import Literal


TitleStyle = Literal["serious", "funny", "mixed"]
UserMode = Literal["novice", "pro"]


@dataclass
class PlanInput:
    book_id: str
    brief: str
    target_words: int = 50000
    tone: str = "中文网文，人物驱动，强情节"
    user_mode: UserMode = "novice"
    must_keep: str = ""
    banned: str = ""
    direction_hint: str = ""
