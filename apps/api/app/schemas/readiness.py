import uuid

from pydantic import BaseModel, ConfigDict


class ReadinessItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    application_type: str
    application_id: uuid.UUID | None
    label: str
    owner_agent: str          # which agent the Start button kicks off
    instruction: str          # templated prompt the GUI sends with the run
    stage: str
    current_agent: str | None = None
    blocked_on: list[str] | None = None
    external_ref: str | None = None
    notes: str | None = None


class ReadinessSnapshotOut(BaseModel):
    intake_complete: bool
    intake_missing_fields: list[str]
    intake_missing_documents: list[str]
    ready: list[ReadinessItemOut]
    blocked: list[ReadinessItemOut]
    in_flight: list[ReadinessItemOut]
    complete: list[ReadinessItemOut]
