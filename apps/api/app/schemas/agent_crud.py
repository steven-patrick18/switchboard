import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ToolCatalogEntry(BaseModel):
    """One tool the operator can pick from the GUI catalog."""

    name: str
    description: str
    tier: str  # T0..T3 — drives the UI color and tooltip


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")
    description: str | None = Field(default=None, max_length=255)
    system_prompt: str = Field(min_length=20, max_length=20000)
    tool_names: list[str] = Field(default_factory=list, max_length=64)
    model: str | None = Field(default=None, max_length=64)
    effort: str | None = Field(default=None, pattern=r"^(low|medium|high|max|xhigh)$")
    enabled: bool = True


class AgentUpdate(BaseModel):
    description: str | None = Field(default=None, max_length=255)
    system_prompt: str | None = Field(default=None, min_length=20, max_length=20000)
    tool_names: list[str] | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=64)
    effort: str | None = Field(default=None, pattern=r"^(low|medium|high|max|xhigh)$")
    enabled: bool | None = None


class AgentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID | None  # None for built-in code-defined agents
    name: str
    description: str | None
    system_prompt: str
    tool_names: list[str]
    model: str | None
    effort: str | None
    enabled: bool
    is_builtin: bool
    lesson_count: int = 0  # How many corrections the operator has taught
    created_at: datetime | None
    updated_at: datetime | None


class LessonOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_name: str
    source: str  # rejection | edit | manual
    action_type: str | None
    lesson: str
    source_approval_id: uuid.UUID | None
    created_at: datetime


class LessonCreate(BaseModel):
    lesson: str = Field(min_length=10, max_length=4000)
