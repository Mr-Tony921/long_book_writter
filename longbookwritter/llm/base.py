from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class LLMResult:
    success: bool
    content: str
    error_type: str | None = None
    status_code: int | None = None
    raw_response: str | None = None


class BaseTextClient(ABC):
    @abstractmethod
    def generate_text(self, prompt_text: str, model: str | None = None) -> LLMResult:
        """Generate text from the given prompt."""

