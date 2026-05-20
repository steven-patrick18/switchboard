import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ClientCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    ein: str | None = Field(default=None, max_length=20)
    state: str | None = Field(default=None, min_length=2, max_length=2)
    playbook_id: str | None = Field(default=None, max_length=64)


class ClientUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    ein: str | None = Field(default=None, max_length=20)
    state: str | None = Field(default=None, min_length=2, max_length=2)
    stage: str | None = Field(default=None, max_length=64)
    playbook_id: str | None = Field(default=None, max_length=64)
    autonomy_level: str | None = Field(default=None, max_length=32)


class ClientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    ein: str | None
    state: str | None
    stage: str
    playbook_id: str | None
    autonomy_level: str
    created_at: datetime
