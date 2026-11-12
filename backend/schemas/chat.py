from typing import Literal
import unicodedata
from pydantic import BaseModel, ConfigDict, Field, field_validator
class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=2000)
    language: Literal["hi", "kn", "en"] = "hi"
    session_id: str | None = Field(default=None, min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        value = "".join(
            char for char in value
            if unicodedata.category(char) != "Cc"
            or char in "\n\t"
        )

        value = value.strip()

        if not value:
            raise ValueError("Message cannot be empty or whitespace")

        return value

class ChatSource(BaseModel):
    source_name: str
    source_url: str | None = None

    # PDF source traceability
    page_start: int | None = None
    page_end: int | None = None
    source_file: str | None = None

    # Source verification and review status
    source_type: str | None = None
    verified: bool | None = None
    review_status: str | None = None
class ChatResponse(BaseModel):
    answer: str
    language: Literal["hi", "kn", "en"]
    session_id: str
    answer_message_id: int | None = None
    intent: str
    confidence: float
    entities: dict[str, list[str]]
    classification_model: str
    fallback_used: bool

    source_name: str | None = None
    source_url: str | None = None
    sources: list[ChatSource] = Field(default_factory=list)

    # Response quality scoring (Milestone 2): low scores are queued for review.
    quality_score: float | None = None
    needs_review: bool = False





from datetime import datetime, timezone


class ConversationMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class ConversationState(BaseModel):
    session_id: str
    language: Literal["hi", "kn", "en"] = "hi"
    messages: list[ConversationMessage] = Field(default_factory=list)
    preferences: dict[str, str] = Field(default_factory=dict)
    context: dict[str, str] = Field(default_factory=dict)