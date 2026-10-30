from typing import Literal

from pydantic import BaseModel, Field, field_validator
class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    language: Literal["hi", "kn", "en"] = "hi"
    session_id: str | None = None

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError("Message cannot be empty or whitespace")

        return value

class ChatSource(BaseModel):
    source_name: str
    source_url: str | None = None
class ChatResponse(BaseModel):
    answer: str
    language: Literal["hi", "kn", "en"]
    session_id: str
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