import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class IntakeUpsert(BaseModel):
    """All fields optional — intake is built up incrementally, then locked
    via submit once the mandated set is complete."""

    legal_name: str | None = Field(default=None, max_length=255)
    entity_type: str | None = Field(default=None, max_length=64)
    formation_state: str | None = Field(default=None, min_length=2, max_length=2)
    ein: str | None = Field(default=None, max_length=20)
    principal_address: dict | None = None
    officer_name: str | None = Field(default=None, max_length=255)
    officer_title: str | None = Field(default=None, max_length=128)
    officer_email: str | None = Field(default=None, max_length=255)
    primary_contact_name: str | None = Field(default=None, max_length=255)
    primary_contact_email: str | None = Field(default=None, max_length=255)
    primary_contact_phone: str | None = Field(default=None, max_length=32)
    target_states: list[str] | None = None
    intends_international: bool | None = None
    estimated_monthly_revenue: float | None = Field(default=None, ge=0)
    ocn: str | None = Field(default=None, max_length=16)
    extra: dict | None = None


class RequiredDocOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    label: str
    mandatory: bool
    needs_scan: bool
    provided: bool


class CompletenessOut(BaseModel):
    complete: bool
    stage: str
    missing_fields: list[str]
    missing_documents: list[str]
    required_documents: list[RequiredDocOut]


class IntakeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    client_id: uuid.UUID
    legal_name: str | None
    entity_type: str | None
    formation_state: str | None
    ein: str | None
    principal_address: dict | None
    officer_name: str | None
    officer_title: str | None
    officer_email: str | None
    primary_contact_name: str | None
    primary_contact_email: str | None
    primary_contact_phone: str | None
    target_states: list[str] | None
    intends_international: bool
    estimated_monthly_revenue: float | None
    ocn: str | None
    extra: dict | None
    submitted_at: datetime | None


class IntakeStatus(BaseModel):
    intake: IntakeOut | None
    completeness: CompletenessOut


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: str
    version: int
    s3_key: str | None
    filename: str | None
    mime: str | None
    size_bytes: int | None
    created_at: datetime
